"""The wrapping layout, executed under node.

web/refpack.js pins the node to whatever width the user drags it to and wraps reference
tiles onto extra lines to fit. The arithmetic that decides how many fit, and how tall a
section therefore is, is the part that can be wrong without anyone noticing until a tile
is drawn off the edge of the slab or a hit region lands on the wrong row.

Extracted from the shipped file between its MMRP-LAYOUT markers and run under node, the
same harness tests/test_migration.py uses for MMRP-MIGRATE - so this tests the real code
rather than a copy of it that can drift. The block deliberately depends on nothing but
the CL constants, which are passed in.

The invariant worth stating once: the add square is reserved for UNCONDITIONALLY in
tilesPerRow, which is what makes linesFor correct with no special case. A full line
always has room for the square after it, so the square can never be pushed off the right
edge and never forces a line of its own.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Mirrors the CL block in refpack.js. Duplicated rather than parsed out of the file
# because a test that derived them would pass whatever the file said, including wrong.
CL = {
    "x0": 10, "padTop": 8, "headerH": 15, "headGap": 12, "tile": 131, "gap": 6,
    "stripPad": 3, "rowGap": 18, "addBtn": 44, "addGap": 24, "emptyRowH": 30,
}
KINDS = ["image", "video", "audio"]


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-LAYOUT")
    end = text.find("// <<< MMRP-LAYOUT")
    assert start != -1 and end != -1, (
        "the MMRP-LAYOUT markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them, so removing them silently stops the wrapping "
        "arithmetic from being covered at all"
    )
    return text[start:end]


def _run(expression: str):
    script = (
        f"const CL = {json.dumps(CL)};\n"
        f"const KINDS = {json.dumps(KINDS)};\n"
        + _extract()
        + f"\nconsole.log(JSON.stringify({expression}));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _refs(images=0, videos=0, audios=0):
    mk = lambda n: [{"file": f"f{i}"} for i in range(n)]  # noqa: E731
    return {"images": mk(images), "videos": mk(videos), "audios": mk(audios)}


# ---- how many fit across ---------------------------------------------------------


@pytest.mark.parametrize("view_w,expected", [
    (1295, 9),   # the old fixed width: the nine-tile row it was chosen to guarantee
    (1000, 6),
    (700, 4),
    (400, 2),
    (200, 1),
    (10, 1),     # absurd, but never zero - a zero would divide the layout by nothing
])
@requires_node
def test_tiles_per_row_tracks_the_width(view_w, expected):
    assert _run(f"tilesPerRow({view_w})") == expected


@requires_node
def test_the_old_fixed_width_still_fits_all_nine_images():
    """1340 node - 2x10 block inset - 2x10 slab padding = 1300 of drawable width. The
    pin is gone but the width it was chosen for must still behave, or every workflow
    saved at that size silently re-wraps on load."""
    assert _run("tilesPerRow(1300)") >= 9


# One tile plus its add square: CONTENT.minRowW in refpack.js, and the floor
# minNodeWidth() guarantees the slab is never dragged below.
MIN_ROW_W = CL["tile"] + CL["addGap"] + CL["addBtn"]


@requires_node
def test_a_full_line_always_leaves_room_for_the_add_square():
    """The invariant the whole scheme rests on. If this fails, a section at cap draws its
    add square off the right edge of the slab, where it cannot be clicked.

    Checked from MIN_ROW_W up, not from zero. tilesPerRow clamps to a minimum of one tile,
    so below that width it deliberately overflows rather than render a section with no
    tiles at all - the honest reading is "one tile is the floor", and minNodeWidth is what
    makes sure the node can never be dragged there. The test states the same bound rather
    than pretending the arithmetic covers widths the UI forbids."""
    checks = _run(f"""
        (() => {{
            const out = [];
            for (let w = {MIN_ROW_W}; w <= 1600; w += 1) {{
                const n = tilesPerRow(w);
                const used = n * (CL.tile + CL.gap) - CL.gap + CL.addGap + CL.addBtn;
                out.push(used <= w);
            }}
            return out;
        }})()
    """)
    assert all(checks), "a full line overflows at some width"


@requires_node
def test_below_the_floor_it_still_returns_a_usable_row_rather_than_zero():
    """Dividing the layout by zero tiles per row would be worse than overflowing."""
    assert _run("tilesPerRow(10)") == 1
    assert _run("tilesPerRow(0)") == 1


# ---- how tall that makes a section -----------------------------------------------


@pytest.mark.parametrize("count,per_row,expected", [
    (0, 6, 1),   # empty still needs a line: the add square lives on it
    (1, 6, 1),
    (6, 6, 1),   # exactly full - the add square rides on the same line, no extra
    (7, 6, 2),
    (9, 6, 2),
    (9, 1, 9),
])
@requires_node
def test_lines_for_counts_tiles_not_the_add_square(count, per_row, expected):
    assert _run(f"linesFor({count}, {per_row})") == expected


@requires_node
def test_only_the_empty_sections_collapse():
    """A section with references keeps its full tile strip; only the empty ones shrink. So
    uploading one image expands that section and leaves video and audio compact."""
    rows = _run("computeCanvasRows(%s, 1300).rows"
                % json.dumps(_refs(images=1)))
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["image"].get("empty") is not True, "the image section has a reference"
    assert by_kind["video"].get("empty") is True
    assert by_kind["audio"].get("empty") is True
    # the image section is a full strip; the empty ones are the short row
    assert by_kind["video"]["stripH"] == 0
    assert by_kind["image"]["stripH"] > CL["tile"] - 1


def test_an_empty_section_collapses_to_a_short_row():
    """An empty section is one short labelled row (emptyRowH) plus the inter-section gap,
    not a reserved full-height tile strip. A fresh node with three empty sections is three
    of those, so it opens compact instead of showing three empty wells."""
    per_section = CL["emptyRowH"] + CL["rowGap"]
    expected = CL["padTop"] + 3 * per_section
    assert _run("computeCanvasRows(%s, 1300).height" % json.dumps(_refs())) == expected
    # concretely, far shorter than the three full wells it used to draw
    assert expected < 554


@requires_node
def test_adding_a_reference_that_wraps_makes_the_slab_taller():
    """"Adding media is a height change" - stated as a test because it is the behaviour
    the whole design was chosen for."""
    narrow = json.dumps(_refs(images=2))
    wrapped = json.dumps(_refs(images=3))
    short = _run(f"computeCanvasRows({narrow}, 400).height")
    tall = _run(f"computeCanvasRows({wrapped}, 400).height")
    assert tall == short + CL["tile"] + CL["gap"]


@requires_node
def test_sections_stack_without_overlapping_at_any_width():
    """Each section must start below the previous one's strip. An off-by-one here draws
    one section's tiles over another's header."""
    refs = json.dumps(_refs(images=9, videos=3, audios=3))
    rows = _run(f"computeCanvasRows({refs}, 380).rows")
    for prev, nxt in zip(rows, rows[1:]):
        prev_bottom = prev["stripY"] + prev["stripH"]
        assert nxt["y"] >= prev_bottom, (prev, nxt)
    last = rows[-1]
    assert _run(f"computeCanvasRows({refs}, 380).height") >= last["stripY"] + last["stripH"]


@requires_node
def test_every_section_reports_the_same_tiles_per_row():
    """One number for the whole slab, so the three strips line up down the left edge."""
    refs = json.dumps(_refs(images=9, videos=1, audios=0))
    rows = _run(f"computeCanvasRows({refs}, 700).rows")
    assert len({r["perRow"] for r in rows}) == 1
