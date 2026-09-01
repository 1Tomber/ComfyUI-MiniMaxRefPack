"""The prompt highlight backdrop, replayed against a minimal DOM shim.

There is no local ComfyUI, so the one intricate new DOM piece - syncTagOverlay, which turns
the scanned tags into the tinted spans behind the textarea - is exercised here rather than
only on the pod. tests/tag_overlay_harness.js runs the REAL function (lifted out with the
resize harness's own grabFn) against a shim just big enough to build elements, and checks
the spans line up with the tags, carry the right class (live / stray / armed), and leave
the surrounding prose byte-for-byte intact. A failure means the backdrop and the textarea
would show different text or mis-tint a tag.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "tag_overlay_harness.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@requires_node
def test_overlay_builder_matches_the_scan():
    proc = subprocess.run(
        [NODE, str(HARNESS)],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"overlay harness failed:\n{proc.stdout}\n{proc.stderr}"
