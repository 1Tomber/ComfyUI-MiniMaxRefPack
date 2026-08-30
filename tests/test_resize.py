"""The node's resize behaviour, replayed against ComfyUI's own drag algorithm.

The height drag was got wrong five times in a row, and every wrong version looked right
until a live drag disproved it. The reason is that the height the frontend hands `setSize`
does not carry the user's intent: the drag computes "size at mousedown plus total pointer
delta" and then clamps it UP to `computeSize()` on both axes, so dragging the node narrower
wraps a tile onto a new line, raises the minimum height, and passes that new minimum - a
frame byte-identical to a deliberate drag down to the floor. Every fix that reasoned about
that number worked on the case that prompted it and leaked on the next.

So this does not test the arithmetic in isolation. `resize_harness/frontend.js` is a
verbatim port of the 1.51.9 drag and widget-layout code (bundle offsets cited in it),
`extract.js` lifts the real functions out of web/refpack.js, and `run.js` drives the two
together over the scenarios that each historical bug came from, plus the ones nobody had
thought to try.

It also answers a problem the node cannot otherwise solve. Node resizing is ComfyUI's
machinery, and the code under test reads fields that belong to them - `resizing_node`,
`pointer.eDown`, `pointer.resizeDirection`. We cannot own their internals, so we own a
test that fails loudly when they move, rather than a user finding out. A failure here after
a frontend upgrade means the ported contract has moved: re-derive it against the new
bundle, do not relax the expectations.

Two alternative designs were built and measured against this harness and rejected on the
numbers: disarming the height clamp via `computeSize` (16/23 - it breaks N-corner
bottom-anchoring, because that clamp is also what pins the bottom edge), and moving the
layout onto ComfyUI's native `computeLayoutSize`/`distributeSpace` protocol (15/23 - the
prompt becomes a flex remainder, and during a drag litegraph rewrites the height absolutely
from its own pointerdown snapshot, so content growth eats the text box). The shipped
pointer model scores 21/21 on the scenarios that apply to it.
"""

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "resize_harness"
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run_harness(refpack=None):
    env = None
    if refpack is not None:
        import os

        env = dict(os.environ, REFPACK_PATH=str(refpack))
    return subprocess.run(
        [NODE, str(HARNESS / "run.js")],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT), env=env,
    )


@requires_node
def test_every_resize_scenario_passes():
    proc = _run_harness()
    assert proc.returncode == 0, (
        "resize scenarios failed:\n" + proc.stdout[-4000:] + "\n" + proc.stderr[-2000:]
    )
    assert "scenarios pass" in proc.stdout


@requires_node
def test_the_harness_actually_discriminates():
    """A harness that passes whatever the file says is worse than no harness.

    This project has twice produced a green result from a check that never applied - a
    grep guard that matched an unrelated line, and a probe at coordinates where old and
    new code agreed. So the suite proves, every run, that breaking the real thing turns
    this red: the direction sign is what makes an N-corner drag grow the prompt upward
    instead of shrinking it, and nothing else in the file compensates for it.
    """
    src = REFPACK_JS.read_text(encoding="utf-8")
    marker = "const signedDy = anchor.north ? -dy : dy;"
    assert marker in src, (
        "the direction-sign line this test perturbs has moved; update the perturbation "
        "rather than deleting the check"
    )
    broken = src.replace(marker, "const signedDy = dy;  // PERTURBED", 1)
    assert broken != src

    tmp = HARNESS / "_perturbed_refpack.js"
    try:
        tmp.write_text(broken, encoding="utf-8", newline="\n")
        proc = _run_harness(tmp)
        assert proc.returncode != 0, (
            "the harness passed against code with the drag direction inverted, so it is "
            "not testing what it claims:\n" + proc.stdout[-3000:]
        )
    finally:
        tmp.unlink(missing_ok=True)


@requires_node
def test_the_ported_frontend_contract_is_still_declared():
    """The port is only trustworthy while it says what it was ported from.

    Every load-bearing behaviour in frontend.js carries the bundle offset it came from.
    If those go, the next person cannot tell a faithful port from a convenient one.
    """
    fe = (HARNESS / "frontend.js").read_text(encoding="utf-8")
    for expected in ["resizing_node", "resizeDirection", "computeSize", "distributeSpace"]:
        assert expected in fe, f"the ported drag contract no longer mentions {expected}"
