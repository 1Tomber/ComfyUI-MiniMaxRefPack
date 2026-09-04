"""_fill_scale: the 'fit inside' (rotate_expand=False) scale that makes a rotated frame cover the
source box with no black corners. Pure math, so it imports media directly (torch/PIL are lazy)."""
import math
from minimax_refpack.media import _fill_scale


def test_no_rotation_is_identity():
    assert _fill_scale(1920, 1080, 0) == 1.0
    assert _fill_scale(1920, 1080, 180) == 1.0   # 180 % 180 == 0


def test_square_at_45_is_root_two():
    # a square rotated 45 deg needs sqrt(2) so its inscribed square covers the original
    assert _fill_scale(500, 500, 45) == math.sqrt(2)


def test_matches_the_formula():
    for w, h, deg in [(1920, 1080, 30), (1080, 1920, 12.5), (640, 640, 63)]:
        r = math.radians(deg % 180)
        expected = abs(math.cos(r)) + max(w / h, h / w) * abs(math.sin(r))
        assert _fill_scale(w, h, deg) == expected


def test_always_at_least_one_for_any_angle():
    # it never shrinks (would reintroduce black); grows with the angle away from a quarter turn
    for deg in range(0, 180, 7):
        assert _fill_scale(1280, 720, deg) >= 1.0 - 1e-9


def test_degenerate_size_is_one():
    assert _fill_scale(0, 100, 30) == 1.0
    assert _fill_scale(100, 0, 30) == 1.0
