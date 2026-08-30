"""The browser and Python must read `use_soundtrack` the same way.

It is a flag with a default, which is exactly the shape that goes wrong twice: "absent
means on" and "present means whatever it is truthy as" are two rules, and each obvious
one-liner satisfies only one of them. `!!r.use_soundtrack` makes an absent key OFF;
`r.use_soundtrack !== false` makes null, 0 and "" ON. Both have shipped here.

It is not a cosmetic disagreement. A video's soundtrack claims its `<Audio N>` before any
standalone audio does, so one falsy flag renumbers every audio tag after it - the browser
shows one numbering while the queued payload uses another, and the next edit round-trips
the browser's answer back into the file.

The values only differ on a file somebody wrote by hand, because both serialisers always
write a real boolean. A hand-written portable config is precisely the case this feature
exists for, which is why the table below is exhaustive rather than illustrative.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from minimax_refpack.refs import Reference

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Every JSON value a hand-written file can put there. `absent` is its own case and is
# spelled by leaving the key out entirely, which is not the same as null.
CASES = [
    ("absent", ...),
    ("null", None),
    ("false", False),
    ("true", True),
    ("zero", 0),
    ("one", 1),
    ("empty string", ""),
    ("non-empty string", "yes"),
    ("empty list", []),
    ("non-empty list", [1]),
]


def _python_answer(value):
    entry = {"kind": "video", "file": "v.mp4"}
    if value is not ...:
        entry["use_soundtrack"] = value
    return Reference.from_dict(entry).use_soundtrack


def _js_answers():
    """fromReferencesList's answer for every case, in one node run."""
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("function fromReferencesList")
    assert start != -1, "fromReferencesList has been renamed; this test reads it directly"
    end = text.find("\n}\n", start) + 3
    def _grab(name):
        at = text.find(f"function {name}")
        assert at != -1, f"{name} has been renamed; this test reads it directly"
        return text[at:text.find(chr(10) + "}" + chr(10), at) + 3]

    helpers = _grab("takeEdit") + _grab("pythonTruthy")

    payload = []
    for _, value in CASES:
        entry = {"kind": "video", "file": "v.mp4"}
        if value is not ...:
            entry["use_soundtrack"] = value
        payload.append(entry)

    script = (
        helpers
        + text[start:end]
        + f"\nconst inputs = {json.dumps(payload)};\n"
        + "console.log(JSON.stringify(inputs.map("
        + "(e) => fromReferencesList([e]).videos[0].use_soundtrack)));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_both_halves_agree_on_every_json_value():
    js = _js_answers()
    disagreements = []
    for (label, value), js_answer in zip(CASES, js):
        py_answer = _python_answer(value)
        if bool(js_answer) != bool(py_answer):
            disagreements.append(f"{label}: browser={js_answer!r} python={py_answer!r}")
    assert not disagreements, (
        "the two halves read use_soundtrack differently, which renumbers every <Audio N> "
        "after the affected video:\n  " + "\n  ".join(disagreements)
    )


def test_python_side_is_absent_means_on_otherwise_truthiness():
    """Pins the contract the browser is being matched against, so if this side ever moves
    the parity test above cannot quietly start agreeing on the wrong answer."""
    assert _python_answer(...) is True, "an absent flag means ON"
    for falsy in (None, False, 0, "", []):
        assert _python_answer(falsy) is False, f"{falsy!r} should be OFF"
    for truthy in (True, 1, "yes", [1]):
        assert _python_answer(truthy) is True, f"{truthy!r} should be ON"
