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

import os

from aiohttp import web

from .refs import (
    Pack,
    ReferenceError,
    ReferenceSet,
    delete_pack,
    load_pack,
    list_pack_names,
    save_pack,
    validate_pack_name,
)
from . import media, prompt

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
    if os.path.commonpath((base_dir, target)) != base_dir:
        return None
    return target


# ---- handlers ---------------------------------------------------------------
# Plain functions, not nested under the route decorators, so tests can call them
# directly with a fake request (a stub exposing .query, .match_info, async .json()).


async def list_packs(request: web.Request) -> web.Response:
    input_dir = folder_paths.get_input_directory()
    return web.json_response({"packs": list_pack_names(input_dir)})


async def get_pack(request: web.Request) -> web.Response:
    name = request.match_info.get("name", "")
    input_dir = folder_paths.get_input_directory()
    try:
        pack = load_pack(input_dir, name)
    except (ReferenceError, OSError):
        # ReferenceError = bad name (refs.py validates before touching disk),
        # OSError = FileNotFoundError from open() when the name is fine but absent.
        return web.Response(status=404)
    d = pack.to_dict()
    d["missing"] = pack.references.resolve(input_dir)
    return web.json_response(d)


async def save_pack_route(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="body must be JSON")
    if not isinstance(body, dict):
        return web.Response(status=400, text="body must be a JSON object")

    input_dir = folder_paths.get_input_directory()
    try:
        name = validate_pack_name(body.get("name") if isinstance(body.get("name"), str) else "")
        refset = ReferenceSet.from_obj(body.get("references", []))
        refset.validate()
    except ReferenceError as e:
        return web.Response(status=400, text=str(e))

    # Defense in depth: refs.py resolves each reference's filename against the input
    # dir (missing_files) but doesn't confine it - that's fine there (existence check
    # only), but this route is the one place an attacker's filename first lands, so
    # refuse anything that couldn't be a real file in the input dir.
    for ref in refset.references:
        if _safe_join(input_dir, ref.file) is None:
            return web.Response(status=400, text=f"bad reference file: {ref.file!r}")

    # Free-form strings straight from the UI. They are never turned into a path and
    # never executed - they only repopulate two widgets on load - so they need no
    # validation beyond being strings.
    def _str(key: str) -> str:
        v = body.get(key)
        return v if isinstance(v, str) else ""

    save_pack(
        input_dir,
        Pack(
            name=name,
            references=refset,
            direction=_str("direction"),
            model=_str("model"),
            reasoning_effort=_str("reasoning_effort"),
        ),
    )
    return web.json_response({"saved": name})


async def delete_pack_route(request: web.Request) -> web.Response:
    name = request.match_info.get("name", "")
    input_dir = folder_paths.get_input_directory()
    try:
        validate_pack_name(name)
    except ReferenceError:
        return web.json_response({"deleted": False})
    return web.json_response({"deleted": delete_pack(input_dir, name)})


async def probe_route(request: web.Request) -> web.Response:
    input_dir = folder_paths.get_input_directory()
    path = _safe_join(input_dir, request.query.get("file", ""))
    if path is None or not os.path.isfile(path):
        return web.Response(status=404)
    return web.json_response(media.probe(path))


async def thumb_route(request: web.Request) -> web.Response:
    input_dir = folder_paths.get_input_directory()
    path = _safe_join(input_dir, request.query.get("file", ""))
    if path is None or not os.path.isfile(path):
        return web.Response(status=404)
    return web.Response(body=media.thumbnail_png(path), content_type="image/png")


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
    route for this on purpose."""
    return web.json_response({"default": prompt._read_system_prompt()})


# ---- registration -------------------------------------------------------------

_ROUTES: tuple[tuple[str, str, object], ...] = (
    ("GET", "/minimax_refpack/packs", list_packs),
    ("GET", "/minimax_refpack/packs/{name}", get_pack),
    ("POST", "/minimax_refpack/packs", save_pack_route),
    ("DELETE", "/minimax_refpack/packs/{name}", delete_pack_route),
    ("GET", "/minimax_refpack/probe", probe_route),
    ("GET", "/minimax_refpack/thumb", thumb_route),
    ("GET", "/minimax_refpack/files", list_files_route),
    ("GET", "/minimax_refpack/system_prompt", system_prompt_route),
)

if PromptServer is not None and getattr(PromptServer, "instance", None) is not None:
    _method_fn = {
        "GET": PromptServer.instance.routes.get,
        "POST": PromptServer.instance.routes.post,
        "DELETE": PromptServer.instance.routes.delete,
    }
    for _method, _path, _handler in _ROUTES:
        _method_fn[_method](_path)(_handler)
