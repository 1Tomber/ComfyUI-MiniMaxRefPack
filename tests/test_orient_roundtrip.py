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
    # pythonTruthy is a dependency of fromReferencesList - it is how the soundtrack
    # flag is read the same way Python reads it - so running the converters means
    # shipping it alongside them. See tests/test_soundtrack_parity.py.
    def grab(name):
        at = text.find(f"function {name}")
        assert at != -1, f"{name} moved - this test runs the shipped code, not a copy"
        return text[at:text.find("\n}\n", at) + 3]

    start = text.find("export function fromReferencesList")
    end = text.find("export function editSummary")
    assert start != -1 and end != -1, (
        "the references_json converters moved - this test runs the shipped pair"
    )
    return grab("takeEdit") + grab("pythonTruthy") + text[start:end]


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


# ---- the two halves must agree about which way a mirror goes -------------------------


def _screen_cells(im):
    w, h = im.size
    return [[im.getpixel((x, y))[0] for x in range(w)] for y in range(h)]


@requires_node
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("screen_axis", ["h", "v"])
def test_the_browsers_mirror_axis_matches_what_the_pipeline_does(rotate, screen_axis):
    """The bug this exists for: the editor decided a mirror in the DISPLAYED frame and
    stored it in the SOURCE frame without translating, so at 90 and 270 the picture
    mirrored the wrong way AND the crop rect was reflected on the wrong axis - leaving the
    emitted crop over different pixels than the ones the user boxed.

    A test of either half alone would have passed. This one runs the browser's mapping
    under node and then checks the answer against the real media._orient_pil.
    """
    from PIL import Image

    from minimax_refpack import media

    source = Image.new("RGB", (3, 2))
    source.putdata([(10 * (y * 3 + x + 1), 0, 0) for y in range(2) for x in range(3)])

    text = REFPACK_JS.read_text(encoding="utf-8")
    start, end = text.find("// >>> MMRP-ORIENT"), text.find("// <<< MMRP-ORIENT")
    assert start != -1 and end != -1, "the MMRP-ORIENT markers are gone"
    script = (text[start:end]
              + f'\nconsole.log(JSON.stringify(sourceFlipAxis("{screen_axis}", {rotate})));\n')
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    source_axis = json.loads(proc.stdout.strip())

    shown = _screen_cells(media._orient_pil(source, rotate=rotate, flip=None, expand=True))
    if screen_axis == "h":
        wanted = [list(reversed(row)) for row in shown]     # mirror left-right on screen
    else:
        wanted = list(reversed(shown))                      # mirror top-bottom on screen

    got = _screen_cells(
        media._orient_pil(source, rotate=rotate, flip=source_axis, expand=True)
    )
    assert got == wanted, (
        f"clicking the {screen_axis!r} mirror at {rotate} deg stored flip={source_axis!r}, "
        f"which does not produce the mirror the user asked for"
    )


# ---- orientation belongs to the visual kinds only ------------------------------------


@requires_node
def test_an_audio_reference_does_not_keep_orientation():
    """refs.Reference.from_dict gates orientation on `kind in ("image", "video")`, but the
    browser spread it into every kind - so an audio reference in a hand-written
    references_json kept rotate/flip through the round trip and re-serialised them, and
    its tile badge summarised an orientation the server had already dropped. crop and trim
    were gated per kind here all along; orientation was not."""
    out = _round_trip([
        {"kind": "audio", "file": "vo.wav", "rotate": 90, "flip": "h", "rotate_expand": False},
    ])
    assert out == [{"kind": "audio", "file": "vo.wav"}], out


@requires_node
@pytest.mark.parametrize("kind", ["image", "video"])
def test_a_visual_reference_still_keeps_orientation(kind):
    out = _round_trip([{"kind": kind, "file": f"a.{kind}", "rotate": 90, "flip": "h"}])
    assert out[0]["rotate"] == 90
    assert out[0]["flip"] == "h"


@requires_node
def test_a_rotate_value_python_would_reject_is_dropped():
    """Python RAISES for these, so a references_json written by hand renders as perfectly
    fine in the browser and then fails the whole node when queued. Dropping is the right
    outcome - coercing would make the browser accept what the server refuses - but the
    round trip must not carry it."""
    for bad in ["90", True, [90], {"deg": 90}, float("nan")]:
        out = _round_trip([{"kind": "image", "file": "a.png", "rotate": bad}])
        assert "rotate" not in out[0], f"rotate={bad!r} survived the round trip"
