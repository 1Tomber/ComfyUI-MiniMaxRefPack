"""The reference set: its shape, its pack file, and the tags MiniMax will assign it.

FROZEN CONTRACT. Every other module in this pack reads this one. Pure Python — no torch,
no ComfyUI imports — so it can be tested anywhere.

The tag numbering below is not a convention we invented. It is read out of
ComfyUI v0.32.0 `comfy_extras/nodes_minimax_h3.py:216-274`, which builds the tokenizer's
presentation list in this order:

  1. every reference image, in slot order, skipping empty slots      -> <Picture 1..n>  (:231)
  2. per reference video, in slot order:
       its soundtrack's audio entry is appended FIRST                -> <Audio a>       (:259)
       then the video itself                                        -> <Video 1..n>    (:263)
  3. every standalone reference audio, in slot order                 -> <Audio a>       (:273)

So <Audio N> counts video soundtracks before standalone audio, and <Video N> is numbered
independently of <Audio N>. Ordinals are per-type, 1-based, over the COMPACTED list.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Kind = Literal["image", "video", "audio"]

# Hard caps, from the Autogrow group definitions at nodes_minimax_h3.py:183,187,191,195.
MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_BY_KIND: dict[str, int] = {"image": MAX_IMAGES, "video": MAX_VIDEOS, "audio": MAX_AUDIOS}

KINDS: tuple[Kind, ...] = ("image", "video", "audio")

PACK_DIR = "minimax_ref_packs"
PACK_VERSION = 1

_SAFE_PACK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class ReferenceError(ValueError):
    """A reference set that MiniMax could not accept."""


@dataclass
class Reference:
    """One reference asset, named by its filename inside the ComfyUI input directory."""

    kind: Kind
    file: str
    # video only: also emit the clip's soundtrack on the index-matched video_audio_N output
    use_soundtrack: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "file": self.file}
        if self.kind == "video":
            d["use_soundtrack"] = bool(self.use_soundtrack)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Reference":
        kind = d.get("kind")
        if kind not in KINDS:
            raise ReferenceError(f"unknown reference kind {kind!r}")
        file = d.get("file")
        if not isinstance(file, str) or not file.strip():
            raise ReferenceError("reference is missing a file name")
        return cls(
            kind=kind,
            file=file.strip(),
            use_soundtrack=bool(d.get("use_soundtrack", False)) if kind == "video" else False,
        )


@dataclass
class TaggedReference:
    """A reference plus the socket it lands on and the tag MiniMax will give it."""

    ref: Reference
    slot: int                 # 1-based index within its own kind == its socket ordinal
    tag: str                  # "<Picture 2>" / "<Video 1>" / "<Audio 2>"
    audio_tag: str | None = None   # videos with use_soundtrack: the tag of their soundtrack

    @property
    def kind(self) -> Kind:
        return self.ref.kind

    @property
    def file(self) -> str:
        return self.ref.file

    def to_dict(self) -> dict[str, Any]:
        d = self.ref.to_dict()
        d.update({"slot": self.slot, "tag": self.tag})
        if self.audio_tag is not None:
            d["audio_tag"] = self.audio_tag
        return d


@dataclass
class ReferenceSet:
    """An ordered set of references. Order within a kind is the socket order."""

    references: list[Reference] = field(default_factory=list)

    # ---- construction -------------------------------------------------------

    @classmethod
    def from_json(cls, raw: str | None) -> "ReferenceSet":
        """Parse the node's hidden `references_json` widget. Empty/blank -> empty set."""
        if raw is None:
            return cls()
        raw = raw.strip()
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ReferenceError(f"references are not valid JSON: {e}") from e
        return cls.from_obj(data)

    @classmethod
    def from_obj(cls, data: Any) -> "ReferenceSet":
        """Accept either a bare list of references or a {"references": [...]} envelope."""
        if isinstance(data, dict):
            data = data.get("references", [])
        if not isinstance(data, list):
            raise ReferenceError("references must be a list")
        return cls([Reference.from_dict(d) for d in data if isinstance(d, dict)])

    def to_json(self) -> str:
        return json.dumps({"references": [r.to_dict() for r in self.references]})

    # ---- queries ------------------------------------------------------------

    def of_kind(self, kind: Kind) -> list[Reference]:
        return [r for r in self.references if r.kind == kind]

    def counts(self) -> dict[str, int]:
        return {k: len(self.of_kind(k)) for k in KINDS}

    def is_empty(self) -> bool:
        return not self.references

    def validate(self) -> None:
        """Raise if the set exceeds MiniMax's hard caps."""
        for kind in KINDS:
            n = len(self.of_kind(kind))
            cap = MAX_BY_KIND[kind]
            if n > cap:
                raise ReferenceError(
                    f"MiniMax H3 accepts at most {cap} reference {kind}s, got {n}"
                )

    def missing_files(self, input_dir: str) -> list[str]:
        """Filenames that do not resolve under the ComfyUI input directory, in set order."""
        missing = []
        for r in self.references:
            path = os.path.join(input_dir, r.file)
            if not os.path.isfile(path):
                missing.append(r.file)
        return missing

    def resolve(self, input_dir: str) -> list[str]:
        """Alias of missing_files: a pack "resolves" when this comes back empty."""
        return self.missing_files(input_dir)

    # ---- the tag rule -------------------------------------------------------

    def assign_tags(self) -> list[TaggedReference]:
        """Compute the tag MiniMax will assign each reference. See module docstring."""
        self.validate()
        tagged: list[TaggedReference] = []

        for i, ref in enumerate(self.of_kind("image"), start=1):
            tagged.append(TaggedReference(ref=ref, slot=i, tag=f"<Picture {i}>"))

        audio_n = 0
        for i, ref in enumerate(self.of_kind("video"), start=1):
            audio_tag = None
            if ref.use_soundtrack:
                # the soundtrack's <Audio j> is emitted BEFORE its <Video k> (:259 then :263)
                audio_n += 1
                audio_tag = f"<Audio {audio_n}>"
            tagged.append(
                TaggedReference(ref=ref, slot=i, tag=f"<Video {i}>", audio_tag=audio_tag)
            )

        for i, ref in enumerate(self.of_kind("audio"), start=1):
            audio_n += 1
            tagged.append(TaggedReference(ref=ref, slot=i, tag=f"<Audio {audio_n}>"))

        return tagged

    def tag_map(self) -> dict[str, str]:
        """{filename: tag} for prompt context. A video with a soundtrack appears once,
        with both tags joined, so the model can address either."""
        out: dict[str, str] = {}
        for t in self.assign_tags():
            out[t.file] = t.tag if t.audio_tag is None else f"{t.tag} {t.audio_tag}"
        return out


# ---- packs -----------------------------------------------------------------


@dataclass
class Pack:
    """A saved reference set. Lives at <input>/minimax_ref_packs/<name>.json."""

    name: str
    references: ReferenceSet
    direction: str = ""
    # Settings the pack was written with. Optional and defaulted to "" rather than
    # versioned in, so packs saved before these existed still load - PACK_VERSION is a
    # hard gate (from_dict rejects a mismatch outright), and bumping it would strand
    # every pack already on disk for the sake of two additive fields.
    model: str = ""
    reasoning_effort: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": PACK_VERSION,
            "name": self.name,
            "direction": self.direction,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "references": [r.to_dict() for r in self.references.references],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pack":
        if not isinstance(d, dict):
            raise ReferenceError("pack file is not a JSON object")
        version = d.get("version")
        if version != PACK_VERSION:
            raise ReferenceError(
                f"pack version {version!r} is not supported (this build reads v{PACK_VERSION})"
            )
        name = d.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ReferenceError("pack is missing a name")
        return cls(
            name=name.strip(),
            references=ReferenceSet.from_obj(d.get("references", [])),
            direction=d.get("direction") or "",
            model=d.get("model") or "",
            reasoning_effort=d.get("reasoning_effort") or "",
        )

    @classmethod
    def from_json(cls, raw: str) -> "Pack":
        try:
            return cls.from_dict(json.loads(raw))
        except json.JSONDecodeError as e:
            raise ReferenceError(f"pack file is not valid JSON: {e}") from e

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def validate_pack_name(name: str) -> str:
    """Reject anything that could escape the pack directory or confuse the filesystem."""
    name = (name or "").strip()
    if not _SAFE_PACK_NAME.match(name):
        raise ReferenceError(
            "pack name must be 1-64 characters of letters, digits, space, dot, dash or underscore"
        )
    if name.startswith(".") or "/" in name or "\\" in name or ".." in name:
        raise ReferenceError("invalid pack name")
    return name


def packs_dir(input_dir: str) -> str:
    return os.path.join(input_dir, PACK_DIR)


def pack_path(input_dir: str, name: str) -> str:
    return os.path.join(packs_dir(input_dir), validate_pack_name(name) + ".json")


def list_pack_names(input_dir: str) -> list[str]:
    d = packs_dir(input_dir)
    if not os.path.isdir(d):
        return []
    names = [f[:-5] for f in os.listdir(d) if f.endswith(".json")]
    return sorted(names, key=str.lower)


def load_pack(input_dir: str, name: str) -> Pack:
    with open(pack_path(input_dir, name), "r", encoding="utf-8") as f:
        return Pack.from_json(f.read())


def save_pack(input_dir: str, pack: Pack) -> str:
    path = pack_path(input_dir, pack.name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".partial"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(pack.to_json())
    os.replace(tmp, path)
    return path


def delete_pack(input_dir: str, name: str) -> bool:
    path = pack_path(input_dir, name)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---- output socket names ---------------------------------------------------

def output_names() -> tuple[str, ...]:
    """The node's 20 outputs, in declaration order.

    image_1..9 -> ref_images.ref_image_0..8
    video_1..3 -> ref_videos.ref_video_0..2
    video_audio_1..3 -> ref_video_audios.ref_video_audio_0..2  (index-paired to video_N)
    audio_1..3 -> ref_audios.ref_audio_0..2
    prompt -> the MiniMax node's prompt widget
    debug -> the full payload that went to the LLM, for a preview/console node

    APPEND-ONLY. ComfyUI stores a link by its output SLOT INDEX, so inserting a socket
    anywhere but the end silently re-points every existing link in a saved workflow.
    `debug` went on the end for exactly that reason - users have graphs wired to these
    sockets by hand.
    """
    names: list[str] = [f"image_{i}" for i in range(1, MAX_IMAGES + 1)]
    names += [f"video_{i}" for i in range(1, MAX_VIDEOS + 1)]
    names += [f"video_audio_{i}" for i in range(1, MAX_VIDEOS + 1)]
    names += [f"audio_{i}" for i in range(1, MAX_AUDIOS + 1)]
    names.append("prompt")
    names.append("debug")
    return tuple(names)


# The trailing STRING sockets, in output_names() order. Everything before them is a
# media socket that starts as None; these start as "".
_STRING_OUTPUTS = ("prompt", "debug")


def output_types() -> tuple[str, ...]:
    types: list[str] = ["IMAGE"] * MAX_IMAGES
    types += ["IMAGE"] * MAX_VIDEOS
    types += ["AUDIO"] * MAX_VIDEOS
    types += ["AUDIO"] * MAX_AUDIOS
    types += ["STRING"] * len(_STRING_OUTPUTS)
    return tuple(types)


def slot_index(name: str) -> int:
    """Index of an output socket by name, for building the 20-tuple."""
    return output_names().index(name)


def empty_outputs() -> list[Any]:
    """A full output tuple of Nones (plus empty strings for prompt/debug)."""
    out: list[Any] = [None] * (len(output_names()) - len(_STRING_OUTPUTS))
    out += [""] * len(_STRING_OUTPUTS)
    return out


def iter_kind_slots(kind: Kind) -> Iterable[str]:
    """Socket names for a kind, in slot order."""
    if kind == "image":
        return (f"image_{i}" for i in range(1, MAX_IMAGES + 1))
    if kind == "video":
        return (f"video_{i}" for i in range(1, MAX_VIDEOS + 1))
    return (f"audio_{i}" for i in range(1, MAX_AUDIOS + 1))
