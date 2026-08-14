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
    Pack,
    Reference,
    ReferenceError,
    ReferenceSet,
    delete_pack,
    empty_outputs,
    list_pack_names,
    load_pack,
    output_names,
    output_types,
    save_pack,
    slot_index,
    validate_pack_name,
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


# ---- packs -----------------------------------------------------------------


def test_pack_save_load_delete(tmp_path):
    p = Pack(name="bryan dorm", references=ReferenceSet([img("a.jpg")]), direction="handheld")
    save_pack(str(tmp_path), p)
    assert list_pack_names(str(tmp_path)) == ["bryan dorm"]
    back = load_pack(str(tmp_path), "bryan dorm")
    assert back.direction == "handheld"
    assert back.references.counts()["image"] == 1
    assert delete_pack(str(tmp_path), "bryan dorm") is True
    assert list_pack_names(str(tmp_path)) == []
    assert delete_pack(str(tmp_path), "bryan dorm") is False


def test_pack_dir_absent_lists_nothing(tmp_path):
    assert list_pack_names(str(tmp_path)) == []


def test_pack_survives_a_video_with_sound(tmp_path):
    p = Pack(name="p", references=ReferenceSet([vid("v.mp4", sound=True)]))
    save_pack(str(tmp_path), p)
    assert load_pack(str(tmp_path), "p").references.references[0].use_soundtrack is True


def test_future_pack_version_is_refused():
    with pytest.raises(ReferenceError):
        Pack.from_json(json.dumps({"version": 99, "name": "x", "references": []}))


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", ".hidden", "", "   ", "x" * 65])
def test_pack_names_that_could_escape_are_refused(bad):
    with pytest.raises(ReferenceError):
        validate_pack_name(bad)


@pytest.mark.parametrize("good", ["bryan_dorm", "Bryan Dorm 2", "a.b-c"])
def test_reasonable_pack_names_pass(good):
    assert validate_pack_name(good) == good


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


def test_pack_round_trips_model_and_reasoning_effort():
    p = Pack(
        name="cfg",
        references=ReferenceSet([Reference(kind="image", file="a.png")]),
        direction="a steer",
        model="google/gemini-3-flash-preview",
        reasoning_effort="high",
    )
    back = Pack.from_json(p.to_json())
    assert back.model == "google/gemini-3-flash-preview"
    assert back.reasoning_effort == "high"
    assert back.direction == "a steer"


def test_a_pack_saved_before_these_fields_existed_still_loads():
    """Packs already on disk were written without model/reasoning_effort. Version stays 1,
    so they must keep loading rather than being rejected by the version gate."""
    old = json.dumps({
        "version": 1,
        "name": "Bitches",
        "direction": "a standoff",
        "references": [{"kind": "image", "file": "a.png"}],
    })
    pack = Pack.from_json(old)
    assert pack.model == ""
    assert pack.reasoning_effort == ""
    assert pack.direction == "a standoff"
    assert len(pack.references.references) == 1


def test_saved_settings_survive_a_disk_round_trip(tmp_path):
    p = Pack(
        name="cfg", references=ReferenceSet([]), direction="d",
        model="m/1", reasoning_effort="low",
    )
    save_pack(str(tmp_path), p)
    back = load_pack(str(tmp_path), "cfg")
    assert (back.model, back.reasoning_effort) == ("m/1", "low")


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
