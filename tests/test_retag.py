"""Keeping the tags in the prompt pointing at the reference the user meant.

The tags are the contract between the direction text and the sockets, and they are
POSITIONAL. Delete <Picture 2> of three and the third image becomes <Picture 2>; turn a
video's soundtrack off and every <Audio N> after it renumbers, because a soundtrack claims
its audio tag BEFORE its own video tag (refs.py assign_tags). Either way a direction that
said "<Picture 2> wears the jacket" silently starts describing a different image.

Extracted from the shipped web/refpack.js between its MMRP-RETAG markers and executed
under node, the same harness tests/test_migration.py uses - so this tests the real code
rather than a copy of it that can drift.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from minimax_refpack.refs import Reference, ReferenceSet

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-RETAG")
    end = text.find("// <<< MMRP-RETAG")
    assert start != -1 and end != -1, (
        "the MMRP-RETAG markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them, so removing them silently stops the retagging "
        "rule from being covered at all"
    )
    # assignTags lives outside the markers; the block needs it to build tag maps.
    a_start = text.find("export function assignTags(refs) {")
    a_end = text.find("\n}\n", a_start) + 3
    assert a_start != -1
    return text[a_start:a_end] + text[start:end]


def _run(expression: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expression}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _refs(images=(), videos=(), audios=()):
    return {
        "images": [{"file": f} for f in images],
        "videos": [{"file": f, "use_soundtrack": s} for f, s in videos],
        "audios": [{"file": f} for f in audios],
    }


def _remap(before, after):
    return _run(f"tagRemap(assignTags({json.dumps(before)}), assignTags({json.dumps(after)}))")


def _retag(text, before, after):
    return _run(
        f"retag({json.dumps(text)}, "
        f"tagRemap(assignTags({json.dumps(before)}), assignTags({json.dumps(after)})))"
    )


# ---- the substitution itself ------------------------------------------------------


@requires_node
def test_a_swap_does_not_collapse():
    """The reason this is one pass and not a loop. Rewriting tag by tag chains: with
    {1->2, 2->1} a sequential replace turns every <Picture 1> into <Picture 2>, then turns
    every <Picture 2> - including the ones it just wrote - back into <Picture 1>, and the
    swap silently becomes a no-op."""
    out = _run('retag("<Picture 1> and <Picture 2>", '
               '{"<Picture 1>": "<Picture 2>", "<Picture 2>": "<Picture 1>"})')
    assert out == "<Picture 2> and <Picture 1>"


@requires_node
def test_subject_tags_are_never_touched():
    """<Subject N> is assigned by the user, not derived from position, so it never
    renumbers - rewriting one would be corruption rather than repair."""
    out = _run('retag("<Subject 1> holds <Picture 2>", '
               '{"<Picture 2>": "<Picture 1>", "<Subject 1>": "<Subject 9>"})')
    assert out == "<Subject 1> holds <Picture 1>"


@requires_node
def test_an_empty_map_returns_the_text_unchanged():
    assert _run('retag("<Picture 1> stays", {})') == "<Picture 1> stays"


# ---- deleting a reference ---------------------------------------------------------


@requires_node
def test_deleting_an_image_renumbers_the_ones_after_it():
    before = _refs(images=["a.png", "b.png", "c.png"])
    after = _refs(images=["a.png", "c.png"])
    assert _remap(before, after) == {"<Picture 3>": "<Picture 2>"}
    assert _retag("<Picture 3> wears the jacket", before, after) == \
        "<Picture 2> wears the jacket"


@requires_node
def test_a_deleted_reference_is_left_alone_rather_than_repointed():
    """Its tag has no successor. Pointing it at whatever inherited the number would make
    the sentence describe a different image, which is worse than leaving a dangling tag
    the user can see and fix."""
    before = _refs(images=["a.png", "b.png"])
    after = _refs(images=["a.png"])
    assert _retag("<Picture 2> is gone", before, after) == "<Picture 2> is gone"


# ---- the audio counter, which is the subtle one -----------------------------------


@requires_node
def test_turning_a_soundtrack_off_renumbers_every_audio_after_it():
    """A soundtrack claims its <Audio N> BEFORE its own <Video N>, and standalone audio
    continues the same counter - so a video's toggle renumbers clips it has nothing to do
    with. This is the case that is hardest to spot by hand."""
    before = _refs(videos=[("v.mp4", True)], audios=["music.wav"])
    after = _refs(videos=[("v.mp4", False)], audios=["music.wav"])
    assert _remap(before, after) == {"<Audio 2>": "<Audio 1>"}
    assert _retag("the track in <Audio 2> sets the pace", before, after) == \
        "the track in <Audio 1> sets the pace"


@requires_node
def test_a_video_keeps_its_video_tag_when_only_its_soundtrack_moves():
    before = _refs(videos=[("a.mp4", True), ("b.mp4", True)])
    after = _refs(videos=[("a.mp4", False), ("b.mp4", True)])
    mapping = _remap(before, after)
    assert mapping == {"<Audio 2>": "<Audio 1>"}
    assert "<Video 1>" not in mapping and "<Video 2>" not in mapping


@requires_node
def test_deleting_a_video_renumbers_both_its_video_and_its_audio_neighbours():
    before = _refs(videos=[("a.mp4", True), ("b.mp4", True)], audios=["m.wav"])
    after = _refs(videos=[("b.mp4", True)], audios=["m.wav"])
    assert _remap(before, after) == {
        "<Video 2>": "<Video 1>", "<Audio 2>": "<Audio 1>", "<Audio 3>": "<Audio 2>",
    }


# ---- the JS mirror still matches Python -------------------------------------------


@requires_node
def test_the_js_tag_rule_still_agrees_with_refs_py():
    """tagRemap is only correct while assignTags is. Both halves exist deliberately, so
    they are checked against each other on a set that exercises the audio interleave."""
    js = _run(f"assignTags({json.dumps(_refs(['a.png'], [('v.mp4', True)], ['m.wav']))})")
    py = ReferenceSet([
        Reference(kind="image", file="a.png"),
        Reference(kind="video", file="v.mp4", use_soundtrack=True),
        Reference(kind="audio", file="m.wav"),
    ]).assign_tags()
    assert [t["tag"] for t in js["images"]] == [t.tag for t in py if t.kind == "image"]
    assert [t["tag"] for t in js["videos"]] == [t.tag for t in py if t.kind == "video"]
    assert [t["tag"] for t in js["audios"]] == [t.tag for t in py if t.kind == "audio"]
    assert js["videos"][0]["audioTag"] == "<Audio 1>"
    assert js["audios"][0]["tag"] == "<Audio 2>"


# ---- adding a reference is not always an append, as far as the tags go --------------


@requires_node
def test_uploading_a_video_renumbers_standalone_audio():
    """The case that looks like a pure append and is not. A video's soundtrack claims its
    <Audio N> BEFORE all standalone audio, so adding one clip shifts every standalone
    <Audio> up by one - and a prompt that said "<Audio 1>" about the user's music silently
    starts meaning the new video's soundtrack."""
    before = _refs(audios=["music.wav"])
    after = _refs(videos=[("v.mp4", True)], audios=["music.wav"])
    assert _remap(before, after) == {"<Audio 1>": "<Audio 2>"}
    assert _retag("the track in <Audio 1> sets the pace", before, after) == \
        "the track in <Audio 2> sets the pace"


@requires_node
def test_uploading_a_muted_video_leaves_the_audio_numbering_alone():
    before = _refs(audios=["music.wav"])
    after = _refs(videos=[("v.mp4", False)], audios=["music.wav"])
    assert _remap(before, after) == {}


@requires_node
def test_uploading_an_image_really_is_a_plain_append():
    """Images append at the end of their own kind and touch nothing, so the retag pass
    must be a no-op rather than a rewrite that happens to come out the same."""
    before = _refs(images=["a.png"], audios=["m.wav"])
    after = _refs(images=["a.png", "b.png"], audios=["m.wav"])
    assert _remap(before, after) == {}


@requires_node
def test_a_probe_finding_a_silent_clip_renumbers_the_audio_after_it():
    """syncProbes clears use_soundtrack when a clip turns out to have no audio track. That
    releases its <Audio N>, so everything after it moves down - and this fires on loading a
    workflow whose file was swapped for a silent one, exactly when a prompt is already
    written against the old numbering."""
    before = _refs(videos=[("v.mp4", True)], audios=["m.wav"])
    after = _refs(videos=[("v.mp4", False)], audios=["m.wav"])
    assert _remap(before, after) == {"<Audio 2>": "<Audio 1>"}


# ---- two references to one file ------------------------------------------------------


@requires_node
def test_duplicate_files_do_not_collapse_onto_one_tag():
    """The same file can legitimately appear twice - addFiles does no de-duplication, and
    an upload with an existing name overwrites on the server and returns that name - and
    the two are NOT interchangeable, because they can carry different crops, rotations or
    subjects.

    Pairing on the first entry with a matching name collapsed them: deleting b from
    [a, b, a] mapped <Picture 3> to <Picture 1> instead of <Picture 2>, pointing the prompt
    at the wrong one and leaving the survivor with no tag referring to it.
    """
    out = _run("""(() => {
        const A = { file: "a.png" }, B = { file: "b.png" };
        const before = assignTags({ images: [A, B, A], videos: [], audios: [] });
        const after = assignTags({ images: [A, A], videos: [], audios: [] });
        return tagRemap(before, after);
    })()""")
    assert out == {"<Picture 3>": "<Picture 2>"}, (
        "the second copy of a.png moved from slot 3 to slot 2; slot 1 did not move"
    )


@requires_node
def test_a_reorder_of_identical_files_is_a_permutation_not_a_collapse():
    """Three copies of one file, reordered. Whatever the pairing decides, no two source
    tags may land on the same destination - that is what loses a reference from the text.
    """
    out = _run("""(() => {
        const A = { file: "same.png" };
        const before = assignTags({ images: [A, A, A], videos: [], audios: [] });
        const after = assignTags({ images: [A, A], videos: [], audios: [] });
        const map = tagRemap(before, after);
        const targets = Object.values(map);
        return { map, distinct: new Set(targets).size === targets.length };
    })()""")
    assert out["distinct"], f"two tags collapsed onto one destination: {out['map']}"


@requires_node
def test_duplicate_videos_renumber_their_own_audio_tags():
    """The soundtrack tag renumbers independently of <Video N>, and it was looked up
    through the same first-match pairing."""
    out = _run("""(() => {
        const V = { file: "clip.mp4", use_soundtrack: true };
        const W = { file: "other.mp4", use_soundtrack: true };
        const before = assignTags({ images: [], videos: [V, W, V], audios: [] });
        const after = assignTags({ images: [], videos: [V, V], audios: [] });
        const map = tagRemap(before, after);
        const audioTargets = Object.entries(map)
            .filter(([k]) => k.startsWith("<Audio"))
            .map(([, v]) => v);
        return { map, audioDistinct: new Set(audioTargets).size === audioTargets.length };
    })()""")
    assert out["audioDistinct"], f"two <Audio N> collapsed: {out['map']}"


@requires_node
def test_distinct_files_are_unaffected():
    """The fix must not disturb the ordinary case, which is every case without duplicates."""
    out = _run("""(() => {
        const A = { file: "a.png" }, B = { file: "b.png" }, C = { file: "c.png" };
        const before = assignTags({ images: [A, B, C], videos: [], audios: [] });
        const after = assignTags({ images: [B, C], videos: [], audios: [] });
        return tagRemap(before, after);
    })()""")
    assert out == {"<Picture 2>": "<Picture 1>", "<Picture 3>": "<Picture 2>"}
