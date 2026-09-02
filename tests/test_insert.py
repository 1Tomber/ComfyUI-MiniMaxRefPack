"""Double-clicking a tile writes its tag into the prompt at the caret.

The spacing is the part worth testing. The tags go to a model that is told to use them
exactly, so "<Picture 1>wears" and "<Picture 1> wears" are different token sequences, and
a tag jammed against a neighbouring word is the failure this exists to prevent. Making
the user remember the space is how you get the first one.

Extracted from web/refpack.js between its MMRP-INSERT markers and run under node, the
same harness the other JS-side suites use.
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
    start = text.find("// >>> MMRP-INSERT")
    end = text.find("// <<< MMRP-INSERT")
    assert start != -1 and end != -1, (
        "the MMRP-INSERT markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them, so removing them silently stops tag insertion "
        "from being covered at all"
    )
    return text[start:end]


def _splice(value, start, end, text="<Picture 1>"):
    script = (
        _extract()
        + f"\nconsole.log(JSON.stringify(spliceTag({json.dumps(value)}, {start}, {end}, "
          f"{json.dumps(text)})));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_into_an_empty_box_it_is_just_the_tag():
    assert _splice("", 0, 0)["value"] == "<Picture 1>"


@requires_node
def test_a_space_is_added_before_a_tag_that_would_touch_a_word():
    assert _splice("the woman in", 12, 12)["value"] == "the woman in <Picture 1>"


@requires_node
def test_no_second_space_when_there_is_already_one():
    assert _splice("the woman in ", 13, 13)["value"] == "the woman in <Picture 1>"


@requires_node
def test_a_space_is_added_after_when_text_follows():
    assert _splice("wears a coat", 0, 0)["value"] == "<Picture 1> wears a coat"


@requires_node
def test_inserting_mid_sentence_spaces_both_sides():
    assert _splice("the woman in wears a coat", 13, 13)["value"] == \
        "the woman in <Picture 1> wears a coat"


@requires_node
def test_a_selection_is_replaced_not_pushed_aside():
    """Selecting a stale tag and double-clicking a tile should swap it, which is the
    natural way to repoint a sentence at a different reference."""
    out = _splice("the woman in <Picture 9> smiles", 13, 24)
    assert out["value"] == "the woman in <Picture 1> smiles"


@requires_node
def test_a_newline_counts_as_spacing():
    """Otherwise every tag starting a new line would get a leading space."""
    assert _splice("first line\n", 11, 11)["value"] == "first line\n<Picture 1>"


@requires_node
def test_the_caret_lands_after_what_was_inserted():
    """So the user can keep typing. If it landed before, they would type INTO the tag."""
    out = _splice("wears a coat", 0, 0)
    assert out["value"][:out["caret"]] == "<Picture 1> "


@requires_node
def test_insert_is_the_padded_string_the_caret_is_measured_against():
    """The caret is start + len(insert), so the undo-preserving writer MUST be handed
    `insert` (the padded tag), not the bare tag - otherwise it inserts fewer characters
    than the caret advances and the cursor lands a space or two past the text. This is the
    invariant behind the 'cursor jumps forward on double-click insert' fix."""
    out = _splice("abcdef", 3, 3)   # a caret between two non-space chars -> padded both sides
    assert out["insert"] == " <Picture 1> "
    assert out["caret"] - 3 == len(out["insert"])            # caret counts the padding
    assert out["value"][3:3 + len(out["insert"])] == out["insert"]   # and that padding IS inserted


@requires_node
def test_the_insert_goes_through_the_undo_preserving_writer():
    """spliceTag decides WHAT the text becomes; the writer decides whether the browser's
    undo can still see it. Assigning .value resets the undo stack, so typing a sentence,
    inserting a tag and pressing Ctrl+Z did nothing at all.

    Read from the source rather than executed, because the alternative is a full DOM: what
    matters here is that the insert path calls the writer at all, and tests/test_undo.py
    covers what the writer then does.
    """
    src = REFPACK_JS.read_text(encoding="utf-8")
    start = src.index("function insertIntoDirection(")
    body = src[start:src.index(chr(10) + "}" + chr(10), start)]
    assert "writeTextPreservingUndo(" in body, (
        "the insert writes .value directly again, which discards the undo history"
    )
    assert body.index("writeTextPreservingUndo(") < body.index("el.value = value"), (
        "the direct assignment must be the FALLBACK, not the first choice"
    )
