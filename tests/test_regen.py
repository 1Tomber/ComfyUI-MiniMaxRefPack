"""The Regenerate-Prompt control's pure logic, executed under node.

One <select> shows AND sets what the next queue does: Cached (reuse), Next (regenerate
once, then back to Cached), Always (regenerate every queue). It reads as Next automatically
the moment an input changes (dirty). regenStateFrom is that decision; regenEnvelopeToken is
the token IS_CHANGED reads off the references_json envelope, and must mirror nodes.py.
Pure, marker-delimited.
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


def _extract() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-REGEN")
    end = text.find("// <<< MMRP-REGEN")
    assert start != -1 and end != -1, "the MMRP-REGEN markers are gone from web/refpack.js"
    return text[start:end].replace("export ", "")


def _call(expr):
    script = _extract() + f"\nconsole.log(JSON.stringify({expr}));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


# ---- regenStateFrom(mode, force, lastSig, curSig) ------------------------------------


@requires_node
def test_always_always_reads_as_always():
    assert _call('regenStateFrom("always", false, "sig", "sig")') == "always"
    assert _call('regenStateFrom("always", true, null, "x")') == "always"


@requires_node
def test_a_forced_one_shot_is_next():
    assert _call('regenStateFrom("cached", true, "sig", "sig")') == "next"


@requires_node
def test_a_changed_input_is_next_automatically():
    # dirty: the current signature differs from the one captured at the last generation
    assert _call('regenStateFrom("cached", false, "old", "new")') == "next"


@requires_node
def test_a_clean_resting_node_is_cached():
    assert _call('regenStateFrom("cached", false, "sig", "sig")') == "cached"


@requires_node
def test_never_generated_yet_reads_as_next():
    # no baseline captured -> the next queue will run
    assert _call('regenStateFrom("cached", false, null, "sig")') == "next"


# ---- regenEnvelopeToken(mode, nonce) - must mirror nodes.py --------------------------


@requires_node
def test_always_writes_the_always_token():
    assert _call('regenEnvelopeToken("always", 0)') == "always"
    assert _call('regenEnvelopeToken("always", 5)') == "always"


@requires_node
def test_a_bumped_nonce_is_the_token_and_zero_is_omitted():
    assert _call('regenEnvelopeToken("cached", 3)') == 3
    assert _call('regenEnvelopeToken("cached", 0)') is None   # null -> omitted from the envelope
