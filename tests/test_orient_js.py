"""Turning the crop rect with the frame, executed under node.

A crop is FRACTIONS of the frame. Rotating the media without rotating the rect silently
re-points it: [0, 0, 0.5, 1] means "the left half", and after a quarter turn the left half
is somewhere else entirely. Moving the rect with the frame keeps the same PIXELS selected,
which is what "turn it" means to the person clicking the button, and for a quarter turn it
is exact.

Extracted from the shipped web/refpack.js between its MMRP-ORIENT markers, the same
harness tests/test_migration.py uses.
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


# ---- turning the crop rect with the frame ------------------------------------------
#
# A crop is FRACTIONS of the frame, so rotating the media without rotating the rect
# silently re-points it: [0, 0, 0.5, 1] means "the left half", and after a quarter turn
# the left half is somewhere else entirely. Moving the rect with the frame keeps the same
# PIXELS selected, which is what "turn it" means to the person clicking the button.


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-ORIENT")
    end = text.find("// <<< MMRP-ORIENT")
    assert start != -1 and end != -1, (
        "the MMRP-ORIENT markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them"
    )
    return text[start:end]


def _run(expression: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expression}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_four_quarter_turns_return_the_rect_unchanged():
    """The cheapest possible check that the transform is a real rotation and not an
    approximation that drifts."""
    out = _run("[0, 1, 2, 3].reduce((r) => rotateCropRect(r, 90), "
                      "[0.1, 0.2, 0.3, 0.4])")
    assert out == pytest.approx([0.1, 0.2, 0.3, 0.4])


@requires_node
def test_a_quarter_turn_swaps_the_rect_axes():
    """The left half becomes the top half: turning the frame clockwise carries what was
    on the left round to the top."""
    assert _run("rotateCropRect([0, 0, 0.5, 1], 90)") == pytest.approx([0, 0, 1, 0.5])


@requires_node
def test_turning_back_and_forth_is_a_no_op():
    assert _run("rotateCropRect(rotateCropRect([0.1, 0.2, 0.3, 0.4], 90), -90)") \
        == pytest.approx([0.1, 0.2, 0.3, 0.4])


@requires_node
def test_the_rect_stays_inside_the_unit_square_through_every_turn():
    """refs.py's validate_crop rejects a rect that leaves 0..1, so a transform that drifts
    outside would make the modal save something the node then refuses to load."""
    bad = _run("""
        (() => {
            const out = [];
            for (const r of [[0,0,1,1],[0.1,0.2,0.3,0.4],[0,0.75,0.25,0.25],[0.6,0,0.4,0.1]]) {
                let cur = r;
                for (let i = 0; i < 4; i++) {
                    cur = rotateCropRect(cur, 90);
                    const [x, y, w, h] = cur;
                    if (x < -1e-9 || y < -1e-9 || x + w > 1 + 1e-9 || y + h > 1 + 1e-9) {
                        out.push(cur);
                    }
                }
            }
            return out;
        })()
    """)
    assert bad == []


@requires_node
def test_mirroring_reflects_the_rect_on_the_same_axis():
    assert _run('mirrorCropRect([0.1, 0.2, 0.3, 0.4], "h")') \
        == pytest.approx([0.6, 0.2, 0.3, 0.4])
    assert _run('mirrorCropRect([0.1, 0.2, 0.3, 0.4], "v")') \
        == pytest.approx([0.1, 0.4, 0.3, 0.4])


@pytest.mark.parametrize("start,axis,expected", [
    (None, "h", "h"), ("h", "h", None), ("h", "v", "hv"),
    ("hv", "h", "v"), ("hv", "v", "h"), ("v", "v", None),
])
@requires_node
def test_toggling_a_mirror_axis_normalises_to_the_python_spelling(start, axis, expected):
    """refs.validate_flip accepts h/v/hv only, so the UI must never produce "vh"."""
    got = _run(f"toggleFlipAxis({json.dumps(start)}, {json.dumps(axis)})")
    assert got == expected


# ---- the free angle ----------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (0, 0), (90, 90), (180, 180), (-90, 270),
    (89.0, 90), (91.5, 90), (2.0, 0), (358.0, 0), (178.5, 180),
    (45, 45), (30.5, 30.5), (86, 86),   # outside the snap window, kept exactly
])
def test_the_slider_snaps_to_the_lossless_angles(value, expected):
    """Not tidiness: a quarter turn is lossless (transpose / strides) while any other
    angle resamples every frame of a clip. A slider parked on 89.6 would cost a full
    re-render and look identical, so snapping is what keeps the cheap path reachable by
    hand."""
    assert _run(f"snapAngle({value})") == pytest.approx(expected)


def test_snapping_can_be_turned_off_for_a_deliberate_near_quarter_angle():
    assert _run("snapAngle(89.4, 0)") == pytest.approx(89.4)


def test_snapping_normalises_into_one_turn():
    assert _run("snapAngle(-270)") == pytest.approx(90)
    assert _run("snapAngle(450)") == pytest.approx(90)
