"""Writing into the prompt textarea without destroying its undo history, under node.

Assigning `.value` resets the undo stack in Chromium and Firefox. So typing a sentence,
inserting a tag and pressing Ctrl+Z did nothing at all - not "undid the insert", nothing -
and every retag pass (reorder, delete, soundtrack toggle) did the same to whatever the user
had been writing.

execCommand("insertText") is deprecated and remains the only way to make a programmatic
edit that the browser's own undo can see. Extracted from between the MMRP-UNDO markers and
run under node with a stand-in `document`, so the ordering and the fallback are pinned
without a browser.
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
    start = text.find("// >>> MMRP-UNDO")
    end = text.find("// <<< MMRP-UNDO")
    assert start != -1 and end != -1, (
        "the MMRP-UNDO markers are gone from web/refpack.js - this test extracts the "
        "shipped writer through them, and without them it would be testing nothing"
    )
    return text[start:end]


def _run(body: str):
    script = _extract() + "\n" + body
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


HARNESS = """
const calls = [];
const el = {
    value: "hello world",
    focus() { calls.push("focus:el"); },
    setSelectionRange(a, b) { calls.push(`select:${a}-${b}`); },
};
const other = { focus() { calls.push("focus:other"); } };
globalThis.document = {
    activeElement: other,
    execCommand(name, showUi, text) {
        calls.push(`exec:${name}:${text}`);
        return EXEC_RESULT;
    },
};
"""


@requires_node
def test_it_uses_the_undo_preserving_path_when_it_can():
    out = _run("const EXEC_RESULT = true;" + HARNESS + """
        const ok = writeTextPreservingUndo(el, 0, 11, "goodbye", false);
        console.log(JSON.stringify({ ok, calls }));
    """)
    assert out["ok"] is True
    assert out["calls"] == ["focus:el", "select:0-11", "exec:insertText:goodbye"], out["calls"]


@requires_node
def test_it_puts_the_caret_back_when_asked():
    """A retag runs while the user is on the CANVAS. Stealing focus into the prompt box
    would be a worse bug than the one being fixed."""
    out = _run("const EXEC_RESULT = true;" + HARNESS + """
        writeTextPreservingUndo(el, 0, 11, "x", true);
        console.log(JSON.stringify({ calls }));
    """)
    assert out["calls"][-1] == "focus:other"


@requires_node
def test_it_does_not_move_focus_when_not_asked():
    """An insert is the user deliberately typing into the prompt, so the caret belongs
    there afterwards - that is the existing behaviour and it must not change."""
    out = _run("const EXEC_RESULT = true;" + HARNESS + """
        writeTextPreservingUndo(el, 0, 11, "x", false);
        console.log(JSON.stringify({ calls }));
    """)
    assert "focus:other" not in out["calls"]


@requires_node
def test_it_reports_failure_so_the_caller_can_fall_back():
    """A browser that has finally removed execCommand, or one that refuses the edit, must
    leave the caller free to assign - a lost undo stack beats lost text."""
    out = _run("const EXEC_RESULT = false;" + HARNESS + """
        const ok = writeTextPreservingUndo(el, 0, 11, "x", false);
        console.log(JSON.stringify({ ok }));
    """)
    assert out["ok"] is False


@requires_node
def test_it_reports_failure_when_there_is_no_document_at_all():
    out = _run("""
        const el = { value: "x", focus() {}, setSelectionRange() {} };
        console.log(JSON.stringify({ ok: writeTextPreservingUndo(el, 0, 1, "y", false) }));
    """)
    assert out["ok"] is False


@requires_node
def test_it_reports_failure_when_the_element_throws():
    """A detached textarea throws on focus. That is a fallback, not a crash in a reorder."""
    out = _run("""
        globalThis.document = { activeElement: null, execCommand: () => true };
        const el = {
            value: "x",
            focus() { throw new Error("not in the document"); },
            setSelectionRange() {},
        };
        console.log(JSON.stringify({ ok: writeTextPreservingUndo(el, 0, 1, "y", false) }));
    """)
    assert out["ok"] is False


@requires_node
def test_a_missing_element_is_refused_rather_than_thrown():
    out = _run("""
        globalThis.document = { activeElement: null, execCommand: () => true };
        console.log(JSON.stringify({
            nullEl: writeTextPreservingUndo(null, 0, 1, "y", false),
            undefinedEl: writeTextPreservingUndo(undefined, 0, 1, "y", false),
        }));
    """)
    assert out["nullEl"] is False
    assert out["undefinedEl"] is False
