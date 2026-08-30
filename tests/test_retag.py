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


# ---- moving a reference within its section -----------------------------------------
#
# `to` is an INSERTION point in the ORIGINAL array, which is the natural thing for a drop
# target to be ("put it in this gap") and the source of the one off-by-one that matters:
# once the dragged item is lifted out, every index past it shifts down by one.


@pytest.mark.parametrize("frm,to,expected", [
    (0, 2, ["b", "a", "c"]),      # forward: lands in the gap that was between b and c
    (0, 3, ["b", "c", "a"]),      # to the very end
    (2, 0, ["c", "a", "b"]),      # backward: indices before it do not shift
    (1, 0, ["b", "a", "c"]),
    (0, 0, ["a", "b", "c"]),      # onto its own left edge - a no-op
    (0, 1, ["a", "b", "c"]),      # onto its own right edge - also a no-op
    (2, 2, ["a", "b", "c"]),
])
@requires_node
def test_move_ref_treats_to_as_an_insertion_point(frm, to, expected):
    assert _run(f'moveRef(["a", "b", "c"], {frm}, {to})') == expected


@requires_node
def test_move_ref_does_not_mutate_the_array_it_was_given():
    """applyRefs swaps the whole state object, and a mutation here would edit the state
    the caller is still comparing against."""
    out = _run('(() => { const a = ["a", "b", "c"]; moveRef(a, 0, 3); return a; })()')
    assert out == ["a", "b", "c"]


@pytest.mark.parametrize("frm,to", [(-1, 0), (5, 0), (0, 99)])
@requires_node
def test_move_ref_clamps_rather_than_throwing(frm, to):
    """A drop can be computed from stale hit regions if a repaint lands mid-gesture, so
    an out-of-range index has to be survivable rather than fatal."""
    out = _run(f'moveRef(["a", "b", "c"], {frm}, {to})')
    assert sorted(out) == ["a", "b", "c"]


@requires_node
def test_moving_an_image_renumbers_the_tags_and_rewrites_the_prompt():
    """The whole point of pairing the move with the retag pass: <Picture 3> has to follow
    the image it was written about."""
    before = _refs(images=["a.png", "b.png", "c.png"])
    after = {"images": _run('moveRef(%s, 2, 0)' % json.dumps(before["images"])),
             "videos": [], "audios": []}
    assert [r["file"] for r in after["images"]] == ["c.png", "a.png", "b.png"]
    assert _retag("<Picture 3> wears the jacket", before, after) == \
        "<Picture 1> wears the jacket"


def test_subjects_survive_the_references_json_round_trip():
    """Both converters WHITELIST fields, so anything not named in them is dropped between
    the working state and the widget - which presents as "the grouping did not save"
    rather than as an error. This is the guard for that whole class of bug."""
    js = """
        (() => {
            const list = [{kind: "image", file: "a.png", subjects: [2, 1]},
                          {kind: "video", file: "v.mp4", use_soundtrack: true, subjects: [3]},
                          {kind: "audio", file: "s.wav", subjects: [1]}];
            return toReferencesList(fromReferencesList(list));
        })()
    """
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("export function fromReferencesList")
    end = text.find("export function editSummary")
    assert start != -1 and end != -1
    # pythonTruthy is a dependency of fromReferencesList - it is how the soundtrack
    # flag is read the same way Python reads it - so running the converters means
    # shipping it alongside them. See tests/test_soundtrack_parity.py.
    def grab(name):
        at = text.find(f"function {name}")
        assert at != -1, f"{name} moved - this test runs the shipped code, not a copy"
        return text[at:text.find("\n}\n", at) + 3]

    block = grab("takeEdit") + grab("pythonTruthy") + text[start:end]
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", block + f"\nconsole.log(JSON.stringify({js}));\n"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip())
    assert [r.get("subjects") for r in out] == [[1, 2], [3], [1]]

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


# ---- where a drop lands, once a section spans several rows --------------------------


def _extract_drop() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-DROP")
    end = text.find("// <<< MMRP-DROP")
    assert start != -1 and end != -1, "the MMRP-DROP markers are gone from web/refpack.js"
    return "const CL = { tile: 131, gap: 6 };\n" + text[start:end]


def _drop_at(count, per_row, x, y):
    """Tile regions laid out exactly as draw() paints them, then the real dropIndexAt."""
    tiles = [
        {"type": "tile", "kind": "image", "index": i,
         "x": 10 + (i % per_row) * 137, "y": 8 + (i // per_row) * 137, "w": 131, "h": 131}
        for i in range(count)
    ]
    node = {"_mmrpHit": {"regions": tiles}}
    script = (_extract_drop()
              + f"\nconst node = {json.dumps(node)};\n"
              + f"console.log(JSON.stringify(dropIndexAt(node, 'image', {x}, {y})));\n")
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_dropping_past_the_end_of_a_short_last_row_appends():
    """The bug: five tiles in rows of three leaves the last row short, and a drop in the
    empty space to its right used to be won by a tile on the row ABOVE - returning 3, the
    START of the last row, instead of 5. A single weighted distance cannot express "same
    row"; the row has to be chosen first."""
    # The coordinates matter, and are narrower than they look. Level with the last row's
    # vertical CENTRE the old weighted distance happened to be right; only within about
    # 17px of the row's TOP EDGE did the vertical term shrink enough for a tile on the row
    # above to win on horizontal distance alone. Measured by running both versions over the
    # whole area: they differ on 177 points, all in y 143..159.
    assert _drop_at(5, 3, x=500, y=150) == 5


def _off_strip(x, y, view_w=560, view_h=290):
    """The real droppedOffStrip, over a slab of the given size."""
    script = (_extract_drop()
              + f"\nconsole.log(JSON.stringify(droppedOffStrip("
              + f"{{x: {x}, y: {y}}}, {view_w}, {view_h})));\n")
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_releasing_off_the_slab_is_an_abort():
    """Dragging a tile out of the node and letting go on empty canvas has to mean "never
    mind". Without this it silently committed the reorder and retagged the prompt to
    match, leaving Escape as the only way out of a drag."""
    assert _off_strip(1500, 150) is True      # far to the right
    assert _off_strip(280, -400) is True      # above the node
    assert _off_strip(-600, 150) is True      # left of it
    assert _off_strip(280, 900) is True       # below it


@requires_node
def test_a_drop_just_past_the_edge_still_counts():
    """The slack matters as much as the bound: aiming at the empty space after the last
    tile is how a drop is normally made, and a near miss must not read as an abort."""
    assert _off_strip(280, 150) is False      # squarely inside
    assert _off_strip(600, 150) is False      # past the right edge, within one tile
    assert _off_strip(280, -60) is False      # just above the top edge
    assert _off_strip(280, 350) is False      # just below the bottom edge


@requires_node
def test_an_unmeasurable_slab_never_aborts():
    """Before the DOM widget has been laid out the canvas reports zero, and every drop
    would look "off the slab". Reordering degrades to its old always-drop behaviour rather
    than becoming impossible."""
    assert _off_strip(280, 150, view_w=0, view_h=0) is False


@requires_node
def test_dropping_past_the_end_of_a_full_single_row_appends():
    assert _drop_at(3, 3, x=800, y=8 + 65) == 3


@pytest.mark.parametrize("y", [143, 150, 159, 180, 210, 250, 275])
@requires_node
def test_the_whole_height_of_the_last_row_appends(y):
    """The full band, not just the strip that used to be wrong - so a future rewrite
    cannot fix the edge and break the middle."""
    assert _drop_at(5, 3, x=500, y=y) == 5


@requires_node
def test_dropping_right_of_a_full_upper_row_stays_on_that_row():
    """The other side of the same coin: a drop level with a FULL row belongs at that row's
    end, not carried down to the last row."""
    assert _drop_at(5, 3, x=800, y=8 + 65) == 3


@requires_node
def test_dropping_before_the_first_tile_is_index_zero():
    assert _drop_at(5, 3, x=0, y=8 + 65) == 0


@pytest.mark.parametrize("i,expected", [(0, 0), (1, 1), (2, 2)])
@requires_node
def test_a_drop_on_a_tiles_left_half_inserts_before_it(i, expected):
    assert _drop_at(5, 3, x=10 + i * 137 + 20, y=8 + 65) == expected


@pytest.mark.parametrize("i,expected", [(0, 1), (1, 2), (2, 3)])
@requires_node
def test_a_drop_on_a_tiles_right_half_inserts_after_it(i, expected):
    assert _drop_at(5, 3, x=10 + i * 137 + 110, y=8 + 65) == expected

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
