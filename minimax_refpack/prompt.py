"""OpenRouter client: turns a reference set + the user's steer into a MiniMax prompt.

FROZEN CONTRACT (signatures only):
    DEFAULT_MODEL, PromptError, available_models(), write_prompt(...)

Basic secure communication, nothing more: HTTPS, a bearer
header, a timeout, and the API key never appears in a log line, an exception message,
or a returned string. No retry framework, no keyring, no proxy layer.
"""

from __future__ import annotations

import base64
import io
import math
import os
import time
import wave

import numpy as np
import requests
from PIL import Image

from . import media

DEFAULT_MODEL = "google/gemini-3-flash-preview"

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# This runs inside INPUT_TYPES, so it must never hang the node list.
_MODELS_TIMEOUT = 5
_CHAT_TIMEOUT = 120

_REQUIRED_MODALITIES = {"text", "image", "audio", "video"}
_MODELS_CACHE_TTL = 3600

# Never invent extra ids here — this is the "the models endpoint is unreachable"
# emergency path, not a curated list. It only needs to be non-empty and headed
# by the default.
_FALLBACK_MODELS = [DEFAULT_MODEL]

_AUDIO_FORMATS = {".wav": "wav", ".mp3": "mp3"}

# OpenRouter normalises `reasoning` across providers and drops it for models that don't
# reason, so sending it unconditionally is safe. Omitting the field entirely is NOT the
# same as "off" - it means "provider default", which for Gemini 3 Flash came back with
# reasoning_tokens: 0 on every eval run.
DEFAULT_REASONING_EFFORT = "medium"
REASONING_EFFORTS = ("none", "low", "medium", "high")

_SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")
_REPLACEMENT_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt_replacement.md")

# Two registers, one per job. "standard" is the six-section Ref2VA writer; "replacement"
# swaps one thing in a reference video for the thing in a reference image and changes
# nothing else. They are separate FILES, not two halves of one prompt: the Ref2VA rules
# are ~22KB of hard formatting mandates and putting both in context produces hybrids.
MODES = ("auto", "standard", "replacement")
_PROMPT_PATHS = {"standard": _SYSTEM_PROMPT_PATH, "replacement": _REPLACEMENT_PROMPT_PATH}

# Free models were measured and rejected for this job (2026-08-15): gemma-4-31b:free
# returned HTTP 429 on every call, gpt-oss-20b:free returned no `content` field.
# flash-lite scored 6/6 at ~0.6s and ~$0.03 per 1000 calls.
CLASSIFIER_MODEL = "google/gemini-2.5-flash-lite"
_CLASSIFY_TIMEOUT = 20

# Measured, not guessed: eval/classify scores prompt variants over 20 simple and complex
# directions. Slimmer variants all failed the same way - a false REPLACEMENT on an
# ordinary scene write that happens to borrow a face or a camera move from a reference.
# What works is ENUMERATING what STANDARD covers (abstractions like "however much it
# borrows" scored 12/20) plus the two contrasting examples, which together took the last
# case: 20/20 on repeat runs vs 19/20 without the examples. ~141 tokens, ~$0.00004/call.
_CLASSIFY_SYSTEM = (
    "Classify the user's video-generation request. Answer with exactly one word.\n"
    "REPLACEMENT - they want a specific object, product or character in a reference VIDEO "
    "swapped for the thing shown in a reference IMAGE, with everything else in that video "
    "left exactly as it is.\n"
    "STANDARD - anything else: a new scene, a continuation of a clip, a described shot, or "
    "a scene built from reference imagery.\n"
    "e.g. putting the character from the image into the video is REPLACEMENT; "
    "taking the motion from the video to animate a character is STANDARD.\n"
    "Answer REPLACEMENT or STANDARD."
)

_models_cache: list[str] | None = None
_models_cache_at: float = 0.0


class PromptError(RuntimeError):
    """The OpenRouter call could not produce a prompt."""


# ---- model list --------------------------------------------------------------


def available_models() -> list[str]:
    """Models whose input_modalities is a superset of {text,image,audio,video}.

    Never raises and never hangs: short timeout, and on any failure (network,
    bad body, empty result) falls back to a hardcoded list headed by DEFAULT_MODEL.
    """
    global _models_cache, _models_cache_at
    now = time.time()
    if _models_cache is not None and (now - _models_cache_at) < _MODELS_CACHE_TTL:
        return _models_cache

    try:
        resp = requests.get(_MODELS_URL, timeout=_MODELS_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = sorted(
            {
                m["id"]
                for m in data
                if isinstance(m, dict)
                and m.get("id")
                and _REQUIRED_MODALITIES <= set((m.get("architecture") or {}).get("input_modalities") or [])
            }
        )
        if not models:
            raise ValueError("no models matched the modality filter")
        if DEFAULT_MODEL in models:
            models = [DEFAULT_MODEL] + [m for m in models if m != DEFAULT_MODEL]
        _models_cache = models
        _models_cache_at = now
        return models
    except Exception:
        return list(_FALLBACK_MODELS)


# ---- key resolution -----------------------------------------------------------


def _resolve_api_key(api_key: str) -> str:
    """UI box -> OPENROUTER_API_KEY -> LLM_KEY. Mirrors ComfyUI-Openrouter_node/node.py:42-67."""
    if api_key and api_key.strip():
        return api_key.strip()
    for env_name in ("OPENROUTER_API_KEY", "LLM_KEY"):
        env_key = os.environ.get(env_name)
        if env_key and env_key.strip():
            return env_key.strip()
    raise PromptError(
        "no OpenRouter API key: pass one in the node, or set OPENROUTER_API_KEY or LLM_KEY"
    )


# ---- tensor -> wire encoding ----------------------------------------------------


def _to_numpy(x):
    if hasattr(x, "detach"):
        x = x.detach()
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.asarray(x)


# Everything the VLM sees goes through _tensor_to_jpeg_b64 - the standalone stills AND
# the six frames sampled per video - so the cap lives in one place.
#
# It used to send native-resolution PNG, which for one 5000x2550 sheet plus one 10s 1080p
# clip meant ~24MB of base64 uploaded off the pod after ~3.7s of PNG compression. All of it
# wasted: the vision models tile images at 768px, so resolution above the cap is discarded
# on the far side. Measured on the same pair of references: 10.4MB -> 0.19MB for the sheet
# (1816ms -> 2ms), 2.3MB -> 0.30MB per frame. q90 loss is invisible to a model writing a
# scene description; it is not a delivery format.
VLM_IMAGE_LONG_EDGE = 1536
VLM_JPEG_QUALITY = 90


def _tensor_to_jpeg_b64(tensor) -> str:
    """A [H,W,3] or [1,H,W,3] float tensor in 0..1 -> base64 JPEG bytes, long edge capped
    at VLM_IMAGE_LONG_EDGE. Downscale only - a small reference is sent as it is."""
    arr = _to_numpy(tensor).astype(np.float32)
    if arr.ndim == 4:
        arr = arr[0]
    arr = np.clip(arr, 0.0, 1.0)
    arr = (arr * 255.0).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    # thumbnail() shrinks in place and never enlarges, which is the behaviour we want.
    img.thumbnail((VLM_IMAGE_LONG_EDGE, VLM_IMAGE_LONG_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=VLM_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _waveform_to_wav_b64(waveform, sample_rate: int) -> str:
    """A [1,C,T] or [C,T] float waveform in -1..1 -> base64 PCM16 WAV bytes."""
    arr = _to_numpy(waveform).astype(np.float32)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.ndim == 1:
        arr = arr[None, :]
    channels = arr.shape[0]
    pcm = np.clip(arr, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    interleaved = pcm.T.reshape(-1).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(interleaved)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _six_evenly_spaced(n: int) -> list[int]:
    if n <= 0:
        return []
    if n <= 6:
        return list(range(n))
    return [round(i * (n - 1) / 5) for i in range(6)]


def _image_part(jpeg_b64: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}}


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


# ---- payload assembly -----------------------------------------------------------


def _read_system_prompt(mode: str = "standard") -> str:
    path = _PROMPT_PATHS.get(mode, _SYSTEM_PROMPT_PATH)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def classify_mode(*, direction: str, references, api_key: str, model: str = "") -> str:
    """'replacement' or 'standard' for a direction. Never raises.

    Replacement is only reachable with at least one video AND one image to swap between,
    so the cheap structural check runs first and skips the call entirely when the set
    can't support it. Any failure - no key, network, rate limit, a model that answers
    something else - falls back to 'standard', which is the right register for almost
    every job and never produces a catastrophically wrong format.
    """
    counts = references.counts()
    if not counts.get("video") or not counts.get("image"):
        return "standard"
    if not (direction and direction.strip()):
        return "standard"
    try:
        key = _resolve_api_key(api_key)
        resp = requests.post(
            _CHAT_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model or CLASSIFIER_MODEL,
                "max_tokens": 6,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user", "content": direction},
                ],
            },
            timeout=_CLASSIFY_TIMEOUT,
        )
        if resp.status_code != 200:
            return "standard"
        answer = (resp.json()["choices"][0]["message"].get("content") or "").strip().upper()
    except Exception:
        return "standard"
    return "replacement" if "REPLACEMENT" in answer else "standard"


def _file_audio_part(path: str, tag: str, filename: str) -> dict:
    """A standalone audio reference: inline if OpenRouter takes the format, else a text
    line naming the tag, filename and duration. Never crashes on audio."""
    ext = os.path.splitext(filename)[1].lower()
    fmt = _AUDIO_FORMATS.get(ext)
    if fmt is None:
        return _text_part(f"{tag}: audio reference {filename} (format {ext or 'unknown'} not inlined)")
    try:
        with open(path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
    except Exception:
        return _text_part(f"{tag}: audio reference {filename} (could not be read)")


def _video_audio_part(audio: dict, audio_tag: str, filename: str) -> dict:
    """A video's soundtrack, as decoded PCM by media.load_video. Never crashes on audio."""
    try:
        waveform = audio["waveform"]
        sample_rate = audio["sample_rate"]
        b64 = _waveform_to_wav_b64(waveform, sample_rate)
        return {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}
    except Exception:
        return _text_part(f"{audio_tag}: soundtrack of {filename} (could not be encoded)")


def _target_format_lines(width: int, height: int, length_seconds: float) -> list[str]:
    """The TARGET FORMAT block's lines. 0/absent means unspecified: a line whose value
    is unknown is omitted rather than sent as a zero, and the frame line needs both
    dimensions (half a frame spec pins no aspect ratio)."""
    lines: list[str] = []
    if width and height:
        g = math.gcd(int(width), int(height))
        lines.append(f"frame: {int(width)} x {int(height)} (aspect ratio {int(width) // g}:{int(height) // g})")
    if length_seconds:
        lines.append(f"duration: {float(length_seconds):.3f} seconds")
    return lines


def _build_content(
    references, input_dir: str, direction: str,
    *, width: int = 0, height: int = 0, length_seconds: float = 0.0,
) -> list[dict]:
    """`references` is a refs.ReferenceSet; untyped here to avoid importing refs.py
    for a type hint alone (nothing else in this module needs it).

    Layout: one manifest text part, then every media part preceded by its own
    `<kind>_reference <Tag>` label line. The manifest alone is not enough - a model
    handed nine bare images in a row has to count positions to work out which one is
    <Picture 7>, and a video's six sampled frames sit in the same flat list as the
    standalone images. The inline labels make each part self-identifying, which is
    what system_prompt.md's per-type handling rules key off.
    """
    tagged = references.assign_tags()

    manifest: list[str] = []
    parts: list[dict] = []

    for t in tagged:
        path = os.path.join(input_dir, t.file)
        label = t.tag if t.audio_tag is None else f"{t.tag} {t.audio_tag}"
        manifest.append(f"{label}: {t.file}")

        if t.kind == "image":
            tensor = media.load_image(path)
            parts.append(_text_part(f"image_reference {t.tag} ({t.file}) - the next image:"))
            parts.append(_image_part(_tensor_to_jpeg_b64(tensor)))

        elif t.kind == "video":
            frames, audio = media.load_video(path, target_fps=24)
            idxs = _six_evenly_spaced(len(frames))
            manifest.append(f"  ({len(idxs)} frames of {t.tag} follow, in order)")
            parts.append(
                _text_part(
                    f"video_reference {t.tag} ({t.file}) - the next {len(idxs)} images are frames "
                    f"sampled evenly across this one clip, in playback order:"
                )
            )
            for i in idxs:
                parts.append(_image_part(_tensor_to_jpeg_b64(frames[i])))
            if t.audio_tag is not None:
                if audio is not None:
                    parts.append(
                        _text_part(
                            f"audio_reference {t.audio_tag} - the soundtrack of {t.tag} ({t.file}), "
                            f"the next audio clip:"
                        )
                    )
                    parts.append(_video_audio_part(audio, t.audio_tag, t.file))
                else:
                    manifest.append(f"  ({t.audio_tag}: {t.file}'s soundtrack could not be read)")

        else:  # audio
            parts.append(_text_part(f"audio_reference {t.tag} ({t.file}) - the next audio clip:"))
            parts.append(_file_audio_part(path, t.tag, t.file))

    text = "USER DIRECTION:\n" + direction
    fmt = _target_format_lines(width, height, length_seconds)
    if fmt:
        text += "\n\nTARGET FORMAT:\n" + "\n".join(fmt)
    text += "\n\nReference manifest:\n" + "\n".join(manifest)
    return [_text_part(text)] + parts


def render_payload(payload: dict) -> str:
    """The chat payload as readable text, with base64 media replaced by a size note.

    Rendered from the payload dict itself, immediately before it is posted, so the
    debug output can never drift from what actually went over the wire. The API key
    lives in the Authorization header and is NOT part of this dict - keep it that way;
    nothing here may ever be handed the headers.
    """
    lines: list[str] = [f"model: {payload.get('model')}"]
    for extra in ("reasoning", "temperature", "max_tokens"):
        if extra in payload:
            lines.append(f"{extra}: {payload[extra]}")

    # DISPLAY ORDER ONLY. The system message really is first on the wire, and must stay
    # there - but it is ~190 lines, which buried the direction near the bottom of the
    # debug socket and made it look like it was sent last. Reading order here is
    # direction first, system prompt after; the payload dict is untouched.
    messages = list(payload.get("messages", []))
    ordered = [m for m in messages if m.get("role") != "system"]
    ordered += [m for m in messages if m.get("role") == "system"]
    if len(ordered) > 1:
        lines.append("")
        lines.append("(shown user-message-first for reading; on the wire the system message goes first)")

    for message in ordered:
        role = message.get("role")
        content = message.get("content")
        lines.append("")
        lines.append(f"===== {str(role).upper()} MESSAGE =====")
        if isinstance(content, str):
            lines.append(content)
            continue
        for part in content or []:
            kind = part.get("type")
            if kind == "text":
                lines.append(part.get("text", ""))
            elif kind == "image_url":
                url = part.get("image_url", {}).get("url", "")
                b64 = url.split(",", 1)[1] if "," in url else ""
                mime = url[5 : url.find(";")] if url.startswith("data:") else "image"
                lines.append(f"    [{mime}, ~{_b64_size(b64)} bytes, not shown]")
            elif kind == "input_audio":
                ia = part.get("input_audio", {})
                lines.append(
                    f"    [audio {ia.get('format', '?')}, ~{_b64_size(ia.get('data', ''))} bytes, not shown]"
                )
            else:
                lines.append(f"    [unrecognized part type {kind!r}]")
    return "\n".join(lines)


def _b64_size(b64: str) -> int:
    """Decoded byte count of a base64 string, near enough for a debug line."""
    return max(0, len(b64) * 3 // 4)


def _short_reason(resp: requests.Response) -> str:
    try:
        msg = resp.json().get("error", {}).get("message")
        if msg:
            return str(msg)[:200]
    except ValueError:
        pass
    return (resp.text or "")[:200]


# ---- the call -------------------------------------------------------------------


def write_prompt(
    *, references, input_dir: str, direction: str, api_key: str, model: str,
    system_prompt: str = "", width: int = 0, height: int = 0, length_seconds: float = 0.0,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT, job_type: str = "auto",
    classifier_model: str = "", debug: list | None = None,
) -> str:
    """Pass a list as `debug` to have the rendered payload appended to it. A sink rather
    than a second return value, so the frozen `-> str` contract and every existing caller
    stay untouched, and so the payload survives a raised PromptError."""
    key = _resolve_api_key(api_key)

    # job_type picks the register. "auto" asks a cheap classifier; anything else is taken
    # at its word and makes no extra call. An explicit system_prompt overrides all of it.
    resolved = job_type if job_type in ("standard", "replacement") else "standard"
    if job_type == "auto":
        resolved = classify_mode(
            direction=direction, references=references, api_key=api_key, model=classifier_model
        )

    # Non-blank (after strip) -> the workflow's own edited prompt, used verbatim (not
    # stripped). Blank -> fall back to the packaged file for the resolved job type, read
    # at call time (not import time) so an edit on disk lands on the next queue.
    if not (system_prompt and system_prompt.strip()):
        system_prompt = _read_system_prompt(resolved)
    content = _build_content(
        references, input_dir, direction,
        width=width, height=height, length_seconds=length_seconds,
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    }
    # "none" is OpenRouter's own way of saying "don't reason", so it goes on the wire
    # like any other level. An unrecognised value is dropped rather than sent - a typo
    # in a widget must not turn into a 400 that costs a whole queue.
    if reasoning_effort in REASONING_EFFORTS:
        payload["reasoning"] = {"effort": reasoning_effort}

    # Filled before the call, so a failed request still leaves the caller the payload
    # that caused it - that's when it's worth the most.
    if debug is not None:
        # Exactly ONE entry: the node reads debug_sink[0]. The routing line rides on top
        # of the payload render rather than being a second entry.
        routing = f"job_type: {job_type} -> {resolved} (system prompt: {resolved}.md)"
        debug.append(routing + "\n" + render_payload(payload))

    try:
        resp = requests.post(
            _CHAT_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=_CHAT_TIMEOUT,
        )
    except requests.RequestException as e:
        # Deliberately not interpolating str(e): the underlying exception is out of our
        # control and must never be trusted to keep the Authorization header out of its
        # message. The type name is enough to debug a network failure.
        raise PromptError(f"OpenRouter request failed: {type(e).__name__}") from None

    if resp.status_code != 200:
        raise PromptError(f"OpenRouter returned {resp.status_code}: {_short_reason(resp)}")

    try:
        message = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise PromptError("OpenRouter response was malformed") from None

    if not isinstance(message, str) or not message.strip():
        raise PromptError("OpenRouter returned an empty completion")

    return message.strip()
