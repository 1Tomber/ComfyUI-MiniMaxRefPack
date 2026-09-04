"""_fill_scale: the 'fit inside' (rotate_expand=False) straighten fill - the minimal scale that
keeps the KEPT crop free of black when the frame is rotated about its centre. crop=None means the
whole frame, which reduces to the classic cover scale f = |cosθ| + max(w/h,h/w)|sinθ|. Pure math,
so it imports media directly (torch/PIL are lazy)."""
import math

import pytest

from minimax_refpack.media import _fill_scale


def _cover(w, h, deg):
    r = math.radians(deg % 180)
    return abs(math.cos(r)) + max(w / h, h / w) * abs(math.sin(r))


# ---- whole frame (crop=None) reduces to the classic cover scale ---------------------------


def test_no_rotation_is_identity():
    assert _fill_scale(1920, 1080, 0) == 1.0


def test_square_at_45_is_root_two():
    # a square rotated 45 deg needs sqrt(2) so its inscribed square covers the original
    assert _fill_scale(500, 500, 45) == pytest.approx(math.sqrt(2))


def test_whole_frame_matches_the_cover_formula():
    for w, h, deg in [(1920, 1080, 30), (1080, 1920, 12.5), (640, 640, 63), (1280, 720, 180)]:
        assert _fill_scale(w, h, deg) == pytest.approx(_cover(w, h, deg))


def test_full_crop_equals_no_crop():
    # the crop spanning the whole frame must give exactly what crop=None gives
    for w, h, deg in [(1920, 1080, 30), (800, 600, 47)]:
        assert _fill_scale(w, h, deg, [0, 0, 1, 1]) == pytest.approx(_fill_scale(w, h, deg))


def test_always_at_least_one_for_any_angle():
    # it never shrinks (would reintroduce black); grows with the angle away from a quarter turn
    for deg in range(0, 180, 7):
        assert _fill_scale(1280, 720, deg) >= 1.0 - 1e-9


def test_degenerate_size_is_one():
    assert _fill_scale(0, 100, 30) == 1.0
    assert _fill_scale(100, 0, 30) == 1.0


# ---- the straighten point: a crop zooms only as much as IT needs --------------------------


def test_small_centred_crop_needs_no_zoom():
    # a little interior crop is nowhere near the black corners, so any angle leaves it at 1.0
    for deg in (10, 30, 45, 80):
        assert _fill_scale(1000, 1000, deg, [0.4, 0.4, 0.2, 0.2]) == pytest.approx(1.0)


def test_interior_crop_zooms_less_than_the_whole_frame():
    # the whole point of the change: an off-centre-but-interior crop needs strictly less fill
    # than covering the entire frame would
    crop = [0.2, 0.25, 0.4, 0.35]
    for w, h, deg in [(1920, 1080, 20), (1080, 1920, 35)]:
        assert _fill_scale(w, h, deg, crop) < _fill_scale(w, h, deg) - 1e-6


def test_crop_reaching_a_corner_matches_the_full_cover():
    # a crop whose corner sits at the frame corner is as demanding as the whole frame on that side
    w, h, deg = 1600, 900, 18
    # crop that still touches all four extremes == full frame
    assert _fill_scale(w, h, deg, [0.0, 0.0, 1.0, 1.0]) == pytest.approx(_fill_scale(w, h, deg))


def test_fill_keeps_the_crop_black_free():
    # THE invariant, checked geometrically: with f from _fill_scale, every crop corner
    # inverse-rotated about the centre and un-scaled lands back inside [0,w]x[0,h] (no black).
    w, h = 1280, 720
    for deg in (7, 23, 41, 66):
        for crop in ([0.1, 0.1, 0.5, 0.4], [0.0, 0.3, 1.0, 0.4], [0.35, 0.35, 0.3, 0.3]):
            f = _fill_scale(w, h, deg, crop)
            r = math.radians(deg)
            cos, sin = math.cos(r), math.sin(r)
            x0, y0, cw, ch = crop
            for fx in (x0, x0 + cw):
                for fy in (y0, y0 + ch):
                    dx, dy = fx * w - w / 2, fy * h - h / 2
                    # inverse-rotate then un-scale, offset from the centre
                    sx = w / 2 + (cos * dx + sin * dy) / f
                    sy = h / 2 + (-sin * dx + cos * dy) / f
                    assert -1e-6 <= sx <= w + 1e-6, f"{deg} {crop} sx={sx}"
                    assert -1e-6 <= sy <= h + 1e-6, f"{deg} {crop} sy={sy}"
