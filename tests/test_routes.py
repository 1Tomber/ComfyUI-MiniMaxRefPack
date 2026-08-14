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
from minimax_refpack.refs import Pack, Reference, ReferenceSet, save_pack


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

    def probe(self, path):
        return {"kind": "image", "width": 4, "height": 4, "fps": None, "duration": None, "has_audio": False}

    def thumbnail_png(self, path, max_edge=256):
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


def test_pack_save_list_get_delete_round_trip(input_dir):
    save_body = {"name": "bryan dorm", "direction": "handheld", "references": [
        {"kind": "image", "file": "a.jpg"},
    ]}
    resp = run(routes.save_pack_route(FakeRequest(body=save_body)))
    assert resp.status == 200
    assert body_json(resp) == {"saved": "bryan dorm"}

    resp = run(routes.list_packs(FakeRequest()))
    assert body_json(resp) == {"packs": ["bryan dorm"]}

    resp = run(routes.get_pack(FakeRequest(match_info={"name": "bryan dorm"})))
    assert resp.status == 200
    got = body_json(resp)
    assert got["name"] == "bryan dorm"
    assert got["direction"] == "handheld"
    # "a.jpg" was never created on disk -> it's missing.
    assert got["missing"] == ["a.jpg"]

    resp = run(routes.delete_pack_route(FakeRequest(match_info={"name": "bryan dorm"})))
    assert body_json(resp) == {"deleted": True}

    resp = run(routes.list_packs(FakeRequest()))
    assert body_json(resp) == {"packs": []}

    resp = run(routes.delete_pack_route(FakeRequest(match_info={"name": "bryan dorm"})))
    assert body_json(resp) == {"deleted": False}


def test_missing_list_reports_a_deleted_reference_file(input_dir):
    open(os.path.join(input_dir, "here.jpg"), "wb").close()
    save_pack(input_dir, Pack(name="p", references=ReferenceSet([
        Reference(kind="image", file="here.jpg"),
        Reference(kind="image", file="gone.jpg"),
    ])))

    resp = run(routes.get_pack(FakeRequest(match_info={"name": "p"})))
    assert body_json(resp)["missing"] == ["gone.jpg"]

    os.remove(os.path.join(input_dir, "here.jpg"))
    resp = run(routes.get_pack(FakeRequest(match_info={"name": "p"})))
    assert body_json(resp)["missing"] == ["here.jpg", "gone.jpg"]


def test_get_pack_404_when_absent(input_dir):
    resp = run(routes.get_pack(FakeRequest(match_info={"name": "nope"})))
    assert resp.status == 404


def test_save_pack_400_on_bad_name(input_dir):
    resp = run(routes.save_pack_route(FakeRequest(body={"name": "../escape", "references": []})))
    assert resp.status == 400


def test_save_pack_400_on_bad_refs(input_dir):
    # exceeds the hard image cap (MAX_IMAGES = 9) - refs.py's validate() rejects it.
    body = {"name": "ok", "references": [{"kind": "image", "file": f"{i}.jpg"} for i in range(10)]}
    resp = run(routes.save_pack_route(FakeRequest(body=body)))
    assert resp.status == 400


def test_save_pack_400_on_a_traversal_reference_file(input_dir):
    body = {"name": "ok", "references": [{"kind": "image", "file": "../../etc/passwd"}]}
    resp = run(routes.save_pack_route(FakeRequest(body=body)))
    assert resp.status == 400


def test_save_pack_400_on_non_json_body(input_dir):
    resp = run(routes.save_pack_route(FakeRequest(body=None)))
    assert resp.status == 400


# ---- probe / thumb / files ----------------------------------------------------


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


# ---- traversal table ----------------------------------------------------------

TRAVERSAL_ATTEMPTS = ["../../etc/passwd", "/etc/passwd", "..%2f..%2fetc", "subdir/../../x"]


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_probe_refuses_traversal(input_dir, bad):
    assert run(routes.probe_route(FakeRequest(query={"file": bad}))).status == 404


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_thumb_refuses_traversal(input_dir, bad):
    assert run(routes.thumb_route(FakeRequest(query={"file": bad}))).status == 404


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_get_pack_refuses_traversal(input_dir, bad):
    assert run(routes.get_pack(FakeRequest(match_info={"name": bad}))).status == 404


@pytest.mark.parametrize("bad", TRAVERSAL_ATTEMPTS)
def test_delete_pack_refuses_traversal(input_dir, bad):
    resp = run(routes.delete_pack_route(FakeRequest(match_info={"name": bad})))
    assert body_json(resp) == {"deleted": False}


def test_a_plain_valid_name_is_accepted_by_every_route(input_dir):
    open(os.path.join(input_dir, "ok.png"), "wb").close()
    save_pack(input_dir, Pack(name="ok", references=ReferenceSet()))

    assert run(routes.probe_route(FakeRequest(query={"file": "ok.png"}))).status == 200
    assert run(routes.thumb_route(FakeRequest(query={"file": "ok.png"}))).status == 200
    assert run(routes.get_pack(FakeRequest(match_info={"name": "ok"}))).status == 200
    resp = run(routes.delete_pack_route(FakeRequest(match_info={"name": "ok"})))
    assert body_json(resp) == {"deleted": True}


# ---- system prompt: read-only default ------------------------------------------


def test_system_prompt_route_returns_the_packaged_default():
    from minimax_refpack import prompt as prompt_module

    resp = run(routes.system_prompt_route(FakeRequest()))
    assert resp.status == 200
    with open(prompt_module._SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        assert body_json(resp) == {"default": f.read()}


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
