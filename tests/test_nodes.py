"""Tests for minimax_refpack.nodes.MiniMaxH3ReferencePack.

folder_paths is not importable outside a ComfyUI process (verified: bare
`import folder_paths` raises ModuleNotFoundError in this venv), so `fake_folder_paths`
injects a stub module into sys.modules pointed at a tmp_path. media.load_* and
prompt.write_prompt/PromptError are monkeypatched per the task brief - real decoding is
media.py's job and is covered by test_media.py.
"""

import json
import sys
import types

import pytest

from minimax_refpack import nodes, refs


@pytest.fixture
def fake_folder_paths(tmp_path, monkeypatch):
    module = types.ModuleType("folder_paths")
    module.get_input_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", module)
    return tmp_path


def _touch(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"x")


def _stub_prompt(monkeypatch, text="a prompt"):
    monkeypatch.setattr(nodes.prompt, "write_prompt", lambda *a, **k: text, raising=False)


# ---- slot placement -----------------------------------------------------------


def test_slot_placement_for_a_mixed_set(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg", "i2.jpg", "v1.mp4", "a1.wav")

    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    monkeypatch.setattr(nodes.media, "load_video", lambda path, crop=None, trim=None, flip=None, rotate=None, rotate_expand=True, max_edge=0: (f"VIDEO:{path}", f"AUDIO:{path}"))
    monkeypatch.setattr(nodes.media, "load_audio", lambda path, trim=None: f"AUD:{path}")
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [
        {"kind": "image", "file": "i1.jpg"},
        {"kind": "image", "file": "i2.jpg"},
        {"kind": "video", "file": "v1.mp4", "use_soundtrack": True},
        {"kind": "audio", "file": "a1.wav"},
    ]})

    out = nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )
    by_name = dict(zip(refs.output_names(), out))

    assert by_name["image_1"] == f"IMG:{tmp_path / 'i1.jpg'}"
    assert by_name["image_2"] == f"IMG:{tmp_path / 'i2.jpg'}"
    assert by_name["video_1"] == f"VIDEO:{tmp_path / 'v1.mp4'}"
    assert by_name["video_audio_1"] == f"AUDIO:{tmp_path / 'v1.mp4'}"
    assert by_name["audio_1"] == f"AUD:{tmp_path / 'a1.wav'}"
    assert by_name["prompt"] == "a prompt"

    filled = {"image_1", "image_2", "video_1", "video_audio_1", "audio_1", "prompt", "debug"}
    for name in refs.output_names():
        if name not in filled:
            assert by_name[name] is None, f"{name} should be empty"


def test_video_without_use_soundtrack_leaves_video_audio_slot_empty(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "v1.mp4")
    monkeypatch.setattr(nodes.media, "load_video", lambda path, crop=None, trim=None, flip=None, rotate=None, rotate_expand=True, max_edge=0: (f"VIDEO:{path}", f"AUDIO:{path}"))
    _stub_prompt(monkeypatch)

    # use_soundtrack now defaults to True, so the OFF case has to be explicit
    references_json = json.dumps(
        {"references": [{"kind": "video", "file": "v1.mp4", "use_soundtrack": False}]}
    )
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )
    by_name = dict(zip(refs.output_names(), out))

    assert by_name["video_1"] == f"VIDEO:{tmp_path / 'v1.mp4'}"
    assert by_name["video_audio_1"] is None


def test_system_prompt_widget_is_passed_through_to_write_prompt(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    captured = {}

    def fake_write_prompt(**kwargs):
        captured.update(kwargs)
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json,
        system_prompt="a custom system prompt",
    )

    assert captured["system_prompt"] == "a custom system prompt"


def test_empty_set_with_a_direction_still_writes_the_prompt(fake_folder_paths, monkeypatch):
    """Zero references is not zero input: with a direction, the writer still runs and
    the prompt socket carries its answer."""
    captured = {}

    def fake_write_prompt(**kwargs):
        captured.update(kwargs)
        return "written from the direction alone"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    out = nodes.MiniMaxH3ReferencePack().build(
        direction="a neon alley chase", openrouter_api_key="", model="m", references_json=""
    )

    assert out[refs.slot_index("prompt")] == "written from the direction alone"
    assert captured["direction"] == "a neon alley chase"
    assert captured["references"].is_empty()
    # every media socket stays empty - there is nothing to fan out
    assert out[:18] == tuple(refs.empty_outputs())[:18]
    # the debug header says out loud what happened
    assert "written from the direction alone" in out[refs.slot_index("debug")].lower()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_empty_set_with_a_blank_direction_never_calls_the_prompt_writer(
    fake_folder_paths, monkeypatch, blank
):
    """The one remaining skip: no references AND nothing typed = nothing to write from."""
    called = []
    monkeypatch.setattr(nodes.prompt, "write_prompt", lambda *a, **k: called.append(1), raising=False)

    out = nodes.MiniMaxH3ReferencePack().build(
        direction=blank, openrouter_api_key="", model="m", references_json=""
    )

    assert called == []
    # Every socket but `debug` is untouched; `debug` still reports why nothing happened.
    assert out[:19] == tuple(refs.empty_outputs())[:19]
    assert "nothing to write from" in out[refs.slot_index("debug")]


# ---- crop / trim wire-through ---------------------------------------------------


def test_crop_and_trim_reach_the_loaders(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg", "v1.mp4", "a1.wav")
    calls = {}

    def li(path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True):
        calls["image"] = crop
        return "IMG"

    def lv(path, crop=None, trim=None, flip=None, rotate=None, rotate_expand=True, max_edge=0):
        calls["video"] = (crop, trim)
        return ("V", None)

    def la(path, trim=None):
        calls["audio"] = trim
        return "A"

    monkeypatch.setattr(nodes.media, "load_image", li)
    monkeypatch.setattr(nodes.media, "load_video", lv)
    monkeypatch.setattr(nodes.media, "load_audio", la)
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [
        {"kind": "image", "file": "i1.jpg", "crop": [0.1, 0.1, 0.5, 0.5]},
        {"kind": "video", "file": "v1.mp4", "use_soundtrack": False,
         "crop": [0, 0, 0.5, 1.0], "trim": [2.0, 6.5]},
        {"kind": "audio", "file": "a1.wav", "trim": [1.0, 3.0]},
    ]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )

    assert calls["image"] == [0.1, 0.1, 0.5, 0.5]
    assert calls["video"] == ([0, 0, 0.5, 1.0], [2.0, 6.5])
    assert calls["audio"] == [1.0, 3.0]


def test_unedited_references_pass_no_crop_or_trim(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    calls = {}

    def li(path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True):
        calls["image"] = crop
        return "IMG"

    monkeypatch.setattr(nodes.media, "load_image", li)
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )
    assert calls["image"] is None


def test_is_changed_key_moves_when_only_an_edit_changes(fake_folder_paths):
    """crop/trim ride inside references_json, which IS_CHANGED already folds in -
    confirmed here rather than assumed, per the ledger entry for this feature."""
    _touch(fake_folder_paths, "v1.mp4")
    kwargs = dict(direction="d", openrouter_api_key="", model="m")

    def key(ref):
        return nodes.MiniMaxH3ReferencePack.IS_CHANGED(
            references_json=json.dumps({"references": [ref]}), **kwargs
        )

    base = {"kind": "video", "file": "v1.mp4"}
    cropped = {**base, "crop": [0.1, 0.1, 0.5, 0.5]}
    trimmed = {**base, "trim": [2.0, 6.5]}

    assert len({key(base), key(cropped), key(trimmed)}) == 3


# ---- use_openrouter opt-out -------------------------------------------------------


def _forbid_http(monkeypatch):
    """Any HTTP attempt from the prompt writer fails the test loudly."""
    from minimax_refpack import prompt as prompt_mod

    def boom(*a, **k):
        raise AssertionError("HTTP call attempted with use_openrouter=False")

    monkeypatch.setattr(prompt_mod.requests, "post", boom)
    monkeypatch.setattr(prompt_mod.requests, "get", boom)


def test_opt_out_returns_direction_verbatim_and_calls_nothing(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    _forbid_http(monkeypatch)

    def never(*a, **k):
        raise AssertionError("write_prompt called with use_openrouter=False")

    monkeypatch.setattr(nodes.prompt, "write_prompt", never, raising=False)

    direction = "  keep me EXACTLY,\n as typed \t"
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction=direction, openrouter_api_key="", model="m",
        references_json=references_json, use_openrouter=False,
    )
    by_name = dict(zip(refs.output_names(), out))

    assert by_name["prompt"] == direction
    # references still fan out - only the prompt writing is skipped
    assert by_name["image_1"] == f"IMG:{tmp_path / 'i1.jpg'}"


def test_opt_out_passes_direction_even_with_an_empty_reference_set(fake_folder_paths, monkeypatch):
    """The passthrough must not ride on the empty-set skip: with references_json empty
    the opted-out prompt output is still the direction, not the empty string."""
    _forbid_http(monkeypatch)
    monkeypatch.setattr(
        nodes.prompt, "write_prompt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")), raising=False,
    )

    out = nodes.MiniMaxH3ReferencePack().build(
        direction="just my words", openrouter_api_key="", model="m",
        references_json="", use_openrouter=False,
    )
    by_name = dict(zip(refs.output_names(), out))
    assert by_name["prompt"] == "just my words"


def test_opt_in_still_calls_the_prompt_writer(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    _stub_prompt(monkeypatch, text="written by the VLM")

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="steer", openrouter_api_key="", model="m",
        references_json=references_json, use_openrouter=True,
    )
    by_name = dict(zip(refs.output_names(), out))
    assert by_name["prompt"] == "written by the VLM"


# ---- width / height / length reach the prompt writer -------------------------------


def test_build_passes_width_height_length_to_write_prompt(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    captured = {}

    def fake_write_prompt(**kwargs):
        captured.update(kwargs)
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json,
        width=1920, height=1080, length_seconds=12.25,
    )

    assert captured["width"] == 1920
    assert captured["height"] == 1080
    assert captured["length_seconds"] == 12.25


def test_build_defaults_width_height_length_to_unspecified_when_absent(fake_folder_paths, monkeypatch):
    """An old workflow without the new widgets calls build() without them."""
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    captured = {}

    def fake_write_prompt(**kwargs):
        captured.update(kwargs)
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )

    assert captured["width"] == 0
    assert captured["height"] == 0
    assert captured["length_seconds"] == 0


# ---- over-cap rejection ---------------------------------------------------------


def test_over_cap_rejection(fake_folder_paths):
    tmp_path = fake_folder_paths
    filenames = [f"{i}.jpg" for i in range(refs.MAX_IMAGES + 1)]
    _touch(tmp_path, *filenames)
    references_json = json.dumps({"references": [{"kind": "image", "file": f} for f in filenames]})

    with pytest.raises(refs.ReferenceError):
        nodes.MiniMaxH3ReferencePack().build(
            direction="", openrouter_api_key="", model="m", references_json=references_json
        )


# ---- missing-file error ----------------------------------------------------------


def test_missing_file_error_names_every_missing_file(fake_folder_paths):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "here.jpg")
    references_json = json.dumps({"references": [
        {"kind": "image", "file": "here.jpg"},
        {"kind": "image", "file": "gone1.jpg"},
        {"kind": "audio", "file": "gone2.wav"},
    ]})

    with pytest.raises(ValueError) as exc:
        nodes.MiniMaxH3ReferencePack().build(
            direction="", openrouter_api_key="", model="m", references_json=references_json
        )

    assert "gone1.jpg" in str(exc.value)
    assert "gone2.wav" in str(exc.value)
    assert "here.jpg" not in str(exc.value)


# ---- IS_CHANGED -------------------------------------------------------------------


def test_is_changed_key_moves_when_a_files_bytes_change(fake_folder_paths):
    path = fake_folder_paths / "i1.jpg"
    path.write_bytes(b"aaa")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m", references_json=references_json)

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(**kwargs)
    path.write_bytes(b"a completely different set of bytes")
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(**kwargs)

    assert key1 != key2


def test_is_changed_key_moves_for_a_same_length_re_upload(fake_folder_paths):
    """The case the signature exists for, and the one nothing was checking.

    The browser re-uploads with overwrite=true under the SAME filename, so
    references_json can be byte-identical across two different uploads. The neighbouring
    test writes replacement bytes of a different length, so its key moves on st_size alone
    - drop st_mtime_ns from the signature entirely and the whole suite still passes, which
    makes a regression to size-only hashing invisible.

    Same length, different bytes: size cannot see it, so only mtime can.
    """
    import os

    path = fake_folder_paths / "i1.jpg"
    path.write_bytes(b"aaaa")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m",
                  references_json=references_json)

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(**kwargs)

    path.write_bytes(b"bbbb")
    assert path.stat().st_size == 4, "the test's premise: the size did not change"
    # Forced rather than assumed: two writes in the same tick can land on one timestamp,
    # and a test that passes only when the clock cooperates is not a test.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(**kwargs)

    assert key1 != key2, (
        "a re-upload the same length as the file it replaced must still move the key - "
        "otherwise ComfyUI cache-hits and emits the PREVIOUS reference's result"
    )


def test_is_changed_key_moves_when_direction_changes(fake_folder_paths):
    (fake_folder_paths / "i1.jpg").write_bytes(b"aaa")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(
        direction="a", openrouter_api_key="", model="m", references_json=references_json
    )
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(
        direction="b", openrouter_api_key="", model="m", references_json=references_json
    )

    assert key1 != key2


def test_is_changed_key_moves_when_references_json_changes(fake_folder_paths):
    _touch(fake_folder_paths, "i1.jpg", "i2.jpg")
    kwargs = dict(direction="d", openrouter_api_key="", model="m")

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(
        references_json=json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]}), **kwargs
    )
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(
        references_json=json.dumps({"references": [{"kind": "image", "file": "i2.jpg"}]}), **kwargs
    )

    assert key1 != key2


def test_is_changed_key_moves_when_only_system_prompt_changes(fake_folder_paths):
    _touch(fake_folder_paths, "i1.jpg")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m", references_json=references_json)

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(system_prompt="a custom system prompt", **kwargs)
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(system_prompt="a different system prompt", **kwargs)

    assert key1 != key2


def test_is_changed_key_moves_when_use_openrouter_toggles(fake_folder_paths):
    _touch(fake_folder_paths, "i1.jpg")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m", references_json=references_json)

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(use_openrouter=True, **kwargs)
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(use_openrouter=False, **kwargs)

    assert key1 != key2


def test_is_changed_key_moves_when_target_format_changes(fake_folder_paths):
    _touch(fake_folder_paths, "i1.jpg")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m", references_json=references_json)

    key1 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(width=1280, height=720, length_seconds=8.0, **kwargs)
    key2 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(width=1280, height=720, length_seconds=12.0, **kwargs)
    key3 = nodes.MiniMaxH3ReferencePack.IS_CHANGED(width=720, height=1280, length_seconds=8.0, **kwargs)

    assert len({key1, key2, key3}) == 3


# ---- the api key never leaks into a raised message ---------------------------------


def test_api_key_never_appears_in_a_raised_message(fake_folder_paths, monkeypatch):
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    secret = "sk-super-secret-key"

    class FakePromptError(Exception):
        pass

    monkeypatch.setattr(nodes.prompt, "PromptError", FakePromptError, raising=False)

    def boom(*args, **kwargs):
        raise FakePromptError(f"upstream call failed for key {secret}")

    monkeypatch.setattr(nodes.prompt, "write_prompt", boom, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})

    with pytest.raises(ValueError) as exc:
        nodes.MiniMaxH3ReferencePack().build(
            direction="", openrouter_api_key=secret, model="m", references_json=references_json
        )

    assert secret not in str(exc.value)


# ---- the debug socket ---------------------------------------------------------------


def test_debug_carries_the_rendered_payload_and_the_settings(fake_folder_paths, monkeypatch):
    """The debug socket exists to answer "what exactly did the model get?" - so it must
    carry the real payload, not a re-derivation of it."""
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    def fake_write_prompt(**kwargs):
        # stand in for what write_prompt really appends: the rendered payload
        kwargs["debug"].append("model: m\n\n===== SYSTEM MESSAGE =====\nrendered payload here")
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="a steer", openrouter_api_key="", model="m", references_json=references_json,
        width=1344, height=768, length_seconds=12.25,
    )
    debug = out[refs.slot_index("debug")]

    assert "prompt_provider: openrouter" in debug
    assert "width: 1344" in debug and "height: 768" in debug and "length_seconds: 12.25" in debug
    assert "<Picture 1> i1.jpg" in debug
    assert "packaged default" in debug
    assert "--- payload sent to OpenRouter ---" in debug
    assert "rendered payload here" in debug
    # the prompt socket is unaffected
    assert out[refs.slot_index("prompt")] == "a prompt"


def test_a_legacy_use_openrouter_false_still_passes_through(fake_folder_paths, monkeypatch):
    """The 0.3.1 opt-out, driven the way an old API client still drives it.

    `use_openrouter=False` was replaced by `prompt_provider="none"`, but a stored API
    prompt or a script keeps sending the boolean. The server must honour it rather than
    quietly running a paid call the user switched off.
    """
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    def boom(*a, **k):
        raise AssertionError("write_prompt must not run when auto-prompting is off")

    monkeypatch.setattr(nodes.prompt, "write_prompt", boom, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="passed straight through", openrouter_api_key="", model="m",
        references_json=references_json, use_openrouter=False,
    )
    debug = out[refs.slot_index("debug")]

    assert "prompt_provider: none" in debug
    assert "Auto-prompting is OFF" in debug
    assert "passed straight through" in debug
    assert "--- payload sent to" not in debug
    assert out[refs.slot_index("prompt")] == "passed straight through"


def test_debug_reports_unspecified_dimensions(fake_folder_paths, monkeypatch):
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    monkeypatch.setattr(nodes.prompt, "write_prompt", lambda **k: "p", raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json,
        width=0, height=0, length_seconds=0.0,
    )
    assert "(unspecified)" in out[refs.slot_index("debug")]


def test_api_key_never_appears_in_the_debug_socket(fake_folder_paths, monkeypatch):
    """`debug` is the socket most likely to end up in a screenshot."""
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    secret = "sk-or-v1-never-show-this"

    def leaky_write_prompt(**kwargs):
        kwargs["debug"].append(f"Authorization: Bearer {secret}")
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", leaky_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key=secret, model="m", references_json=references_json,
    )
    assert secret not in out[refs.slot_index("debug")]


def test_debug_header_shows_what_auto_resolved_to(fake_folder_paths, monkeypatch):
    """With job_type=auto the header alone would only say "auto". Which register
    actually ran decides the entire output format, so it belongs at the top."""
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")

    def fake_write_prompt(**kwargs):
        kwargs["debug"].append(
            "job_type: auto -> replacement (system prompt: replacement.md)\nmodel: m"
        )
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake_write_prompt, raising=False)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="d", openrouter_api_key="", model="m",
        references_json=references_json, job_type="auto",
    )
    header = out[refs.slot_index("debug")].split("--- payload")[0]
    assert "job_type: auto -> replacement" in header
    assert "\njob_type: auto\n" not in header, "the unresolved line should be replaced"


def test_debug_header_keeps_the_raw_job_type_when_no_call_is_made(fake_folder_paths, monkeypatch):
    """Nothing resolved, so nothing to hoist - the header still reports the setting."""
    _touch(fake_folder_paths, "i1.jpg")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: f"IMG:{path}")
    monkeypatch.setattr(
        nodes.prompt, "write_prompt",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not run")), raising=False,
    )
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    out = nodes.MiniMaxH3ReferencePack().build(
        direction="d", openrouter_api_key="", model="m",
        references_json=references_json, job_type="auto", use_openrouter=False,
    )
    assert "job_type: auto" in out[refs.slot_index("debug")]


# ---- the reference-image size cap -------------------------------------------------


def test_max_reference_edge_reaches_the_image_loader(fake_folder_paths, monkeypatch):
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    calls = {}

    def li(path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True):
        calls["max_edge"] = max_edge
        return "IMG"

    monkeypatch.setattr(nodes.media, "load_image", li)
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m",
        references_json=references_json, max_reference_edge=1024,
    )

    assert calls["max_edge"] == 1024


def test_the_cap_defaults_to_2048_when_the_widget_is_absent(fake_folder_paths, monkeypatch):
    """A workflow saved before this input existed restores without it. ComfyUI stops
    filling widgets_values when the saved array runs out, so build() is called with no
    max_reference_edge at all - and must still cap, exactly like the widget's default."""
    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg")
    calls = {}

    def li(path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True):
        calls["max_edge"] = max_edge
        return "IMG"

    monkeypatch.setattr(nodes.media, "load_image", li)
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m", references_json=references_json
    )

    assert calls["max_edge"] == 2048
    declared = nodes.MiniMaxH3ReferencePack.INPUT_TYPES()["optional"]["max_reference_edge"]
    assert declared[1]["default"] == 2048


def test_the_cap_is_declared_last_so_old_workflows_restore_unchanged():
    """Widgets restore POSITIONALLY - a new input anywhere but the end re-points every
    saved value in a workflow written by an older build.

    Pinning the whole order, not just the last name: that is what the rule actually says,
    and it catches an insertion in the middle, which is the failure that matters. The one
    deliberate exception is prompt_provider, which REPLACED use_openrouter in its own slot
    rather than being appended - a type change in place, covered by the migration tests.
    """
    spec = nodes.MiniMaxH3ReferencePack.INPUT_TYPES()
    assert list(spec["required"]) + list(spec["optional"]) == [
        "direction", "references_json", "system_prompt", "prompt_provider",
        "openrouter_api_key", "openrouter_model", "reasoning_effort", "api_base",
        "local_model_slug", "job_type", "width", "height", "length_seconds",
        "max_reference_edge",
        "system_prompt_replacement",
        "local_ttl",
        "local_server",
        "local_send_reasoning",
        "local_extra_body",
    ]


def test_the_global_cap_never_reaches_the_video_loader(fake_folder_paths, monkeypatch):
    """The GLOBAL max_reference_edge is images-only: core already resizes reference videos
    itself (CU/comfy_extras/nodes_minimax_h3.py adapt_canvas), so handing the same cap to
    the video loader would fight it. A video with no per-reference setting gets no cap."""
    tmp_path = fake_folder_paths
    _touch(tmp_path, "v1.mp4")
    calls = {}

    def lv(path, crop=None, trim=None, max_edge=0, **kwargs):
        calls["max_edge"] = max_edge
        return ("V", None)

    monkeypatch.setattr(nodes.media, "load_video", lv)
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [{"kind": "video", "file": "v1.mp4"}]})
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m",
        references_json=references_json, max_reference_edge=1024,
    )
    assert calls["max_edge"] == 0, "the global cap must not reach the video loader"


def test_a_per_reference_cap_does_reach_the_video_loader(fake_folder_paths, monkeypatch):
    """The per-reference downscale is the exception, and the whole point: it lets a heavy
    clip be shrunk below core's default to fit VRAM, which the global cap deliberately
    will not do."""
    tmp_path = fake_folder_paths
    _touch(tmp_path, "v1.mp4")
    calls = {}

    def lv(path, crop=None, trim=None, max_edge=0, **kwargs):
        calls["max_edge"] = max_edge
        return ("V", None)

    monkeypatch.setattr(nodes.media, "load_video", lv)
    _stub_prompt(monkeypatch)

    references_json = json.dumps(
        {"references": [{"kind": "video", "file": "v1.mp4", "max_edge": 512}]}
    )
    nodes.MiniMaxH3ReferencePack().build(
        direction="", openrouter_api_key="", model="m",
        references_json=references_json, max_reference_edge=1024,
    )
    assert calls["max_edge"] == 512, "a per-reference max_edge must reach the video loader"


def test_the_cap_moves_the_is_changed_key(fake_folder_paths):
    """It changes the pixels on the socket, so a cache hit across two values is wrong."""
    _touch(fake_folder_paths, "i1.jpg")
    references_json = json.dumps({"references": [{"kind": "image", "file": "i1.jpg"}]})
    kwargs = dict(direction="d", openrouter_api_key="", model="m",
                  references_json=references_json)

    a = nodes.MiniMaxH3ReferencePack.IS_CHANGED(max_reference_edge=2048, **kwargs)
    b = nodes.MiniMaxH3ReferencePack.IS_CHANGED(max_reference_edge=1024, **kwargs)

    assert a != b


# ---- structured logging -----------------------------------------------------------


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "MiniMaxRefPack"]


def test_build_logs_a_summary_and_a_line_per_reference(fake_folder_paths, monkeypatch, caplog):
    import logging

    tmp_path = fake_folder_paths
    _touch(tmp_path, "i1.jpg", "v1.mp4", "a1.wav")
    monkeypatch.setattr(nodes.media, "load_image", lambda path, crop=None, max_edge=0, flip=None, rotate=None, rotate_expand=True: "IMG")
    monkeypatch.setattr(nodes.media, "load_video", lambda path, crop=None, trim=None, flip=None, rotate=None, rotate_expand=True, max_edge=0: ("V", "A"))
    monkeypatch.setattr(nodes.media, "load_audio", lambda path, trim=None: "AUD")
    _stub_prompt(monkeypatch)

    references_json = json.dumps({"references": [
        {"kind": "image", "file": "i1.jpg", "crop": [0.1, 0.1, 0.5, 0.5]},
        {"kind": "video", "file": "v1.mp4", "use_soundtrack": True, "trim": [2.0, 6.5]},
        {"kind": "audio", "file": "a1.wav"},
    ]})

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        nodes.MiniMaxH3ReferencePack().build(
            direction="d", openrouter_api_key="", model="m", references_json=references_json
        )

    lines = _lines(caplog)
    assert any("event=build images=1 videos=1 audios=1" in ln for ln in lines)
    # the tag carries a space, so the formatter quotes it - that is what keeps the line
    # splittable on " " by anything reading these back
    assert any('event=reference kind=image slot=1 tag="<Picture 1>" file=i1.jpg crop=[0.1,0.1,0.5,0.5]' in ln for ln in lines)
    assert any('event=reference kind=video slot=1 tag="<Video 1>" file=v1.mp4 trim=[2,6.5] soundtrack="<Audio 1>"' in ln for ln in lines)
    # <Audio 2>, not <Audio 1>: the video's soundtrack is numbered first (refs.py's tag rule)
    assert any('event=reference kind=audio slot=1 tag="<Audio 2>" file=a1.wav' in ln for ln in lines)
    assert any("event=build_done" in ln and "ms=" in ln for ln in lines)


def test_build_logs_why_no_prompt_was_written(fake_folder_paths, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(nodes.prompt, "write_prompt", _never_called, raising=False)

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        nodes.MiniMaxH3ReferencePack().build(
            direction="   ", openrouter_api_key="", model="m", references_json=""
        )

    assert any("event=prompt_skipped reason=" in ln for ln in _lines(caplog))


def _never_called(*a, **k):
    raise AssertionError("write_prompt must not be called")


def test_the_api_key_never_reaches_a_log_line(fake_folder_paths, monkeypatch, caplog):
    import logging

    _stub_prompt(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="MiniMaxRefPack"):
        nodes.MiniMaxH3ReferencePack().build(
            direction="d", openrouter_api_key="sk-or-v1-deadbeef", model="m", references_json=""
        )

    assert not any("deadbeef" in ln for ln in _lines(caplog))


# ---- the cache key must not move for a UI preference -------------------------------
#
# references_json is an ENVELOPE. The browser stores UI preferences in it beside the
# references - the prompt box's height, whether tags are rewritten - and Python reads
# nothing but `references`. Folding the raw string into the cache key meant a cosmetic
# gesture re-ran the node: on OpenRouter that is a billed call, and the prompt that comes
# back is different, because the model writes a new one every time.


def _key(references_json, **kw):
    kw.setdefault("direction", "d")
    return nodes.MiniMaxH3ReferencePack.IS_CHANGED(references_json=references_json, **kw)


REFS = [{"kind": "image", "file": "a.png"}]


@pytest.mark.parametrize("preference", [
    {"prompt_h": 260},          # the user dragged the prompt box taller
    {"retag": False},           # the user turned tag rewriting off
    {"prompt_h": 44, "retag": False},
])
def test_a_ui_preference_does_not_move_the_cache_key(fake_folder_paths, preference):
    plain = _key(json.dumps({"references": REFS}))
    withpref = _key(json.dumps({"references": REFS, **preference}))
    assert plain == withpref, f"{preference} re-runs the node and bills a fresh VLM call"


def test_reformatting_the_same_reference_list_does_not_move_the_key(fake_folder_paths):
    """The same consequence from a different direction: the widget's exact text is not
    the input, the reference list is."""
    compact = json.dumps({"references": REFS}, separators=(",", ":"))
    spaced = json.dumps({"references": REFS}, indent=2)
    assert _key(compact) == _key(spaced)


def test_an_actual_reference_change_still_moves_the_key(fake_folder_paths):
    """The guard on the guard - a key that never moves would serve a stale prompt for a
    reference set the user has genuinely changed."""
    one = _key(json.dumps({"references": REFS}))
    two = _key(json.dumps({"references": REFS + [{"kind": "image", "file": "b.png"}]}))
    assert one != two


def test_an_edit_to_a_reference_still_moves_the_key(fake_folder_paths):
    edited = [{"kind": "image", "file": "a.png", "crop": [0, 0, 0.5, 0.5]}]
    assert _key(json.dumps({"references": REFS})) != _key(json.dumps({"references": edited}))

# ---- a silent video must not mint an <Audio N> ---------------------------------------


def _set(*entries):
    from minimax_refpack.refs import ReferenceSet

    return ReferenceSet.from_obj(list(entries))


def _tags(reference_set):
    return [(t.tag, t.audio_tag) for t in reference_set.assign_tags()]


def test_a_silent_video_does_not_take_an_audio_tag(monkeypatch):
    """use_soundtrack defaults ON and assign_tags trusts it, so a clip with no audio
    track told the model "<Audio 1>: the soundtrack inside <Video 1>". The comment on the
    default says the probe clears the flag - but the only probe is on the browser route,
    so it never runs for a headless queue."""
    monkeypatch.setattr(nodes.media, "probe", lambda path: {"has_audio": False})
    refset = _set({"kind": "video", "file": "silent.mp4"})

    silenced = nodes._clear_silent_soundtracks(refset, "/in")

    assert silenced == ["silent.mp4"]
    assert _tags(refset) == [("<Video 1>", None)]


def test_a_silent_video_does_not_shift_the_real_audio_numbering(monkeypatch):
    """The consequence that actually bites: a video's soundtrack claims its audio tag
    BEFORE any standalone audio does, so one phantom renumbers every real one after it -
    while the sockets do not shift, because the emit is already guarded on the decoded
    audio being present. Prompt and sockets then name different files."""
    monkeypatch.setattr(nodes.media, "probe", lambda path: {"has_audio": False})
    refset = _set(
        {"kind": "video", "file": "silent.mp4"},
        {"kind": "audio", "file": "voice.wav"},
    )

    before = _tags(refset)
    assert before == [("<Video 1>", "<Audio 1>"), ("<Audio 2>", None)], (
        "the bug, stated: the real voice-over is <Audio 2> because a silent clip took 1"
    )

    nodes._clear_silent_soundtracks(refset, "/in")
    assert _tags(refset) == [("<Video 1>", None), ("<Audio 1>", None)]


def test_a_video_with_sound_keeps_its_tag(monkeypatch):
    monkeypatch.setattr(nodes.media, "probe", lambda path: {"has_audio": True})
    refset = _set({"kind": "video", "file": "talkie.mp4"})

    assert nodes._clear_silent_soundtracks(refset, "/in") == []
    assert _tags(refset) == [("<Video 1>", "<Audio 1>")]


def test_a_flag_the_user_turned_off_is_not_probed(monkeypatch):
    """No point opening a container to confirm something nobody asked for."""
    def explode(path):
        raise AssertionError("must not probe a video whose soundtrack is already off")

    monkeypatch.setattr(nodes.media, "probe", explode)
    refset = _set({"kind": "video", "file": "muted.mp4", "use_soundtrack": False})
    assert nodes._clear_silent_soundtracks(refset, "/in") == []


@pytest.mark.parametrize("failure", [
    lambda path: (_ for _ in ()).throw(RuntimeError("av is not installed")),
    lambda path: (_ for _ in ()).throw(OSError("no such file")),
    lambda path: {},                      # a probe that cannot say
    lambda path: {"has_audio": None},     # ...or says it does not know
])
def test_a_probe_that_cannot_tell_leaves_the_flag_alone(monkeypatch, failure):
    """This exists to correct a confident wrong answer, not to invent a new way for a
    build to fail. Anything other than a definite "no audio" changes nothing."""
    monkeypatch.setattr(nodes.media, "probe", failure)
    refset = _set({"kind": "video", "file": "clip.mp4"})

    assert nodes._clear_silent_soundtracks(refset, "/in") == []
    assert _tags(refset) == [("<Video 1>", "<Audio 1>")]


def test_images_and_audio_are_never_probed(monkeypatch):
    def explode(path):
        raise AssertionError("only videos carry a soundtrack flag")

    monkeypatch.setattr(nodes.media, "probe", explode)
    refset = _set({"kind": "image", "file": "a.png"}, {"kind": "audio", "file": "v.wav"})
    assert nodes._clear_silent_soundtracks(refset, "/in") == []
