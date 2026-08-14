"""File -> ComfyUI tensor/audio loading, 24fps resample, and probe metadata.

FROZEN CONTRACT: nodes.py, routes.py and prompt.py all import these
five names directly. Only `resample_indices` is pure/torch-free by requirement (it must
be exhaustively unit-testable); everything else lazy-imports torch/av/PIL/comfy so a
plain `pytest tests/test_media.py` runs without ComfyUI installed (folder_paths,
comfy_api, comfy_extras are not importable outside a ComfyUI process - verified: a bare
`import folder_paths` in this venv raises ModuleNotFoundError).

The two `_video_from_file_cls` / `_audio_load_fn` indirections below exist so tests can
monkeypatch the ComfyUI-side decoder without comfy_api/comfy_extras being installed at
all - they patch the accessor, not the (unimportable) real module.
"""

from __future__ import annotations

import mimetypes


def resample_indices(n_src: int, src_fps: float, target_fps: int = 24) -> list[int]:
    """Which source frame each output frame reads from, resampling to `target_fps`.

    Preserves the clip's real-world duration: output frame count is
    round(n_src * target_fps / src_fps), not n_src. Output frame i reads source frame
    round(i * src_fps / target_fps), clamped to [0, n_src-1]. Core never touches
    framerate (CU/comfy_extras/nodes_minimax_h3.py:246-252 only resizes/trims by frame
    count) - this resample is ours alone.
    """
    if n_src <= 0:
        return []
    if src_fps <= 0:
        raise ValueError(f"source fps must be positive, got {src_fps!r}")
    n_out = max(1, round(n_src * target_fps / src_fps))
    return [min(max(round(i * src_fps / target_fps), 0), n_src - 1) for i in range(n_out)]


def _guess_kind(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("video/"):
        return "video"
    if mime and mime.startswith("audio/"):
        return "audio"
    return "image"


def _video_from_file_cls():
    """Indirection point so tests can substitute a fake without comfy_api installed."""
    from comfy_api.latest._input_impl.video_types import VideoFromFile

    return VideoFromFile


def _audio_load_fn():
    """Indirection point so tests can substitute a fake without comfy_extras installed."""
    from comfy_extras.nodes_audio import load as _load

    return _load


def load_image(path: str):
    """[1, H, W, 3] float32 in 0..1. Plain PIL decode - references are single stills
    (not animated), so we skip the ImageSequence handling CU/nodes.py:1734 LoadImage
    needs for animated webp."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


def load_audio(path: str) -> dict:
    """{"waveform": [1, C, L], "sample_rate": int} - CU/comfy_extras/nodes_audio.py:333
    returns (waveform, sample_rate); the AUDIO socket shape wraps it with a batch dim
    (:380-381)."""
    waveform, sample_rate = _audio_load_fn()(path)
    return {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}


def load_video(path: str, target_fps: int = 24):
    """(frames [N,H,W,3] resampled to target_fps, audio dict or None).

    CU/comfy_api/latest/_input_impl/video_types.py:118 VideoFromFile.get_components().
    `.audio` is already built as {"waveform","sample_rate"} (video_types.py:445-448),
    the same shape core AUDIO sockets use, so it's passed through unchanged.

    MiniMax needs >=5 frames per reference video (CU/comfy_extras/nodes_minimax_h3.py:250)
    - if resampling lands under that, raise loudly naming the file and its duration
    rather than silently padding.
    """
    components = _video_from_file_cls()(path).get_components()
    frames = components.images
    n_src = frames.shape[0]
    src_fps = float(components.frame_rate)
    indices = resample_indices(n_src, src_fps, target_fps)
    if len(indices) < 5:
        duration = (n_src / src_fps) if src_fps else 0.0
        raise ValueError(
            f"reference video {path!r} has only {len(indices)} frame(s) at {target_fps}fps "
            f"(source: {n_src} frames, {duration:.2f}s) - MiniMax H3 needs at least 5"
        )
    return frames[indices], components.audio


def probe(path: str) -> dict:
    """{"kind","width","height","fps","duration","has_audio"} for the modal's reference rows."""
    kind = _guess_kind(path)

    if kind == "image":
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
        return {"kind": "image", "width": w, "height": h, "fps": None, "duration": None, "has_audio": False}

    if kind == "video":
        import av

        v = _video_from_file_cls()(path)
        w, h = v.get_dimensions()
        fps = float(v.get_frame_rate())
        duration = v.get_duration()
        with av.open(path) as container:
            has_audio = len(container.streams.audio) > 0
        return {"kind": "video", "width": w, "height": h, "fps": fps, "duration": duration, "has_audio": has_audio}

    # audio
    import av

    with av.open(path) as container:
        duration = float(container.duration / av.time_base) if container.duration else 0.0
    return {"kind": "audio", "width": None, "height": None, "fps": None, "duration": duration, "has_audio": True}


def thumbnail_png(path: str, max_edge: int = 256) -> bytes:
    """First frame for a video, downscaled full image for a still. Decodes only that one
    frame for video (never the whole clip)."""
    import io as _io

    from PIL import Image

    if _guess_kind(path) == "video":
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            frame = next(container.decode(stream))
        img = frame.to_image()
    else:
        img = Image.open(path).convert("RGB")

    img.thumbnail((max_edge, max_edge))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
