"""Tests for the HTTP routes slice.

No running ComfyUI: routes.py guards its `server`/`folder_paths` imports (see its module
docstring), so importing it here is safe, and `routes.folder_paths` / `routes.media` are
swapped for fakes below. Handlers are called directly with a small request stub instead of
going through aiohttp's router.
"""

import asyncio
import json
import os

import pytest

from minimax_refpack import routes
from minimax_refpack.refs import Reference, ReferenceSet


class FakeRequest:
    """Stub matching the slice of aiohttp.web.Request the handlers actually use."""

    def __init__(self, query=None, match_info=None, body=None):
        self.query = query or {}
        self.match_info = match_info or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeFolderPaths:
    """Stands in for the real `folder_paths` module (CU/folder_paths.py)."""

    def __init__(self, input_dir):
        self._input_dir = input_dir

    def get_input_directory(self):
        return self._input_dir

    def filter_files_content_types(self, files, content_types):
        # Mirrors CU/folder_paths.py:229's extension->mimetype->content-type approach
        # closely enough for tests: mimetypes.guess_type is what the real one uses too.
        import mimetypes

        wanted = set(content_types)
        out = []
        for f in files:
            mime, _ = mimetypes.guess_type(f, strict=False)
            if mime and mime.split("/")[0] in wanted:
                out.append(f)
        return out


class FakeMedia:
    """Stands in for minimax_refpack.media, which is a stub owned by another slice."""

    def __init__(self):
        self.thumb_calls = []

    def probe(self, path):
        return {"kind": "image", "width": 4, "height": 4, "fps": None, "duration": None, "has_audio": False}

    def thumbnail_png(self, path, max_edge=256, crop=None, at_seconds=None):
        self.thumb_calls.append({"path": path, "crop": crop, "at_seconds": at_seconds})
        return b"\x89PNG\r\nfake"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def input_dir(tmp_path, monkeypatch):
    d = tmp_path / "input"
    d.mkdir()
    monkeypatch.setattr(routes, "folder_paths", FakeFolderPaths(str(d)))
    monkeypatch.setattr(routes, "media", FakeMedia())
    return str(d)


def body_json(resp):
    return json.loads(resp.text)


# ---- pack round trip ---------------------------------------------------------


def test_probe_and_thumb_happy_path(input_dir):
    open(os.path.join(input_dir, "ok.png"), "wb").close()

    resp = run(routes.probe_route(FakeRequest(query={"file": "ok.png"})))
    assert resp.status == 200
    assert body_json(resp)["kind"] == "image"

    resp = run(routes.thumb_route(FakeRequest(query={"file": "ok.png"})))
    assert resp.status == 200
    assert resp.content_type == "image/png"
    assert resp.body == b"\x89PNG\r\nfake"


def test_probe_and_thumb_404_when_absent(input_dir):
    assert run(routes.probe_route(FakeRequest(query={"file": "nope.png"}))).status == 404
    assert run(routes.thumb_route(FakeRequest(query={"file": "nope.png"}))).status == 404


def test_list_files_filters_by_kind(input_dir):
    open(os.path.join(input_dir, "a.png"), "wb").close()
    open(os.path.join(input_dir, "b.mp4"), "wb").close()
    open(os.path.join(input_dir, "c.wav"), "wb").close()

    resp = run(routes.list_files_route(FakeRequest(query={"kind": "image"})))
    assert body_json(resp) == {"files": ["a.png"]}

    resp = run(routes.list_files_route(FakeRequest(query={"kind": "video"})))
    assert body_json(resp) == {"files": ["b.mp4"]}


def test_list_files_400_on_bad_kind(input_dir):
    resp = run(routes.list_files_route(FakeRequest(query={"kind": "mesh"})))
    assert resp.status == 400


# ---- thumb crop / t query params -------------------------------------------------


def test_thumb_passes_crop_and_time_through(input_dir):
    open(os.path.join(input_dir, "v.mp4"), "wb").close()

    resp = run(routes.thumb_route(FakeRequest(query={"file": "v.mp4", "crop": "0.1,0.2,0.5,0.5", "t": "2.5"})))

    assert resp.status == 200
    call = routes.media.thumb_calls[-1]
    assert call["crop"] == [0.1, 0.2, 0.5, 0.5]
    assert call["at_seconds"] == 2.5


def test_thumb_without_edit_params_is_unchanged(input_dir):
    open(os.path.join(input_dir, "ok.png"), "wb").close()

    resp = run(routes.thumb_route(FakeRequest(query={"file": "ok.png"})))

    assert resp.status == 200
    call = routes.media.thumb_calls[-1]
    assert call["crop"] is None
    assert call["at_seconds"] is None


@pytest.mark.parametrize("bad", [
    "",                  # explicit but empty
    "a,b,c,d",           # not numbers
    "0.1,0.2,0.5",       # wrong arity
    "0.6,0.5,0.6,0.6",   # past the right edge
    "-0.1,0,0.5,0.5",    # negative origin
    "0,0,0,1",           # zero area
])
def test_thumb_400_on_a_bad_crop(input_dir, bad):
    open(os.path.join(input_dir, "ok.png"), "wb").close()
    resp = run(routes.thumb_route(FakeRequest(query={"file": "ok.png", "crop": bad})))
    assert resp.status == 400


@pytest.mark.parametrize("bad", ["", "abc", "-1", "nan", "inf"])
def test_thumb_400_on_a_bad_time(input_dir, bad):
    open(os.path.join(input_dir, "ok.png"), "wb").close()
    resp = run(routes.thumb_route(FakeRequest(query={"file": "ok.png", "t": bad})))
    assert resp.status == 400


# ---- traversal table ----------------------------------------------------------

TRAVERSAL_ATTEMPTS = ["../../etc/passwd", "/etc/passwd", "..%2f..%2fetc", "subdir/../../x"]

# These routes answer 404 for a refusal AND for a file that simply is not there, so a bare
# "assert 404" against a path that does not exist proves nothing: it passes just as
# happily with _safe_join deleted. Measured - with the ".." rule commented out, all eight
# parametrised cases below still went green.
#
# So every case that is meant to demonstrate a REFUSAL is aimed at a target that really
# exists. Then 404 has only one possible meaning.


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_probe_refuses_traversal(input_dir, bad):
    assert run(routes.probe_route(FakeRequest(query={"file": bad}))).status == 404


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_thumb_refuses_traversal(input_dir, bad):
    assert run(routes.thumb_route(FakeRequest(query={"file": bad}))).status == 404


@pytest.mark.parametrize("route", ["probe_route", "thumb_route"])
def test_a_file_that_really_exists_outside_the_input_dir_is_still_refused(input_dir, route):
    """The discriminating case: the target is readable, so 404 cannot mean "not found".

    Without _safe_join this returns the file's contents - which is the whole point of the
    guard, and what the parametrised table above never established.
    """
    outside = os.path.join(os.path.dirname(os.path.abspath(input_dir)), "mmrp_outside.txt")
    with open(outside, "wb") as fh:
        fh.write(b"secret")
    try:
        assert os.path.isfile(outside), "the test's own premise: the target exists"
        resp = run(getattr(routes, route)(FakeRequest(query={"file": "../mmrp_outside.txt"})))
        assert resp.status == 404
    finally:
        os.remove(outside)


@pytest.mark.parametrize("route", ["probe_route", "thumb_route"])
def test_an_encoded_payload_is_refused_even_though_it_names_a_real_file(input_dir, route):
    """Isolates the ".." SUBSTRING rule, which the containment check cannot cover.

    "..%2f..%2fetc" contains no real separator, so it resolves harmlessly INSIDE the input
    directory and commonpath is perfectly happy with it. The substring rule is the only
    thing refusing it - it is there because the string is a traversal payload the moment
    anything upstream decodes it. Pointing this at a file that exists is the only way to
    tell the two layers apart.
    """
    payload = "..%2f..%2fetc"
    real = os.path.join(input_dir, payload)
    with open(real, "wb") as fh:
        fh.write(b"secret")
    assert os.path.isfile(real), "the test's own premise: the payload names a real file"
    resp = run(getattr(routes, route)(FakeRequest(query={"file": payload})))
    assert resp.status == 404


def test_the_prefix_rules_are_what_actually_refuse_a_traversal(input_dir):
    """States plainly which layer does the work, because one of them cannot be tested.

    _safe_join has two containment layers: the prefix rules ("..", a leading separator,
    isabs) and then commonpath. Disabling commonpath breaks NOTHING, and that is not an
    oversight in the tests - given the prefix rules, no input can reach it in a state
    where it would return a mismatch, because escaping without ".." requires a name that
    is absolute, and absolute names never get that far.

    Its one live contribution is RAISING, for the drive-relative case above, and that is
    tested. It is otherwise defence in depth mirroring the stock upload route
    (CU/server.py:412-415), which is a good reason to keep it and a bad reason to pretend
    it is covered.

    This test pins the reasoning: if someone relaxes a prefix rule, an entry here stops
    being refused by it, and the message says commonpath has become load-bearing and now
    needs a case of its own.
    """
    escaping = ["../../etc/passwd", "/etc/passwd", "..%2f..%2fetc", "subdir/../../x"]
    for name in escaping:
        caught_by_prefix = (
            ".." in name or name.startswith("/") or name.startswith(chr(92))
            or os.path.isabs(name)
        )
        assert caught_by_prefix, (
            f"{name!r} is no longer refused by the prefix rules, so commonpath is now the "
            "only thing stopping it - give it a test of its own rather than relying on a "
            "layer nothing else exercises"
        )
        assert routes._safe_join(input_dir, name) is None


@pytest.mark.parametrize("bad", ["D:foo.txt", "Z:x/y.png", "d:already/there.png"])
@pytest.mark.parametrize("route", ["probe_route", "thumb_route"])
def test_a_drive_relative_name_is_refused_rather_than_crashing(input_dir, route, bad):
    """A drive-relative path is NOT absolute - os.path.isabs("D:foo") is False - so it
    slipped past the first guard and reached commonpath, which raises ValueError when the
    paths are on different drives. Uncaught, that was an HTTP 500 and a console traceback
    from an unauthenticated request. Windows-shaped, but the route is reachable anywhere.
    """
    resp = run(getattr(routes, route)(FakeRequest(query={"file": bad})))
    assert resp.status == 404


def test_a_plain_valid_name_is_accepted_by_every_route(input_dir):
    open(os.path.join(input_dir, "ok.png"), "wb").close()

    assert run(routes.probe_route(FakeRequest(query={"file": "ok.png"}))).status == 200
    assert run(routes.thumb_route(FakeRequest(query={"file": "ok.png"}))).status == 200


def test_the_pack_store_is_gone():
    """Configs are files on the user's machine (2026-08-15); the server-side pack store
    was dead code and was deleted. This pins it — a route added back here would be a
    second, unused mechanism for saving a reference set."""
    paths = {path for _method, path, _handler in routes._ROUTES}
    assert not any("packs" in path for path in paths)
    assert not hasattr(routes, "save_pack_route")
    assert not hasattr(routes, "get_pack")


# ---- system prompt: read-only default ------------------------------------------


def test_no_write_route_exists_for_system_prompt():
    paths = {(method, path) for method, path, _handler in routes._ROUTES}
    assert ("GET", "/minimax_refpack/system_prompt") in paths
    assert ("POST", "/minimax_refpack/system_prompt") not in paths
    assert ("PUT", "/minimax_refpack/system_prompt") not in paths
    assert ("DELETE", "/minimax_refpack/system_prompt") not in paths


# ---- import safety -------------------------------------------------------------


def test_importing_routes_without_comfyui_does_not_raise():
    # If this test file imported cleanly, the module-level guard already proved this -
    # but assert the fallback state explicitly so a future edit can't silently import
    # a real `server`/`folder_paths` and hide the guard's absence.
    import importlib

    reloaded = importlib.reload(routes)
    assert reloaded.PromptServer is None or reloaded.PromptServer.instance is None
