"""The compact manager and the unpacker.

The original node has 20 output sockets, so wiring it into the generation node means
eighteen links running across the graph. The compact variant emits the media as ONE
`pack` socket; an unpacker sitting beside the generation node fans it back out, turning
the long run into a single link.

`prompt` and `debug` stay separate outputs on the compact node deliberately: the prompt
usually goes somewhere other than the generation node and debug goes to a text preview,
so bundling them would only mean unpacking them again next door.
"""

import json
import sys
import types

import pytest

from minimax_refpack import nodes, refs


@pytest.fixture
def fake_folder_paths(tmp_path, monkeypatch):
    """Mirrors tests/test_nodes.py - build() imports folder_paths at call time, which only
    exists inside a running ComfyUI."""
    module = types.ModuleType("folder_paths")
    module.get_input_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", module)
    return tmp_path


def _compact(**kw):
    kw.setdefault("direction", "")
    return nodes.MiniMaxH3ReferencePackCompact().build_compact(**kw)


# ---- why it is a separate class ----------------------------------------------------


def test_the_original_node_is_untouched():
    """ComfyUI stores a link by its output SLOT INDEX. Dropping sockets from the existing
    node would silently re-point every link in every saved workflow, which is why this is
    a new class key rather than a change to that one."""
    assert nodes.MiniMaxH3ReferencePack.RETURN_NAMES == refs.output_names()
    assert len(nodes.MiniMaxH3ReferencePack.RETURN_TYPES) == 20


def test_the_compact_node_has_three_outputs_instead_of_twenty():
    assert nodes.MiniMaxH3ReferencePackCompact.RETURN_NAMES == ("pack", "prompt", "debug")


def test_both_managers_share_one_implementation():
    """The point of subclassing: there is one build, one INPUT_TYPES, one IS_CHANGED. Two
    copies would drift the moment either is touched."""
    assert issubclass(nodes.MiniMaxH3ReferencePackCompact, nodes.MiniMaxH3ReferencePack)
    assert (nodes.MiniMaxH3ReferencePackCompact.INPUT_TYPES()
            == nodes.MiniMaxH3ReferencePack.INPUT_TYPES())
    # __func__, because IS_CHANGED is a classmethod: accessing it on each class yields a
    # distinct bound object even though the underlying function is the same one.
    assert (nodes.MiniMaxH3ReferencePackCompact.IS_CHANGED.__func__
            is nodes.MiniMaxH3ReferencePack.IS_CHANGED.__func__)
    assert (nodes.MiniMaxH3ReferencePackCompact._build_outputs
            is nodes.MiniMaxH3ReferencePack._build_outputs)


def test_the_pack_type_is_its_own(fake_folder_paths):
    """A custom link type is the whole of the type safety here - ComfyUI matches links by
    string, so `pack` must not be connectable to an IMAGE or AUDIO input."""
    assert refs.PACK_TYPE not in ("IMAGE", "AUDIO", "STRING")
    assert nodes.MiniMaxH3ReferencePackCompact.RETURN_TYPES[0] == refs.PACK_TYPE
    assert nodes.MiniMaxH3ReferenceUnpack.INPUT_TYPES()["required"]["pack"][0] == refs.PACK_TYPE


# ---- the round trip ------------------------------------------------------------------


def test_the_unpacker_restores_exactly_what_the_original_node_emits(fake_folder_paths, monkeypatch):
    """The contract that matters: pack then unpack must equal the 18 media sockets the
    original node would have produced from the same inputs."""
    monkeypatch.setattr(nodes.media, "load_image",
                        lambda path, crop=None, max_edge=0, **kw: f"IMG:{path}")
    monkeypatch.setattr(nodes.prompt, "write_prompt", lambda **kw: "a prompt", raising=False)
    for name in ("i1.jpg", "i2.jpg"):
        (fake_folder_paths / name).write_bytes(b"x")
    references_json = json.dumps({"references": [
        {"kind": "image", "file": "i1.jpg"}, {"kind": "image", "file": "i2.jpg"}]})

    full = nodes.MiniMaxH3ReferencePack().build(direction="d", references_json=references_json)
    pack, prompt_text, debug_text = _compact(direction="d", references_json=references_json)
    restored = nodes.MiniMaxH3ReferenceUnpack().unpack(pack)

    media_count = len(refs.media_output_names())
    assert restored == full[:media_count], "the unpacked sockets must match the original's"
    assert prompt_text == full[refs.slot_index("prompt")]
    assert debug_text == full[refs.slot_index("debug")]


def test_the_unpacker_emits_one_value_per_media_socket():
    empty = {}
    out = nodes.MiniMaxH3ReferenceUnpack().unpack(empty)
    assert len(out) == len(refs.media_output_names()) == 18
    assert set(out) == {None}, "an absent slot is None, which the target node already skips"


def test_the_pack_is_keyed_by_socket_name(fake_folder_paths, monkeypatch):
    """Keyed rather than positional, so the producer and the unpacker cannot drift the way
    two parallel lists would."""
    monkeypatch.setattr(nodes.prompt, "write_prompt", lambda **kw: "p", raising=False)
    pack, _, _ = _compact(direction="d")
    assert set(pack) == set(refs.media_output_names())


def test_a_pack_that_is_not_a_pack_says_so():
    """Types make this unreachable through the UI, but an API client posts whatever it
    likes - and a bare TypeError deep in a tuple comprehension would not explain itself."""
    with pytest.raises(ValueError) as e:
        nodes.MiniMaxH3ReferenceUnpack().unpack("not a pack")
    assert "pack" in str(e.value).lower()


# ---- registration ---------------------------------------------------------------------


def test_all_three_nodes_are_registered_with_display_names():
    for key in ("MiniMaxH3ReferencePack", "MiniMaxH3ReferencePackCompact",
                "MiniMaxH3ReferenceUnpack"):
        assert key in nodes.NODE_CLASS_MAPPINGS
        assert key in nodes.NODE_DISPLAY_NAME_MAPPINGS


def test_the_browser_ui_is_registered_for_both_managers():
    """The compact node is the same node with a different output shape, so it needs the
    whole canvas UI. Gating the extension on one name would leave it a bare widget stack."""
    import pathlib
    js = (pathlib.Path(__file__).resolve().parent.parent / "web" / "refpack.js").read_text(
        encoding="utf-8")
    assert '"MiniMaxH3ReferencePackCompact"' in js
    assert "NODE_NAMES.includes(nodeData.name)" in js
