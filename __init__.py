"""ComfyUI-MiniMaxRefPack — reference management for MiniMax H3 Reference-to-Video."""

# ComfyUI imports this file as a package, so the relative form is the real one. pytest
# walks the repo root as a package too and imports this file with no parent, hence the
# absolute fallback (the repo root is on sys.path during tests, see conftest.py).
try:
    from .minimax_refpack.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    from .minimax_refpack import routes  # noqa: F401  registers the HTTP routes
except ImportError:  # pragma: no cover - test-time import path
    from minimax_refpack.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    from minimax_refpack import routes  # noqa: F401

WEB_DIRECTORY = "web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
