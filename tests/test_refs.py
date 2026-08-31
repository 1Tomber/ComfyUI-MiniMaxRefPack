"""Tests for the frozen reference contract.

The tag-order cases below are the whole point of this module: they encode what
ComfyUI's MiniMaxH3ReferenceToVideo actually does at nodes_minimax_h3.py:216-274.
If one of these changes, the node is lying to the user about <Picture N> / <Audio N>.
"""

import json
import os

import pytest

from minimax_refpack.refs import (
    MAX_AUDIOS,
    MAX_IMAGES,
    MAX_VIDEOS,
    Reference,
    ReferenceError,
    ReferenceSet,
    empty_outputs,
    output_names,
    output_types,
    slot_index,
    validate_crop,
    validate_max_edge,
    validate_flip,
    validate_rotate,
    validate_subjects,
    validate_trim,
)


def img(name):
    return Reference(kind="image", file=name)


def vid(name, sound=False):
    return Reference(kind="video", file=name, use_soundtrack=sound)


def aud(name):
    return Reference(kind="audio", file=name)


# ---- tag numbering ---------------------------------------------------------


def test_images_are_numbered_in_order():
    s = ReferenceSet([img("a.jpg"), img("b.png"), img("c.webp")])
    assert [t.tag for t in s.assign_tags()] == ["<Picture 1>", "<Picture 2>", "<Picture 3>"]


def test_slot_equals_ordinal_because_the_node_compacts():
    s = ReferenceSet([img("a.jpg"), img("b.png")])
    assert [t.slot for t in s.assign_tags()] == [1, 2]


def test_video_soundtrack_takes_an_audio_number_before_standalone_audio():
    # nodes_minimax_h3.py:259 appends the soundtrack's audio item BEFORE the video at :263,
    # and standalone ref_audios come last at :273.
    s = ReferenceSet([vid("clip.mp4", sound=True), aud("vo.wav")])
    tagged = {t.file: t for t in s.assign_tags()}
    assert tagged["clip.mp4"].tag == "<Video 1>"
    assert tagged["clip.mp4"].audio_tag == "<Audio 1>"
    assert tagged["vo.wav"].tag == "<Audio 2>"


def test_standalone_audio_added_first_still_numbers_after_a_soundtrack():
    # insertion order in the UI must not change the numbering: kind order wins
    s = ReferenceSet([aud("vo.wav"), vid("clip.mp4", sound=True)])
    tagged = {t.file: t for t in s.assign_tags()}
    assert tagged["clip.mp4"].audio_tag == "<Audio 1>"
    assert tagged["vo.wav"].tag == "<Audio 2>"


def test_video_without_soundtrack_takes_no_audio_number():
    s = ReferenceSet([vid("a.mp4"), vid("b.mp4", sound=True), aud("vo.wav")])
    tagged = {t.file: t for t in s.assign_tags()}
    assert tagged["a.mp4"].audio_tag is None
    assert tagged["a.mp4"].tag == "<Video 1>"
    assert tagged["b.mp4"].tag == "<Video 2>"
    assert tagged["b.mp4"].audio_tag == "<Audio 1>"
    assert tagged["vo.wav"].tag == "<Audio 2>"


def test_videos_and_audio_are_numbered_independently():
    s = ReferenceSet([vid("a.mp4", sound=True), vid("b.mp4", sound=True)])
    tagged = {t.file: t for t in s.assign_tags()}
    assert (tagged["a.mp4"].tag, tagged["a.mp4"].audio_tag) == ("<Video 1>", "<Audio 1>")
    assert (tagged["b.mp4"].tag, tagged["b.mp4"].audio_tag) == ("<Video 2>", "<Audio 2>")


def test_full_house_numbering():
    s = ReferenceSet(
        [img("i1.jpg"), img("i2.jpg"), vid("v1.mp4", sound=True), vid("v2.mp4"), aud("a1.wav")]
    )
    got = {t.file: (t.tag, t.audio_tag) for t in s.assign_tags()}
    assert got == {
        "i1.jpg": ("<Picture 1>", None),
        "i2.jpg": ("<Picture 2>", None),
        "v1.mp4": ("<Video 1>", "<Audio 1>"),
        "v2.mp4": ("<Video 2>", None),
        "a1.wav": ("<Audio 2>", None),
    }


def test_tag_map_joins_a_videos_two_tags():
    s = ReferenceSet([vid("v1.mp4", sound=True)])
    assert s.tag_map() == {"v1.mp4": "<Video 1> <Audio 1>"}


def test_empty_set_tags_to_nothing():
    assert ReferenceSet().assign_tags() == []


# ---- caps ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,cap,maker", [("image", MAX_IMAGES, img), ("video", MAX_VIDEOS, vid), ("audio", MAX_AUDIOS, aud)]
)
def test_cap_is_hard(kind, cap, maker):
    ok = ReferenceSet([maker(f"{i}.x") for i in range(cap)])
    ok.validate()
    over = ReferenceSet([maker(f"{i}.x") for i in range(cap + 1)])
    with pytest.raises(ReferenceError) as e:
        over.validate()
    assert str(cap) in str(e.value)


def test_caps_match_the_minimax_node():
    # nodes_minimax_h3.py:183,187,191,195
    assert (MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS) == (9, 3, 3)


# ---- json round trip -------------------------------------------------------


def test_json_round_trip():
    s = ReferenceSet([img("a.jpg"), vid("v.mp4", sound=True), aud("s.wav")])
    again = ReferenceSet.from_json(s.to_json())
    assert [r.to_dict() for r in again.references] == [r.to_dict() for r in s.references]


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_widget_is_an_empty_set(raw):
    assert ReferenceSet.from_json(raw).is_empty()


def test_bare_list_is_accepted():
    s = ReferenceSet.from_json(json.dumps([{"kind": "image", "file": "a.jpg"}]))
    assert s.counts() == {"image": 1, "video": 0, "audio": 0}


def test_broken_json_is_a_reference_error():
    with pytest.raises(ReferenceError):
        ReferenceSet.from_json("{not json")


def test_unknown_kind_is_rejected():
    with pytest.raises(ReferenceError):
        ReferenceSet.from_json(json.dumps([{"kind": "mesh", "file": "a.obj"}]))


def test_missing_filename_is_rejected():
    with pytest.raises(ReferenceError):
        ReferenceSet.from_json(json.dumps([{"kind": "image", "file": "  "}]))


def test_use_soundtrack_only_sticks_to_videos():
    s = ReferenceSet.from_json(
        json.dumps([{"kind": "image", "file": "a.jpg", "use_soundtrack": True}])
    )
    assert s.references[0].use_soundtrack is False


# ---- resolution ------------------------------------------------------------


def test_missing_files_names_what_is_gone(tmp_path):
    (tmp_path / "here.jpg").write_bytes(b"x")
    s = ReferenceSet([img("here.jpg"), img("gone.jpg"), aud("also_gone.wav")])
    assert s.missing_files(str(tmp_path)) == ["gone.jpg", "also_gone.wav"]


def test_a_pack_resolves_when_every_file_exists(tmp_path):
    (tmp_path / "here.jpg").write_bytes(b"x")
    assert ReferenceSet([img("here.jpg")]).resolve(str(tmp_path)) == []


def test_a_directory_is_not_a_file(tmp_path):
    os.makedirs(tmp_path / "adir.jpg")
    assert ReferenceSet([img("adir.jpg")]).missing_files(str(tmp_path)) == ["adir.jpg"]


# ---- output sockets --------------------------------------------------------


def test_twenty_outputs_in_declaration_order():
    names = output_names()
    assert len(names) == 20
    assert names[0] == "image_1"
    assert names[8] == "image_9"
    assert names[9:12] == ("video_1", "video_2", "video_3")
    assert names[12:15] == ("video_audio_1", "video_audio_2", "video_audio_3")
    assert names[15:18] == ("audio_1", "audio_2", "audio_3")
    assert names[18] == "prompt"
    assert names[19] == "debug"


def test_the_19_media_and_prompt_slots_keep_their_indices():
    """ComfyUI stores a link by output slot INDEX. Every socket that existed before
    `debug` was appended must keep the index it had, or a saved workflow silently
    re-points its links (image_9 -> video_1, and so on)."""
    names = output_names()
    expected = (
        [f"image_{i}" for i in range(1, 10)]
        + [f"video_{i}" for i in range(1, 4)]
        + [f"video_audio_{i}" for i in range(1, 4)]
        + [f"audio_{i}" for i in range(1, 4)]
        + ["prompt"]
    )
    assert list(names[:19]) == expected
    assert slot_index("prompt") == 18


def test_output_types_line_up():
    types = output_types()
    assert len(types) == len(output_names())
    assert types[:12] == ("IMAGE",) * 12
    assert types[12:18] == ("AUDIO",) * 6
    assert types[18] == "STRING"
    assert types[19] == "STRING"


def test_empty_outputs_is_all_none_but_the_two_strings():
    out = empty_outputs()
    assert len(out) == 20
    assert out[:18] == [None] * 18
    assert out[18] == ""
    assert out[19] == ""


# ---- pack settings (model / reasoning effort) --------------------------------


def test_video_soundtrack_is_on_by_default():
    """A reference video's sound is part of the reference. Opt out, don't opt in."""
    assert Reference(kind="video", file="v.mp4").use_soundtrack is True
    # and a dict that simply omits the key inherits that default
    assert Reference.from_dict({"kind": "video", "file": "v.mp4"}).use_soundtrack is True
    # explicit False still wins
    assert Reference.from_dict(
        {"kind": "video", "file": "v.mp4", "use_soundtrack": False}
    ).use_soundtrack is False
    # non-videos never carry it
    assert Reference.from_dict({"kind": "image", "file": "i.png"}).use_soundtrack is False


def test_a_default_video_takes_an_audio_tag():
    s = ReferenceSet([Reference(kind="video", file="v.mp4"), aud("vo.wav")])
    tagged = {t.file: t for t in s.assign_tags()}
    assert tagged["v.mp4"].audio_tag == "<Audio 1>"
    assert tagged["vo.wav"].tag == "<Audio 2>"


# ---- crop / trim ------------------------------------------------------------


def test_crop_and_trim_round_trip():
    r = Reference.from_dict(
        {"kind": "video", "file": "v.mp4", "crop": [0.1, 0.2, 0.5, 0.5], "trim": [2.0, 6.5]}
    )
    assert r.crop == [0.1, 0.2, 0.5, 0.5]
    assert r.trim == [2.0, 6.5]
    d = r.to_dict()
    assert d["crop"] == [0.1, 0.2, 0.5, 0.5]
    assert d["trim"] == [2.0, 6.5]


def test_crop_and_trim_are_omitted_when_unset():
    """Pre-edit references_json values and v1 pack files must keep loading AND
    re-serialising unchanged, so an unedited reference emits no crop/trim keys."""
    for r in (img("a.jpg"), vid("v.mp4"), aud("s.wav")):
        d = r.to_dict()
        assert "crop" not in d
        assert "trim" not in d
    old = Reference.from_dict({"kind": "video", "file": "v.mp4"})
    assert old.crop is None and old.trim is None


@pytest.mark.parametrize("bad", [
    "half",                     # not a list
    [0.1, 0.2, 0.5],            # wrong arity
    [0.1, 0.2, 0.5, "h"],       # not numbers
    [-0.1, 0.0, 0.5, 0.5],      # negative origin
    [0.0, 0.0, 0.0, 0.5],       # zero area
    [0.0, 0.0, -0.5, 0.5],      # negative area
    [0.8, 0.0, 0.5, 0.5],       # x + w past the frame
    [0.0, 0.8, 0.5, 0.5],       # y + h past the frame
])
def test_bad_crops_are_rejected(bad):
    with pytest.raises(ReferenceError):
        Reference.from_dict({"kind": "image", "file": "a.jpg", "crop": bad})


@pytest.mark.parametrize("bad", [
    "later", [1.0], [1.0, 2.0, 3.0], ["a", "b"],
    [-1.0, 2.0],  # negative start
    [3.0, 3.0],   # end == start
    [4.0, 2.0],   # end < start
])
def test_bad_trims_are_rejected(bad):
    with pytest.raises(ReferenceError):
        Reference.from_dict({"kind": "audio", "file": "s.wav", "trim": bad})


def test_crop_only_sticks_to_images_and_videos():
    # mirrors use_soundtrack: an inapplicable field is dropped, not fatal
    box = [0, 0, 0.5, 0.5]
    assert Reference.from_dict({"kind": "audio", "file": "s.wav", "crop": box}).crop is None
    assert Reference.from_dict({"kind": "image", "file": "a.jpg", "crop": box}).crop == box
    assert Reference.from_dict({"kind": "video", "file": "v.mp4", "crop": box}).crop == box


def test_trim_only_sticks_to_videos_and_audio():
    assert Reference.from_dict({"kind": "image", "file": "a.jpg", "trim": [1.0, 2.0]}).trim is None
    assert Reference.from_dict({"kind": "video", "file": "v.mp4", "trim": [1.0, 2.0]}).trim == [1.0, 2.0]
    assert Reference.from_dict({"kind": "audio", "file": "s.wav", "trim": [1.0, 2.0]}).trim == [1.0, 2.0]


def test_crop_and_trim_survive_a_references_json_round_trip():
    """The pack store is gone, but this is the state the node actually restores from."""
    original = ReferenceSet([
        Reference(kind="video", file="v.mp4", crop=[0.0, 0.0, 0.5, 0.5], trim=[1.0, 3.0]),
    ])

    back = ReferenceSet.from_json(original.to_json()).references[0]

    assert back.crop == [0.0, 0.0, 0.5, 0.5]
    assert back.trim == [1.0, 3.0]


# ---- subject grouping ---------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ([1], [1]), ([3, 1], [1, 3]), ([2, 2], [2]), (5, [5]), ([], []),
    ([1.0], [1]),
])
def test_subjects_normalise_to_a_sorted_unique_list(value, expected):
    assert validate_subjects(value) == expected


@pytest.mark.parametrize("bad", [0, 10, -1, 1.5, "1", [True], [None], {"a": 1}])
def test_subjects_refuse_anything_outside_one_to_nine(bad):
    with pytest.raises(ReferenceError):
        validate_subjects(bad)


def test_subject_numbers_are_never_compacted():
    """They are the user's. Picking 1 and 5 must produce <Subject 5>, not a helpfully
    renumbered <Subject 2> - a node that quietly renamed what someone clicked would be
    worse than a gap in the numbering."""
    s = ReferenceSet([
        Reference(kind="image", file="a.png", subjects=[1]),
        Reference(kind="image", file="b.png", subjects=[5]),
    ])
    assert sorted(s.subject_groups()) == [1, 5]


def test_an_ungrouped_set_serialises_exactly_as_it_used_to():
    assert Reference(kind="image", file="a.png").to_dict() == {"kind": "image", "file": "a.png"}
    assert ReferenceSet([Reference(kind="image", file="a.png")]).subject_groups() == {}


def test_one_reference_can_define_several_subjects():
    """A photo of a woman in a room is both the character and the location, and the
    packaged prompt has a line for each."""
    s = ReferenceSet([Reference(kind="image", file="a.png", subjects=[1, 2])])
    assert s.subject_groups() == {1: ["<Picture 1>"], 2: ["<Picture 1>"]}


def test_several_references_can_define_one_subject():
    s = ReferenceSet([
        Reference(kind="image", file="a.png", subjects=[1]),
        Reference(kind="image", file="b.png", subjects=[1]),
        Reference(kind="image", file="c.png"),
    ])
    assert s.subject_groups() == {1: ["<Picture 1>", "<Picture 2>"]}


def test_a_video_contributes_its_soundtrack_tag_to_the_same_subject():
    """A character's clip and that character's voice are the same subject, and the
    soundtrack has its own <Audio N> - so grouping the video must carry both."""
    s = ReferenceSet([Reference(kind="video", file="v.mp4", use_soundtrack=True, subjects=[2])])
    assert s.subject_groups() == {2: ["<Video 1>", "<Audio 1>"]}


def test_a_muted_video_contributes_only_its_video_tag():
    s = ReferenceSet([Reference(kind="video", file="v.mp4", use_soundtrack=False, subjects=[2])])
    assert s.subject_groups() == {2: ["<Video 1>"]}


def test_subjects_are_allowed_on_audio_too():
    """The packaged prompt: "<Audio 1> is the voice-timbre reference for <Subject 1>"."""
    s = ReferenceSet([Reference(kind="audio", file="a.wav", subjects=[1])])
    assert s.subject_groups() == {1: ["<Audio 1>"]}


def test_subject_groups_round_trip_through_json():
    s = ReferenceSet([Reference(kind="image", file="a.png", subjects=[2, 1])])
    assert ReferenceSet.from_json(s.to_json()).subject_groups() == {1: ["<Picture 1>"],
                                                                   2: ["<Picture 1>"]}
# ---- orientation: rotate and flip -------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (90, 90.0), (180, 180.0), (270, 270.0), (0, 0.0),
    (360, 0.0),      # a full turn is no turn
    (-90, 270.0),    # anticlockwise arrives as its clockwise equivalent
    (450, 90.0),     # and so does more than a full turn
    (12.5, 12.5),    # free angles are accepted; the quarter turns are just lossless
])
def test_rotate_normalises_into_one_turn(value, expected):
    assert validate_rotate(value) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["90", None, True, float("nan"), float("inf"), [90]])
def test_rotate_refuses_anything_that_is_not_a_finite_number(bad):
    with pytest.raises(ReferenceError):
        validate_rotate(bad)


@pytest.mark.parametrize("value,expected", [
    ("h", "h"), ("v", "v"), ("hv", "hv"),
    ("HV", "hv"), (" h ", "h"),
    ("vh", "hv"),   # the axes commute, so this is the same thing spelled backwards
])
def test_flip_normalises(value, expected):
    assert validate_flip(value) == expected


@pytest.mark.parametrize("bad", ["x", "", None, 1, "hh"])
def test_flip_refuses_anything_else(bad):
    with pytest.raises(ReferenceError):
        validate_flip(bad)


def test_an_unoriented_reference_serialises_exactly_as_it_used_to():
    """The compatibility guarantee. A references_json value or a v1 pack file written
    before rotation existed must round-trip byte-identical, which is what lets
    PACK_VERSION stay at 1."""
    d = Reference(kind="image", file="a.png").to_dict()
    assert d == {"kind": "image", "file": "a.png"}
    assert "rotate" not in d and "flip" not in d and "rotate_expand" not in d


def test_rotate_expand_only_rides_along_with_a_rotation():
    """On its own it means nothing, and writing it would change the serialisation of
    every unrotated reference for no reason."""
    assert "rotate_expand" not in Reference(kind="image", file="a.png",
                                            rotate_expand=False).to_dict()
    assert Reference(kind="image", file="a.png", rotate=90,
                     rotate_expand=False).to_dict()["rotate_expand"] is False


def test_orientation_round_trips():
    ref = Reference.from_dict({"kind": "video", "file": "v.mp4", "rotate": 90,
                               "flip": "h", "rotate_expand": False})
    assert (ref.rotate, ref.flip, ref.rotate_expand) == (90.0, "h", False)
    assert Reference.from_dict(ref.to_dict()).to_dict() == ref.to_dict()


def test_orientation_is_dropped_for_audio_rather_than_being_fatal():
    """Same treatment crop gets on an audio reference: an inapplicable field is ignored,
    not an error, so a hand-edited or older config still loads."""
    ref = Reference.from_dict({"kind": "audio", "file": "a.wav", "rotate": 90, "flip": "h"})
    assert ref.rotate is None and ref.flip is None


def test_oriented_reports_whether_anything_will_move():
    assert not Reference(kind="image", file="a.png").oriented
    assert Reference(kind="image", file="a.png", rotate=90).oriented
    assert Reference(kind="image", file="a.png", flip="h").oriented
    # A full turn normalises to 0, so it is genuinely not oriented.
    assert not Reference(kind="image", file="a.png",
                         rotate=validate_rotate(360)).oriented

# ---- non-finite numbers --------------------------------------------------------------


@pytest.mark.parametrize("crop", [
    [float("nan"), 0.0, 0.5, 0.5],
    [0.0, float("nan"), 0.5, 0.5],
    [0.0, 0.0, float("nan"), 0.5],
    [0.0, 0.0, 0.5, float("nan")],
    [float("inf"), 0.0, 0.5, 0.5],
    [0.0, 0.0, float("inf"), 0.5],
    [float("-inf"), 0.0, 0.5, 0.5],
])
def test_a_non_finite_crop_is_refused(crop):
    """NaN fails EVERY comparison, so a range check written as `x < 0 or x + w > 1` waves
    it straight through - and the value then reaches the pixel pipeline, where
    int(nan * width) raises from somewhere with no useful context. A validator's whole job
    is to turn that into one clear sentence at the boundary."""
    with pytest.raises(ReferenceError):
        validate_crop(crop)


@pytest.mark.parametrize("trim", [
    [float("nan"), 1.0],
    [0.0, float("nan")],
    [0.0, float("inf")],
    [float("inf"), float("inf")],
])
def test_a_non_finite_trim_is_refused(trim):
    """An infinite end asks for a window that never closes; NaN passes `end <= start`
    because that comparison is False for NaN too."""
    with pytest.raises(ReferenceError):
        validate_trim(trim)


def test_ordinary_values_still_pass():
    """The guard must not have become a blanket refusal of anything unusual."""
    assert validate_crop([0.0, 0.0, 1.0, 1.0]) == [0.0, 0.0, 1.0, 1.0]
    assert validate_crop([0, 0, 1, 1]) == [0.0, 0.0, 1.0, 1.0]
    assert validate_crop([0.25, 0.125, 0.5, 0.75]) == [0.25, 0.125, 0.5, 0.75]
    assert validate_trim([0.0, 0.001]) == [0.0, 0.001]
    assert validate_trim([12, 3600]) == [12.0, 3600.0]


# ---- the per-reference downscale cap -------------------------------------------------


@pytest.mark.parametrize("value", [512, 1, 2048, 4096])
def test_a_positive_whole_max_edge_is_accepted(value):
    assert validate_max_edge(value) == value


@pytest.mark.parametrize("value", [0, -1, -512, 512.5, "512", None, True, float("nan"),
                                   float("inf")])
def test_a_bad_max_edge_is_refused(value):
    with pytest.raises(ReferenceError):
        validate_max_edge(value)


def test_max_edge_round_trips_on_the_visual_kinds():
    for kind, file in (("image", "a.png"), ("video", "v.mp4")):
        r = Reference.from_dict({"kind": kind, "file": file, "max_edge": 512})
        assert r.max_edge == 512
        assert r.to_dict()["max_edge"] == 512


def test_max_edge_is_dropped_on_audio():
    r = Reference.from_dict({"kind": "audio", "file": "a.wav", "max_edge": 512})
    assert r.max_edge is None
    assert "max_edge" not in r.to_dict()


def test_an_unset_max_edge_serialises_nothing():
    r = Reference.from_dict({"kind": "video", "file": "v.mp4"})
    assert r.max_edge is None
    assert "max_edge" not in r.to_dict()
