"""The HTTP surface the node's browser UI calls.

Importing this module is what registers the routes (see `__init__.py`), so registration
happens at import time, below. `server` and `folder_paths` only exist inside a running
ComfyUI process, so both imports are guarded — a plain `pytest` run (no ComfyUI on
sys.path) must not explode, it just leaves the routes unregistered. Handler functions are
still plain `async def`s that tests call directly with a fake request stub.

SECURITY: `file` and `name` arrive off the wire and get turned into filesystem paths. The
server may run `ComfyUI --listen` with no auth in front of it, so a traversal
here reads the whole filesystem. Every path this module builds goes through `_safe_join`,
which mirrors the containment check ComfyUI's own upload route uses:
`os.path.abspath` + `os.path.commonpath` at ComfyUI server.py:412-415 (v0.32.0).
"""

from __future__ import annotations

import math
import os

from aiohttp import web

from .refs import ReferenceError, validate_crop, validate_flip, validate_rotate
from . import endpoint, logs, media, prompt

try:
    from server import PromptServer
except ImportError:  # pragma: no cover - exercised only when ComfyUI isn't on sys.path
    PromptServer = None

try:
    import folder_paths
except ImportError:  # pragma: no cover - same as above
    folder_paths = None


def _safe_join(base_dir: str, name: str) -> str | None:
    """Resolve `name` under `base_dir`, or None if it could escape it.

    The ".." substring block is stricter than the commonpath check alone: it also
    catches a component like "..%2f..%2fetc" which, undecoded, contains no real path
    separator and would resolve harmlessly inside base_dir on this filesystem, but is a
    traversal payload once a client or proxy decodes it upstream of us.
    """
    if not name or not isinstance(name, str):
        return None
    if ".." in name or name.startswith("/") or name.startswith("\\") or os.path.isabs(name):
        return None
    base_dir = os.path.abspath(base_dir)
    target = os.path.abspath(os.path.join(base_dir, name))
    # CU/server.py:412-415 - the same containment check the stock upload route uses.
    try:
        if os.path.commonpath((base_dir, target)) != base_dir:
            return None
    except ValueError:
        # commonpath raises when the paths cannot be compared at all - on Windows, when
        # they are on different drives. "D:foo.txt" reaches here rather than being caught
        # above, because a DRIVE-RELATIVE path is not absolute: os.path.isabs("D:foo") is
        # False, while abspath resolves it against that drive's own working directory and
        # lands outside base_dir entirely.
        #
        # Uncaught, this was an HTTP 500 and a traceback in the ComfyUI console from an
        # unauthenticated request. Not comparable means not contained, so it is refused
        # like any other escape.
        return None
    return target


# ---- handlers ---------------------------------------------------------------
# Plain functions, not nested under the route decorators, so tests can call them
# directly with a fake request (a stub exposing .query, .match_info, async .json()).


async def probe_route(request: web.Request) -> web.Response:
    input_dir = folder_paths.get_input_directory()
    name = request.query.get("file", "")
    path = _safe_join(input_dir, name)
    if path is None or not os.path.isfile(path):
        logs.warn("probe", file=name, status=404)
        return web.Response(status=404)
    info = media.probe(path)
    logs.debug("probe", file=name, kind=info["kind"], duration=info["duration"],
               has_audio=info["has_audio"])
    return web.json_response(info)


def _same_server(a: str, b: str) -> bool:
    """Do these two URLs name the same endpoint, spelled the same way?

    PRESENTATION ONLY, and deliberately timid. It exists so that a typed api_base which
    is character-for-character one of the candidates is not listed twice, once as
    "custom" and once under its own name. It is NOT a security check and must never be
    used as one: what may be probed is decided by endpoint.is_loopback, on the literal
    string, because normalising a host is where SSRF filters go wrong.

    It does NOT fold localhost, ::1 and 127.0.0.1 together, though an earlier version
    did. They are distinct bind addresses: a server can listen on 127.0.0.1 and not on
    ::1. Folding them meant that typing `http://[::1]:1234/v1` DROPPED the 127.0.0.1
    candidate from the sweep, and then the IPv6 probe found nothing - so a running LM
    Studio vanished from the list entirely. Listing one server twice is a blemish;
    hiding a running one is the bug this button exists to prevent.

    What is folded is textual only: the case of the host (DNS is case-insensitive, so it
    is genuinely the same target) and a trailing slash.
    """
    from urllib.parse import urlparse

    def key(url: str) -> tuple:
        text = (url or "").strip().rstrip("/")
        try:
            p = urlparse(text)
            # .port parses lazily and raises on a malformed authority - `p.port` on
            # "http://127.0.0.1:1234\\v1" throws ValueError, which reached aiohttp as an
            # uncaught 500 and a traceback in the console. It has to be INSIDE the guard;
            # having it outside is what made a stray backslash - routine on Windows - kill
            # the whole sweep. Falling back to the raw text just means "not equal to
            # anything but itself", which is the safe answer for a URL we cannot parse.
            return (p.scheme, (p.hostname or "").lower(), p.port, p.path)
        except ValueError:
            return (text,)

    return key(a) == key(b)


def _parse_crop_param(raw: str | None) -> list[float] | None:
    """`crop=x,y,w,h` comma-separated fractions -> validated list; None when absent.
    Raises ValueError on junk - the route turns that into a 400, never a traceback."""
    if raw is None:
        return None
    try:
        crop = [float(p) for p in raw.split(",")]
    except ValueError:
        raise ValueError(f"crop must be four comma-separated numbers, got {raw!r}") from None
    try:
        return validate_crop(crop)
    except ReferenceError as e:
        raise ValueError(str(e)) from None


def _parse_flip_param(raw: str | None) -> str | None:
    """`flip=h|v|hv` -> validated string; None when absent."""
    if raw is None or raw == "":
        return None
    try:
        return validate_flip(raw)
    except ReferenceError as e:
        raise ValueError(str(e)) from None


def _parse_rotate_param(raw: str | None) -> float | None:
    """`rotate=<degrees>` -> validated float; None when absent."""
    if raw is None or raw == "":
        return None
    try:
        return validate_rotate(float(raw))
    except (TypeError, ValueError) as e:
        raise ValueError(f"rotate must be a number of degrees, got {raw!r}") from None
    except ReferenceError as e:
        raise ValueError(str(e)) from None


def _parse_seconds_param(raw: str | None) -> float | None:
    """`t=<seconds>` -> float; None when absent. Raises ValueError on junk."""
    if raw is None:
        return None
    try:
        t = float(raw)
    except ValueError:
        raise ValueError(f"t must be a number of seconds, got {raw!r}") from None
    if not math.isfinite(t) or t < 0:
        raise ValueError(f"t must be a finite number of seconds >= 0, got {raw!r}")
    return t


async def thumb_route(request: web.Request) -> web.Response:
    input_dir = folder_paths.get_input_directory()
    name = request.query.get("file", "")
    path = _safe_join(input_dir, name)
    if path is None or not os.path.isfile(path):
        # Not chatter: a tile asking for a file the server cannot serve is the exact
        # shape of the "no preview" bug, and it should be visible without --verbose.
        logs.warn("thumb", file=name, status=404)
        return web.Response(status=404)
    try:
        crop = _parse_crop_param(request.query.get("crop"))
        at_seconds = _parse_seconds_param(request.query.get("t"))
        # The tile and the editor's frame both come through here, so the preview has to
        # carry the orientation too - otherwise the thumbnail shows the source while the
        # socket emits the rotated frame.
        flip = _parse_flip_param(request.query.get("flip"))
        rotate = _parse_rotate_param(request.query.get("rotate"))
        expand = request.query.get("expand", "1") not in ("0", "false", "False")
    except ValueError as e:
        logs.warn("thumb", file=name, status=400, reason=str(e))
        return web.Response(status=400, text=str(e))
    # One line per tile per redraw - DEBUG, or a full reference set drowns the console.
    logs.debug("thumb", file=name, crop=crop, t=at_seconds, flip=flip, rotate=rotate)
    return web.Response(
        body=media.thumbnail_png(path, crop=crop, at_seconds=at_seconds, flip=flip,
                                 rotate=rotate, rotate_expand=expand),
        content_type="image/png",
    )


async def list_files_route(request: web.Request) -> web.Response:
    kind = request.query.get("kind", "")
    if kind not in ("image", "video", "audio"):
        return web.Response(status=400, text="kind must be image, video or audio")
    input_dir = folder_paths.get_input_directory()
    # CU/folder_paths.py:229 - the same content-type filter the core input pickers use.
    files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
    return web.json_response({"files": folder_paths.filter_files_content_types(files, [kind])})


async def system_prompt_route(request: web.Request) -> web.Response:
    """Read-only: the modal's "Load default" button. The user's edit lives in the
    workflow's `system_prompt` widget, never written back here - there is no write
    route for this on purpose.

    `mode` picks WHICH packaged prompt. This route used to call _read_system_prompt() with
    no argument at all, so the parameter's own "standard" default always won: with
    job_type on `replacement`, Load default handed back system_prompt.md - the wrong file,
    at 26KB, with no indication anything was off. The two registers are deliberately
    separate files (prompt.py: "putting both in context produces hybrids"), which is
    exactly why handing over the other one is worth catching.

    An unrecognised mode resolves to standard rather than 400ing, mirroring
    _read_system_prompt's own dict.get and the way an unknown provider or reasoning effort
    is absorbed elsewhere: a typo must not cost someone their prompt. `auto` resolves to
    standard too - which register auto picks is decided at run time, by a classifier, and
    this route has no reference set to classify.

    The resolved mode is echoed back so the modal can label what it is showing instead of
    assuming it got what it asked for.
    """
    mode = (request.query.get("mode") or "standard").strip().lower()
    if mode not in prompt._PROMPT_PATHS:
        mode = "standard"
    return web.json_response({"default": prompt._read_system_prompt(mode), "mode": mode})


async def detect_route(request):
    """Which OpenAI-compatible servers this ComfyUI can reach on the usual local ports.

    Runs the probe HERE rather than in the browser, deliberately. `localhost` has to mean
    whatever the ComfyUI process can reach: a Dockerised or pod ComfyUI must be told the
    truth about its own network instead of the user's laptop, and the browser could not
    read the responses anyway without every local server opting into CORS.

    SECURITY: `base` is loopback-only and is never resolved (endpoint.is_loopback). This
    process will happily connect to whatever it is told to, and ComfyUI often runs with no
    auth in front of it, so an unrestricted version of this route is a port scanner and an
    SSRF probe for anyone holding the URL. Only a literal 127.0.0.1 / localhost / ::1 is
    accepted; anything else is refused without a connection being attempted.
    """
    base = (request.query.get("base") or "").strip()
    if base:
        if not endpoint.is_loopback(base):
            logs.log("detect_refused", reason="not_loopback")
            return web.json_response(
                {"error": "only loopback addresses can be probed from here; type a "
                          "remote URL into api_base by hand instead"},
                status=400,
            )
        # The typed base goes in FRONT of the usual ports and the whole thing is swept in
        # one pass, rather than probing the typed one and returning only that.
        #
        # Merged because the button answers "what can I talk to", and having typed one URL
        # does not mean the sweep has nothing to add - a second server on a known port is
        # exactly the case where seeing both matters. Swept in ONE pass because
        # detect_local_servers probes concurrently: a separate probe first would cost a
        # second serial timeout, turning the advertised ~1s scan into ~10s whenever the
        # typed address has nothing listening on it.
        typed = [("custom", base)]
        rest = [c for c in endpoint.LOCAL_CANDIDATES if not _same_server(c[1], base)]
        return web.json_response({"servers": prompt.detect_local_servers(typed + rest)})

    return web.json_response({"servers": prompt.detect_local_servers()})


# ---- registration -------------------------------------------------------------

_ROUTES: tuple[tuple[str, str, object], ...] = (
    ("GET", "/minimax_refpack/probe", probe_route),
    ("GET", "/minimax_refpack/thumb", thumb_route),
    ("GET", "/minimax_refpack/files", list_files_route),
    ("GET", "/minimax_refpack/system_prompt", system_prompt_route),
    ("GET", "/minimax_refpack/detect", detect_route),
)

if PromptServer is not None and getattr(PromptServer, "instance", None) is not None:
    _method_fn = {"GET": PromptServer.instance.routes.get}
    for _method, _path, _handler in _ROUTES:
        _method_fn[_method](_path)(_handler)
