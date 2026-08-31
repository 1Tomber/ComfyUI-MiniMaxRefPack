"""Which subjects the chip row shows, executed under node.

The bar above the prompt shows one <Subject N> chip per number actually in use, and is
hidden when none are. `assignedSubjectNumbers` is that derivation - sorted, unique, across
every reference and every kind - and it also decides the bar's height contribution, so a
node with no subjects stays exactly as tall as before the feature existed.

Extracted from between the MMRP-SUBJBAR markers and run under node, the same harness
tests/test_migration.py uses.
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
    start = text.find("// >>> MMRP-SUBJBAR")
    end = text.find("// <<< MMRP-SUBJBAR")
    assert start != -1 and end != -1, (
        "the MMRP-SUBJBAR markers are gone from web/refpack.js - this test extracts the "
        "shipped derivation through them"
    )
    return text[start:end]


def _nums(refs):
    script = _extract() + f"\nconsole.log(JSON.stringify(assignedSubjectNumbers({json.dumps(refs)})));\n"
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_no_subjects_means_an_empty_list():
    """This is what hides the bar and keeps the node its original height."""
    assert _nums({"images": [{"file": "a.png"}], "videos": [], "audios": []}) == []
    assert _nums({}) == []
    assert _nums(None) == []


@requires_node
def test_numbers_are_unique_and_sorted_across_every_kind():
    refs = {
        "images": [{"file": "a", "subjects": [3, 1]}, {"file": "b", "subjects": [1]}],
        "videos": [{"file": "v", "subjects": [2]}],
        "audios": [{"file": "s", "subjects": [3]}],
    }
    assert _nums(refs) == [1, 2, 3]


@requires_node
def test_a_reference_can_carry_several_subjects():
    refs = {"images": [{"file": "a", "subjects": [1, 4, 2]}], "videos": [], "audios": []}
    assert _nums(refs) == [1, 2, 4]


@requires_node
def test_out_of_range_and_non_integer_numbers_are_ignored():
    """The bar mirrors refs.validate_subjects (1..9, whole numbers), so a hand-edited value
    outside that range does not conjure a chip that the server would reject anyway."""
    refs = {"images": [{"file": "a", "subjects": [0, 10, 2.5, -1, 5]}],
            "videos": [], "audios": []}
    assert _nums(refs) == [5]


@requires_node
def test_a_missing_subjects_field_is_harmless():
    refs = {"images": [{"file": "a"}, {"file": "b", "subjects": None}],
            "videos": [], "audios": []}
    assert _nums(refs) == []
