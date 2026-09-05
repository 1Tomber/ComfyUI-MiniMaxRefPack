"""MMRP-TILT: the turned-frame geometry behind cropping a reference at a free angle, under node.

The editor shows ONE frame - the picture turned about its centre inside the rotation's bounding
box - and the crop is a fraction rect of it, which is exactly what media.py crops after
rotate(expand=True). So the property every rect here must satisfy is the one media.py would see:
each corner, turned back into source axes about the frame centre, lands inside the source box.
That is "no black under the crop", checked here in Python, independently of the JS.

Extracted from the shipped web/refpack.js between its MMRP-TILT markers, the same harness the
other marker blocks use.
"""

import json
import math
import pathlib
import shutil
import subprocess

import pytest

from minimax_refpack.media import _fill_scale

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-TILT")
    end = text.find("// <<< MMRP-TILT")
    assert start != -1 and end != -1, (
        "the MMRP-TILT markers are gone from web/refpack.js - this test runs the shipped "
        "turned-frame geometry through them; without them the crop editor's free-angle "
        "behaviour has no coverage at all"
    )
    return text[start:end].replace("export ", "")


def _run(tail: str):
    script = _extract() + "\n" + tail
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


def _call(expr: str):
    return _run(f"console.log(JSON.stringify({expr}));")


# ---- the reference geometry, in Python, the way media.py sees the frame -----------------------


def _dims(W, H, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return abs(c) * W + abs(s) * H, abs(s) * W + abs(c) * H


def _to_source(W, H, deg, px, py):
    """A frame point -> offset from the picture centre in source axes (the picture turned back)."""
    bw, bh = _dims(W, H, deg)
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    dx, dy = px - bw / 2, py - bh / 2
    return c * dx + s * dy, -s * dx + c * dy


def _black_free(W, H, deg, rect, eps=1e-6):
    x, y, w, h = rect
    for px in (x, x + w):
        for py in (y, y + h):
            sx, sy = _to_source(W, H, deg, px, py)
            if abs(sx) > W / 2 + eps or abs(sy) > H / 2 + eps:
                return False
    return True


def _in_frame(W, H, deg, rect, eps=1e-6):
    bw, bh = _dims(W, H, deg)
    x, y, w, h = rect
    return x >= -eps and y >= -eps and x + w <= bw + eps and y + h <= bh + eps


def _scaled(rect, k):
    x, y, w, h = rect
    return [x + w / 2 - w * k / 2, y + h / 2 - h * k / 2, w * k, h * k]


CASES = [(1920, 1080, 12.5), (1080, 1920, 30), (640, 640, 45), (320, 240, 45), (320, 240, 80),
         (1280, 720, 135), (800, 600, 200), (1920, 1080, 350)]


# ---- extraction contract ----------------------------------------------------------------------


@requires_node
def test_the_block_ships_every_name_the_editor_uses():
    src = _extract()
    for name in ("tiltIsFree", "tiltFrame", "tiltToPx", "tiltToCrop", "tiltContains",
                 "tiltInscribed", "tiltFit", "tiltFollow", "tiltDrag",
                 "polyContains", "clipPolyToBox", "nearestInPoly", "polyChord"):
        assert f"function {name}" in src, f"{name} vanished from the MMRP-TILT block"


# ---- the frame ---------------------------------------------------------------------------------


@requires_node
def test_free_angle_is_anything_off_a_quarter_turn():
    out = _call("[0, 90, 180, 270, 360, -90, 5, 45, 89.9, 90.1, -30, 359.5].map(tiltIsFree)")
    assert out == [False] * 6 + [True] * 6


@requires_node
def test_bounding_box_matches_the_formula_and_quarter_turns_are_exact():
    exprs = ", ".join(f"tiltFrame({W}, {H}, {d})" for W, H, d in CASES + [(1920, 1080, 90), (1920, 1080, 0)])
    frames = _call(f"[{exprs}]")
    for (W, H, d), fr in zip(CASES, frames):
        bw, bh = _dims(W, H, d)
        assert fr["bw"] == pytest.approx(bw) and fr["bh"] == pytest.approx(bh)
        # the polygon is the picture: its corners land back on the source corners, and it spans
        # exactly the bounding box
        xs = [p[0] for p in fr["poly"]]
        ys = [p[1] for p in fr["poly"]]
        assert min(xs) == pytest.approx(0, abs=1e-9) and max(xs) == pytest.approx(bw)
        assert min(ys) == pytest.approx(0, abs=1e-9) and max(ys) == pytest.approx(bh)
        for px, py in fr["poly"]:
            sx, sy = _to_source(W, H, d, px, py)
            assert abs(sx) == pytest.approx(W / 2) and abs(sy) == pytest.approx(H / 2)
    ninety, zero = frames[-2], frames[-1]
    assert (ninety["bw"], ninety["bh"]) == pytest.approx((1080, 1920))   # the transposed box
    assert (zero["bw"], zero["bh"]) == pytest.approx((1920, 1080))       # the source box


@requires_node
def test_px_and_crop_round_trip():
    out = _call("(() => { const fr = tiltFrame(1920, 1080, 33); const c = [0.1, 0.2, 0.3, 0.4];"
                " return [tiltToCrop(fr, tiltToPx(fr, c)), tiltToPx(fr, c)]; })()")
    assert out[0] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    bw, bh = _dims(1920, 1080, 33)
    assert out[1] == pytest.approx([0.1 * bw, 0.2 * bh, 0.3 * bw, 0.4 * bh])


@requires_node
def test_contains_agrees_with_the_python_check():
    fr = "tiltFrame(320, 240, 45)"
    bw, bh = _dims(320, 240, 45)
    centre = [bw / 2 - 20, bh / 2 - 15, 40, 30]     # interior
    corner = [0, 0, 40, 30]                          # over a black corner
    out = _call(f"[tiltContains({fr}, {centre}), tiltContains({fr}, {corner})]")
    assert out == [True, False]
    assert _black_free(320, 240, 45, centre) and not _black_free(320, 240, 45, corner)


# ---- the inscribed box (Fit inside with no crop of your own) -----------------------------------


@requires_node
def test_inscribed_is_the_whole_frame_off_a_free_angle():
    out = _call("[0, 90, 180, 270].map((d) => tiltInscribed(tiltFrame(1920, 1080, d)))")
    assert out == [[0, 0, 1, 1]] * 4


@requires_node
def test_inscribed_is_the_largest_black_free_box_of_the_source_shape():
    exprs = ", ".join(f"tiltToPx(tiltFrame({W}, {H}, {d}), tiltInscribed(tiltFrame({W}, {H}, {d})))"
                      for W, H, d in CASES)
    rects = _call(f"[{exprs}]")
    for (W, H, d), rect in zip(CASES, rects):
        bw, bh = _dims(W, H, d)
        x, y, w, h = rect
        assert _black_free(W, H, d, rect, eps=1e-6)
        assert not _black_free(W, H, d, _scaled(rect, 1.001)), "not tight"
        assert w / h == pytest.approx(W / H)                       # the source's shape
        assert x + w / 2 == pytest.approx(bw / 2) and y + h / 2 == pytest.approx(bh / 2)
        # and it is exactly the server's whole-frame cover: W / _fill_scale
        assert w == pytest.approx(W / _fill_scale(W, H, d))


# ---- fitting (a placed crop under Fit inside as the picture turns) -----------------------------


@requires_node
def test_fit_leaves_an_interior_rect_alone_and_does_nothing_off_a_free_angle():
    bw, bh = _dims(320, 240, 45)
    rect = [bw / 2 - 20, bh / 2 - 15, 40, 30]
    out = _call(f"[tiltFit(tiltFrame(320, 240, 45), {rect}),"
                f" tiltFit(tiltFrame(320, 240, 90), [0, 0, 40, 30])]")
    assert out[0] == pytest.approx(rect)
    assert out[1] == pytest.approx([0, 0, 40, 30])


@requires_node
def test_fit_slides_a_rect_that_fits_instead_of_shrinking_it():
    # 30x20 over the black top-left corner at 45 deg: it fits on the picture, so it is moved, not cut
    out = _call("tiltFit(tiltFrame(320, 240, 45), [0, 0, 30, 20])")
    assert out[2:] == pytest.approx([30, 20])
    assert _black_free(320, 240, 45, out, eps=1e-6)
    assert out[0] > 0 and out[1] > 0
    # and only as far as needed: half a pixel back toward where it came from is black again
    back = [out[0] - 0.5, out[1] - 0.5, 30, 20]
    assert not _black_free(320, 240, 45, back)


@requires_node
def test_fit_shrinks_keeping_the_aspect_only_when_no_position_can_hold_the_rect():
    for W, H, d in CASES:
        bw, bh = _dims(W, H, d)
        rect = [bw * 0.05, bh * 0.1, bw * 0.8, bh * 0.7]     # far bigger than the picture allows
        out = _call(f"tiltFit(tiltFrame({W}, {H}, {d}), {rect})")
        x, y, w, h = out
        assert _black_free(W, H, d, out, eps=1e-6 * max(bw, bh))
        assert w / h == pytest.approx(rect[2] / rect[3])
        assert w < rect[2]
        assert not _black_free(W, H, d, _scaled(out, 1.001)), "shrunk more than needed"


# ---- following the content as the angle changes ------------------------------------------------


@requires_node
def test_follow_keeps_the_centre_on_the_same_point_of_the_picture_and_the_size():
    W, H = 1920, 1080
    crop = [0.55, 0.3, 0.2, 0.25]
    out = _call(f"(() => {{ const a = tiltFrame({W}, {H}, 5), b = tiltFrame({W}, {H}, 20);"
                f" const c = tiltFollow(a, b, {crop}); return [tiltToPx(a, {crop}), tiltToPx(b, c)]; }})()")
    before, after = out
    sb = _to_source(W, H, 5, before[0] + before[2] / 2, before[1] + before[3] / 2)
    sa = _to_source(W, H, 20, after[0] + after[2] / 2, after[1] + after[3] / 2)
    assert sa == pytest.approx(sb)
    assert after[2:] == pytest.approx(before[2:])


@requires_node
def test_follow_round_trips_and_keeps_the_full_frame_full():
    out = _call("(() => { const a = tiltFrame(1920, 1080, 0), b = tiltFrame(1920, 1080, 37);"
                " const c = [0.4, 0.4, 0.2, 0.2];"
                " return [tiltFollow(b, a, tiltFollow(a, b, c)), tiltFollow(a, b, [0, 0, 1, 1])]; })()")
    assert out[0] == pytest.approx([0.4, 0.4, 0.2, 0.2])
    assert out[1] == [0, 0, 1, 1]


@requires_node
def test_follow_shrinks_only_when_the_new_box_cannot_hold_the_rect():
    # a full-width strip at 0 is 320 px wide; the box at 80 deg is only ~292 wide
    out = _call("(() => { const a = tiltFrame(320, 240, 0), b = tiltFrame(320, 240, 80);"
                " return tiltToPx(b, tiltFollow(a, b, [0, 0.4, 1, 0.2])); })()")
    bw, bh = _dims(320, 240, 80)
    x, y, w, h = out
    assert w == pytest.approx(bw)                       # shrunk to fit ...
    assert w / h == pytest.approx(320 / 48)             # ... aspect kept
    assert _in_frame(320, 240, 80, out)


# ---- the constrained drag ------------------------------------------------------------------------


@requires_node
def test_move_keeps_the_size_and_a_zero_delta_is_the_identity_even_when_touching():
    W, H, d = 320, 240, 45
    bw, bh = _dims(W, H, d)
    interior = [bw / 2 - 30, bh / 2 - 20, 60, 40]
    out = _call(f"(() => {{ const fr = tiltFrame({W}, {H}, {d});"
                f" const ins = tiltToPx(fr, tiltInscribed(fr));"
                f" return [tiltDrag(fr, {interior}, 'move', 0, 0, 0.02),"
                f" tiltDrag(fr, ins, 'move', 0, 0, 0.02), ins,"
                f" tiltDrag(fr, {interior}, 'move', 7, -4, 0.02)]; }})()")
    assert out[0] == pytest.approx(interior)
    assert out[1] == pytest.approx(out[2], abs=1e-6)         # the inscribed box cannot move, and does not jump
    assert out[3] == pytest.approx([interior[0] + 7, interior[1] - 4, 60, 40])   # a free move is exact


@requires_node
def test_move_stops_at_the_picture_and_slides_along_it():
    # a shallow angle, and pulls that only just cross the picture's right side: that side is an
    # EDGE there, so the two pulls land on different points of it. (At 45 deg the side is a vertex,
    # and a pull thousands of px past any edge projects to its end vertex - both meet one point.)
    W, H, d = 1920, 1080, 12.5
    bw, bh = _dims(W, H, d)
    rect = [bw / 2 - 30, bh / 2 - 20, 60, 40]
    out = _call(f"(() => {{ const fr = tiltFrame({W}, {H}, {d});"
                f" return [tiltDrag(fr, {rect}, 'move', 1000, 0, 0.02),"
                f" tiltDrag(fr, {rect}, 'move', 1000, 200, 0.02),"
                f" tiltDrag(fr, {rect}, 'move', -5000, -5000, 0.02)]; }})()")
    for r in out:
        assert r[2:] == pytest.approx([60, 40])
        assert _black_free(W, H, d, r, eps=1e-6) and _in_frame(W, H, d, r)
    assert out[0][0] > rect[0]                                   # it did move toward the pointer
    assert not _black_free(W, H, d, [out[0][0] + 0.5, out[0][1], 60, 40])   # and is against the edge
    assert out[1] != pytest.approx(out[0])                       # a different pull, a different spot


@requires_node
def test_corner_drag_keeps_the_anchor_and_the_minimum_and_stays_on_the_picture():
    W, H, d = 1920, 1080, 12.5
    bw, bh = _dims(W, H, d)
    rect = [bw * 0.3, bh * 0.3, bw * 0.2, bh * 0.2]
    out = _call(f"(() => {{ const fr = tiltFrame({W}, {H}, {d});"
                f" return [tiltDrag(fr, {rect}, 'se', 5000, 5000, 0.02),"
                f" tiltDrag(fr, {rect}, 'se', -5000, -5000, 0.02),"
                f" tiltDrag(fr, {rect}, 'nw', -5000, 5000, 0.02),"
                f" tiltDrag(fr, {rect}, 'ne', 3, -2, 0.02)]; }})()")
    for r in out:
        assert _black_free(W, H, d, r, eps=1e-6) and _in_frame(W, H, d, r)
    x, y, w, h = rect
    assert out[0][:2] == pytest.approx([x, y])                    # se drag: the nw corner anchors
    assert not _black_free(W, H, d, [x, y, out[0][2] + 1, out[0][3] + 1]), "not against the edge"
    assert out[1][:2] == pytest.approx([x, y])
    assert out[1][2:] == pytest.approx([bw * 0.02, bh * 0.02])    # collapsed onto the minimum
    assert out[2][0] + out[2][2] == pytest.approx(x + w)          # nw drag: the se corner anchors
    assert out[2][1] + out[2][3] == pytest.approx(y + h)
    assert out[3] == pytest.approx([x, y - 2, w + 3, h + 2])      # a small free drag is exact


@requires_node
def test_a_random_walk_never_puts_black_under_the_crop():
    """Sixty random moves and corner drags per case, seeded so a failure reproduces: every rect
    stays on the picture and inside the frame, moves keep the size, corners keep the minimum."""
    cases = json.dumps(CASES)
    walk = _run(f"""
const rng = (seed) => () => {{ seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }};
const out = [];
for (const [W, H, d] of {cases}) {{
    const fr = tiltFrame(W, H, d);
    const r = rng(W * 7 + H * 13 + Math.round(d * 100));
    const ins = tiltToPx(fr, tiltInscribed(fr));
    let rect = [ins[0] + ins[2] / 4, ins[1] + ins[3] / 4, ins[2] / 2, ins[3] / 2];
    const steps = [];
    for (let i = 0; i < 60; i++) {{
        const mode = ["move", "nw", "ne", "sw", "se"][Math.floor(r() * 5)];
        const dx = (r() - 0.5) * 2 * fr.bw, dy = (r() - 0.5) * 2 * fr.bh;
        rect = tiltDrag(fr, rect, mode, dx, dy, 0.02);
        steps.push([mode, rect]);
    }}
    out.push(steps);
}}
console.log(JSON.stringify(out));
""")
    for (W, H, d), steps in zip(CASES, walk):
        bw, bh = _dims(W, H, d)
        eps = 1e-6 * max(bw, bh)
        prev = None
        for mode, rect in steps:
            assert _black_free(W, H, d, rect, eps=eps), (W, H, d, mode, rect)
            assert _in_frame(W, H, d, rect, eps=eps), (W, H, d, mode, rect)
            assert rect[2] >= 0.02 * bw - eps and rect[3] >= 0.02 * bh - eps, (W, H, d, mode, rect)
            if mode == "move" and prev is not None:
                assert rect[2:] == pytest.approx(prev[2:])
            prev = rect


# ---- the polygon helpers ------------------------------------------------------------------------


@requires_node
def test_polygon_helpers():
    sq = "[[0, 0], [10, 0], [10, 10], [0, 10]]"
    out = _call(f"[clipPolyToBox({sq}, 5, 5, 20, 20), clipPolyToBox({sq}, 20, 20, 30, 30),"
                f" nearestInPoly({sq}, [3, 4]), nearestInPoly({sq}, [15, 5]), nearestInPoly({sq}, [15, 15]),"
                f" polyChord({sq}, 0, 5), polyChord({sq}, 1, 10), polyChord({sq}, 0, 11),"
                f" polyContains({sq}, [5, 5]), polyContains({sq}, [10, 5]), polyContains({sq}, [10.01, 5]),"
                f" polyContains([[1, 1], [1, 1], [1, 1]], [1, 1])]")
    clipped, empty, inside, right, corner, chx, chy, miss, c_in, c_edge, c_out, c_degenerate = out
    assert sorted(map(tuple, clipped)) == [(5, 5), (5, 10), (10, 5), (10, 10)]
    assert empty == []
    assert inside == [3, 4]
    assert right == pytest.approx([10, 5])
    assert corner == pytest.approx([10, 10])
    assert chx == [0, 10] and chy == [0, 10] and miss is None
    assert c_in and c_edge and not c_out and not c_degenerate
