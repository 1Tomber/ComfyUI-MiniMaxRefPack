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

import math
import mimetypes
import os

from . import logs


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


def _orient_pil(img, flip, rotate, expand: bool = True):
    """Apply flip then rotation to a PIL image. Returns it unchanged when both are unset.

    ORDER IS THE CONTRACT: flip, then rotate, then (at the call site) crop. The crop rect
    is expressed in the frame the editor drew, and the editor draws the oriented frame, so
    cropping first would select a different region than the user boxed. semmlerino hit
    exactly this on a portrait phone clip and wrote it down: the preview showed one region
    and the node emitted another.

    Quarter turns go through Image.transpose, which is lossless and needs no resampling.
    Everything else resamples once, filling anything outside the source with BLACK - the
    same thing MiniMax will see as absent frame, and a far better default than the white
    PIL would give.
    """
    if not flip and not rotate:
        return img

    from PIL import Image

    if flip:
        if "h" in flip:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if "v" in flip:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if not rotate:
        return img
    turns = _quarter_turns(rotate)
    if turns is not None:
        if turns:
            img = img.transpose(
                (Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180,
                 Image.Transpose.ROTATE_90)[turns - 1]
            )
        return img
    # PIL rotates COUNTER-clockwise and `rotate` is clockwise, so it is negated here -
    # once, at the single place the angle is applied.
    return img.rotate(-float(rotate), expand=bool(expand), resample=Image.BICUBIC,
                      fillcolor=(0, 0, 0))


def _quarter_turns(rotate) -> int | None:
    """0/1/2/3 quarter turns clockwise, or None when the angle is not a multiple of 90.

    The tolerance is float noise from a UI that rounds, nothing more: a slider parked on
    89.9 is a free angle and must resample, or the result would not match the preview.
    """
    if not rotate:
        return 0
    value = float(rotate) % 360.0
    nearest = round(value / 90.0)
    if abs(value - nearest * 90.0) > 1e-6:
        return None
    return int(nearest) % 4


def _orient_array(arr, flip, rotate, expand: bool = True):
    """The same transform for a decoded video frame (H, W, 3 uint8).

    Quarter turns use numpy so a clip does not pay a PIL round trip per frame; a free
    angle has to resample, so it borrows _orient_pil.
    """
    if not flip and not rotate:
        return arr

    import numpy as np

    if flip:
        if "h" in flip:
            arr = arr[:, ::-1]
        if "v" in flip:
            arr = arr[::-1]
    if not rotate:
        return np.ascontiguousarray(arr)
    turns = _quarter_turns(rotate)
    if turns is not None:
        # np.rot90 turns COUNTER-clockwise; negate for clockwise.
        return np.ascontiguousarray(np.rot90(arr, -turns))
    from PIL import Image

    out = _orient_pil(Image.fromarray(np.ascontiguousarray(arr)), None, rotate, expand)
    return np.ascontiguousarray(np.array(out))


def _orient_tensor(frames, flip, rotate, expand: bool = True):
    """The same transform for a decoded frame batch, (N, H, W, C) float in 0..1.

    Quarter turns and flips are strides - torch does them without touching pixel data, so
    a whole clip costs nothing. A free angle has to resample every frame through PIL,
    which is why the editor snaps to the quarter turns unless the user asks otherwise.
    """
    # Nothing to do is the common case by far, and it must cost nothing - including not
    # importing torch, which this module is careful never to require unless a tensor is
    # actually being touched.
    if not flip and not rotate:
        return frames

    import torch

    if flip:
        if "h" in flip:
            frames = torch.flip(frames, dims=[2])
        if "v" in flip:
            frames = torch.flip(frames, dims=[1])
    if not rotate:
        return frames.contiguous()
    turns = _quarter_turns(rotate)
    if turns is not None:
        # torch.rot90 turns COUNTER-clockwise over (H, W); negate for clockwise.
        return torch.rot90(frames, -turns, dims=(1, 2)).contiguous() if turns else frames.contiguous()

    import numpy as np

    src = (frames.detach().cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    out = np.stack([_orient_array(f, None, rotate, expand) for f in src])
    return torch.from_numpy(out.astype("float32") / 255.0)


def _crop_box(crop, width: int, height: int) -> tuple[int, int, int, int]:
    """Fraction rect [x, y, w, h] -> integer (left, top, right, bottom) pixel box.

    THE one fraction->pixel rule - the loaders and the thumbnails all come through
    here, so a tile's thumb and the emitted tensor can never disagree. Each edge is
    floor(fraction * dimension + 0.5): half-up, not Python's round(), whose
    round-half-to-even would move an edge by a pixel depending on parity. Then clamp
    to the frame so a rect that rounds past an edge still keeps at least one pixel
    on each axis - a zero-width crop can never come out of here.
    """
    x, y, w, h = crop
    left = min(max(int(math.floor(x * width + 0.5)), 0), width - 1)
    top = min(max(int(math.floor(y * height + 0.5)), 0), height - 1)
    right = min(max(int(math.floor((x + w) * width + 0.5)), left + 1), width)
    bottom = min(max(int(math.floor((y + h) * height + 0.5)), top + 1), height)
    return left, top, right, bottom


def _stream_origin(stream):
    """Where this stream's timeline actually begins: (seconds, pts units).

    A trim means "seconds from the start of THIS clip", which is what the frame-index
    path in _decode_video takes it to mean. But a container's video stream does not have
    to start at pts 0 - a stream copy out of a transport stream, or any remux that
    preserves timestamps, leaves start_time non-zero - and the pts-based paths were
    reading raw presentation stamps as though they were clip-relative seconds.

    The same trim then picked different frames depending on which path ran: the sockets
    got one window from _decode_video and the VLM got another from _transcode_window. A
    near-start trim on a shifted stream selected nothing at all, so the model was handed
    an empty clip while the sockets emitted real frames - the two halves describing
    different footage, with nothing to show anyone that they had diverged.
    """
    start_pts = getattr(stream, "start_time", None)
    if start_pts is None:
        return 0.0, 0
    try:
        return float(start_pts * stream.time_base), int(start_pts)
    except (TypeError, ValueError):
        return 0.0, 0


def _widen_to(lo: int, hi: int, minimum: int, limit: int) -> tuple[int, int]:
    """Grow the half-open span [lo, hi) to at least `minimum`, staying inside 0..limit."""
    if limit <= minimum:
        return 0, limit
    if hi - lo >= minimum:
        return lo, hi
    hi = min(limit, lo + minimum)
    return max(0, hi - minimum), hi


def _slice_audio(audio: dict, trim, path: str = "") -> dict:
    """Slice a {"waveform","sample_rate"} dict to [start, end) seconds. Returns a NEW
    dict - callers may still hold the unsliced original.

    Bounds-checked, because the same situation on the video side raises and names the file
    and the window (see load_video's minimum-frames guard) while this silently returned a
    ZERO-SAMPLE waveform. Nothing downstream notices: the socket emits it, the timed log
    records ok=true, and _waveform_to_wav_b64 happily encodes a valid empty wav for the
    model. The user is told nothing at all, about audio that is simply not there.
    """
    sr = audio["sample_rate"]
    start, end = trim
    waveform = audio["waveform"]
    shape = getattr(waveform, "shape", None)
    total = int(shape[-1]) if shape else None
    length = (total / sr) if (total is not None and sr) else None

    if length is not None and start >= length:
        raise ValueError(
            f"reference audio {path!r} trimmed to {start:.2f}-{end:.2f}s starts past the "
            f"end of the clip ({length:.2f}s) - the trim would emit no audio at all"
        )
    if length is not None and end > length + 1e-9:
        # A window that merely overruns the end still has real samples in it, so it is
        # honoured rather than refused - but not in silence, because "my trim said 6s and
        # I got 2" is otherwise indistinguishable from a decoder problem.
        logs.log("audio_trim_clipped", file=os.path.basename(path) if path else None,
                 requested_end=round(end, 3), clip_seconds=round(length, 3))

    return {"waveform": waveform[..., int(start * sr):int(end * sr)], "sample_rate": sr}


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


def load_image(path: str, crop=None, max_edge: int = 0, flip=None, rotate=None,
               rotate_expand: bool = True):
    """[1, H, W, 3] float32 in 0..1. Plain PIL decode - references are single stills
    (not animated), so we skip the ImageSequence handling CU/nodes.py:1734 LoadImage
    needs for animated webp.

    `crop` is a [x, y, w, h] fraction rect (refs.Reference.crop), applied AFTER the
    EXIF transpose so the fractions refer to the image as the editor showed it.

    `max_edge` caps the LONG edge (0 = off), applied AFTER the crop so it measures what
    is actually emitted. Core sizes reference images off the SHORT edge - `scale =
    min(1.0, REF_IMAGE_SHORT_EDGE / min(w, h))` at CU/comfy_extras/nodes_minimax_h3.py:301
    with REF_IMAGE_SHORT_EDGE = 2048 (:29) - so at ref_image_size="max" a wide sheet
    reaches the VAE enormous: 5000x2550 lands at 4000x2048, ~32k reference tokens that
    ride every sampling step. thumbnail() shrinks in place and never enlarges, so a
    reference already under the cap is emitted untouched.
    """
    import os

    import numpy as np
    import torch
    from PIL import Image, ImageOps

    with logs.timed("load_image", file=os.path.basename(path), crop=crop,
                    cap=max_edge or None) as fields:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        fields["src"] = f"{img.width}x{img.height}"
        # EXIF transpose -> flip -> rotate -> crop -> max_edge. The crop rect is drawn on
        # the ORIENTED frame in the editor, so it has to be applied to the oriented frame
        # here or it selects a different region than the user boxed.
        img = _orient_pil(img, flip, rotate, rotate_expand)
        if crop is not None:
            img = img.crop(_crop_box(crop, img.width, img.height))
        if max_edge:
            img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        fields["out"] = f"{img.width}x{img.height}"
        arr = np.array(img).astype(np.float32) / 255.0
        return torch.from_numpy(arr)[None, ...]


def load_audio(path: str, trim=None) -> dict:
    """{"waveform": [1, C, L], "sample_rate": int} - CU/comfy_extras/nodes_audio.py:333
    returns (waveform, sample_rate); the AUDIO socket shape wraps it with a batch dim
    (:380-381). `trim` = [start, end] seconds, sliced exactly the way a video's
    soundtrack is (_slice_audio)."""
    import os

    with logs.timed("load_audio", file=os.path.basename(path), trim=trim) as fields:
        waveform, sample_rate = _audio_load_fn()(path)
        audio = {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}
        if trim is not None:
            audio = _slice_audio(audio, trim, path)
        fields["sample_rate"] = sample_rate
        return audio


def load_video(path: str, target_fps: int = 24, crop=None, trim=None, flip=None,
               rotate=None, rotate_expand: bool = True):
    """(frames [N,H,W,3] resampled to target_fps, audio dict or None).

    CU/comfy_api/latest/_input_impl/video_types.py:118 VideoFromFile.get_components().
    `.audio` is already built as {"waveform","sample_rate"} (video_types.py:445-448),
    the same shape core AUDIO sockets use, so it's passed through unchanged.

    `trim` = [start, end] seconds. Cut on SOURCE frames first - a source frame is kept
    when its timestamp i/src_fps lands in [start, end) - then the usual resample runs
    on that span, so the output preserves the span's real duration. The soundtrack is
    sliced to the SAME window or it would drift out of sync with its frames.

    `crop` = [x, y, w, h] fractions, applied to the resampled frames (_crop_box).

    MiniMax needs >=5 frames per reference video (CU/comfy_extras/nodes_minimax_h3.py:250)
    - the check fires on the RESULT: a trim too short to survive it raises, naming the
    file and the requested window, never silently clamping or padding.
    """
    import os

    with logs.timed("load_video", file=os.path.basename(path), crop=crop, trim=trim) as fields:
        return _decode_video(path, target_fps, crop, trim, fields, flip, rotate,
                             rotate_expand)


def _decode_video(path, target_fps, crop, trim, fields, flip=None, rotate=None,
                  rotate_expand=True):
    """The body of load_video. Split out only so the timing/logging wrapper above stays
    a plain `with` block instead of wrapping 30 lines."""
    components = _video_from_file_cls()(path).get_components()
    frames = components.images
    n_src = frames.shape[0]
    src_fps = float(components.frame_rate)
    audio = components.audio
    fields["src_frames"] = n_src
    fields["fps"] = src_fps

    first = 0
    n_sel = n_src
    if trim is not None:
        start, end = trim
        # The 1e-9 keeps a frame that lands exactly on a boundary from flipping sides
        # over float noise (e.g. end=2.0 at 24fps must exclude frame 48, include 47).
        first = max(0, math.ceil(start * src_fps - 1e-9))
        n_sel = max(0, min(n_src, math.ceil(end * src_fps - 1e-9)) - first)

    indices = resample_indices(n_sel, src_fps, target_fps)
    if len(indices) < 5:
        duration = (n_src / src_fps) if src_fps else 0.0
        window = f" trimmed to {trim[0]:.2f}-{trim[1]:.2f}s" if trim is not None else ""
        raise ValueError(
            f"reference video {path!r}{window} has only {len(indices)} frame(s) at {target_fps}fps "
            f"(source: {n_src} frames, {duration:.2f}s) - MiniMax H3 needs at least 5"
        )
    out = frames[[first + i for i in indices]]
    # Orient before cropping, for the reason in _orient_pil: the rect was drawn on the
    # oriented frame.
    out = _orient_tensor(out, flip, rotate, rotate_expand)
    if crop is not None:
        left, top, right, bottom = _crop_box(crop, out.shape[2], out.shape[1])
        out = out[:, top:bottom, left:right, :]
    if trim is not None and audio is not None:
        audio = _slice_audio(audio, trim, path)
    fields["frames"] = len(indices)
    fields["audio"] = audio is not None
    return out, audio


# Containers OpenRouter forwards as-is for a `video_url` part. Anything else is
# re-encoded to mp4 rather than gambling on the provider accepting it.
VLM_VIDEO_MIMES = {
    ".mp4": "video/mp4",
    ".mov": "video/mov",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}


def video_clip_bytes(path: str, crop=None, trim=None, flip=None, rotate=None,
                     rotate_expand: bool = True) -> tuple[bytes, str]:
    """(bytes, mime) of the WHOLE clip, for the VLM's `video_url` part.

    The VLM reads video natively - one 10s 1080p clip cost 660 video tokens plus 250
    audio tokens on google/gemini-3-flash-preview - so it gets the clip, not stills.

    Untouched references take the fast path: the file's own bytes, nothing decoded,
    soundtrack included. A crop or a trim means the file no longer matches what the
    node's sockets emit, so that window is re-encoded (video only - its soundtrack is
    still sent as its own labelled part, exactly as before).
    """
    import os

    with logs.timed("video_bytes", file=os.path.basename(path), crop=crop, trim=trim) as fields:
        mime = VLM_VIDEO_MIMES.get(os.path.splitext(path)[1].lower())
        # An ORIENTED clip must never take the raw-bytes fast path. The sockets emit the
        # rotated frames, so sending the untouched file would show the VLM a different
        # video from the one being generated with - silently, since nothing downstream
        # compares them.
        oriented = bool(flip) or bool(rotate)
        if crop is None and trim is None and not oriented and mime is not None:
            with open(path, "rb") as f:
                data = f.read()
            fields["mode"] = "file"
            fields["bytes"] = len(data)
            return data, mime
        data = _transcode_window(path, crop, trim, flip, rotate, rotate_expand)
        fields["mode"] = "re-encoded"
        fields["bytes"] = len(data)
        return data, "video/mp4"


def _transcode_window(path: str, crop, trim, flip=None, rotate=None,
                      rotate_expand: bool = True) -> bytes:
    """The cropped/trimmed window as an in-memory mp4, video only.

    Decodes only the window: `container.seek` lands on the keyframe at or before the
    in-point (the same seek-then-skip-to-pts core uses,
    CU/comfy_api/latest/_input_impl/video_types.py:316-325) and decoding stops at the
    out-point, so a 4s window out of a 10 minute file costs 4s of decoding.
    """
    import io

    import av
    import numpy as np

    out_buf = io.BytesIO()
    start, end = (trim if trim is not None else (0.0, None))

    with av.open(path) as src:
        stream = src.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate or 24
        origin_seconds, origin_pts = _stream_origin(stream)
        if start:
            src.seek(int(start / stream.time_base) + origin_pts, stream=stream)

        out = av.open(out_buf, "w", format="mp4")
        enc = None
        try:
            for frame in src.decode(stream):
                # Clip-relative, so a stream that does not start at pts 0 still
                # trims from its own beginning - see _stream_origin.
                t = (float(frame.pts * stream.time_base) - origin_seconds
                     if frame.pts is not None else 0.0)
                if t < start - 1e-9:
                    continue
                if end is not None and t >= end - 1e-9:
                    break
                arr = frame.to_ndarray(format="rgb24")
                arr = _orient_array(arr, flip, rotate, rotate_expand)
                if crop is not None:
                    left, top, right, bottom = _crop_box(crop, arr.shape[1], arr.shape[0])
                    # h264 needs an EVEN count on each axis, and the rounding below turns
                    # an odd 1 into 0 - an empty array that av rejects from inside the
                    # encoder with an IndexError, on a reference the node otherwise
                    # accepts: _crop_box guarantees one pixel, validate_crop only checks
                    # that the fraction is positive, and the socket path emits it happily.
                    # So the box is widened to two pixels here rather than crashing.
                    left, right = _widen_to(left, right, 2, arr.shape[1])
                    top, bottom = _widen_to(top, bottom, 2, arr.shape[0])
                    arr = arr[top:bottom, left:right]
                if enc is None:
                    enc = out.add_stream("libx264", rate=rate)
                    # h264 needs even dimensions; a crop can land on an odd pixel count
                    enc.width = arr.shape[1] - (arr.shape[1] % 2)
                    enc.height = arr.shape[0] - (arr.shape[0] % 2)
                    enc.pix_fmt = "yuv420p"
                arr = np.ascontiguousarray(arr[: enc.height, : enc.width])
                for packet in enc.encode(av.VideoFrame.from_ndarray(arr, format="rgb24")):
                    out.mux(packet)
            if enc is not None:
                for packet in enc.encode():
                    out.mux(packet)
        finally:
            out.close()

    return out_buf.getvalue()


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


def thumbnail_png(path: str, max_edge: int = 256, crop=None, at_seconds=None, flip=None,
                  rotate=None, rotate_expand: bool = True) -> bytes:
    """One frame for a video, downscaled full image for a still. `crop` (fraction
    rect) is applied before the downscale, through the same _crop_box rule the
    loaders use, so the tile always previews exactly what the pack will emit.

    `at_seconds` picks the video frame: an indexed seek in the stream's own time
    base, then decode forward until the target pts - the same seek-then-skip-to-pts
    approach core uses (CU/comfy_api/latest/_input_impl/video_types.py:316-325). That
    decodes at most the GOP between the landed keyframe and the target, never the
    whole clip; without it, only frame 0 is decoded, exactly as before. A time past
    the end keeps the last decodable frame rather than failing the tile.
    """
    import io as _io

    from PIL import Image, ImageOps

    if _guess_kind(path) == "video":
        import av

        with av.open(path) as container:
            stream = container.streams.video[0]
            if at_seconds:
                # Offset by the stream's own start, so `at_seconds` counts from
                # the beginning of the clip rather than from pts 0.
                origin_seconds, origin_pts = _stream_origin(stream)
                target_pts = int(at_seconds / stream.time_base) + origin_pts
                container.seek(target_pts, stream=stream)
                frame = None
                for frame in container.decode(stream):
                    if frame.pts is not None and frame.pts >= target_pts:
                        break
                if frame is None:
                    raise ValueError(f"could not decode a frame of {path!r} at {at_seconds}s")
            else:
                frame = next(container.decode(stream))
        img = frame.to_image()
    else:
        # exif_transpose FIRST, exactly as load_image does. Without it a phone photo's
        # tile showed the un-rotated sensor frame while the socket emitted the upright
        # one, so a crop drawn on the tile selected a different region than it displayed -
        # the very mismatch the order below is written to prevent, one step earlier.
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")

    # Same order as every other apply point: orient, then crop. This is the one the user
    # SEES - the tile and the editor's frame both come through here - so a mismatch with
    # the socket path would show one thing and emit another.
    img = _orient_pil(img, flip, rotate, rotate_expand)
    if crop is not None:
        img = img.crop(_crop_box(crop, img.width, img.height))
    img.thumbnail((max_edge, max_edge))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
