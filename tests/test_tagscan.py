"""The prompt tag scanner, executed under node.

`scanPromptTags` is the single reader behind all of the tag UI - the highlight backdrop,
the hover-to-tile mapping, and the "delete stray tags" action - so it has to agree with
what tagRemap leaves behind (a deleted reference becomes ``<Kind #>``) and with what the
set currently holds (a number past the count is stale). Pure, marker-delimited.
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
    start = text.find("// >>> MMRP-TAGSCAN")
    end = text.find("// <<< MMRP-TAGSCAN")
    assert start != -1 and end != -1, "the MMRP-TAGSCAN markers are gone from web/refpack.js"
    return text[start:end].replace("export ", "")


def _call(expr: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _scan(text: str, counts: dict):
    return _call(f"scanPromptTags({json.dumps(text)}, {json.dumps(counts)})")


@requires_node
def test_the_markers_are_still_there():
    # extraction itself asserts they exist; this makes their removal a red test, not a
    # suite that quietly stops covering anything.
    assert "scanPromptTags" in _extract()


@requires_node
def test_a_live_tag_is_found_with_its_span_and_is_not_stray():
    out = _scan("wearing <Picture 2>", {"images": 3})
    assert len(out) == 1
    tag = out[0]
    assert tag["kind"] == "Picture" and tag["num"] == 2 and tag["stray"] is False
    assert tag["text"] == "<Picture 2>"
    assert tag["start"] == 8 and tag["end"] == 19


@requires_node
def test_a_number_past_the_count_is_stray():
    out = _scan("<Picture 4> is gone", {"images": 3})
    assert out[0]["stray"] is True and out[0]["num"] == 4


@requires_node
def test_the_hash_marker_is_always_stray_and_carries_no_number():
    out = _scan("<Picture #> and <Audio #>", {"images": 3, "audios": 2})
    assert [t["stray"] for t in out] == [True, True]
    assert [t["num"] for t in out] == [None, None]
    assert [t["raw"] for t in out] == ["#", "#"]


@requires_node
def test_zero_and_negative_would_be_stray_though_the_ui_never_writes_them():
    # defensive: a hand-edited "<Picture 0>" points at nothing
    assert _scan("<Picture 0>", {"images": 3})[0]["stray"] is True


@requires_node
def test_each_kind_is_judged_against_its_own_count():
    text = "<Picture 1> <Video 2> <Audio 1>"
    out = _scan(text, {"images": 1, "videos": 1, "audios": 1})
    strays = {t["kind"]: t["stray"] for t in out}
    assert strays == {"Picture": False, "Video": True, "Audio": False}


@requires_node
def test_spans_survive_repeated_and_adjacent_tags():
    # the offsets have to be exact for the backdrop and for offset-splice reassignment;
    # two copies of the same tag must not collapse to one span.
    text = "<Picture 1><Picture 1>"
    out = _scan(text, {"images": 2})
    assert len(out) == 2
    assert (out[0]["start"], out[0]["end"]) == (0, 11)
    assert (out[1]["start"], out[1]["end"]) == (11, 22)


@requires_node
def test_subjects_are_not_judged_when_no_set_is_given():
    # highlighter path: subjects render as ordinary tags, never stray, if the set is absent
    out = _scan("<Subject 5> holds <Picture 1>", {"images": 1})
    subj = [t for t in out if t["kind"] == "Subject"][0]
    assert subj["stray"] is False and subj["num"] == 5


@requires_node
def test_a_subject_not_in_use_is_stray_once_the_set_is_supplied():
    out = _scan("<Subject 5>", {"images": 1, "subjects": [1, 2]})
    assert out[0]["stray"] is True
    out2 = _scan("<Subject 2>", {"images": 1, "subjects": [1, 2]})
    assert out2[0]["stray"] is False


@requires_node
def test_an_empty_prompt_scans_to_nothing():
    assert _scan("", {"images": 3}) == []
    assert _scan("no tags here", {"images": 3}) == []


@requires_node
def test_missing_counts_treat_every_numbered_tag_as_stray():
    # a kind with count 0 (or absent) has no valid number, so anything referring to it is
    # dangling - which is exactly right after the last reference of that kind is deleted.
    out = _scan("<Video 1>", {})
    assert out[0]["stray"] is True


@requires_node
def test_rewrite_tag_at_splices_one_span():
    out = _call('rewriteTagAt("a <Picture #> b", 2, 13, "<Picture 2>")')
    assert out == "a <Picture 2> b"


@requires_node
def test_rewrite_tag_at_refuses_a_span_out_of_range():
    # a stale span (text edited underneath it) leaves the text alone rather than corrupting
    assert _call('rewriteTagAt("short", 2, 99, "X")') == "short"
    assert _call('rewriteTagAt("short", -1, 3, "X")') == "short"


def _strip(text: str, counts: dict):
    return _call(f"stripStrayTags({json.dumps(text)}, {json.dumps(counts)})")


@requires_node
def test_strip_removes_a_stray_and_the_space_it_leaves():
    assert _strip("a <Picture #> b", {"images": 0}) == "a b"
    assert _strip("<Picture #> leads", {"images": 0}) == "leads"
    assert _strip("trails <Picture #>", {"images": 0}) == "trails"


@requires_node
def test_strip_leaves_live_tags_and_prose_alone():
    text = "the <Picture 1> and <Picture #> scene"
    assert _strip(text, {"images": 1}) == "the <Picture 1> and scene"
    # nothing stray -> byte-identical, deliberate double spaces preserved
    assert _strip("kept  spacing <Picture 1>", {"images": 1}) == "kept  spacing <Picture 1>"


@requires_node
def test_strip_handles_two_adjacent_strays_without_eating_too_much():
    assert _strip("a <Picture #> <Video #> b", {"images": 0, "videos": 0}) == "a b"


@requires_node
def test_strip_also_removes_a_number_that_outran_the_set():
    # the "did not become a #, but points at nothing" case
    assert _strip("gone <Picture 4> here", {"images": 2}) == "gone here"


# ---- the backdrop colour class ------------------------------------------------------


def _cls(tag, caret=None, subject=None):
    import json as _json
    return _call(f"tagSpanClass({_json.dumps(tag)}, {_json.dumps(caret)}, {_json.dumps(subject)})")


_PIC1 = {"kind": "Picture", "num": 1, "stray": False, "start": 5, "end": 16, "text": "<Picture 1>"}
_SUB2 = {"kind": "Subject", "num": 2, "stray": False, "start": 0, "end": 11, "text": "<Subject 2>"}


@requires_node
def test_a_plain_tag_is_the_neutral_grey_default():
    assert _cls(_PIC1) == "mmrp-tag"


@requires_node
def test_a_stray_tag_is_red_even_under_the_caret():
    stray = {**_PIC1, "stray": True}
    assert _cls(stray) == "mmrp-tag mmrp-tag-stray"
    # broken beats active: a stray the caret sits on still reads red
    caret = {"start": 5, "end": 16, "text": "<Picture 1>", "kind": "Picture"}
    assert _cls(stray, caret=caret) == "mmrp-tag mmrp-tag-stray"


@requires_node
def test_the_tag_the_caret_is_on_is_blue():
    caret = {"start": 5, "end": 16, "text": "<Picture 1>", "kind": "Picture"}
    assert _cls(_PIC1, caret=caret) == "mmrp-tag mmrp-tag-active"
    # a different tag is not
    other = {**_PIC1, "start": 20, "end": 31, "num": 2, "text": "<Picture 2>"}
    assert _cls(other, caret=caret) == "mmrp-tag"


@requires_node
def test_a_highlighted_subject_colours_all_its_tags_blue():
    assert _cls(_SUB2, subject=2) == "mmrp-tag mmrp-tag-active"
    assert _cls(_SUB2, subject=3) == "mmrp-tag"      # a different subject
    assert _cls(_SUB2, subject=None) == "mmrp-tag"   # nothing highlighted
    # the subject rule does not touch media tags
    assert _cls(_PIC1, subject=1) == "mmrp-tag"
