"""The trim editor's overview/zoom-window geometry, executed under node.

The editor grows a two-tier timeline: an overview bar mapping the whole clip with a
draggable/resizable window, and the detail bar re-scaled to that window for precise scrubbing.
The window arithmetic - resize/pan/center clamping, the min-window floor, and the pin/clip helpers
that keep an off-window trim edge or playhead from being lost - lives in the MMRP-ZOOM marker block
as pure functions so it is covered here the way MMRP-CUE is by test_cue.py, not only through the
browser. The DOM wiring (which bar was measured, listener setup) is left to live testing.

The window is [start, end] seconds and is a pure VIEW layer: none of these helpers take or touch
`trim`, so by signature they cannot move it.
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
    start = text.find("// >>> MMRP-ZOOM")
    end = text.find("// <<< MMRP-ZOOM")
    assert start != -1 and end != -1, (
        "the MMRP-ZOOM markers are gone from web/refpack.js - this test extracts the real shipped "
        "window geometry through them, so removing them silently drops all coverage of the "
        "zoom/pan/resize clamping and the off-window pin/clip helpers"
    )
    return text[start:end].replace("export ", "")


def _call(expr):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


APPROX = 1e-9


def _approx(seq):
    return [pytest.approx(v, abs=APPROX) if isinstance(v, (int, float)) else v for v in seq]


# ---- extraction contract ------------------------------------------------------------------


@requires_node
def test_the_markers_are_still_there():
    src = _extract()
    for name in ("clampWindow", "panWindow", "resizeWindowStart", "resizeWindowEnd",
                 "centerWindow", "placeWindow", "pinToBar", "clipSpan", "winFrac", "fracToWin"):
        assert name in src, f"{name} vanished from the MMRP-ZOOM block"


# ---- frac <-> time round trip -------------------------------------------------------------


@requires_node
def test_frac_time_round_trip():
    assert _call("fracToWin(0.25, 2, 6)") == 3
    assert _call("winFrac(3, 2, 6)") == 0.25
    # round trips, both directions
    assert _call("fracToWin(winFrac(3.7, 2, 6), 2, 6)") == pytest.approx(3.7, abs=APPROX)
    assert _call("winFrac(fracToWin(0.6, 2, 6), 2, 6)") == pytest.approx(0.6, abs=APPROX)


@requires_node
def test_winfrac_is_raw_and_zero_span_safe():
    assert _call("winFrac(0, 2, 6)") == -0.5     # negative kept, NOT clamped (pin detection needs it)
    assert _call("winFrac(5, 5, 5)") == 0        # zero-width window -> 0, never NaN/Infinity


# ---- the min-window floor -----------------------------------------------------------------


@requires_node
def test_effmin_capped_to_duration():
    assert _call("effMinWin(10, 2)") == 2
    assert _call("effMinWin(1, 2)") == 1         # a clip shorter than the floor caps the floor
    assert _call("effMinWin(10, 0)") == 0


@requires_node
def test_min_window_policy():
    assert _call("minWindowSec(10, 30, 6)") == pytest.approx(0.2, abs=APPROX)
    assert _call("minWindowSec(0.1, 30, 6)") == pytest.approx(0.1, abs=APPROX)  # capped to duration
    assert _call("minWindowSec(10, 0, 6)") == pytest.approx(0.2, abs=APPROX)    # fps unknown -> floor


# ---- resize (handle drags) ----------------------------------------------------------------


@requires_node
def test_resize_start():
    assert _call("resizeWindowStart(3, 8, 10, 1)") == [3, 8]
    assert _call("resizeWindowStart(7.5, 8, 10, 1)") == [7, 8]   # stops at end - minWin
    assert _call("resizeWindowStart(9, 8, 10, 1)") == [7, 8]     # cannot cross the right edge
    assert _call("resizeWindowStart(-3, 8, 10, 1)") == [0, 8]
    assert _call("resizeWindowStart(1, 2, 3, 5)") == [0, 3]      # duration < minWin -> inert/full


@requires_node
def test_resize_end():
    assert _call("resizeWindowEnd(2, 7, 10, 1)") == [2, 7]
    assert _call("resizeWindowEnd(2, 99, 10, 1)") == [2, 10]
    assert _call("resizeWindowEnd(3, 3.5, 10, 1)") == _approx([3, 4])   # min window floor
    assert _call("resizeWindowEnd(3, 1, 10, 1)") == [3, 4]              # cannot cross the left edge
    assert _call("resizeWindowEnd(9.5, 20, 10, 1)") == [9, 10]          # start pre-clamped, size kept


# ---- place / pan / center (size-preserving) ----------------------------------------------


@requires_node
def test_place_window():
    assert _call("placeWindow(3, 4, 10)") == [3, 7]
    assert _call("placeWindow(-2, 4, 10)") == [0, 4]
    assert _call("placeWindow(8, 4, 10)") == [6, 10]
    assert _call("placeWindow(3, 20, 10)") == [0, 10]   # size capped to the clip


@requires_node
def test_pan_preserves_size():
    assert _call("panWindow(2, 5, 1, 10)") == [3, 6]
    assert _call("panWindow(8, 10, 5, 10)") == [8, 10]   # clamped at the end, size 2 kept
    assert _call("panWindow(0, 2, -5, 10)") == [0, 2]    # clamped at the start, size 2 kept
    assert _call("panWindow(0, 10, 3, 10)") == [0, 10]   # whole clip: pinned


@requires_node
def test_pan_size_invariant_sweep():
    # whatever the delta, the window width is preserved
    for delta in range(-20, 21):
        r = _call(f"panWindow(3, 7, {delta}, 10)")
        assert r[1] - r[0] == pytest.approx(4, abs=APPROX), f"size changed at delta={delta}: {r}"


@requires_node
def test_center_window():
    assert _call("centerWindow(5, 4, 10)") == [3, 7]
    assert _call("centerWindow(0.5, 4, 10)") == [0, 4]    # clamped at the start, size kept
    assert _call("centerWindow(9.5, 4, 10)") == [6, 10]   # clamped at the end, size kept
    assert _call("centerWindow(5, 20, 10)") == [0, 10]    # size > clip


# ---- clampWindow: the invariant enforcer --------------------------------------------------


@requires_node
def test_clampwindow_identity_when_legal():
    assert _call("clampWindow(2, 8, 10, 1)") == [2, 8]


@requires_node
def test_clampwindow_reclamps_shorter_reload():
    # the same view on a since-shortened clip (a re-probed / replaced file) clamps into the new dur
    assert _call("clampWindow(2, 8, 5, 1)") == [2, 5]


@requires_node
def test_clampwindow_size_floor_and_crossed():
    assert _call("clampWindow(3, 3.4, 10, 1)") == _approx([3, 4])   # grow to the floor
    assert _call("clampWindow(5, 2, 10, 1)") == [5, 6]              # crossed collapses, then grows


@requires_node
def test_clampwindow_tail_overflow_pulls_start_back():
    assert _call("clampWindow(9.5, 9.6, 10, 1)") == [9, 10]   # push-end hits duration -> pull start back


@requires_node
def test_clampwindow_degenerate():
    assert _call("clampWindow(1, 2, 3, 5)") == [0, 3]       # duration < minWin
    assert _call("clampWindow(-1, 99, 3, 5)") == [0, 3]
    assert _call("clampWindow(0, 0, 0, 1)") == [0, 0]       # no duration yet


@requires_node
def test_clampwindow_idempotent():
    for a, b, d, m in [(9.5, 9.6, 10, 1), (5, 2, 10, 1), (3, 3.4, 10, 1), (2, 8, 5, 1), (1, 2, 3, 5)]:
        once = _call(f"clampWindow({a}, {b}, {d}, {m})")
        twice = _call(f"clampWindow({once[0]}, {once[1]}, {d}, {m})")
        assert once == twice, f"clampWindow not a fixed point for ({a},{b},{d},{m}): {once} -> {twice}"


# ---- pin / clip: keeping an off-window edge from being lost --------------------------------


@requires_node
def test_pin_inside_before_after():
    assert _call("pinToBar(4, 2, 6)") == [0.5, 0]    # visible
    assert _call("pinToBar(0, 2, 6)") == [0, -1]     # off the left -> pinned at 0, side -1
    assert _call("pinToBar(8, 2, 6)") == [1, 1]      # off the right -> pinned at 1, side +1
    assert _call("pinToBar(4, 5, 5)") == [0, 0]      # zero-width window: no NaN


@requires_node
def test_clipspan_visible_and_straddle():
    assert _call("clipSpan(3, 5, 2, 6)") == [0.25, 0.75, True]
    assert _call("clipSpan(0, 5, 2, 6)") == [0, 0.75, True]      # straddles the left edge
    assert _call("clipSpan(3, 9, 2, 6)") == [0.25, 1, True]      # straddles the right edge
    assert _call("clipSpan(0, 9, 2, 6)") == [0, 1, True]         # covers the whole window


@requires_node
def test_clipspan_offscreen_not_drawn():
    assert _call("clipSpan(0, 1, 2, 6)") == [0, 0, False]   # wholly left of the window
    assert _call("clipSpan(7, 9, 2, 6)") == [1, 1, False]   # wholly right of the window


# ---- property: every interaction op lands on a clampWindow fixed point ---------------------


@requires_node
def test_ops_are_clampwindow_fixed_points():
    """A window any interaction helper emits must already satisfy the invariant enforcer - else the
    detail transforms could be fed an illegal window between a drag and the next clamp."""
    cases = [
        ("resizeWindowStart(7.5, 8, 10, 1)", 10, 1),
        ("resizeWindowEnd(3, 3.5, 10, 1)", 10, 1),
        ("panWindow(8, 10, 5, 10)", 10, 1),
        ("centerWindow(9.5, 4, 10)", 10, 1),
        ("placeWindow(8, 4, 10)", 10, 1),
    ]
    for expr, d, m in cases:
        w = _call(expr)
        clamped = _call(f"clampWindow({w[0]}, {w[1]}, {d}, {m})")
        assert clamped == _approx(w), f"{expr} -> {w} is not a clampWindow fixed point ({clamped})"
