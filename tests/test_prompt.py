"""Tests for the OpenRouter prompt writer. All network calls are mocked."""

import numpy as np
import pytest
import requests

from minimax_refpack import prompt
from minimax_refpack.refs import Reference, ReferenceSet


# ---- fixtures ---------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def _models_payload(entries):
    return {"data": entries}


def _model(model_id, modalities):
    return {"id": model_id, "architecture": {"input_modalities": modalities}}


@pytest.fixture(autouse=True)
def _reset_models_cache():
    prompt._models_cache = None
    prompt._models_cache_at = 0.0
    yield
    prompt._models_cache = None
    prompt._models_cache_at = 0.0


def img(name):
    return Reference(kind="image", file=name)


def vid(name, sound=False):
    return Reference(kind="video", file=name, use_soundtrack=sound)


def aud(name):
    return Reference(kind="audio", file=name)


def fake_load_image(path):
    return np.random.rand(1, 4, 4, 3).astype(np.float32)


def fake_load_video_with_audio(path, target_fps=24):
    frames = np.random.rand(9, 4, 4, 3).astype(np.float32)
    audio = {"waveform": np.random.uniform(-1, 1, size=(1, 2, 480)).astype(np.float32), "sample_rate": 24000}
    return frames, audio


def fake_load_video_no_audio(path, target_fps=24):
    frames = np.random.rand(9, 4, 4, 3).astype(np.float32)
    return frames, None


# ---- available_models: modality filter --------------------------------------


def test_filters_to_the_four_modality_superset(monkeypatch):
    payload = _models_payload(
        [
            _model(prompt.DEFAULT_MODEL, ["text", "image", "file", "audio", "video"]),
            _model("some/text-image-only", ["text", "image"]),
            _model("some/full-house", ["text", "image", "audio", "video"]),
        ]
    )
    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    models = prompt.available_models()
    assert "some/text-image-only" not in models
    assert prompt.DEFAULT_MODEL in models
    assert "some/full-house" in models


def test_default_model_is_sorted_first_when_present(monkeypatch):
    payload = _models_payload(
        [
            _model("aaa/before", ["text", "image", "audio", "video"]),
            _model(prompt.DEFAULT_MODEL, ["text", "image", "audio", "video"]),
            _model("zzz/after", ["text", "image", "audio", "video"]),
        ]
    )
    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    models = prompt.available_models()
    assert models[0] == prompt.DEFAULT_MODEL
    assert models[1:] == sorted(models[1:])


def test_available_models_caches_between_calls(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return FakeResponse(200, _models_payload([_model(prompt.DEFAULT_MODEL, ["text", "image", "audio", "video"])]))

    monkeypatch.setattr(prompt.requests, "get", fake_get)
    prompt.available_models()
    prompt.available_models()
    assert len(calls) == 1


# ---- available_models: must never raise, must never hang --------------------


def test_available_models_falls_back_on_network_failure(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(prompt.requests, "get", boom)
    models = prompt.available_models()
    assert models == prompt._FALLBACK_MODELS
    assert models[0] == prompt.DEFAULT_MODEL


def test_available_models_falls_back_on_malformed_body(monkeypatch):
    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: FakeResponse(200, {"unexpected": "shape"}))
    models = prompt.available_models()
    assert models[0] == prompt.DEFAULT_MODEL


def test_available_models_falls_back_when_nothing_matches(monkeypatch):
    payload = _models_payload([_model("text/only", ["text"])])
    monkeypatch.setattr(prompt.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    models = prompt.available_models()
    assert models == prompt._FALLBACK_MODELS


def test_available_models_uses_a_short_timeout(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["timeout"] = timeout
        return FakeResponse(200, _models_payload([]))

    monkeypatch.setattr(prompt.requests, "get", fake_get)
    prompt.available_models()
    assert captured["timeout"] is not None
    assert captured["timeout"] <= 10


# ---- key resolution -----------------------------------------------------------


def test_key_from_argument_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    monkeypatch.setenv("LLM_KEY", "llm-key")
    assert prompt._resolve_api_key(" arg-key ") == "arg-key"


def test_key_falls_back_to_openrouter_api_key_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert prompt._resolve_api_key("") == "env-key"


def test_key_falls_back_to_llm_key_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("LLM_KEY", "llm-key")
    assert prompt._resolve_api_key("  ") == "llm-key"


def test_no_key_anywhere_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_KEY", raising=False)
    with pytest.raises(prompt.PromptError):
        prompt._resolve_api_key("")


def test_key_never_appears_in_the_no_key_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_KEY", raising=False)
    with pytest.raises(prompt.PromptError) as e:
        prompt._resolve_api_key("")
    assert "sk-" not in str(e.value)


def test_key_never_leaks_into_a_request_exception(monkeypatch, tmp_path):
    secret = "sk-or-v1-do-not-leak-me"

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError(f"connection reset while sending key={secret}")

    monkeypatch.setattr(prompt.requests, "post", boom)
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError) as e:
        prompt.write_prompt(
            references=refset, input_dir=str(tmp_path), direction="x", api_key=secret, model=prompt.DEFAULT_MODEL
        )
    assert secret not in str(e.value)


def test_key_never_leaks_into_a_non_200_error(monkeypatch, tmp_path):
    secret = "sk-or-v1-do-not-leak-me-either"
    monkeypatch.setattr(
        prompt.requests,
        "post",
        lambda *a, **k: FakeResponse(401, {"error": {"message": "invalid key"}}, text="unauthorized"),
    )
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError) as e:
        prompt.write_prompt(
            references=refset, input_dir=str(tmp_path), direction="x", api_key=secret, model=prompt.DEFAULT_MODEL
        )
    assert secret not in str(e.value)


# ---- payload assembly ---------------------------------------------------------


def test_mixed_set_manifest_matches_assign_tags(monkeypatch, tmp_path):
    (tmp_path / "vo.wav").write_bytes(b"RIFF....fake wav bytes")

    refset = ReferenceSet([img("a.jpg"), img("b.jpg"), vid("clip.mp4", sound=True), aud("vo.wav")])
    tagged = {t.file: t for t in refset.assign_tags()}

    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_with_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "handheld, warm light")
    manifest_text = content[0]["text"]

    assert manifest_text.startswith("USER DIRECTION:\nhandheld, warm light")
    assert f"{tagged['a.jpg'].tag}: a.jpg" in manifest_text
    assert f"{tagged['b.jpg'].tag}: b.jpg" in manifest_text
    assert f"{tagged['clip.mp4'].tag} {tagged['clip.mp4'].audio_tag}: clip.mp4" in manifest_text
    assert f"{tagged['vo.wav'].tag}: vo.wav" in manifest_text


def test_every_media_part_is_preceded_by_its_own_kind_label(monkeypatch, tmp_path):
    """The manifest alone leaves nine bare images to be told apart by position.
    Each media part carries its own `<kind>_reference <Tag>` line immediately before it,
    which is what system_prompt.md's per-type handling rules key off."""
    (tmp_path / "vo.wav").write_bytes(b"RIFF....fake wav bytes")
    refset = ReferenceSet([img("a.jpg"), img("b.jpg"), vid("clip.mp4", sound=True), aud("vo.wav")])
    tagged = {t.file: t for t in refset.assign_tags()}

    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_with_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "direction")

    # every media part has a text part somewhere before it
    for i, part in enumerate(content):
        if part.get("type") in ("image_url", "input_audio"):
            assert any(p.get("type") == "text" for p in content[:i])

    labels = [p["text"] for p in content if p.get("type") == "text"]
    joined = "\n".join(labels)
    assert f"image_reference {tagged['a.jpg'].tag} (a.jpg)" in joined
    assert f"image_reference {tagged['b.jpg'].tag} (b.jpg)" in joined
    assert f"video_reference {tagged['clip.mp4'].tag} (clip.mp4)" in joined
    assert f"audio_reference {tagged['clip.mp4'].audio_tag} - the soundtrack" in joined
    assert f"audio_reference {tagged['vo.wav'].tag} (vo.wav)" in joined


def test_video_frame_label_sits_directly_before_its_frames(monkeypatch, tmp_path):
    refset = ReferenceSet([vid("clip.mp4", sound=False)])
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_with_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "direction")
    # [0] manifest, [1] the video label, [2:8] the six frames
    assert content[1]["type"] == "text"
    assert content[1]["text"].startswith("video_reference <Video 1> (clip.mp4)")
    assert "6 images" in content[1]["text"]
    assert all(p["type"] == "image_url" for p in content[2:8])


def test_mixed_set_has_six_frame_parts_for_the_video(monkeypatch, tmp_path):
    (tmp_path / "vo.wav").write_bytes(b"fake wav bytes")
    refset = ReferenceSet([img("a.jpg"), img("b.jpg"), vid("clip.mp4", sound=True), aud("vo.wav")])

    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_with_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "direction")
    image_parts = [p for p in content if p.get("type") == "image_url"]
    # 2 standalone images + 6 sampled video frames
    assert len(image_parts) == 2 + 6

    audio_parts = [p for p in content if p.get("type") == "input_audio"]
    # the video's soundtrack + the standalone wav
    assert len(audio_parts) == 2
    assert all(a["input_audio"]["format"] == "wav" for a in audio_parts)


def test_video_without_soundtrack_emits_no_audio_part(monkeypatch, tmp_path):
    refset = ReferenceSet([vid("clip.mp4", sound=False)])
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_with_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "direction")
    assert not any(p.get("type") == "input_audio" for p in content)
    assert sum(1 for p in content if p.get("type") == "image_url") == 6


def test_video_soundtrack_missing_from_media_degrades_to_text(monkeypatch, tmp_path):
    refset = ReferenceSet([vid("clip.mp4", sound=True)])
    monkeypatch.setattr(prompt.media, "load_video", fake_load_video_no_audio, raising=False)

    content = prompt._build_content(refset, str(tmp_path), "direction")
    assert not any(p.get("type") == "input_audio" for p in content)
    assert "could not be read" in content[0]["text"]


def test_audio_fallback_to_text_on_odd_format(tmp_path):
    (tmp_path / "weird.ogg").write_bytes(b"not really ogg")
    refset = ReferenceSet([aud("weird.ogg")])

    content = prompt._build_content(refset, str(tmp_path), "direction")
    audio_parts = [p for p in content if p.get("type") == "input_audio"]
    text_parts = [p for p in content if p.get("type") == "text"]

    assert audio_parts == []
    assert any("weird.ogg" in p["text"] for p in text_parts)


def test_supported_audio_format_is_inlined(tmp_path):
    (tmp_path / "vo.mp3").write_bytes(b"fake mp3 bytes")
    refset = ReferenceSet([aud("vo.mp3")])

    content = prompt._build_content(refset, str(tmp_path), "direction")
    audio_parts = [p for p in content if p.get("type") == "input_audio"]
    assert len(audio_parts) == 1
    assert audio_parts[0]["input_audio"]["format"] == "mp3"


# ---- TARGET FORMAT block (node width/height/length -> leading text part) --------


def test_target_format_block_appears_between_direction_and_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    refset = ReferenceSet([img("a.jpg")])

    content = prompt._build_content(
        refset, str(tmp_path), "handheld", width=1280, height=720, length_seconds=8.0
    )
    text = content[0]["text"]

    assert "TARGET FORMAT:" in text
    assert "frame: 1280 x 720 (aspect ratio 16:9)" in text
    assert "duration: 8.000 seconds" in text
    assert text.index("USER DIRECTION:") < text.index("TARGET FORMAT:") < text.index("Reference manifest:")


def test_target_format_aspect_ratio_is_reduced(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    refset = ReferenceSet([img("a.jpg")])

    content = prompt._build_content(
        refset, str(tmp_path), "d", width=640, height=480, length_seconds=0
    )
    assert "frame: 640 x 480 (aspect ratio 4:3)" in content[0]["text"]


def test_target_format_omits_duration_when_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    refset = ReferenceSet([img("a.jpg")])

    content = prompt._build_content(
        refset, str(tmp_path), "d", width=1280, height=720, length_seconds=0
    )
    text = content[0]["text"]
    assert "frame: 1280 x 720" in text
    assert "duration" not in text


def test_target_format_omits_frame_when_either_dimension_is_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    refset = ReferenceSet([img("a.jpg")])

    content = prompt._build_content(
        refset, str(tmp_path), "d", width=0, height=720, length_seconds=8.0
    )
    text = content[0]["text"]
    assert "frame:" not in text
    assert "duration: 8.000 seconds" in text


def test_no_target_format_when_nothing_is_specified(monkeypatch, tmp_path):
    """Existing callers pass no width/height/length - the payload must not change."""
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    refset = ReferenceSet([img("a.jpg")])

    content = prompt._build_content(refset, str(tmp_path), "d")
    assert "TARGET FORMAT" not in content[0]["text"]


def test_write_prompt_threads_target_format_into_the_payload(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="x", api_key="k",
        model=prompt.DEFAULT_MODEL, width=1920, height=1080, length_seconds=12.25,
    )
    text = captured["json"]["messages"][1]["content"][0]["text"]
    assert "frame: 1920 x 1080 (aspect ratio 16:9)" in text
    assert "duration: 12.250 seconds" in text


# ---- write_prompt: the full round trip ----------------------------------------


def test_write_prompt_returns_stripped_completion(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.media, "load_image", fake_load_image, raising=False)
    monkeypatch.setattr(
        prompt.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"choices": [{"message": {"content": "  a fine prompt  "}}]}),
    )
    refset = ReferenceSet([img("a.jpg")])
    out = prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="steer", api_key="key", model=prompt.DEFAULT_MODEL
    )
    assert out == "a fine prompt"


def test_write_prompt_sends_bearer_header(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="steer", api_key="my-key", model=prompt.DEFAULT_MODEL
    )
    assert captured["url"].startswith("https://")
    assert captured["headers"]["Authorization"] == "Bearer my-key"
    assert captured["timeout"] is not None


def test_write_prompt_401_is_a_prompt_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        prompt.requests,
        "post",
        lambda *a, **k: FakeResponse(401, {"error": {"message": "invalid api key"}}, text="unauthorized"),
    )
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError) as e:
        prompt.write_prompt(references=refset, input_dir=str(tmp_path), direction="x", api_key="k", model=prompt.DEFAULT_MODEL)
    assert "401" in str(e.value)


def test_write_prompt_500_is_a_prompt_error(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.requests, "post", lambda *a, **k: FakeResponse(500, None, text="server exploded"))
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError) as e:
        prompt.write_prompt(references=refset, input_dir=str(tmp_path), direction="x", api_key="k", model=prompt.DEFAULT_MODEL)
    assert "500" in str(e.value)


def test_write_prompt_empty_completion_is_a_prompt_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        prompt.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"choices": [{"message": {"content": "   "}}]}),
    )
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError):
        prompt.write_prompt(references=refset, input_dir=str(tmp_path), direction="x", api_key="k", model=prompt.DEFAULT_MODEL)


def test_write_prompt_malformed_body_is_a_prompt_error(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt.requests, "post", lambda *a, **k: FakeResponse(200, {"unexpected": "shape"}))
    refset = ReferenceSet([])
    with pytest.raises(prompt.PromptError):
        prompt.write_prompt(references=refset, input_dir=str(tmp_path), direction="x", api_key="k", model=prompt.DEFAULT_MODEL)


def test_write_prompt_reads_system_prompt_at_call_time(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    prompt.write_prompt(references=refset, input_dir=str(tmp_path), direction="x", api_key="k", model=prompt.DEFAULT_MODEL)
    system_message = captured["json"]["messages"][0]
    assert system_message["role"] == "system"
    with open(prompt._SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        assert system_message["content"] == f.read()


# ---- system_prompt override ---------------------------------------------------


def test_write_prompt_uses_a_supplied_system_prompt_verbatim(monkeypatch, tmp_path):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    custom = "You are a custom MiniMax prompt writer.\nBe terse."
    prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="x", api_key="k",
        model=prompt.DEFAULT_MODEL, system_prompt=custom,
    )
    system_message = captured["json"]["messages"][0]
    assert system_message["role"] == "system"
    assert system_message["content"] == custom


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_write_prompt_blank_system_prompt_falls_back_to_packaged_file(monkeypatch, tmp_path, blank):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="x", api_key="k",
        model=prompt.DEFAULT_MODEL, system_prompt=blank,
    )
    system_message = captured["json"]["messages"][0]
    with open(prompt._SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        assert system_message["content"] == f.read()


def test_write_prompt_fallback_still_reads_the_file_at_call_time(monkeypatch, tmp_path):
    """Same guarantee as test_write_prompt_reads_system_prompt_at_call_time, but proven
    with an explicit blank system_prompt argument rather than the default omission."""
    captured = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json["messages"][0]["content"])
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    refset = ReferenceSet([])
    original = prompt._read_system_prompt()

    prompt.write_prompt(
        references=refset, input_dir=str(tmp_path), direction="x", api_key="k",
        model=prompt.DEFAULT_MODEL, system_prompt="",
    )

    edited = original + "\n\nEDITED FOR THIS TEST"
    with open(prompt._SYSTEM_PROMPT_PATH, "w", encoding="utf-8") as f:
        f.write(edited)
    try:
        prompt.write_prompt(
            references=refset, input_dir=str(tmp_path), direction="x", api_key="k",
            model=prompt.DEFAULT_MODEL, system_prompt="",
        )
    finally:
        with open(prompt._SYSTEM_PROMPT_PATH, "w", encoding="utf-8") as f:
            f.write(original)

    assert captured[0] == original
    assert captured[1] == edited


# ---- reasoning effort -----------------------------------------------------------


def _capture_payload(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["payload"] = json
        return FakeResponse(200, {"choices": [{"message": {"content": "p"}}]})

    monkeypatch.setattr(prompt.requests, "post", fake_post)
    return captured


def test_reasoning_effort_defaults_to_medium(monkeypatch, tmp_path):
    captured = _capture_payload(monkeypatch)
    prompt.write_prompt(
        references=ReferenceSet([]), input_dir=str(tmp_path), direction="d",
        api_key="k", model=prompt.DEFAULT_MODEL,
    )
    assert captured["payload"]["reasoning"] == {"effort": "medium"}
    assert prompt.DEFAULT_REASONING_EFFORT == "medium"


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_every_legal_effort_goes_on_the_wire(monkeypatch, tmp_path, effort):
    """'none' is OpenRouter's own 'do not reason' value, so it is sent, not dropped."""
    captured = _capture_payload(monkeypatch)
    prompt.write_prompt(
        references=ReferenceSet([]), input_dir=str(tmp_path), direction="d",
        api_key="k", model=prompt.DEFAULT_MODEL, reasoning_effort=effort,
    )
    assert captured["payload"]["reasoning"] == {"effort": effort}


def test_an_unrecognised_effort_is_dropped_rather_than_sent(monkeypatch, tmp_path):
    """A bad widget value must not turn into a 400 that kills a whole queue."""
    captured = _capture_payload(monkeypatch)
    prompt.write_prompt(
        references=ReferenceSet([]), input_dir=str(tmp_path), direction="d",
        api_key="k", model=prompt.DEFAULT_MODEL, reasoning_effort="ludicrous",
    )
    assert "reasoning" not in captured["payload"]


def test_render_payload_shows_the_reasoning_field(monkeypatch, tmp_path):
    sink = []
    _capture_payload(monkeypatch)
    prompt.write_prompt(
        references=ReferenceSet([]), input_dir=str(tmp_path), direction="d",
        api_key="k", model=prompt.DEFAULT_MODEL, reasoning_effort="high", debug=sink,
    )
    assert "reasoning: {'effort': 'high'}" in sink[0]


def test_debug_shows_the_user_message_before_the_system_prompt(monkeypatch, tmp_path):
    """Display order only: the ~190-line system prompt buried the direction at the
    bottom of the debug socket. The payload itself must still send system first."""
    captured = _capture_payload(monkeypatch)
    sink = []
    prompt.write_prompt(
        references=ReferenceSet([]), input_dir=str(tmp_path), direction="MY DIRECTION HERE",
        api_key="k", model=prompt.DEFAULT_MODEL, system_prompt="THE SYSTEM RULES", debug=sink,
    )
    text = sink[0]
    assert text.index("===== USER MESSAGE =====") < text.index("===== SYSTEM MESSAGE =====")
    assert text.index("MY DIRECTION HERE") < text.index("THE SYSTEM RULES")

    # ...but the wire order is unchanged: system is still messages[0].
    assert [m["role"] for m in captured["payload"]["messages"]] == ["system", "user"]
