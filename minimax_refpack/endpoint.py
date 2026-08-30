"""Where a chat call goes, what it may carry, and how long to wait for it.

One value object so that "am I talking to OpenRouter or to something on localhost" is
answered once, at the top of a run, instead of being re-derived at each of the three
places that used to hardcode a URL (the writer, the classifier, the model list).

Deliberately pure: no network, no key lookup, no exceptions except the one programmer
error of asking for a local endpoint without saying where. Key POLICY lives in prompt.py,
because `_resolve_api_key` is a pinned surface with its own tests and because raising
PromptError from here would import the module that imports this one.

The security-relevant part: a `local` endpoint is built only from the base URL the user
typed. It can never fall back to OpenRouter's host, which is what makes "nothing leaves
the machine" a property of the code rather than a promise in the README.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

PROVIDERS = ("openrouter", "local", "none")
DEFAULT_PROVIDER = "openrouter"

# OpenRouter reports per-model input modalities and the node filters on all four
# (prompt.py:35). Nothing else does, so a local endpoint is assumed to be a vision chat
# model and no more. Confirmed against Ollama's OpenAI-compatibility docs 2026-08-17:
# /v1/chat/completions documents base64 images; video and audio are not mentioned, and
# /v1/models carries no capability field to probe.
_OPENROUTER_ACCEPTS = frozenset({"text", "image", "audio", "video"})
_LOCAL_ACCEPTS = frozenset({"text", "image"})

# OpenRouter's numbers are today's constants, kept byte for byte so the openrouter path
# is unchanged. The local numbers assume the worst realistic case rather than the best:
# a large model on CPU, first token minutes away. A too-long timeout costs a user who
# already knows their machine is slow; a too-short one looks like a broken node.
_OPENROUTER_TIMEOUTS = {"chat": 120, "classify": 20, "models": 5}
_LOCAL_TIMEOUTS = {"chat": 900, "classify": 120, "models": 5}


# Where a local server is likely to be. Probed in this order and reported in this order,
# so the answer is stable between runs. 127.0.0.1 rather than "localhost" on purpose: it
# skips name resolution, which on a machine with a slow or IPv6-first resolver is most of
# the probe's latency.
#
# 8000 and 8080 are shared with half the dev tooling in existence, so finding something
# listening there proves nothing. That is why the probe validates the RESPONSE SHAPE
# before reporting a server, instead of trusting the port.
LOCAL_CANDIDATES = (
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
    ("Jan", "http://127.0.0.1:1337/v1"),
    ("text-generation-webui", "http://127.0.0.1:5000/v1"),
)

# Literal loopback only, and deliberately NOT resolved. Accepting a hostname and looking
# it up would turn the detect route into a scanner: ComfyUI often runs with no auth in
# front of it, so anyone holding the proxy URL could ask this process to probe arbitrary
# hosts and read back which ones answered. Refusing to resolve at all closes that, and
# closes DNS rebinding with it. A user with a server somewhere else still types the URL
# into api_base by hand; they just do not get to verify it through this route.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Idle-unload, and why it is not one field.
#
# A model that stays resident between calls is a real problem rather than a tidiness
# one: a 27B Q4 vision model and a diffusion model cannot both hold VRAM on one card, so
# a JIT-loaded prompt writer that squats after answering costs the user the generation
# it was writing the prompt FOR. Both popular local servers can unload on idle, and they
# disagree about the field AND about what zero means:
#
#   LM Studio   `ttl`         seconds. ttl:0 is treated as UNSET and falls back to the
#                             60-minute default, so 1 is its real "unload now".
#   Ollama      `keep_alive`  seconds. keep_alive:0 genuinely means unload immediately.
#
# So `0` cannot be our "off" without either lying to one server or silently rewriting
# the user's number for the other. LOCAL_TTL_OFF is -1: below zero we send nothing at
# all, and any value >= 0 goes on the wire untouched, meaning whatever that server means
# by it. Un-clever on purpose - the alternative is a remapping table nobody can predict.
LOCAL_TTL_OFF = -1

LOCAL_SERVERS = ("auto", "lmstudio", "ollama", "generic")
DEFAULT_LOCAL_SERVER = "auto"

_TTL_FIELD_BY_SERVER = {"lmstudio": "ttl", "ollama": "keep_alive", "generic": None}

# What `auto` infers from, and the ONLY thing it can infer from: an OpenAI-compatible
# /v1 surface reports nothing about which server is behind it. Ports are a guess, so the
# guess is stated in `debug` rather than made silently, and an unrecognised port sends no
# idle-unload field at all rather than picking one and hoping.
_TTL_FIELD_BY_PORT = {1234: "ttl", 11434: "keep_alive"}


def is_loopback(url: str) -> bool:
    """True when this URL names the machine ComfyUI itself is running on, literally.

    Deliberately strict about the AUTHORITY rather than clever about it, because this
    function is a security gate and the thing it guards is dialled by a DIFFERENT parser.

    That gap was exploitable. `urlparse` reads `http://evil.com\\@127.0.0.1/v1` as
    userinfo `evil.com\\` and host `127.0.0.1`, so the gate passed it - while urllib3,
    which is what requests actually dials with, ends the authority at the backslash and
    connects to `evil.com`. Validating with one parser and connecting with another is the
    whole bug class; a single crafted query turned this route into a working SSRF probe and
    internal port scanner against a ComfyUI that usually has no auth in front of it.

    So rather than trying to out-parse the attacker, anything whose authority is not
    boringly unambiguous is refused:

      * whitespace and control characters, which are never legitimate in a URL and are
        the shape of a header-injection attempt
      * userinfo at all - `http://anything@127.0.0.1/` has no legitimate use for a local
        model server, and it is the delivery mechanism for the confusion above. A
        backslash on its OWN is fine and stays allowed: both parsers agree the host of
        `http://127.0.0.1:1234\v1` is 127.0.0.1, and typing one instead of a slash is a
        routine Windows slip. It is only a backslash IN FRONT OF userinfo that smuggles,
        and the rule above already refuses that
      * and finally the host itself, checked twice: once through urlparse and once through
        the parser the HTTP client will really use, which must agree.

    A false negative here costs a user one manual paste into api_base. A false positive
    costs them an open proxy.
    """
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        return False
    # Reject before parsing: these are exactly the characters that make two parsers read
    # one string as two different hosts.
    if any(c.isspace() or ord(c) < 0x20 for c in raw):
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    # Userinfo is the vector, so it is refused whole rather than parsed around.
    if "@" in parsed.netloc:
        return False
    host = (parsed.hostname or "").strip().lower()
    if host not in _LOOPBACK_HOSTS:
        return False
    # Second opinion from the parser that will do the dialling. The checks above already
    # close the known differential; this is here so that the NEXT one - some future
    # normalisation quirk in urllib3 - fails closed instead of silently reopening the hole.
    # Best-effort: if urllib3 is not importable the strict authority rules still stand on
    # their own, and refusing outright would break detection on a machine that can still
    # perfectly well run the node.
    try:
        import urllib3

        dialled = (urllib3.util.parse_url(raw).host or "").strip().lower()
    except ImportError:
        return True
    except Exception:
        # The dialling parser REFUSED the URL - a port out of range, a non-numeric port, a
        # stray control character. requests raises InvalidURL on exactly these, so no
        # connection can be made to anywhere and there is nothing to gate. Allowing it
        # keeps the existing contract that a typo yields "no servers found" rather than a
        # refusal that blames the wrong thing: telling someone who mistyped a port that
        # only loopback addresses are allowed sends them looking for the wrong mistake.
        return True
    return dialled.strip("[]") in {h.strip("[]") for h in _LOOPBACK_HOSTS}


@dataclass(frozen=True)
class Endpoint:
    provider: str
    chat_url: str
    models_url: str
    accepts: frozenset
    chat_timeout: int
    classify_timeout: int
    models_timeout: int
    sends_reasoning: bool
    requires_key: bool
    is_openrouter: bool
    # How this endpoint spells a reasoning request. OpenRouter normalises a NESTED
    # `reasoning: {effort}` across providers; a plain OpenAI-compatible server takes the
    # FLAT `reasoning_effort` and has never heard of the other. Empty = send neither,
    # which is what `sends_reasoning: False` has always meant.
    reasoning_style: str = ""
    # Extra top-level fields merged into the chat payload. Everything server-specific
    # that is not a message lives here rather than in prompt.py, so "where a call goes
    # and what it may carry" stays one answer in one object.
    extra_body: dict = field(default_factory=dict)
    # Which server `auto` decided it was talking to, purely so `debug` can say. Never
    # used to make a second decision.
    flavor: str = ""

    def carries(self, kind: str) -> bool:
        """True when a content part of this kind can go on the wire as-is."""
        return kind in self.accepts

    @property
    def degrades(self) -> bool:
        """True when this endpoint cannot take everything the reference set may hold."""
        return not _OPENROUTER_ACCEPTS <= self.accepts

    def describe(self) -> str:
        """One short phrase for a log line or a node warning."""
        if self.is_openrouter:
            return "openrouter"
        return f"local ({self.chat_url})"

    def describe_extras(self) -> str:
        """What this endpoint adds to the payload beyond the messages, and who decided.

        Written for `debug`. An idle-unload field that silently did nothing - because the
        port was not recognised, or because the server was set to `generic` - is
        indistinguishable from one that worked, right up until the model is still holding
        VRAM. So the absence is reported as loudly as the presence.
        """
        if self.is_openrouter:
            return ""
        bits = [f"server: {self.flavor}"]
        if self.extra_body:
            bits.append("extra fields: " + ", ".join(
                f"{k}={v!r}" for k, v in sorted(self.extra_body.items())
            ))
        else:
            bits.append("extra fields: none (no idle-unload field is being sent)")
        bits.append(
            f"reasoning: {self.reasoning_style or 'not sent'}"
        )
        return " · ".join(bits)


def normalize_provider(raw) -> str:
    """Any value that can reach the widget -> one of PROVIDERS.

    Total by design. `prompt_provider` replaced a BOOLEAN in the same widget slot, so a
    workflow saved at 0.3.1 restores True or False here, and litegraph will happily hand
    over whatever was in the JSON. An unrecognised value resolves to the default rather
    than raising, mirroring how an unknown reasoning_effort is dropped instead of sent
    (prompt.py:510-511): a typo must never cost someone a whole queue.
    """
    if raw is True:
        return "openrouter"
    if raw is False:
        return "none"
    text = str(raw or "").strip().lower()
    if text in PROVIDERS:
        return text
    if text == "true":
        return "openrouter"
    if text == "false":
        return "none"
    return DEFAULT_PROVIDER


def normalize_local_server(raw) -> str:
    """Any value that can reach the widget -> one of LOCAL_SERVERS. Total, like
    normalize_provider and for the same reason."""
    text = str(raw or "").strip().lower()
    return text if text in LOCAL_SERVERS else DEFAULT_LOCAL_SERVER


def ttl_field_for(server: str, base: str) -> tuple[str | None, str]:
    """(field name to carry an idle-unload TTL, the flavour that decided it).

    `auto` reads the port, because nothing else is available: an OpenAI-compatible /v1
    surface does not say what is behind it, and /v1/models carries no vendor field.
    An unrecognised port yields None - no field at all - rather than a coin flip, since
    sending `ttl` to a server that has never heard of it is how a working setup turns
    into a 400 nobody can explain.
    """
    server = normalize_local_server(server)
    if server != "auto":
        return _TTL_FIELD_BY_SERVER.get(server), server

    from urllib.parse import urlparse

    try:
        port = urlparse((base or "").strip()).port
    except ValueError:
        port = None
    field_name = _TTL_FIELD_BY_PORT.get(port)
    if field_name is None:
        return None, "auto:unrecognised"
    flavor = "lmstudio" if field_name == "ttl" else "ollama"
    return field_name, f"auto:{flavor}"


# Fields local_extra_body may not set, because the node's own account of the run depends
# on owning them. `messages` IS the prompt this node exists to assemble - replacing it
# from a text box throws away every reference and, with an empty list, crashes the reader
# that pulls the content parts back out for the size log. `model` is what the debug header
# and the widget both state was used, so overriding it here makes the node lie about where
# the completion came from. Everything else is fair game: that is the point of the field.
_EXTRA_BODY_RESERVED = frozenset({"messages", "model"})


def parse_extra_body(raw) -> dict:
    """The `local_extra_body` widget -> a dict of top-level payload fields.

    Blank is empty, and anything else must be a JSON OBJECT. A list or a bare string
    would merge into nothing sensible, so it is refused here rather than silently
    dropped: this field exists precisely for the case where a server wants something
    this node has never heard of, and a typo that quietly sends nothing would look
    exactly like a server that ignored it.
    """
    import json

    text = (raw or "").strip() if isinstance(raw, str) else raw
    if not text:
        return {}
    parsed = dict(text) if isinstance(text, dict) else None
    if parsed is None:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as e:
            raise ValueError(f"local_extra_body is not valid JSON: {e}") from None
    if not isinstance(parsed, dict):
        raise ValueError(
            f"local_extra_body must be a JSON object like {{\"ttl\": 1}}, got "
            f"{type(parsed).__name__}"
        )
    clashes = sorted(_EXTRA_BODY_RESERVED & set(parsed))
    if clashes:
        raise ValueError(
            f"local_extra_body may not set {', '.join(clashes)}: the node builds "
            f"`messages` from your references and reports `model` in its debug output, "
            f"so overriding either would make it disagree with what it actually sent. "
            f"Use the model widget for the model."
        )
    return parsed


def resolve(
    provider,
    api_base: str = "",
    *,
    local_ttl: int = LOCAL_TTL_OFF,
    local_server: str = DEFAULT_LOCAL_SERVER,
    local_send_reasoning: bool = False,
    local_extra_body: str = "",
) -> Endpoint:
    """The endpoint for a provider. `none` never gets here - it makes no call at all.

    Every local_* argument is keyword-only and defaults to today's behaviour, so every
    existing caller and test that omits them resolves exactly the endpoint it did before.
    """
    provider = normalize_provider(provider)

    if provider == "local":
        base = (api_base or "").strip().rstrip("/")
        if not base:
            raise ValueError(
                "prompt_provider is 'local' but api_base is empty: give the node the "
                "base URL of your server, for example http://localhost:1234/v1"
            )
        # Order matters: the TTL goes in first and the user's own JSON is merged over it,
        # so local_extra_body is always the last word. It is the escape hatch for a server
        # this node has not been taught about, and an escape hatch that could be overridden
        # by a guess would not be one.
        ttl_name, flavor = ttl_field_for(local_server, base)
        extra: dict = {}
        if local_ttl is not None and int(local_ttl) >= 0 and ttl_name:
            extra[ttl_name] = int(local_ttl)
        extra.update(parse_extra_body(local_extra_body))
        return Endpoint(
            provider="local",
            chat_url=f"{base}/chat/completions",
            models_url=f"{base}/models",
            accepts=_LOCAL_ACCEPTS,
            chat_timeout=_LOCAL_TIMEOUTS["chat"],
            classify_timeout=_LOCAL_TIMEOUTS["classify"],
            models_timeout=_LOCAL_TIMEOUTS["models"],
            # Still False by default. A plain OpenAI-compatible server is more likely to
            # reject an unknown top-level field outright than to ignore it, so turning a
            # working local setup into a 400 is not something to do on the user's behalf.
            sends_reasoning=bool(local_send_reasoning),
            requires_key=False,
            is_openrouter=False,
            reasoning_style="flat" if local_send_reasoning else "",
            extra_body=extra,
            flavor=flavor,
        )

    return Endpoint(
        provider="openrouter",
        chat_url=f"{OPENROUTER_BASE}/chat/completions",
        models_url=f"{OPENROUTER_BASE}/models",
        accepts=_OPENROUTER_ACCEPTS,
        chat_timeout=_OPENROUTER_TIMEOUTS["chat"],
        classify_timeout=_OPENROUTER_TIMEOUTS["classify"],
        models_timeout=_OPENROUTER_TIMEOUTS["models"],
        sends_reasoning=True,
        requires_key=True,
        is_openrouter=True,
        # OpenRouter normalises the NESTED shape across providers and drops it for models
        # that do not reason. Unchanged, byte for byte.
        reasoning_style="nested",
        extra_body={},
        flavor="openrouter",
    )
