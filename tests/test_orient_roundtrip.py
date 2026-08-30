"""Orientation has to survive the references_json round trip.

Both converters in web/refpack.js WHITELIST fields, so anything not named in them is
dropped between the working state and the widget. That does not surface as an error - it
surfaces as "I rotated it and it went back", which is much harder to diagnose than a
crash, and it is why this has a test of its own rather than being assumed.

The trap it caught: toReferencesList was handed `{crop: r.crop}` for images and audio but
the WHOLE reference for video, so a new field silently worked for one kind out of three.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from minimax_refpack.refs import ReferenceSet

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _converters() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    helper = text.find("function takeEdit")
    start = text.find("export function fromReferencesList")
    end = text.find("export function editSummary")
    assert helper != -1 and start != -1 and end != -1, (
        "the references_json converters moved - this test runs the shipped pair"
    )
    return text[helper:text.find("\n}\n", helper) + 3] + text[start:end]


def _round_trip(list_in):
    script = (_converters()
              + f"\nconsole.log(JSON.stringify(toReferencesList(fromReferencesList("
                f"{json.dumps(list_in)}))));\n")
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
@pytest.mark.parametrize("kind,extra", [
    ("image", {}),
    ("video", {"use_soundtrack": True}),
])
def test_orientation_survives_the_round_trip(kind, extra):
    """Images were the broken case before the converters were fixed, video the working
    one - so both kinds are checked rather than whichever is convenient."""
    src = [{"kind": kind, "file": "a.bin", "rotate": 90, "flip": "h",
            "rotate_expand": False, **extra}]
    out = _round_trip(src)[0]
    assert out["rotate"] == 90
    assert out["flip"] == "h"
    assert out["rotate_expand"] is False


@requires_node
def test_an_unoriented_reference_gains_no_orientation_keys():
    """The compatibility guarantee, from the JS side: a workflow that never rotated
    anything must keep writing the same references_json it always did."""
    out = _round_trip([{"kind": "image", "file": "a.png"}])[0]
    assert out == {"kind": "image", "file": "a.png"}


@requires_node
def test_the_default_expand_is_not_written_out():
    """rotate_expand: true is the default, so writing it would change the serialisation of
    every rotated reference for no reason."""
    out = _round_trip([{"kind": "image", "file": "a.png", "rotate": 90}])[0]
    assert "rotate_expand" not in out


@requires_node
def test_what_the_browser_writes_is_what_refs_py_reads():
    """The two halves have to agree on the shape, not just on the field names."""
    out = _round_trip([{"kind": "video", "file": "v.mp4", "use_soundtrack": True,
                        "rotate": 270, "flip": "hv", "rotate_expand": False}])
    ref = ReferenceSet.from_obj(out).references[0]
    assert (ref.rotate, ref.flip, ref.rotate_expand) == (270.0, "hv", False)
