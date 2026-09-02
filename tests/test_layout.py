"""The wrapping layout, executed under node.

web/refpack.js pins the node to whatever width the user drags it to and wraps reference
tiles onto extra lines to fit. The arithmetic that decides how many fit, and how tall a
section therefore is, is the part that can be wrong without anyone noticing until a tile
is drawn off the edge of the slab or a hit region lands on the wrong row.

Extracted from the shipped file between its MMRP-LAYOUT markers and run under node, the
same harness tests/test_migration.py uses for MMRP-MIGRATE - so this tests the real code
rather than a copy of it that can drift. The block deliberately depends on nothing but
the CL constants, which are passed in.

The rule worth stating once: tiles pack TIGHT (tilesPerRow reserves nothing for the add
square, so no width wraps them early), and the add square is placed afterwards - it shares
the last tile row when there is room and takes a line of its own only when that row is
exactly full with no slack beside it (addSquareWraps).
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
    (1000, 7),   # tight packing: floor((1000+6)/137) = 7 (was 6 when the add square was reserved)
    (700, 5),    # floor((700+6)/137) = 5 (was 4)
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
def test_a_full_row_of_tiles_never_overflows():
    """Tiles pack tight, so a full row of `perRow` tiles must itself fit the width (the add
    square is placed separately and wraps when it does not fit - see below).

    Checked from MIN_ROW_W up, not from zero. tilesPerRow clamps to a minimum of one tile,
    so below that width it deliberately overflows rather than render a section with no
    tiles at all - the honest reading is "one tile is the floor", and minNodeWidth is what
    makes sure the node can never be dragged there."""
    checks = _run(f"""
        (() => {{
            const out = [];
            for (let w = {MIN_ROW_W}; w <= 1600; w += 1) {{
                const n = tilesPerRow(w);
                const tilesUsed = n * (CL.tile + CL.gap) - CL.gap;
                out.push(tilesUsed <= w);
            }}
            return out;
        }})()
    """)
    assert all(checks), "a full row of tiles overflows at some width"


@requires_node
def test_the_add_square_shares_the_row_when_there_is_room_and_wraps_when_full():
    """The replacement for the old unconditional reservation: the add square shares the last
    tile row unless that row is exactly full with no slack beside it."""
    # a partial last row always has a free slot -> never wraps
    assert _run("addSquareWraps(4, 5, 700)") is False
    # a full row (5 tiles at perRow 5, viewW 700) has no room for the square -> wraps
    assert _run("addSquareWraps(5, 5, 700)") is True
    # a full row WITH slack keeps the square on the same line: 2 tiles at perRow 2 in a wide
    # 700px view leaves plenty of room, so no wrap
    assert _run("addSquareWraps(2, 2, 700)") is False
    # an empty section never wraps (it is a short labelled row, handled elsewhere)
    assert _run("addSquareWraps(0, 5, 700)") is False


@requires_node
def test_the_add_square_never_overflows_however_it_is_placed():
    """Whether it shares the row or wraps, the drawn add square must stay inside the slab.
    This is the guarantee the old reservation gave directly; now it falls out of
    addSquareWraps, so pin it."""
    checks = _run(f"""
        (() => {{
            const out = [];
            for (let w = {MIN_ROW_W}; w <= 1600; w += 7) {{
                const perRow = tilesPerRow(w);
                for (let count = 1; count <= 9; count++) {{
                    const wraps = addSquareWraps(count, perRow, w);
                    const lastRowTiles = wraps ? 0
                        : (count % perRow === 0 ? perRow : count % perRow);
                    const ax = lastRowTiles > 0
                        ? lastRowTiles * (CL.tile + CL.gap) - CL.gap + CL.addGap
                        : 0;
                    out.push(ax + CL.addBtn <= w);
                }}
            }}
            return out;
        }})()
    """)
    assert all(checks), "the add square is drawn off the right edge at some width/count"


@requires_node
def test_below_the_floor_it_still_returns_a_usable_row_rather_than_zero():
    """Dividing the layout by zero tiles per row would be worse than overflowing."""
    assert _run("tilesPerRow(10)") == 1
    assert _run("tilesPerRow(0)") == 1


# ---- how tall that makes a section -----------------------------------------------


@pytest.mark.parametrize("count,per_row,expected", [
    (0, 6, 1),   # floored at one line
    (1, 6, 1),
    (6, 6, 1),   # six tiles are one row; whether the add square adds a line is addSquareWraps
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
