"""The output-size readout math, executed under node.

`outputSize` is what the editor shows next to the downscale slider - source through rotate,
crop, and the long-edge cap - so it has to agree with what the sockets actually emit
(media.scaled_size does the cap on the Python side). Pure, marker-delimited.
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
    start = text.find("// >>> MMRP-OUTSIZE")
    end = text.find("// <<< MMRP-OUTSIZE")
    assert start != -1 and end != -1, "the MMRP-OUTSIZE markers are gone from web/refpack.js"
    return text[start:end].replace("export ", "")


def _call(expr: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_the_long_edge_is_capped_aspect_kept():
    assert _call("outputSize(1920, 1080, 0, true, null, 512)") == [512, 288]
    assert _call("outputSize(1080, 1920, 0, true, null, 512)") == [288, 512]


@requires_node
def test_a_reference_smaller_than_the_cap_is_never_upscaled():
    assert _call("outputSize(400, 300, 0, true, null, 512)") == [400, 300]
    assert _call("outputSize(400, 300, 0, true, null, 0)") == [400, 300]


@requires_node
def test_a_quarter_turn_swaps_the_axes_before_the_cap():
    # 1920x1080 rotated 90 -> 1080x1920, then capped at 512 -> 288x512
    assert _call("outputSize(1920, 1080, 90, true, null, 512)") == [288, 512]
    assert _call("outputSize(1920, 1080, 270, true, null, 512)") == [288, 512]
    assert _call("outputSize(1920, 1080, 180, true, null, 512)") == [512, 288]


@requires_node
def test_the_crop_reduces_the_measured_size():
    # half on each axis, no cap -> a quarter of the pixels
    assert _call("outputSize(1920, 1080, 0, true, [0, 0, 0.5, 0.5], 0)") == [960, 540]
    # then a cap applies to the CROPPED long edge
    assert _call("outputSize(1920, 1080, 0, true, [0, 0, 0.5, 0.5], 480)") == [480, 270]


@requires_node
def test_crop_is_measured_after_the_rotation():
    # rotate 90 -> 1080x1920, crop the top half of THAT -> 1080x960
    assert _call("outputSize(1920, 1080, 90, true, [0, 0, 1.0, 0.5], 0)") == [1080, 960]


@requires_node
def test_a_free_angle_with_expand_grows_to_the_bounding_box():
    # a square rotated 45 with expand grows by ~sqrt(2)
    out = _call("outputSize(100, 100, 45, true, null, 0)")
    assert out[0] == out[1]
    assert 140 <= out[0] <= 142  # 100*sqrt(2) ~= 141


@requires_node
def test_scaled_size_matches_the_python_rule():
    # scaledSizeJs is the exact mirror of media.scaled_size; spot-check the shared cases
    assert _call("scaledSizeJs(3072, 4080, 768)") == [578, 768]
    assert _call("scaledSizeJs(1280, 720, 0)") == [1280, 720]
