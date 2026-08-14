"""Tests for minimax_refpack.media - the pure resample math and the ComfyUI-decoder
seams (VideoFromFile / nodes_audio.load). comfy_api/comfy_extras are not importable
outside a ComfyUI process (verified: bare `import comfy_api` raises ModuleNotFoundError
in this venv), so the seams are monkeypatched at their accessor functions rather than at
the real (unavailable) module path.
"""

import pytest

from minimax_refpack import media


# ---- resample_indices -------------------------------------------------------


def test_identity_when_fps_matches():
    assert media.resample_indices(24, 24, 24) == list(range(24))


def test_downsample_30_to_24():
    idx = media.resample_indices(30, 30, 24)
    assert len(idx) == 24
    assert idx == sorted(idx)
    assert idx[0] == 0
    assert max(idx) <= 29


def test_downsample_60_to_24():
    idx = media.resample_indices(60, 60, 24)
    assert len(idx) == 24
    assert idx == sorted(idx)
    assert idx[0] == 0
    assert max(idx) <= 59


def test_upsample_12_to_24_duplicates_frames_instead_of_inventing_them():
    idx = media.resample_indices(12, 12, 24)
    assert len(idx) == 24
    assert idx == sorted(idx)
    assert max(idx) <= 11
    assert len(set(idx)) < len(idx)  # upsampling must repeat source frames


def test_single_source_frame():
    assert media.resample_indices(1, 24, 24) == [0]
    assert media.resample_indices(1, 30, 24) == [0]


def test_zero_source_frames():
    assert media.resample_indices(0, 30, 24) == []


def test_indices_never_leave_source_bounds():
    for n_src, src_fps in [(1, 1), (5, 7), (100, 23), (2, 240)]:
        idx = media.resample_indices(n_src, src_fps, 24)
        assert all(0 <= i < n_src for i in idx)


def test_zero_or_negative_fps_is_rejected():
    with pytest.raises(ValueError):
        media.resample_indices(10, 0, 24)
    with pytest.raises(ValueError):
        media.resample_indices(10, -5, 24)


# ---- load_video's >=5-frame guard and passthrough ---------------------------


class _ListFrames:
    """Fake images tensor: .shape[0] + fancy list-indexing, no torch required."""

    def __init__(self, n):
        self.shape = (n,)
        self._items = list(range(n))

    def __getitem__(self, idx):
        return [self._items[i] for i in idx]


def _fake_video_from_file(images_n, frame_rate, audio=None):
    class FakeComponents:
        images = _ListFrames(images_n)

    FakeComponents.frame_rate = frame_rate
    FakeComponents.audio = audio

    class FakeVideoFromFile:
        def __init__(self, path):
            self.path = path

        def get_components(self):
            return FakeComponents()

    return FakeVideoFromFile


def test_load_video_raises_when_resampled_clip_is_too_short(monkeypatch):
    # 3 frames at 24fps -> 3 frames at target 24fps, under the 5-frame floor
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(3, 24))
    with pytest.raises(ValueError) as exc:
        media.load_video("clip_too_short.mp4")
    assert "clip_too_short.mp4" in str(exc.value)


def test_load_video_does_not_pad_short_clips_up_to_five(monkeypatch):
    # 4 frames is still under 5 - must raise, never silently duplicate up to 5
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(4, 24))
    with pytest.raises(ValueError):
        media.load_video("clip.mp4")


def test_load_video_passes_through_resampled_frames_and_audio(monkeypatch):
    audio = {"waveform": "wf", "sample_rate": 48000}
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(30, 30, audio))
    frames, out_audio = media.load_video("clip.mp4", target_fps=24)
    assert len(frames) == 24
    assert out_audio is audio


def test_load_video_with_no_soundtrack_returns_none_audio(monkeypatch):
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(30, 30, None))
    _frames, out_audio = media.load_video("clip.mp4")
    assert out_audio is None


# ---- load_audio ---------------------------------------------------------------


def test_load_audio_wraps_waveform_with_a_batch_dim(monkeypatch):
    class FakeWaveform:
        def __init__(self):
            self.unsqueezed_dim = None

        def unsqueeze(self, dim):
            self.unsqueezed_dim = dim
            return self

    fake_wave = FakeWaveform()
    monkeypatch.setattr(media, "_audio_load_fn", lambda: (lambda path: (fake_wave, 44100)))

    result = media.load_audio("vo.wav")

    assert result == {"waveform": fake_wave, "sample_rate": 44100}
    assert fake_wave.unsqueezed_dim == 0


# ---- probe / thumbnail (image path only - real PIL, no ComfyUI needed) --------


def test_probe_an_image(tmp_path):
    from PIL import Image

    p = tmp_path / "a.png"
    Image.new("RGB", (40, 20), "red").save(p)

    info = media.probe(str(p))

    assert info == {"kind": "image", "width": 40, "height": 20, "fps": None, "duration": None, "has_audio": False}


def test_thumbnail_png_downscales_an_image(tmp_path):
    from PIL import Image

    p = tmp_path / "a.png"
    Image.new("RGB", (500, 100), "blue").save(p)

    png_bytes = media.thumbnail_png(str(p), max_edge=100)

    from io import BytesIO

    with Image.open(BytesIO(png_bytes)) as thumb:
        assert thumb.format == "PNG"
        assert max(thumb.size) <= 100
