"""The cue-point trim editor's frame arithmetic, executed under node.

The modal is a source monitor - scrub a playhead, step it a frame at a time, mark In/Out at
it - but the emitted contract stays trim:[start,end] in SECONDS. These MMRP-CUE helpers are
the frame<->second boundary, and the subtle parts (out is exclusive, the min-window floor
that keeps Save from emitting a clip the decoder's >=5-frame guard rejects, and the
full-selection -> null collapse) are exactly what has to be pinned. Pure, marker-delimited.
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
    start = text.find("// >>> MMRP-CUE")
    end = text.find("// <<< MMRP-CUE")
    assert start != -1 and end != -1, "the MMRP-CUE markers are gone from web/refpack.js"
    return text[start:end].replace("export ", "")


def _call(expr: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_the_markers_are_still_there():
    assert "cueToTrimSeconds" in _extract()


@requires_node
def test_frame_second_round_trip():
    assert _call("secToFrame(2.0, 24)") == 48
    assert _call("frameToSec(48, 24)") == 2.0
    assert _call("secToFrame(0, 30)") == 0


@requires_node
def test_seeking_uses_the_frame_midpoint_and_reads_back_the_same_frame():
    # a boundary seek can display the previous frame; the midpoint lands squarely on it
    assert _call("frameSeekSec(10, 24)") == pytest.approx((10 + 0.5) / 24)
    # round-trip: seek to a frame's midpoint, read the cursor back -> same frame
    for f in (0, 1, 47, 100):
        assert _call(f"secToCursorFrame(frameSeekSec({f}, 30), 30)") == f


@requires_node
def test_last_frame_index_is_the_count_minus_one():
    assert _call("lastFrameIndex(10.0, 24)") == 239   # 240 frames -> indices 0..239
    assert _call("lastFrameIndex(0, 24)") == 0


@requires_node
def test_clamp_frame_rounds_and_bounds():
    assert _call("clampFrame(5.4, 0, 100)") == 5
    assert _call("clampFrame(-3, 0, 100)") == 0
    assert _call("clampFrame(250, 0, 100)") == 100


@requires_node
def test_output_frames_matches_the_decoder_resample():
    # media.resample_indices: round(n_sel * 24 / src_fps); n_sel = outF-inF+1
    assert _call("outputFrames(0, 23, 24)") == 24     # 24 kept @24fps -> 24 out
    assert _call("outputFrames(0, 5, 30)") == 5       # 6 kept @30 -> round(4.8)=5


@requires_node
@pytest.mark.parametrize("fps", [23.976, 24, 25, 29.97, 30, 50, 59.94, 60])
def test_min_kept_window_always_clears_the_five_frame_guard(fps):
    # the whole point of the floor: a window at the minimum must NOT decode below 5
    need = _call(f"minKeptFrames({fps})")
    # a window of exactly `need` kept frames (inF=0, outF=need-1)
    out = _call(f"outputFrames(0, {need - 1}, {fps})")
    assert out >= 5, f"min window of {need} frames at {fps}fps yields only {out} output frames"


@requires_node
def test_mark_in_at_the_cursor_keeps_a_legal_window():
    # plenty of room: In just moves to the cursor, Out untouched
    assert _call("markInFrame(10, 0, 200, 239, 24)") == [10, 200]


@requires_node
def test_mark_in_too_close_to_out_pushes_out_later():
    # cursor at 236 with Out at 238 on a 24fps clip (need 6 kept) -> Out pushed to 241,
    # but that exceeds lastF 239, so Out pins at 239 and In is pulled back to keep 6 frames
    got = _call("markInFrame(236, 0, 238, 239, 24)")
    assert got[1] == 239 and got[1] - got[0] + 1 == _call("minKeptFrames(24)")


@requires_node
def test_mark_out_at_the_cursor_keeps_a_legal_window():
    assert _call("markOutFrame(200, 10, 239, 239, 24)") == [10, 200]


@requires_node
def test_mark_out_too_close_to_in_pulls_in_back():
    # Out at 3 with In at 0 (need 6) -> In can't go below 0, so Out is pushed to 5
    got = _call("markOutFrame(3, 0, 239, 239, 24)")
    assert got[0] == 0 and got[1] - got[0] + 1 == _call("minKeptFrames(24)")


@requires_node
def test_out_is_exclusive_in_seconds():
    # keep frames 48..95 inclusive at 24fps -> [2.0, 4.0): end = (95+1)/24 = 4.0
    assert _call("cueToTrimSeconds(48, 95, 24, 10.0, 239)") == [2.0, 4.0]


@requires_node
def test_a_full_selection_maps_to_the_whole_clip_for_the_null_collapse():
    # inF==0 and outF==lastF -> [0, duration], which normalizeTrim collapses to "no trim"
    assert _call("cueToTrimSeconds(0, 239, 24, 10.0, 239)") == [0, 10.0]


@requires_node
def test_the_tail_maps_to_the_real_duration_not_past_it():
    # outF at lastF must not become (lastF+1)/fps, which could overshoot the clip end
    out = _call("cueToTrimSeconds(24, 239, 24, 10.0, 239)")
    assert out == [1.0, 10.0]


@requires_node
def test_trim_seconds_round_trip_back_to_frames():
    # a fresh save: [2.0, 4.0) -> frames 48..95
    assert _call("trimSecToCue([2.0, 4.0], 24, 10.0, 239)") == [48, 95]


@requires_node
def test_legacy_two_dp_seconds_load_onto_the_right_frame():
    # an older build wrote 2dp seconds; round() recovers the frame at a realistic fps
    # frame 7 @30fps = 0.2333s, written as 0.23 -> round(0.23*30)=7
    assert _call("trimSecToCue([0.23, 5.0], 30, 8.0, 239)")[0] == 7


@requires_node
def test_format_clock():
    assert _call("formatClock(0)") == "0:00.000"
    assert _call("formatClock(6.25)") == "0:06.250"
    assert _call("formatClock(75.5)") == "1:15.500"


# ---- timecode MM:SS:FF <-> frames (the read-out toggle) -----------------------------------


@requires_node
def test_frames_to_timecode():
    assert _call("framesToTimecode(0, 24)") == "0:00:00"
    assert _call("framesToTimecode(12, 24)") == "0:00:12"     # 12 frames into the first second
    assert _call("framesToTimecode(180, 24)") == "0:07:12"    # 7.5s = 7s + 12 frames
    assert _call("framesToTimecode(24, 24)") == "0:01:00"     # exactly one second
    assert _call("framesToTimecode(1449, 24)") == "1:00:09"   # past a minute


@requires_node
def test_timecode_to_frames_round_trip():
    for f in (0, 12, 180, 24, 1449, 719):
        tc = _call(f"framesToTimecode({f}, 24)")
        assert _call(f'timecodeToFrames("{tc}", 24)') == f, f"round trip broke at {f} ({tc})"


@requires_node
def test_timecode_to_frames_is_lenient():
    assert _call('timecodeToFrames("0:07:12", 24)') == 180
    assert _call('timecodeToFrames("07:12", 24)') == 180      # SS:FF, minutes omitted
    assert _call('timecodeToFrames("12", 24)') == 12          # bare frames
    assert _call('timecodeToFrames("  1:00:09 ", 24)') == 1449  # whitespace tolerated
    assert _call('timecodeToFrames("", 24)') == 0             # empty -> 0, never NaN
