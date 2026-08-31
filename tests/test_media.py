"""Tests for minimax_refpack.media - the pure resample math and the ComfyUI-decoder
seams (VideoFromFile / nodes_audio.load). comfy_api/comfy_extras are not importable
outside a ComfyUI process (verified: bare `import comfy_api` raises ModuleNotFoundError
in this venv), so the seams are monkeypatched at their accessor functions rather than at
the real (unavailable) module path.
"""

import sys
import types

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


def _fake_video_from_file(images_n, frame_rate, audio=None, record=None):
    """Stands in for comfy VideoFromFile, INCLUDING its start_time/duration windowing.

    The real class seeks to the in-point and stops at the out-point, so get_components
    returns only the window's frames and a soundtrack already sliced to it. The fake does
    the same, clamped to the clip's own length - which is what lets a trim past the end
    come back with too few frames rather than a full decode.
    """
    import math as _math

    def _window(start, dur):
        if not dur:
            return images_n, 0
        lo = max(0, _math.ceil(start * frame_rate))
        hi = min(images_n, _math.ceil((start + dur) * frame_rate))
        return max(0, hi - lo), lo

    class FakeVideoFromFile:
        def __init__(self, path, *, start_time=0, duration=0):
            self.path = path
            self.start_time = start_time
            self.duration = duration
            if record is not None:
                record["start_time"] = start_time
                record["duration"] = duration

        def get_components(self):
            n, _first = _window(self.start_time, self.duration)
            aud = audio
            if self.duration and audio is not None:
                sr = audio["sample_rate"]
                lo = int(self.start_time * sr)
                hi = int((self.start_time + self.duration) * sr)
                aud = {"waveform": audio["waveform"][..., lo:hi], "sample_rate": sr}

            class FakeComponents:
                images = _ListFrames(n)

            FakeComponents.frame_rate = frame_rate
            FakeComponents.audio = aud
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


# ---- the one fraction->pixel rule (_crop_box) --------------------------------


def test_crop_box_rounds_half_up_and_stays_inside_the_frame():
    # 0.25*50 = 12.5 -> 13 (half-up; Python's round() would give 12 here)
    assert media._crop_box([0.25, 0.25, 0.5, 0.5], 100, 50) == (25, 13, 75, 38)
    assert media._crop_box([0.0, 0.0, 1.0, 1.0], 40, 20) == (0, 0, 40, 20)


def test_crop_box_never_collapses_to_zero_area():
    left, top, right, bottom = media._crop_box([0.999, 0.0, 0.001, 1.0], 6, 4)
    assert right - left >= 1 and bottom - top >= 1
    assert 0 <= left < right <= 6
    assert 0 <= top < bottom <= 4


# ---- load_video trim ----------------------------------------------------------


def test_load_video_trim_asks_the_decoder_for_only_the_window(monkeypatch):
    # The whole point: a trim is a seek+duration handed to VideoFromFile, so a 4.5s window
    # out of a 10s clip decodes ~108 frames, not 240. Decoding the whole file first was an
    # 80-second stall and, on a bigger clip, an OOM that killed ComfyUI.
    rec = {}
    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_from_file(240, 24, record=rec))
    frames, _ = media.load_video("clip.mp4", trim=[2.0, 6.5])
    assert rec["start_time"] == pytest.approx(2.0)
    assert rec["duration"] == pytest.approx(4.5)
    # 108 window frames at 24fps, resampled 24->24, all of them
    assert len(frames) == 108


def _fake_video_old_signature(images_n, frame_rate, audio=None):
    """An OLDER VideoFromFile: no start_time/duration, so the windowed call TypeErrors."""
    class FakeVideoFromFile:
        def __init__(self, path):
            self.path = path

        def get_components(self):
            class C:
                images = _ListFrames(images_n)

            C.frame_rate = frame_rate
            C.audio = audio
            return C()

    return FakeVideoFromFile


def test_load_video_falls_back_to_a_whole_file_decode_on_an_old_decoder(monkeypatch):
    # No start_time/duration on the old class -> the windowed call raises TypeError and we
    # decode the whole clip, then pick the window by frame index and slice the soundtrack
    # ourselves - exactly the pre-fix behaviour, so an old ComfyUI is no worse off.
    import numpy as np

    sr = 1000
    audio = {"waveform": np.arange(10 * sr, dtype=np.float32).reshape(1, 1, -1),
             "sample_rate": sr}
    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_old_signature(240, 24, audio))
    frames, out = media.load_video("clip.mp4", trim=[2.0, 6.5])
    assert len(frames) == 108
    assert frames[0] == 48, "the fallback selects by SOURCE index, not window-local"
    assert out["waveform"].shape[-1] == int(6.5 * sr) - int(2.0 * sr), (
        "the fallback slices the soundtrack itself, since the old decoder did not window it"
    )


def test_load_video_without_a_trim_decodes_the_whole_clip(monkeypatch):
    rec = {}
    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_from_file(240, 24, record=rec))
    frames, _ = media.load_video("clip.mp4")
    assert rec["duration"] == 0, "no trim means no window - the decoder gets the whole file"
    assert len(frames) == 240


def test_load_video_trim_then_resamples_the_span(monkeypatch):
    # 60fps window of 2.0s -> 120 window frames -> 48 output frames at 24fps.
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(600, 60))
    frames, _ = media.load_video("clip.mp4", trim=[1.0, 3.0])
    assert len(frames) == 48


def test_load_video_too_short_trim_raises_naming_file_and_window(monkeypatch):
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(240, 24))
    with pytest.raises(ValueError) as exc:
        media.load_video("clip.mp4", trim=[1.0, 1.1])
    msg = str(exc.value)
    assert "clip.mp4" in msg
    assert "1.00" in msg and "1.10" in msg


def test_load_video_trim_entirely_outside_the_clip_raises(monkeypatch):
    # A 1s clip windowed to [5.0, 9.0) seeks past the end, so the decoder returns nothing
    # and load_video refuses it rather than shipping an empty clip.
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(24, 24))
    with pytest.raises(ValueError):
        media.load_video("clip.mp4", trim=[5.0, 9.0])


def test_load_video_trusts_the_decoders_windowed_soundtrack(monkeypatch):
    # VideoFromFile windows the audio to the trim itself, so _decode_video must NOT slice
    # it a second time - the fake windows it here, standing in for the real decoder, and
    # the result must come through untouched (not double-sliced to a quarter of the span).
    import numpy as np

    sr = 1000
    audio = {
        "waveform": np.arange(10 * sr, dtype=np.float32).reshape(1, 1, -1),
        "sample_rate": sr,
    }
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(240, 24, audio))
    _frames, out = media.load_video("clip.mp4", trim=[2.0, 6.5])
    assert out["sample_rate"] == sr
    assert out["waveform"].shape[-1] == int(6.5 * sr) - int(2.0 * sr)
    assert out["waveform"][0, 0, 0] == int(2.0 * sr)
    # the source dict is not mutated in place
    assert audio["waveform"].shape[-1] == 10 * sr


# ---- load_video crop ----------------------------------------------------------


def _fake_video_numpy(n, frame_rate, h, w, audio=None):
    import numpy as np

    class FakeComponents:
        images = np.zeros((n, h, w, 3), dtype=np.float32)

    FakeComponents.frame_rate = frame_rate
    FakeComponents.audio = audio

    class FakeVideoFromFile:
        def __init__(self, path, *, start_time=0, duration=0):
            self.path = path
            self.start_time = start_time
            self.duration = duration

        def get_components(self):
            import math as _math

            if self.duration:
                lo = max(0, _math.ceil(self.start_time * frame_rate))
                hi = min(n, _math.ceil((self.start_time + self.duration) * frame_rate))
                win = max(0, hi - lo)
            else:
                win = n

            import numpy as _np

            class C:
                images = _np.zeros((win, h, w, 3), dtype=_np.float32)

            C.frame_rate = frame_rate
            C.audio = audio
            return C()

    return FakeVideoFromFile


def test_load_video_crop_crops_every_frame(monkeypatch):
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_numpy(24, 24, 40, 60))
    frames, _ = media.load_video("clip.mp4", crop=[0.5, 0.25, 0.5, 0.5])
    assert frames.shape == (24, 20, 30, 3)


def test_load_video_crop_and_trim_compose(monkeypatch):
    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_numpy(240, 24, 40, 60))
    frames, _ = media.load_video("clip.mp4", crop=[0.0, 0.0, 0.5, 0.5], trim=[2.0, 6.5])
    assert frames.shape == (108, 20, 30, 3)


# ---- load_audio trim -----------------------------------------------------------


def test_load_audio_trim_slices_the_waveform(monkeypatch):
    import numpy as np

    sr = 1000

    class FakeWaveform:
        """[C, L] that unsqueezes to a real numpy [1, C, L] so slicing is honest."""

        def __init__(self, arr):
            self.arr = arr

        def unsqueeze(self, dim):
            assert dim == 0
            return self.arr[None, ...]

    arr = np.arange(10 * sr, dtype=np.float32).reshape(1, -1)
    monkeypatch.setattr(media, "_audio_load_fn", lambda: (lambda path: (FakeWaveform(arr), sr)))

    out = media.load_audio("vo.wav", trim=[2.0, 6.5])

    assert out["sample_rate"] == sr
    assert out["waveform"].shape == (1, 1, int(6.5 * sr) - int(2.0 * sr))
    assert out["waveform"][0, 0, 0] == int(2.0 * sr)


# ---- thumbnail crop / video at_seconds ------------------------------------------


def test_thumbnail_png_applies_the_crop(tmp_path):
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (100, 100), "red")
    img.paste((0, 0, 255), (50, 0, 100, 100))
    p = tmp_path / "a.png"
    img.save(p)

    out = media.thumbnail_png(str(p), max_edge=200, crop=[0.5, 0.0, 0.5, 1.0])

    with Image.open(BytesIO(out)) as thumb:
        assert thumb.size == (50, 100)
        assert thumb.getpixel((25, 50)) == (0, 0, 255)


class _FakeAv:
    """Stub of the av surface thumbnail_png's video branch touches. av is not
    installed in this venv (same reason the ComfyUI decoders are stubbed), so the
    seek arithmetic is proven against a recorder instead."""

    def __init__(self, frame_times, time_base):
        from fractions import Fraction

        from PIL import Image

        av_self = self
        self.seeks = []
        self.decoded = []
        self._seek_to = 0

        class FakeStream:
            pass

        stream = FakeStream()
        stream.time_base = Fraction(1, time_base)
        self.stream = stream

        class FakeFrame:
            def __init__(self, pts, color):
                self.pts = pts
                self._color = color

            def to_image(self):
                return Image.new("RGB", (20, 10), self._color)

        colors = ["red", "green", "blue", "yellow", "purple"]
        self.frames = [
            FakeFrame(int(t * time_base), colors[i % len(colors)])
            for i, t in enumerate(frame_times)
        ]

        class FakeStreams:
            video = [stream]

        class FakeContainer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            @property
            def streams(self):
                return FakeStreams()

            def seek(self, offset, stream=None):
                av_self.seeks.append((offset, stream))
                # like a real backward seek: land on the last frame at/before offset
                av_self._seek_to = 0
                for i, f in enumerate(av_self.frames):
                    if f.pts <= offset:
                        av_self._seek_to = i

            def decode(self, stream):
                for f in av_self.frames[av_self._seek_to:]:
                    av_self.decoded.append(f.pts)
                    yield f

        self._container = FakeContainer()

    def open(self, path):
        return self._container


def _install_fake_av(monkeypatch, fake):
    import sys

    monkeypatch.setitem(sys.modules, "av", fake)


def test_thumbnail_png_seeks_video_to_at_seconds(monkeypatch):
    from io import BytesIO

    from PIL import Image

    fake = _FakeAv(frame_times=[0.0, 1.0, 2.0, 3.0], time_base=90000)
    _install_fake_av(monkeypatch, fake)

    out = media.thumbnail_png("clip.mp4", at_seconds=2.0)

    # sought in the stream's own time base, on the stream (CU video_types.py:316-320)
    assert fake.seeks == [(180000, fake.stream)]
    # decoded only from the landed keyframe up to the target, never the whole clip
    assert fake.decoded == [180000]
    with Image.open(BytesIO(out)) as thumb:
        assert thumb.getpixel((5, 5)) == (0, 0, 255)  # the 2.0s frame is blue


def test_thumbnail_png_at_seconds_past_the_end_keeps_the_last_frame(monkeypatch):
    from io import BytesIO

    from PIL import Image

    fake = _FakeAv(frame_times=[0.0, 1.0], time_base=90000)
    _install_fake_av(monkeypatch, fake)

    out = media.thumbnail_png("clip.mp4", at_seconds=99.0)

    with Image.open(BytesIO(out)) as thumb:
        assert thumb.getpixel((5, 5)) == (0, 128, 0)  # the 1.0s frame (green)


def test_thumbnail_png_without_at_seconds_does_not_seek(monkeypatch):
    fake = _FakeAv(frame_times=[0.0, 1.0], time_base=90000)
    _install_fake_av(monkeypatch, fake)

    media.thumbnail_png("clip.mp4")

    assert fake.seeks == []
    assert fake.decoded == [0]


# ---- the reference-image size cap ---------------------------------------------
# Core sizes reference images off the SHORT edge (CU/comfy_extras/nodes_minimax_h3.py:301,
# REF_IMAGE_SHORT_EDGE = 2048 at :29), so a wide sheet reaches the VAE enormous at
# ref_image_size="max". Capping the LONG edge here is the guard.
#
# torch is not installed in this venv (the module lazy-imports it, same reason
# folder_paths and av get stubbed elsewhere in this file), so `fake_torch` stands in
# with an identity from_numpy - the assertions are all about pixel dimensions, which
# the numpy array carries unchanged.


@pytest.fixture
def fake_torch(monkeypatch):
    module = types.ModuleType("torch")
    module.from_numpy = lambda arr: arr
    monkeypatch.setitem(sys.modules, "torch", module)
    return module


def _png(tmp_path, name, size, color="red"):
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", size, color).save(p)
    return str(p)


def test_load_image_caps_the_long_edge(tmp_path, fake_torch):
    out = media.load_image(_png(tmp_path, "wide.png", (500, 250)), max_edge=200)

    assert out.shape[1:3] == (100, 200)  # [1, H, W, 3]


def test_load_image_caps_the_long_edge_of_a_tall_reference(tmp_path, fake_torch):
    out = media.load_image(_png(tmp_path, "tall.png", (250, 500)), max_edge=200)

    assert out.shape[1:3] == (200, 100)


def test_load_image_never_upscales_a_small_reference(tmp_path, fake_torch):
    out = media.load_image(_png(tmp_path, "small.png", (100, 50)), max_edge=2048)

    assert out.shape[1:3] == (50, 100)


def test_load_image_cap_of_zero_is_off(tmp_path, fake_torch):
    out = media.load_image(_png(tmp_path, "wide.png", (500, 250)), max_edge=0)

    assert out.shape[1:3] == (250, 500)


def test_load_image_caps_the_cropped_size_not_the_source(tmp_path, fake_torch):
    """Crop first, then cap. A crop that already brings the long edge under the cap
    leaves the pixels alone - the cap must never see the pre-crop dimensions."""
    path = _png(tmp_path, "wide.png", (800, 400))

    # crop to the left half: 400x400, already under a 500 cap
    untouched = media.load_image(path, crop=[0.0, 0.0, 0.5, 1.0], max_edge=500)
    assert untouched.shape[1:3] == (400, 400)

    # the same crop under a 200 cap does get resized
    capped = media.load_image(path, crop=[0.0, 0.0, 0.5, 1.0], max_edge=200)
    assert capped.shape[1:3] == (200, 200)


def test_load_image_cap_keeps_the_pixels_normalised(tmp_path, fake_torch):
    out = media.load_image(_png(tmp_path, "wide.png", (600, 300), (255, 0, 0)), max_edge=100)

    assert out.min() >= 0.0 and out.max() <= 1.0


# ---- structured logging -------------------------------------------------------


def _mmrp_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "MiniMaxRefPack"]


def test_load_image_logs_what_it_emitted(tmp_path, fake_torch, caplog):
    import logging

    p = _png(tmp_path, "wide.png", (500, 250))

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        media.load_image(p, crop=[0.0, 0.0, 0.5, 1.0], max_edge=200)

    line = next(ln for ln in _mmrp_lines(caplog) if "event=load_image" in ln)
    assert "src=500x250" in line
    assert "crop=[0,0,0.5,1]" in line
    assert "out=200x200" in line
    assert "ms=" in line


def test_load_video_logs_the_trim_and_what_survived_it(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(240, 24))

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        media.load_video("clip.mp4", trim=[2.0, 6.5])

    line = next(ln for ln in _mmrp_lines(caplog) if "event=load_video" in ln)
    assert "trim=[2,6.5]" in line
    assert "frames=108" in line
    assert "ms=" in line


def test_a_failed_load_is_logged_as_a_failure(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(media, "_video_from_file_cls", lambda: _fake_video_from_file(240, 24))

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        with pytest.raises(ValueError):
            media.load_video("clip.mp4", trim=[1.0, 1.1])   # under 5 frames at 24fps

    line = next(ln for ln in _mmrp_lines(caplog) if "event=load_video" in ln)
    assert "ok=false" in line and "error=ValueError" in line


# ---- video bytes for the VLM ---------------------------------------------------
# The VLM takes whole videos (OpenRouter `video_url` parts, verified live against
# google/gemini-3-flash-preview: 10.12s clip -> 660 video tokens + 250 audio tokens).
# An untouched clip is therefore sent as the FILE, which decodes nothing at all; a
# cropped or trimmed one has to be re-encoded so the VLM sees what the socket emits.


def test_an_untouched_clip_is_sent_as_the_file_itself(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x01not-really-an-mp4\x02")

    data, mime = media.video_clip_bytes(str(p))

    assert data == p.read_bytes()
    assert mime == "video/mp4"


def test_the_file_path_carries_the_soundtrack_so_it_needs_no_re_encode(tmp_path):
    """Documented as behaviour: nothing is decoded on this path."""
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"x" * 32)

    assert media.video_clip_bytes(str(p))[1] == "video/mp4"
    assert media.video_clip_bytes(str(p), crop=None, trim=None)[0] == b"x" * 32


@pytest.mark.parametrize("suffix,mime", [
    (".mp4", "video/mp4"), (".mov", "video/mov"), (".webm", "video/webm"), (".mpeg", "video/mpeg"),
])
def test_containers_openrouter_accepts_pass_straight_through(tmp_path, suffix, mime):
    p = tmp_path / f"clip{suffix}"
    p.write_bytes(b"bytes")

    assert media.video_clip_bytes(str(p)) == (b"bytes", mime)


def test_a_container_openrouter_does_not_accept_is_re_encoded(tmp_path, monkeypatch):
    called = {}

    def fake_transcode(path, crop, trim, flip=None, rotate=None, rotate_expand=True):
        called["args"] = (path, crop, trim)
        return b"mp4"

    monkeypatch.setattr(media, "_transcode_window", fake_transcode)
    p = tmp_path / "clip.avi"
    p.write_bytes(b"avi bytes")

    data, mime = media.video_clip_bytes(str(p))

    assert (data, mime) == (b"mp4", "video/mp4")
    assert called["args"] == (str(p), None, None)


@pytest.mark.parametrize("crop,trim", [
    ([0.0, 0.0, 0.5, 1.0], None),
    (None, [2.0, 6.5]),
    ([0.0, 0.0, 0.5, 1.0], [2.0, 6.5]),
])
def test_an_edited_clip_is_re_encoded_so_the_vlm_sees_the_edit(tmp_path, monkeypatch, crop, trim):
    seen = {}

    def fake_transcode(path, c, t, flip=None, rotate=None, rotate_expand=True):
        seen["args"] = (c, t)
        return b"mp4"

    monkeypatch.setattr(media, "_transcode_window", fake_transcode)
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"original")

    data, mime = media.video_clip_bytes(str(p), crop=crop, trim=trim)

    assert data == b"mp4" and mime == "video/mp4"
    assert seen["args"] == (crop, trim)


# ---- orientation: flip then rotate then crop ---------------------------------------
#
# The ORDER is the contract. The crop rect is drawn on the frame the editor shows, and
# the editor shows the ORIENTED frame, so cropping first selects a different region than
# the user boxed. semmlerino hit exactly this on a portrait phone clip and wrote it down:
# the preview showed one region and the node emitted another.


def _rgb(w, h, colour=(10, 20, 30)):
    from PIL import Image
    return Image.new("RGB", (w, h), colour)


@pytest.mark.parametrize("rotate,expected", [
    (0, (40, 20)), (90, (20, 40)), (180, (40, 20)), (270, (20, 40)),
])
def test_a_quarter_turn_swaps_the_axes(rotate, expected):
    out = media._orient_pil(_rgb(40, 20), None, rotate)
    assert (out.width, out.height) == expected


def test_a_quarter_turn_is_lossless_and_goes_the_way_the_buttons_do():
    """Clockwise, because that is what the on-screen arrow means. A pixel at the TOP-LEFT
    must land at the TOP-RIGHT after one clockwise turn - the opposite would be PIL's own
    counter-clockwise convention leaking through."""
    from PIL import Image
    img = Image.new("RGB", (2, 1), (0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))          # top-left is red
    out = media._orient_pil(img, None, 90)
    assert out.size == (1, 2)
    assert out.getpixel((0, 0)) == (255, 0, 0)  # ...now the top of a 1x2 strip


@pytest.mark.parametrize("flip,probe,expected", [
    ("h", (1, 0), (255, 0, 0)),   # mirrored left-right
    ("v", (0, 1), (255, 0, 0)),   # mirrored top-bottom
    ("hv", (1, 1), (255, 0, 0)),  # both
])
def test_flips_mirror_the_expected_axis(flip, probe, expected):
    from PIL import Image
    img = Image.new("RGB", (2, 2), (0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))
    out = media._orient_pil(img, flip, None)
    assert out.getpixel(probe) == expected


def test_flip_happens_before_rotation():
    """They do not commute, so the order has to be pinned rather than assumed. Red starts
    top-left; flip h puts it top-right; rotating 90 clockwise puts it bottom-right."""
    from PIL import Image
    img = Image.new("RGB", (2, 2), (0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))
    out = media._orient_pil(img, "h", 90)
    assert out.getpixel((1, 1)) == (255, 0, 0)


@pytest.mark.parametrize("rotate,expected", [
    (0, 0), (90, 1), (180, 2), (270, 3), (360, 0), (-90, 3),
    (89.9, None), (45, None), (1, None),
])
def test_quarter_turns_are_recognised_and_free_angles_are_not(rotate, expected):
    """A slider parked on 89.9 is a free angle and must resample, or the emitted frame
    would not match the preview the user approved."""
    assert media._quarter_turns(rotate) == expected


def test_a_free_angle_expands_and_fills_the_corners_black():
    out = media._orient_pil(_rgb(40, 20, (10, 20, 30)), None, 45, expand=True)
    assert out.width > 40 and out.height > 20
    assert out.getpixel((0, 0)) == (0, 0, 0), "a corner outside the source must be black"


def test_a_free_angle_can_be_bound_to_the_source_extent_instead():
    out = media._orient_pil(_rgb(40, 20), None, 45, expand=False)
    assert (out.width, out.height) == (40, 20)


def test_an_unoriented_call_returns_the_very_same_object():
    """Nothing to do must cost nothing - it is the common case on every reference."""
    img = _rgb(4, 4)
    assert media._orient_pil(img, None, None) is img
    assert media._orient_pil(img, None, 0) is img


def test_the_array_and_pil_paths_agree_on_a_quarter_turn():
    """Video frames go through numpy and stills through PIL. Two implementations of one
    transform have to produce the same pixels, or a clip and a still of the same frame
    would disagree."""
    import numpy as np
    from PIL import Image
    arr = np.arange(4 * 6 * 3, dtype="uint8").reshape(4, 6, 3)
    via_np = media._orient_array(arr, "h", 90)
    via_pil = np.array(media._orient_pil(Image.fromarray(arr), "h", 90))
    assert np.array_equal(via_np, via_pil)


def test_an_oriented_clip_never_takes_the_raw_bytes_fast_path(tmp_path, monkeypatch):
    """The trap this guards is silent. video_clip_bytes hands the VLM the FILE'S OWN
    BYTES when a clip is untouched, which is right and fast - but the sockets emit the
    ROTATED frames. Send the untouched file and the model is shown a different video from
    the one being generated with, and nothing downstream compares the two, so it never
    surfaces as an error."""
    clip = tmp_path / "v.mp4"
    clip.write_bytes(b"\x00\x00\x00 ftypmp42 original")
    seen = {}

    def fake_transcode(path, crop, trim, flip=None, rotate=None, rotate_expand=True):
        seen.update(path=path, crop=crop, trim=trim, flip=flip, rotate=rotate)
        return b"re-encoded"

    monkeypatch.setattr(media, "_transcode_window", fake_transcode)

    # untouched: the fast path is correct and must survive
    data, mime = media.video_clip_bytes(str(clip))
    assert data == b"\x00\x00\x00 ftypmp42 original" and mime == "video/mp4"
    assert not seen

    for kwargs in ({"rotate": 90}, {"flip": "h"}, {"rotate": 90, "flip": "v"}):
        seen.clear()
        data, mime = media.video_clip_bytes(str(clip), **kwargs)
        assert data == b"re-encoded", f"{kwargs} must force a re-encode"
        assert mime == "video/mp4"

    # a full turn normalises to 0 upstream, so it is not an orientation at all
    seen.clear()
    data, _ = media.video_clip_bytes(str(clip), rotate=0)
    assert data == b"\x00\x00\x00 ftypmp42 original"
    assert not seen


def test_a_free_angle_turns_the_same_way_the_quarter_turns_do():
    """Perturbation-driven: negating the angle inside img.rotate left every other test
    green, because the quarter turns go through transpose and never reach it. PIL rotates
    COUNTER-clockwise and this node's angles are clockwise, so the negation is load
    bearing and only this exercises it.

    A filled quadrant rather than a single pixel, because a free angle resamples and a
    lone bright pixel would be smeared into its neighbours."""
    import numpy as np
    from PIL import Image

    img = Image.new("RGB", (40, 40), (0, 0, 0))
    for x in range(20):
        for y in range(20):
            img.putpixel((x, y), (255, 255, 255))  # white = TOP-LEFT quadrant

    out = np.array(media._orient_pil(img, None, 89.9, expand=False).convert("L"))
    h, w = out.shape
    top_left = out[: h // 2, : w // 2].mean()
    top_right = out[: h // 2, w // 2:].mean()
    # Turned clockwise, the top-left quadrant swings to the TOP-RIGHT.
    assert top_right > top_left, (
        f"top-right {top_right:.1f} should hold the white block after a clockwise turn, "
        f"top-left has {top_left:.1f} - the angle is going the wrong way"
    )


def test_the_thumbnail_applies_the_exif_rotation_like_the_socket_does(tmp_path):
    """The tile and the crop editor both render through thumbnail_png, and the sockets
    render through load_image. If only one of them honours EXIF orientation, a phone photo
    shows one frame and emits another - so a crop drawn on the tile selects a region the
    user never saw. That is the exact mismatch the orient-then-crop order exists to
    prevent, one step earlier in the pipeline."""
    from PIL import Image
    import io as _io

    # 40x20 landscape pixels tagged orientation 6 = "rotate 90 CW to display" -> 20x40.
    src = tmp_path / "phone.jpg"
    img = Image.new("RGB", (40, 20), (10, 20, 30))
    exif = img.getexif()
    exif[274] = 6
    img.save(src, "JPEG", exif=exif)

    with Image.open(_io.BytesIO(media.thumbnail_png(str(src), max_edge=512))) as thumb:
        assert thumb.size == (20, 40), (
            "the thumbnail must be upright, matching what load_image puts on the socket"
        )

# ---- a crop that rounds down to one pixel --------------------------------------------


def test_widen_to_grows_a_span_to_the_minimum():
    """h264 needs an even count on each axis, and the encoder rounds down to get one - so
    a 1px axis becomes 0, which is an empty array av rejects with an IndexError from
    inside the encoder. The reference that produces it is one the node otherwise accepts:
    _crop_box guarantees a pixel, validate_crop only checks the fraction is positive, and
    the socket path emits it happily. Widening beats crashing."""
    assert media._widen_to(5, 6, 2, 100) == (5, 7)
    assert media._widen_to(0, 1, 2, 100) == (0, 2)
    # against the right edge it has to grow LEFTWARDS instead
    assert media._widen_to(99, 100, 2, 100) == (98, 100)


def test_widen_to_leaves_a_wide_enough_span_exactly_alone():
    assert media._widen_to(10, 40, 2, 100) == (10, 40)
    assert media._widen_to(0, 100, 2, 100) == (0, 100)


def test_widen_to_cannot_exceed_the_frame():
    """A source smaller than the minimum cannot be widened into one - the whole frame is
    the best available answer, and it must not return an out-of-bounds slice."""
    assert media._widen_to(0, 1, 2, 1) == (0, 1)
    assert media._widen_to(0, 0, 2, 0) == (0, 0)


@pytest.mark.parametrize("frac", [0.001, 0.004, 0.0001])
def test_a_sub_pixel_crop_still_leaves_two_pixels_to_encode(frac):
    """End to end through the real _crop_box: the fraction a hand-edited references_json
    can carry, then the widening the encoder needs."""
    width = height = 640
    left, top, right, bottom = media._crop_box([0.5, 0.5, frac, frac], width, height)
    left, right = media._widen_to(left, right, 2, width)
    top, bottom = media._widen_to(top, bottom, 2, height)
    assert right - left >= 2 and bottom - top >= 2
    assert 0 <= left < right <= width
    assert 0 <= top < bottom <= height
    # and what the encoder derives from it is even and non-zero
    assert (right - left) - ((right - left) % 2) >= 2
    assert (bottom - top) - ((bottom - top) % 2) >= 2


# ---- a stream that does not start at pts 0 -------------------------------------------


class _FakeStreamAt:
    def __init__(self, start_time, time_base):
        from fractions import Fraction

        self.start_time = start_time
        self.time_base = Fraction(1, time_base)


def test_stream_origin_reads_a_shifted_start():
    """A stream copy out of a transport stream keeps its original timestamps, so the video
    stream starts at a non-zero pts. A trim means seconds from the start of THIS clip -
    which is what the frame-index path in _decode_video takes it to mean - so the
    pts-based paths have to subtract that origin instead of reading raw stamps."""
    seconds, pts = media._stream_origin(_FakeStreamAt(12288, 12288))
    assert seconds == 1.0
    assert pts == 12288


def test_stream_origin_of_an_ordinary_stream_is_zero():
    seconds, pts = media._stream_origin(_FakeStreamAt(0, 12288))
    assert (seconds, pts) == (0.0, 0)


def test_stream_origin_tolerates_a_stream_that_declares_none():
    """Not every container reports one, and a missing start is not an error - it is zero."""
    assert media._stream_origin(_FakeStreamAt(None, 12288)) == (0.0, 0)


class _NoStartTime:
    def __init__(self):
        from fractions import Fraction

        self.time_base = Fraction(1, 1000)


def test_stream_origin_tolerates_a_stream_without_the_attribute_at_all():
    assert media._stream_origin(_NoStartTime()) == (0.0, 0)


def test_thumbnail_png_counts_at_seconds_from_the_clips_own_start(monkeypatch):
    """The behavioural half of the origin fix.

    A stream copy out of a transport stream keeps its original timestamps, so the video
    stream starts at a non-zero pts. `at_seconds` means "into this clip", not "at this
    presentation stamp" - which is what the frame-index path in _decode_video already
    takes it to mean. Reading raw stamps made 0.5s land on the clip's FIRST frame.
    """
    from io import BytesIO

    from PIL import Image

    tb = 90000
    # the clip runs 1.0..2.5s in absolute stream time; its own start is 1.0s
    fake = _FakeAv(frame_times=[1.0, 1.5, 2.0, 2.5], time_base=tb)
    fake.stream.start_time = int(1.0 * tb)
    _install_fake_av(monkeypatch, fake)

    out = media.thumbnail_png("clip.mp4", at_seconds=0.5)

    # 0.5s INTO the clip is absolute 1.5s
    assert fake.seeks == [(int(1.5 * tb), fake.stream)]
    with Image.open(BytesIO(out)) as thumb:
        # frame_times[1] is the second colour in _FakeAv's cycle: green
        assert thumb.getpixel((5, 5)) == (0, 128, 0)


def test_thumbnail_png_is_unchanged_when_the_stream_starts_at_zero(monkeypatch):
    """The ordinary case must not have moved: an origin of zero subtracts nothing."""
    from io import BytesIO

    from PIL import Image

    fake = _FakeAv(frame_times=[0.0, 1.0, 2.0, 3.0], time_base=90000)
    fake.stream.start_time = 0
    _install_fake_av(monkeypatch, fake)

    out = media.thumbnail_png("clip.mp4", at_seconds=2.0)

    assert fake.seeks == [(180000, fake.stream)]
    with Image.open(BytesIO(out)) as thumb:
        assert thumb.getpixel((5, 5)) == (0, 0, 255)

# ---- a trim that runs past the end of the audio --------------------------------------


def _audio(seconds, sr=1000):
    import numpy as np

    return {"waveform": np.zeros((1, 1, int(seconds * sr)), dtype=np.float32),
            "sample_rate": sr}


def test_a_trim_entirely_past_the_end_is_refused():
    """It used to return a ZERO-SAMPLE waveform in silence, and nothing downstream
    noticed: the socket emits it, the timed log records ok=true, and the wav encoder
    happily produces a valid empty file for the model. The video side raises and names the
    file for the same situation - this now matches it."""
    with pytest.raises(ValueError) as excinfo:
        media._slice_audio(_audio(1.0), [2.0, 3.0], "vo.wav")
    message = str(excinfo.value)
    assert "vo.wav" in message, "the message has to name the file"
    assert "2.00-3.00" in message, "and the window that was asked for"
    assert "1.00" in message, "and the length it actually has"


def test_a_trim_that_only_overruns_the_end_is_honoured():
    """There are real samples in that window, so refusing would be worse than clipping.
    What it must not do is clip in silence - see the log assertion below."""
    out = media._slice_audio(_audio(1.0), [0.5, 5.0], "vo.wav")
    assert out["waveform"].shape[-1] == 500


def test_clipping_the_end_is_recorded(caplog):
    """"My trim said 5s and I got 0.5" is otherwise indistinguishable from a decoder
    problem, and this is the only place that knows which it was."""
    import logging

    with caplog.at_level(logging.INFO, logger="MiniMaxRefPack"):
        media._slice_audio(_audio(1.0), [0.5, 5.0], "vo.wav")
    assert any("audio_trim_clipped" in r.getMessage() for r in caplog.records), (
        "a clipped trim must leave a trace"
    )


@pytest.mark.parametrize("trim,expected", [
    ([0.0, 1.0], 1000),      # exactly the clip
    ([0.25, 0.75], 500),     # well inside
    ([0.0, 0.001], 1),       # the smallest real window
])
def test_a_trim_inside_the_clip_is_unaffected(trim, expected):
    """The guard must not have become a refusal of anything near the boundary."""
    assert media._slice_audio(_audio(1.0), trim, "vo.wav")["waveform"].shape[-1] == expected


def test_a_waveform_that_cannot_report_its_length_is_left_alone():
    """Not every stand-in for a tensor exposes .shape - the loaders are swappable, and a
    guard that cannot measure must not start refusing valid work."""
    class Opaque:
        def __getitem__(self, key):
            return "sliced"

    out = media._slice_audio({"waveform": Opaque(), "sample_rate": 1000}, [9.0, 10.0])
    assert out["waveform"] == "sliced"

# ---- a trim window that catches no frames --------------------------------------------


class _FakeTranscodeAv:
    """The av surface _transcode_window touches, for the case where no frame qualifies.

    Deliberately minimal: when every frame falls outside the window the encoder is never
    created and to_ndarray is never called, so none of that has to be faked. av is not
    installed here, same as everywhere else in this file.
    """

    def __init__(self, frame_times, time_base=90000, average_rate=24):
        from fractions import Fraction

        outer = self

        class FakeStream:
            pass

        stream = FakeStream()
        stream.time_base = Fraction(1, time_base)
        stream.average_rate = average_rate
        stream.guessed_rate = average_rate
        stream.start_time = 0
        self.stream = stream
        self.wrote = []

        class FakeFrame:
            def __init__(self, pts):
                self.pts = pts

            def to_ndarray(self, format=None):
                raise AssertionError("no frame should be decoded for an empty window")

        self.frames = [FakeFrame(int(x * time_base)) for x in frame_times]

        class FakeStreams:
            video = [stream]

        class FakeReader:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            @property
            def streams(self):
                return FakeStreams()

            def seek(self, offset, stream=None):
                pass

            def decode(self, stream):
                yield from outer.frames

        class FakeWriter:
            def add_stream(self, *a, **k):
                raise AssertionError("no stream should be added for an empty window")

            def mux(self, packet):
                outer.wrote.append(packet)

            def close(self):
                outer.closed = True

        self.closed = False
        self._reader = FakeReader()
        self._writer = FakeWriter()

    def open(self, target, mode="r", format=None):
        return self._writer if mode == "w" else self._reader


def test_a_trim_window_with_no_frames_is_refused(monkeypatch):
    """It returned an mp4 container with nothing in it - zero bytes of video - and that
    went to the model as a video part it cannot decode, logged as a success.

    load_video REFUSES the very same trim for the very same file. One half of the node
    complaining loudly while the other half silently sends nothing is the disagreement
    this apply-point design exists to prevent. Reachable without doing anything strange:
    a trim outliving a re-upload of a shorter file under the same name.
    """
    fake = _FakeTranscodeAv(frame_times=[0.0, 0.5, 1.0])
    _install_fake_av(monkeypatch, fake)

    with pytest.raises(ValueError) as excinfo:
        media._transcode_window("clip.mp4", None, [8.0, 9.0])

    message = str(excinfo.value)
    assert "clip.mp4" in message, "the message has to name the file"
    assert "8.00-9.00" in message, "and the window that found nothing"
    assert fake.closed, "the container is still closed on the way out"


def test_the_empty_window_message_mentions_the_likely_cause(monkeypatch):
    """The user did not type this trim against this file - it survived a re-upload. Saying
    so is the difference between a fixable message and a puzzling one."""
    fake = _FakeTranscodeAv(frame_times=[0.0, 0.5])
    _install_fake_av(monkeypatch, fake)

    with pytest.raises(ValueError, match="left over from a longer version"):
        media._transcode_window("clip.mp4", None, [30.0, 31.0])


# ---- the downscale cap --------------------------------------------------------------


def test_scaled_size_caps_the_long_edge_keeping_aspect():
    assert media.scaled_size(1920, 1080, 512) == (512, 288)
    assert media.scaled_size(1080, 1920, 512) == (288, 512)


def test_scaled_size_never_upscales_or_fires_when_off():
    assert media.scaled_size(400, 300, 512) == (400, 300)   # already fits
    assert media.scaled_size(1920, 1080, 0) == (1920, 1080)  # cap off
    assert media.scaled_size(1920, 1080, None) == (1920, 1080)


def test_load_video_downscales_the_frames_when_a_cap_is_given(monkeypatch):
    """The knob that keeps a heavy clip from OOM-ing H3: every frame's tokens ride every
    sampling step, so the long edge is capped AFTER the crop. torch is not importable
    here, so the actual interpolate is behind _frame_downscale_fn; the test stubs it to
    prove it is invoked with the right target size."""
    import numpy as np

    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_numpy(24, 24, 1080, 1920))  # 1920x1080 frames
    called = {}

    def fake_resize(frames, new_h, new_w):
        called["size"] = (new_h, new_w)
        return np.zeros((frames.shape[0], new_h, new_w, 3), dtype=np.float32)

    monkeypatch.setattr(media, "_frame_downscale_fn", lambda: fake_resize)

    frames, _ = media.load_video("clip.mp4", max_edge=512)
    assert called["size"] == (288, 512), "1920x1080 capped at 512 -> 512x288 (h, w)"
    assert frames.shape == (24, 288, 512, 3)


def test_load_video_scales_after_the_crop(monkeypatch):
    """The cap measures the CROPPED region, not the source - the same order load_image
    uses, so the socket and the tile thumbnail agree."""
    import numpy as np

    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_numpy(24, 24, 1080, 1920))
    called = {}
    monkeypatch.setattr(media, "_frame_downscale_fn",
                        lambda: (lambda frames, new_h, new_w: (
                            called.__setitem__("size", (new_h, new_w))
                            or np.zeros((frames.shape[0], new_h, new_w, 3), dtype=np.float32))))

    # crop to the left half: 1920x1080 -> 960x1080, then cap 512 on the long edge (1080)
    media.load_video("clip.mp4", crop=[0.0, 0.0, 0.5, 1.0], max_edge=512)
    assert called["size"] == (512, round(960 * 512 / 1080))


def test_load_video_without_a_cap_never_touches_the_downscaler(monkeypatch):
    import numpy as np

    monkeypatch.setattr(media, "_video_from_file_cls",
                        lambda: _fake_video_numpy(24, 24, 1080, 1920))

    def explode():
        raise AssertionError("_frame_downscale_fn must not be called without a cap")

    monkeypatch.setattr(media, "_frame_downscale_fn", explode)
    frames, _ = media.load_video("clip.mp4")   # no max_edge
    assert frames.shape == (24, 1080, 1920, 3)
