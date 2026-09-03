"""The mouse-wheel policy over the node body, executed under node.

Our custom DOM widget used to be a dead zone for the wheel; now a wheel over it zooms the
graph, scrolls the prompt, or does nothing, following a small state machine. The subtle part
- the reveal barrier, the new-spin release, the directional reveal test - lives in the
MMRP-WHEEL marker block as pure functions so it can be covered here the way MMRP-CUE is by
test_cue.py, rather than only through the browser. The DOM listener that feeds these
(onBlockWheel) is thin and left to live testing.

The behaviour under test (confirmed with the user):
  - off the prompt (over tiles/subject): always zoom.
  - over the prompt, text HIDDEN (zoomed out): zoom, and the tick that reveals the text arms a
    barrier that makes the rest of that same gesture inert.
  - over the prompt, text VISIBLE: scroll the textbox only, never zoom.
  - a fresh spin (gap > 150 ms) releases the barrier.
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
    start = text.find("// >>> MMRP-WHEEL")
    end = text.find("// <<< MMRP-WHEEL")
    assert start != -1 and end != -1, (
        "the MMRP-WHEEL markers are gone from web/refpack.js - this test extracts the real "
        "shipped wheel policy through them, so removing them silently drops all coverage of "
        "the zoom / scroll / reveal-barrier logic"
    )
    return text[start:end].replace("export ", "")


def _call(expr):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _decide(over_prompt, text_hidden, gap_ms, barrier):
    return _call(
        "decideWheel({overPrompt: %s, textHidden: %s, gapMs: %s, barrierEngaged: %s})"
        % (json.dumps(over_prompt), json.dumps(text_hidden), json.dumps(gap_ms), json.dumps(barrier))
    )


# ---- extraction contract ------------------------------------------------------------------


@requires_node
def test_markers_present_and_helpers_exported():
    src = _extract()
    for name in ("decideWheel", "crossedReveal", "isNewSpin"):
        assert name in src, f"{name} vanished from the MMRP-WHEEL block"


# ---- off the prompt: always zoom ----------------------------------------------------------


@requires_node
def test_off_prompt_always_zooms_and_never_arms():
    # whether text is hidden or not, off the prompt the wheel zooms and does NOT arm the
    # prompt's barrier (a reveal while zooming over tiles must not swallow the next prompt tick)
    for hidden in (True, False):
        d = _decide(False, hidden, 0, False)
        assert d["action"] == "zoom"
        assert d["armReveal"] is False


@requires_node
def test_off_prompt_zoom_ignores_a_stuck_barrier():
    # leaving the prompt ends the prompt gesture: an engaged barrier must not block zoom/pan
    # over the tiles, and the barrier is cleared so returning to a visible prompt scrolls again
    d = _decide(False, False, 20, True)
    assert d["action"] == "zoom"
    assert d["barrier"] is False


# ---- over the prompt, text hidden: zoom + arm the reveal ----------------------------------


@requires_node
def test_hidden_prompt_zooms_and_arms_reveal():
    d = _decide(True, True, 0, False)
    assert d == {"action": "zoom", "armReveal": True, "barrier": False}


# ---- over the prompt, text visible: scroll only -------------------------------------------


@requires_node
def test_visible_prompt_scrolls_never_zooms():
    d = _decide(True, False, 0, False)
    assert d["action"] == "scrollText"
    assert d["armReveal"] is False


# ---- the reveal barrier -------------------------------------------------------------------


@requires_node
def test_engaged_barrier_makes_the_same_gesture_inert():
    # same gesture (gap <= 150) with the barrier engaged: do nothing, whether the newly
    # revealed text now reads as visible or a further hidden tick
    for hidden in (True, False):
        d = _decide(True, hidden, 20, True)
        assert d["action"] == "nothing"
        assert d["barrier"] is True


@requires_node
def test_a_fresh_spin_releases_the_barrier_and_resumes_scroll():
    d = _decide(True, False, 200, True)
    assert d["action"] == "scrollText"
    assert d["barrier"] is False


# ---- new-spin boundary + robustness -------------------------------------------------------


@requires_node
def test_new_spin_boundary_is_strict_over_150():
    assert _call("isNewSpin(150)") is False   # exactly at the window is still the same gesture
    assert _call("isNewSpin(151)") is True
    assert _call("isNewSpin(0)") is False
    # and it flips the decision at the boundary
    assert _decide(True, False, 150, True)["action"] == "nothing"
    assert _decide(True, False, 151, True)["action"] == "scrollText"


@requires_node
def test_missing_or_first_timestamp_reads_as_a_new_spin():
    # gap NaN/undefined/Infinity (first-ever event, or a remounted node) must NOT be treated as
    # a continuation - otherwise a stale barrier swallows the very first tick. `gap > 150` would
    # get this wrong (NaN > 150 is false).
    assert _call("isNewSpin(Infinity)") is True
    assert _call("isNewSpin(NaN)") is True
    assert _call("isNewSpin(undefined)") is True


# ---- crossedReveal is directional ---------------------------------------------------------


@requires_node
def test_crossed_reveal_only_fires_upward_across_the_threshold():
    thr = 0.5438
    assert _call(f"crossedReveal(0.40, 0.70, {thr})") is True    # hidden -> visible: the reveal
    assert _call(f"crossedReveal(0.70, 0.40, {thr})") is False   # visible -> hidden: never arms
    assert _call(f"crossedReveal(0.60, 0.80, {thr})") is False   # already visible
    assert _call(f"crossedReveal(0.20, 0.40, {thr})") is False   # still hidden
    assert _call(f"crossedReveal(0.5438, 0.60, {thr})") is False # at the threshold counts as shown


# ---- the whole continuous gesture, threaded the way onBlockWheel threads it ----------------


@requires_node
def test_full_gesture_sequence_zoom_reveal_stop_stop_then_scroll():
    """Persist `barrier` between ticks exactly as the DOM wrapper does: a hidden zoom, the tick
    that reveals (text now visible + barrier armed), two more same-gesture ticks that must do
    nothing, then a fresh spin that scrolls. This is the behaviour the user described as 'stop
    first, then a new spin scrolls the text'."""
    ticks = [
        # (overPrompt, textHidden, gapMs) ; expected action
        (True, True, 0,   "zoom"),        # zooming in, still hidden
        (True, True, 20,  "zoom"),        # the reveal tick (wrapper arms the barrier after this)
        (True, False, 20, "nothing"),     # same gesture, text now visible -> inert
        (True, False, 20, "nothing"),     # still the same gesture -> inert
        (True, False, 200, "scrollText"), # fresh spin -> scroll the text
    ]
    barrier = False
    actions = []
    for over, hidden, gap, _expected in ticks:
        d = _decide(over, hidden, gap, barrier)
        actions.append(d["action"])
        # mirror the wrapper: on the armed reveal zoom it would set the barrier from
        # crossedReveal (true on tick 2); otherwise it stores d.barrier.
        if d["action"] == "zoom" and d["armReveal"] and hidden and gap == 20:
            barrier = True   # tick 2 crossed the threshold upward
        else:
            barrier = d["barrier"]
    assert actions == ["zoom", "zoom", "nothing", "nothing", "scrollText"]
