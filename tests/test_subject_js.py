"""The subject picker's geometry and toggling, executed under node.

<Subject N> is a label the model invents in subject_definitions, working out which
references belong together by LOOKING at them. Declaring the grouping instead is the one
thing about the reference set the user knows for certain and the model can only infer.

The picker is painted inside a 131px tile whose four corners are already spoken for
(scissors, delete, music, play), so the cells and the hit regions are generated from ONE
description - a picker whose painted cells and clickable cells disagree would be the worst
possible version of this control.

Extracted from web/refpack.js between its MMRP-SUBJECT markers.
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

TILE, CELL, GAP = 131, 24, 2


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-SUBJECT")
    end = text.find("// <<< MMRP-SUBJECT")
    assert start != -1 and end != -1, (
        "the MMRP-SUBJECT markers are gone from web/refpack.js - this test extracts the "
        "real shipped code through them"
    )
    return text[start:end]


def _run(expression: str):
    script = _extract() + f"\nconsole.log(JSON.stringify({expression}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _cells():
    return _run(f"subjectCells({TILE}, {CELL}, {GAP})")


# ---- the grid ----------------------------------------------------------------------


@requires_node
def test_there_are_ten_cells_none_plus_one_to_nine():
    cells = _cells()
    assert [c["n"] for c in cells] == list(range(10))


@requires_node
def test_the_grid_fits_inside_the_tile():
    """131px is the tile and the corners are already taken, so the picker has to live
    entirely within it or it covers furniture that still needs clicking."""
    for c in _cells():
        assert c["x"] >= 0 and c["y"] >= 0
        assert c["x"] + c["w"] <= TILE, c
        assert c["y"] + c["h"] <= TILE, c


@requires_node
def test_the_grid_is_five_across_and_two_down():
    cells = _cells()
    assert len({c["y"] for c in cells}) == 2
    assert len({c["x"] for c in cells}) == 5


@requires_node
def test_the_grid_is_centred():
    cells = _cells()
    left = min(c["x"] for c in cells)
    right = TILE - max(c["x"] + c["w"] for c in cells)
    assert abs(left - right) <= 1


@requires_node
def test_no_two_cells_overlap():
    """They are hit regions as well as paint, so an overlap would make one number
    unclickable."""
    cells = _cells()
    for i, a in enumerate(cells):
        for b in cells[i + 1:]:
            apart = (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                     or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
            assert apart, (a, b)


# ---- toggling ----------------------------------------------------------------------


@pytest.mark.parametrize("start,n,expected", [
    (None, 1, [1]),
    ([1], 2, [1, 2]),         # a reference can hold several: character AND location
    ([1, 2], 1, [2]),         # toggling off
    ([2, 1], 3, [1, 2, 3]),   # sorted, matching refs.validate_subjects
    ([1, 1], 2, [1, 2]),      # de-duplicated
    ([1, 2, 3], 0, []),       # the empty square clears everything
    (None, 0, []),
])
@requires_node
def test_toggling_a_subject(start, n, expected):
    assert _run(f"toggleSubject({json.dumps(start)}, {n})") == expected


@requires_node
def test_toggling_does_not_mutate_what_it_was_given():
    out = _run("(() => { const a = [1, 2]; toggleSubject(a, 3); return a; })()")
    assert out == [1, 2]


# ---- the persistent pill -------------------------------------------------------------


@pytest.mark.parametrize("subjects,expected", [
    (None, None), ([], None),
    ([1], "1"), ([1, 2], "1 2"), ([1, 2, 3], "1 2 3"),
    ([1, 2, 3, 4], "1 2 3+1"),          # capped: nine numbers do not fit an 18px corner
    ([1, 2, 3, 4, 5, 6], "1 2 3+3"),
])
@requires_node
def test_the_pill_summarises_rather_than_overflowing(subjects, expected):
    assert _run(f"subjectPillText({json.dumps(subjects)})") == expected


# ---- hit priority: the cells have to beat the tile they are painted inside ----------
#
# This is the test that was missing. The picker cells sit INSIDE the tile's own rect, and
# hitTest scans regions in reverse so the last one pushed wins. Pushed where they were
# painted, the cells landed BEFORE the tile - so every click on a number resolved to the
# tile and no subject could ever be set. The feature was completely unreachable and every
# other test still passed, because they all covered geometry and toggling rather than
# which region a click actually resolves to.


def _hit_test_js() -> str:
    """hitTest, extracted from the shipped file."""
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("export function hitTest(node, x, y) {")
    assert start != -1, "hitTest moved"
    return text[start:text.find("\n}\n", start) + 3]


def _resolve(regions, x, y):
    script = (
        _hit_test_js()
        + f"\nconst node = {{ _mmrpHit: {{ regions: {json.dumps(regions)} }} }};\n"
        + f"console.log(JSON.stringify(hitTest(node, {x}, {y})));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _tile_and_cells(tile_x=0, tile_y=0):
    """The regions one tile contributes while its picker is open, in push order."""
    cells = [
        {"type": "subject", "n": c["n"], "x": tile_x + c["x"], "y": tile_y + c["y"],
         "w": c["w"], "h": c["h"]}
        for c in _cells()
    ]
    tile = {"type": "tile", "x": tile_x, "y": tile_y, "w": TILE, "h": TILE}
    return tile, cells


@requires_node
@pytest.mark.parametrize("n", list(range(10)))
def test_a_click_on_a_picker_cell_resolves_to_that_cell(n):
    tile, cells = _tile_and_cells()
    regions = [tile] + cells          # the order draw() must push them in
    cell = cells[n]
    hit = _resolve(regions, cell["x"] + cell["w"] // 2, cell["y"] + cell["h"] // 2)
    assert hit["type"] == "subject", f"cell {n} is unreachable - it resolves to {hit['type']}"
    assert hit["n"] == n


@requires_node
def test_pushing_the_cells_before_the_tile_makes_them_unreachable():
    """Pins WHY the order matters, so a future refactor that reorders the pushes fails
    here with an explanation rather than silently disabling the feature again."""
    tile, cells = _tile_and_cells()
    wrong = cells + [tile]            # what the code did before
    cell = cells[3]
    hit = _resolve(wrong, cell["x"] + cell["w"] // 2, cell["y"] + cell["h"] // 2)
    assert hit["type"] == "tile", "this ordering is the bug; if it now works, update this test"


@requires_node
def test_a_click_on_the_tile_outside_every_cell_still_reaches_the_tile():
    """Clicking the tile's own area closes the picker, so it must still resolve."""
    tile, cells = _tile_and_cells()
    regions = [tile] + cells
    assert _resolve(regions, 4, 4)["type"] == "tile"


@requires_node
def test_toggling_twice_returns_to_where_it_started():
    """What makes the double-click revert exact. The first click of a double-click opens
    the picker and the second toggles a cell; the dblclick handler toggles that same cell
    back before inserting the tag, so the gesture leaves subjects untouched. That is only
    true because toggling is its own inverse."""
    for start in (None, [1], [1, 2], [3, 5, 9]):
        once = _run(f"toggleSubject({json.dumps(start)}, 2)")
        twice = _run(f"toggleSubject({json.dumps(once)}, 2)")
        assert twice == sorted(start or []), f"{start} -> {once} -> {twice}"
