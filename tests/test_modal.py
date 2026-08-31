"""When a modal's backdrop dismisses it, executed under node.

A `click` fires on the nearest common ancestor of its mousedown and mouseup targets, so a
modal opened on mousedown is safe from the click that opened it - that press was on the
canvas. It is NOT safe from the second press of a double-click: the editor opens on
mousedown and appends its overlay synchronously, so the second press and release both land
on the backdrop and the modal that was just opened closes again on its own. Double-clicking
the scissors chip therefore made the editor flash and vanish.

Requiring the press to have STARTED on the backdrop fixes that, and a second annoyance
with it: selecting text in the prompt box and releasing the button outside the panel used
to count as a backdrop click and throw the edit away.

Extracted from between the MMRP-MODAL markers and run under node, the same harness
tests/test_migration.py uses, so this exercises the shipped rule rather than a copy.
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
    start = text.find("// >>> MMRP-MODAL")
    end = text.find("// <<< MMRP-MODAL")
    assert start != -1 and end != -1, (
        "the MMRP-MODAL markers are gone from web/refpack.js - this test extracts the "
        "shipped rule through them, and without them it would be testing nothing"
    )
    return text[start:end]


def _run(expression: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expression}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
@pytest.mark.parametrize("target_is_backdrop,press_on_backdrop,expected", [
    (True, True, True),     # a genuine backdrop click
    (True, False, False),   # released on the backdrop after pressing elsewhere
    (False, True, False),   # pressed on the backdrop, released on the panel
    (False, False, False),  # nothing to do with the backdrop
])
def test_only_a_press_and_release_on_the_backdrop_dismisses(
    target_is_backdrop, press_on_backdrop, expected
):
    got = _run(f"shouldDismissOnBackdrop({json.dumps(target_is_backdrop)}, "
               f"{json.dumps(press_on_backdrop)})")
    assert got is expected


@requires_node
def test_the_second_press_of_a_double_click_does_not_dismiss():
    """The reported case, driven through the real handlers against a fake element.

    mousedown #1 lands on the canvas and opens the modal, so the overlay never sees it.
    mousedown #2 and its release both land on the backdrop - which is a real click on the
    overlay, and used to close the editor the double-click had just opened.
    """
    out = _run("""(() => {
        let removed = 0;
        const overlay = { remove: () => { removed += 1; } };
        dismissOnBackdrop(overlay);
        // the press that opened the modal happened before the overlay existed, so the
        // overlay's own mousedown never ran for it - only the click does
        overlay.onclick({ target: overlay });
        return { removed };
    })()""")
    assert out["removed"] == 0, (
        "a click whose press the overlay never saw must not dismiss it"
    )


@requires_node
def test_a_real_backdrop_click_still_dismisses():
    """The behaviour must survive: clicking outside the panel is how the modal is meant to
    be dismissed, and it is documented as such."""
    out = _run("""(() => {
        let removed = 0;
        const overlay = { remove: () => { removed += 1; } };
        dismissOnBackdrop(overlay);
        overlay.onmousedown({ target: overlay });
        overlay.onclick({ target: overlay });
        return { removed };
    })()""")
    assert out["removed"] == 1


@requires_node
def test_a_drag_out_of_the_panel_does_not_dismiss():
    """Selecting text in the prompt box and releasing outside used to throw the edit away."""
    out = _run("""(() => {
        let removed = 0;
        const overlay = { remove: () => { removed += 1; } };
        const panel = {};
        dismissOnBackdrop(overlay);
        overlay.onmousedown({ target: panel });
        overlay.onclick({ target: overlay });
        return { removed };
    })()""")
    assert out["removed"] == 0


@requires_node
def test_the_press_flag_does_not_leak_into_the_next_click():
    """One backdrop press must arm exactly one dismissal, or a later stray click inherits
    it - the kind of state bug that only shows up on the second modal of a session."""
    out = _run("""(() => {
        let removed = 0;
        const overlay = { remove: () => { removed += 1; } };
        const panel = {};
        dismissOnBackdrop(overlay);
        overlay.onmousedown({ target: overlay });
        overlay.onclick({ target: panel });     // press armed, but released on the panel
        overlay.onclick({ target: overlay });   // ...must not cash in that old press
        return { removed };
    })()""")
    assert out["removed"] == 0
