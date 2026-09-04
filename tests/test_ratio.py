"""parseRatio - the custom crop-ratio field's parser (W:H -> number), run under node."""
import json, pathlib, shutil, subprocess
import pytest

REFPACK_JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "refpack.js"
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract():
    t = REFPACK_JS.read_text(encoding="utf-8")
    s, e = t.find("// >>> MMRP-RATIO"), t.find("// <<< MMRP-RATIO")
    assert s != -1 and e != -1, "the MMRP-RATIO markers are gone from web/refpack.js"
    return t[s:e].replace("export ", "")


def _call(expr):
    p = subprocess.run([NODE, "--input-type=module", "-e", _extract() + f"\nconsole.log(JSON.stringify({expr}));"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


@requires_node
def test_valid_ratios():
    assert _call('parseRatio("3:2")') == pytest.approx(1.5)
    assert _call('parseRatio("16:9")') == pytest.approx(16 / 9)
    assert _call('parseRatio(" 4 / 3 ")') == pytest.approx(4 / 3)   # slashes + whitespace
    assert _call('parseRatio("2x1")') == pytest.approx(2.0)         # x separator
    assert _call('parseRatio("1.5:1")') == pytest.approx(1.5)       # decimals


@requires_node
def test_invalid_ratios_are_null():
    for bad in ["", "3:", ":2", "abc", "3:0", "0:2", "-3:2", "3", "3:2:1"]:
        assert _call(f'parseRatio({json.dumps(bad)})') is None, bad
