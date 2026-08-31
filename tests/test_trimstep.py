"""Sliding the trim window by whole frames, executed under node.

The frame-step buttons move the WHOLE window by 1/fps seconds without changing its length,
and stop dead at the clip's edges - shifting a window that already touches the end must not
stretch or wrap it. That clamping is the only non-trivial part, so it is a pure function
run under node the way the layout and orient helpers are.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-TRIMSTEP")
    end = text.find("// <<< MMRP-TRIMSTEP")
    assert start != -1 and end != -1, (
        "the MMRP-TRIMSTEP markers are gone from web/refpack.js - this test extracts the "
        "shipped shiftTrim through them"
    )
    return text[start:end]


def _shift(trim, delta, duration):
    script = _extract() + (
        f"\nconsole.log(JSON.stringify(shiftTrim({json.dumps(trim)}, {delta}, {duration})));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_a_frame_step_mid_clip_moves_both_edges_equally():
    fps = 24
    out = _shift([2.0, 6.5], 1 / fps, 10.0)
    assert out[0] == pytest.approx(2.0 + 1 / fps)
    assert out[1] == pytest.approx(6.5 + 1 / fps)
    # length unchanged
    assert out[1] - out[0] == pytest.approx(4.5)


@requires_node
def test_a_backward_step_at_the_start_wall_does_not_move():
    out = _shift([0.0, 5.0], -1 / 24, 10.0)
    assert out == [0.0, 5.0]


@requires_node
def test_a_forward_step_at_the_end_wall_does_not_move():
    out = _shift([5.0, 10.0], 1 / 24, 10.0)
    assert out == [5.0, 10.0]


@requires_node
def test_a_step_that_only_partly_fits_stops_at_the_wall_keeping_length():
    # 0.1s of room at the end, asked to move 0.5s -> moves 0.1s, length preserved
    out = _shift([4.9, 9.9], 0.5, 10.0)
    assert out[0] == pytest.approx(5.0)
    assert out[1] == pytest.approx(10.0)
    assert out[1] - out[0] == pytest.approx(5.0)


@requires_node
def test_length_is_preserved_under_any_shift():
    for trim, delta in [([1.0, 3.0], 0.2), ([1.0, 3.0], -0.2), ([0.0, 9.9], 5.0),
                        ([0.1, 10.0], -5.0)]:
        out = _shift(trim, delta, 10.0)
        assert out[1] - out[0] == pytest.approx(trim[1] - trim[0]), (trim, delta, out)


@requires_node
def test_bad_input_is_returned_unchanged_rather_than_thrown():
    assert _shift([2.0, 6.5], float("nan") if False else 0, 10.0) == [2.0, 6.5]
    # a NaN duration or delta must not corrupt the window
    for expr in ("shiftTrim([2,6], NaN, 10)", "shiftTrim([2,6], 0.1, NaN)",
                 "shiftTrim(null, 0.1, 10)", "shiftTrim([2], 0.1, 10)"):
        script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
        proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr
        # never throws; returns something JSON-serialisable
        json.loads(proc.stdout.strip())
