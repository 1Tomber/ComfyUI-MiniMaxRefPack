"""Local-server detection, and the SSRF guard on the route that exposes it.

The security half matters more than the feature half. This process will connect to
whatever it is told to, and ComfyUI is routinely run with `--listen` and no auth, so an
unrestricted detect route is a port scanner for anyone holding the URL. The rule is
loopback-only, and never resolved.
"""

import asyncio
import pathlib
import shutil
import subprocess
import json

import pytest

from minimax_refpack import endpoint, prompt, routes


class FakeRequest:
    def __init__(self, **query):
        self.query = query


def _json(resp):
    return json.loads(resp.body.decode())


def run(coro):
    """Matches tests/test_routes.py - this repo has no pytest-asyncio."""
    return asyncio.run(coro)


# ---- the loopback guard --------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://LOCALHOST:1234/v1",
    "https://127.0.0.1:8443/v1",
    "http://[::1]:1234/v1",
])
def test_loopback_addresses_are_allowed(url):
    assert endpoint.is_loopback(url) is True


@pytest.mark.parametrize("url", [
    "http://192.168.1.50:1234/v1",       # LAN
    "http://10.0.0.5:1234/v1",           # LAN
    "http://169.254.169.254/v1",         # cloud metadata, the classic SSRF target
    "http://evil.example/v1",
    "http://127.0.0.1.evil.example/v1",  # prefix that only LOOKS like loopback
    "http://localhost.evil.example/v1",
    "file:///etc/passwd",
    "ftp://127.0.0.1/v1",
    "",
    "not a url",
])
def test_everything_else_is_refused(url):
    assert endpoint.is_loopback(url) is False


def test_a_hostname_that_would_resolve_to_loopback_is_still_refused():
    """Refusing to resolve at all is what closes DNS rebinding. A name that points at
    127.0.0.1 today can point somewhere else on the next lookup."""
    assert endpoint.is_loopback("http://my-local-box.internal/v1") is False


@pytest.mark.parametrize("url", [
    # THE EXPLOIT. urlparse reads the backslash as part of userinfo and reports host
    # 127.0.0.1; urllib3 - which is what requests dials with - ends the authority at the
    # backslash and connects to evil.example. Validating with one parser and connecting
    # with another is the whole bug.
    "http://evil.example\\@127.0.0.1/v1",
    "http://evil.example:8443\\@localhost/v1",
    "http://169.254.169.254\\@127.0.0.1/v1",   # metadata service, smuggled
    # Userinfo without the trick, refused on its own account: no local model server needs
    # it, and it is the delivery mechanism for the above.
    "http://evil.example@127.0.0.1/v1",
    "http://user:pass@127.0.0.1/v1",
    # Characters that make two parsers read one string as two different hosts.
    "http://127.0.0.1 /v1",
    "http://127.0.0.1\r\nHost: evil.example/v1",
    "http://127.0.0.1\t/v1",
])
def test_a_url_two_parsers_read_differently_is_refused(url):
    """The gate and the HTTP client must agree on the host, or the gate is decoration.

    Anyone able to reach ComfyUI - which is routinely unauthenticated - could otherwise
    hand this route an arbitrary host and port, get a connection made from inside the
    network, and read back which ones answered: an SSRF probe and a port scanner.
    """
    assert endpoint.is_loopback(url) is False


def test_a_backslash_alone_is_a_typo_not_an_attack():
    """A backslash only smuggles when it sits in front of userinfo.

    On its own both parsers agree - the host of `http://127.0.0.1:1234\\v1` is 127.0.0.1
    to urlparse AND to urllib3 - and typing one instead of a slash is a routine Windows
    slip. Refusing it bought nothing and told the user their loopback address was not a
    loopback address. What makes the smuggling work is the `@`, which is refused outright.
    """
    backslash = chr(92)
    assert endpoint.is_loopback(f"http://127.0.0.1:1234{backslash}v1") is True
    assert endpoint.is_loopback(f"http://evil.example{backslash}@127.0.0.1/v1") is False


def test_the_gate_agrees_with_the_parser_that_dials():
    """Belt and braces: whatever urlparse says, the host urllib3 would actually connect to
    has to be loopback too. Pins the invariant rather than the one known trick, so a future
    normalisation quirk fails closed instead of quietly reopening this."""
    urllib3 = pytest.importorskip("urllib3")
    for url in ["http://127.0.0.1:1234/v1", "http://localhost:11434/v1",
                "http://[::1]:8080/v1"]:
        assert endpoint.is_loopback(url) is True
        dialled = (urllib3.util.parse_url(url).host or "").strip("[]").lower()
        assert dialled in {"127.0.0.1", "localhost", "::1"}


def test_models_probe_does_not_follow_redirects():
    """is_loopback only ever vets the base. If redirects were followed, a loopback server
    answering 302 - an open redirect, or simply a hostile one on a port the user was told
    to try - would walk this process off the box to anywhere, 169.254.169.254 included.

    Driven against two real sockets rather than by reading the source: an assertion that
    greps for `allow_redirects=False` is satisfied by the COMMENT that explains it, so it
    would stay green with the argument itself deleted.
    """
    import http.server
    import socketserver
    import threading

    from minimax_refpack import prompt

    reached = []

    def serve(handler):
        srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, srv.server_address[1]

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append("TARGET")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "should-not-be-reachable"}]}')

        def log_message(self, *a):
            pass

    target_srv, target_port = serve(Target)

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append("REDIRECTOR")
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target_port}/models")
            self.end_headers()

        def log_message(self, *a):
            pass

    redir_srv, redir_port = serve(Redirector)

    try:
        result = prompt._models_at(f"http://127.0.0.1:{redir_port}/v1")
    finally:
        redir_srv.shutdown()
        target_srv.shutdown()

    assert "TARGET" not in reached, (
        "the probe followed a redirect off the base it was given; only the base is vetted, "
        "so this reaches any host the redirect names"
    )
    assert reached == ["REDIRECTOR"]
    assert result is None, "a 302 is not an OpenAI-compatible /models answer"


# ---- the route -----------------------------------------------------------------


def test_the_route_refuses_a_non_loopback_base_without_connecting(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("no connection may be attempted for a refused host")

    monkeypatch.setattr(prompt, "_models_at", explode)
    resp = run(routes.detect_route(FakeRequest(base="http://169.254.169.254/v1")))
    assert resp.status == 400
    assert "loopback" in _json(resp)["error"]


def test_the_route_probes_an_explicit_loopback_base(monkeypatch):
    monkeypatch.setattr(prompt, "_models_at", lambda base, *a, **k: ["m1", "m2"])
    resp = run(routes.detect_route(FakeRequest(base="http://127.0.0.1:9999/v1")))
    body = _json(resp)
    assert body["servers"][0]["base"] == "http://127.0.0.1:9999/v1"
    assert body["servers"][0]["models"] == ["m1", "m2"]


def test_a_loopback_base_with_nothing_listening_returns_no_servers(monkeypatch):
    monkeypatch.setattr(prompt, "_models_at", lambda base, *a, **k: None)
    resp = run(routes.detect_route(FakeRequest(base="http://127.0.0.1:9999/v1")))
    assert _json(resp)["servers"] == []


def test_with_no_base_it_sweeps_the_known_candidates(monkeypatch):
    monkeypatch.setattr(prompt, "detect_local_servers",
                        lambda *a, **k: [{"label": "LM Studio",
                                          "base": "http://127.0.0.1:1234/v1",
                                          "models": ["gemma"]}])
    resp = run(routes.detect_route(FakeRequest()))
    assert _json(resp)["servers"][0]["label"] == "LM Studio"


# ---- what counts as a server ---------------------------------------------------


def test_a_port_that_answers_with_the_wrong_shape_is_not_a_server(monkeypatch):
    """8000 and 8080 are shared with most dev tooling. "Something answered" proves
    nothing, so the response has to look like an OpenAI model list."""
    class Resp:
        status_code = 200

        def json(self):
            return {"message": "hello from some unrelated dev server"}

    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: Resp())
    assert prompt._models_at("http://127.0.0.1:8080/v1") is None


def test_a_non_200_is_not_a_server(monkeypatch):
    class Resp:
        status_code = 404

        def json(self):
            return {"data": []}

    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: Resp())
    assert prompt._models_at("http://127.0.0.1:8080/v1") is None


def test_a_closed_port_is_not_a_server(monkeypatch):
    def refused(*a, **k):
        raise prompt.requests.ConnectionError("refused")

    monkeypatch.setattr(prompt.requests, "get", refused)
    assert prompt._models_at("http://127.0.0.1:9/v1") is None


def test_a_real_server_with_nothing_loaded_is_still_a_server(monkeypatch):
    """An empty list is a real answer and must not read as "no server"."""
    class Resp:
        status_code = 200

        def json(self):
            return {"data": []}

    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: Resp())
    assert prompt._models_at("http://127.0.0.1:1234/v1") == []


def test_malformed_entries_are_skipped_not_fatal(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "good"}, {"no_id": 1}, "not-a-dict", {"id": 42}]}

    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: Resp())
    assert prompt._models_at("http://127.0.0.1:1234/v1") == ["good"]


# ---- the sweep -----------------------------------------------------------------


def test_the_sweep_reports_only_what_answered_and_keeps_candidate_order(monkeypatch):
    answers = {
        "http://127.0.0.1:1234/v1": ["gemma"],
        "http://127.0.0.1:11434/v1": ["qwen"],
    }
    monkeypatch.setattr(prompt, "_models_at", lambda base, *a, **k: answers.get(base))
    found = prompt.detect_local_servers()
    assert [f["base"] for f in found] == [
        "http://127.0.0.1:1234/v1", "http://127.0.0.1:11434/v1",
    ]
    assert found[0]["label"] == "LM Studio"
    assert found[1]["label"] == "Ollama"


def test_the_sweep_survives_every_port_being_closed(monkeypatch):
    monkeypatch.setattr(prompt, "_models_at", lambda *a, **k: None)
    assert prompt.detect_local_servers() == []


def test_the_candidate_list_is_loopback_only():
    """A candidate pointing off-box would make the no-argument sweep a scanner."""
    for _label, base in endpoint.LOCAL_CANDIDATES:
        assert endpoint.is_loopback(base), base


# ---- the typed api_base joins the sweep (issue #3) -------------------------------


def test_a_typed_base_is_swept_alongside_the_known_ports(monkeypatch):
    """#3: a server on a port outside LOCAL_CANDIDATES was invisible to the button even
    with its URL already sitting in api_base."""
    monkeypatch.setattr(prompt, "_models_at", lambda base, *a, **k:
                        ["mine"] if ":9999" in base else ["stock"])
    resp = run(routes.detect_route(FakeRequest(base="http://127.0.0.1:9999/v1")))
    servers = _json(resp)["servers"]
    bases = [s["base"] for s in servers]
    assert bases[0] == "http://127.0.0.1:9999/v1", "the typed base leads"
    assert "http://127.0.0.1:1234/v1" in bases, "the known ports are still swept"


def test_the_typed_base_is_probed_in_the_same_pass_not_a_second_one(monkeypatch):
    """One concurrent sweep, not a serial probe then a sweep. A serial version costs a
    second full timeout whenever the typed address has nothing listening, which turns the
    advertised ~1s scan into ~10s."""
    calls = []
    monkeypatch.setattr(prompt, "detect_local_servers",
                        lambda candidates=None, *a, **k: calls.append(candidates) or [])
    run(routes.detect_route(FakeRequest(base="http://127.0.0.1:9999/v1")))
    assert len(calls) == 1, "detect_local_servers must be called exactly once"
    assert calls[0][0] == ("custom", "http://127.0.0.1:9999/v1")


@pytest.mark.parametrize("typed", [
    "http://localhost:1234/v1",     # same server, spelled by name
    "http://127.0.0.1:1234/v1",     # same server, spelled by address
    "http://127.0.0.1:1234/v1/",    # same server, trailing slash
])
def test_a_typed_base_that_is_already_a_candidate_is_listed_once(monkeypatch, typed):
    """Otherwise LM Studio appears twice: once as "custom", once under its own name."""
    monkeypatch.setattr(prompt, "_models_at", lambda *a, **k: ["m"])
    resp = run(routes.detect_route(FakeRequest(base=typed)))
    keyed = [routes._same_server(s["base"], typed) for s in _json(resp)["servers"]]
    assert keyed.count(True) == 1, _json(resp)["servers"]


def test_same_server_folds_spellings_of_one_address_and_nothing_more():
    # Case of the host, and a trailing slash. DNS is case-insensitive, so it really is
    # the same target.
    assert routes._same_server("http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/")
    assert routes._same_server("http://LOCALHOST:1234/v1", "http://localhost:1234/v1")
    # Two hosts sharing a port are two servers.
    assert not routes._same_server("http://127.0.0.1:1234/v1", "http://127.0.0.1:8080/v1")
    assert not routes._same_server("http://192.168.1.5:1234/v1", "http://10.0.0.5:1234/v1")
    assert not routes._same_server("http://127.0.0.1:1234/v1", "https://127.0.0.1:1234/v1")


@pytest.mark.parametrize("other", ["http://localhost:1234/v1", "http://[::1]:1234/v1"])
def test_the_loopback_spellings_are_NOT_folded_together(other):
    """An earlier version folded localhost and ::1 onto 127.0.0.1 to avoid listing one
    server twice. That hid running servers: they are distinct BIND addresses, so a
    server can answer on 127.0.0.1 and not on ::1. With the fold, typing
    http://[::1]:1234/v1 dropped the 127.0.0.1 candidate from the sweep, the IPv6 probe
    then found nothing, and a running LM Studio disappeared from the modal entirely.

    Listing one server twice is a blemish. Hiding a running one defeats the button."""
    assert not routes._same_server(other, "http://127.0.0.1:1234/v1")


def test_a_server_on_127_0_0_1_survives_a_typed_ipv6_base(monkeypatch):
    """The scenario above, end to end: only the IPv4 address answers."""
    monkeypatch.setattr(prompt, "_models_at",
                        lambda base, *a, **k: ["m"] if "127.0.0.1:1234" in base else None)
    resp = run(routes.detect_route(FakeRequest(base="http://[::1]:1234/v1")))
    bases = [s["base"] for s in _json(resp)["servers"]]
    assert "http://127.0.0.1:1234/v1" in bases, "the running server must not vanish"


@pytest.mark.parametrize("base", [
    "http://127.0.0.1:1234\v1",   # backslash instead of slash - routine on Windows
    "http://127.0.0.1:99999/v1",     # port out of range
    "http://localhost:abc/v1",       # port not a number
    "http://127.0.0.1:-1/v1",
])
def test_a_loopback_base_with_an_unparseable_port_does_not_500(monkeypatch, base):
    """urlparse().port parses LAZILY and raises on a malformed authority, so reading it
    outside the try turned a stray character into an uncaught ValueError - HTTP 500 and a
    traceback in the ComfyUI console, with the modal showing "Could not scan". On main
    the same input returned 200 and an empty list, so this was a regression, not a
    pre-existing sharp edge."""
    monkeypatch.setattr(prompt, "_models_at", lambda *a, **k: None)
    resp = run(routes.detect_route(FakeRequest(base=base)))
    assert resp.status == 200


def test_a_non_loopback_base_is_still_refused_even_though_the_sweep_now_merges(monkeypatch):
    """The merge must not have become a way in. Same refusal, no connection attempted."""
    monkeypatch.setattr(prompt, "detect_local_servers",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not sweep")))
    monkeypatch.setattr(prompt, "_models_at",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not probe")))
    resp = run(routes.detect_route(FakeRequest(base="http://169.254.169.254/v1")))
    assert resp.status == 400


# ---- the browser's copy of the predicate ----------------------------------------
#
# web/refpack.js carries its own isLoopbackUrl so a LAN api_base gets an explanation on
# the modal instead of a 400 the user never sees. It is not an enforcement point -- the
# route above is -- but a copy that DISAGREES with the original is its own bug: it would
# either withhold a probe the server would have allowed, or promise one it will refuse.
# So the two are tested against each other on the same inputs, not separately.

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract_loopback_js() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-LOOPBACK")
    end = text.find("// <<< MMRP-LOOPBACK")
    assert start != -1 and end != -1, (
        "the MMRP-LOOPBACK markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them, so removing them silently stops the browser's "
        "half of the loopback rule from being covered at all"
    )
    return text[start:end]


AGREEMENT_CASES = [
    "http://127.0.0.1:1234/v1",
    "http://localhost:1234/v1",
    "http://LOCALHOST:1234/v1",
    "https://127.0.0.1:8443/v1",
    "http://[::1]:1234/v1",
    "http://192.168.1.50:1234/v1",
    "http://10.0.0.5:1234/v1",
    "http://169.254.169.254/v1",
    "http://evil.example/v1",
    "http://127.0.0.1.evil.example/v1",
    "http://localhost.evil.example/v1",
    "file:///etc/passwd",
    "ftp://127.0.0.1/v1",
    "",
    "not a url",
    "  http://127.0.0.1:1234/v1  ",
]


@requires_node
def test_the_browser_copy_agrees_with_endpoint_is_loopback():
    script = _extract_loopback_js() + (
        f"\nconsole.log(JSON.stringify({json.dumps(AGREEMENT_CASES)}"
        ".map((u) => isLoopbackUrl(u))));\n"
    )
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    from_js = json.loads(proc.stdout.strip())
    from_py = [endpoint.is_loopback(u) for u in AGREEMENT_CASES]
    disagree = [
        (u, p, j) for u, p, j in zip(AGREEMENT_CASES, from_py, from_js) if p != j
    ]
    assert not disagree, f"JS and Python disagree on: {disagree}"
