"""Which video a sound-chip click means, executed under node.

The soundtrack chip used to look its video up by FILE NAME, which picks the first
reference carrying that name. Nothing stops two references sharing one: `addFiles` does no
de-duplication, and an upload with an existing name overwrites on the server and comes back
under that same name. So clicking the chip on the second copy of a clip toggled the first -
the tile the user clicked did not change, and a different one did.

Extracted from between the MMRP-SOUND markers and run under node, the same harness
tests/test_migration.py uses, so this exercises the shipped function rather than a copy of
it that can drift.
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


def _extract_sound_js() -> str:
    text = REFPACK_JS.read_text(encoding="utf-8")
    start = text.find("// >>> MMRP-SOUND")
    end = text.find("// <<< MMRP-SOUND")
    assert start != -1 and end != -1, (
        "the MMRP-SOUND markers are gone from web/refpack.js - this test extracts the "
        "block between them, and without them it would be testing nothing"
    )
    return text[start:end]


def _run_js(expression: str):
    script = _extract_sound_js() + f"\nconsole.log(JSON.stringify({expression}));\n"
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}"
    return json.loads(proc.stdout.strip())


@requires_node
def test_the_clicked_copy_is_the_one_that_flips():
    """The bug, stated as a test: three references, two of them the same file."""
    out = _run_js("""(() => {
        const videos = [
            { file: "clip.mp4", use_soundtrack: false },
            { file: "other.mp4", use_soundtrack: false },
            { file: "clip.mp4", use_soundtrack: false },
        ];
        const after = flipSoundtrackAt(videos, 2);
        return { on: after.map((v) => v.use_soundtrack) };
    })()""")
    assert out["on"] == [False, False, True], (
        "clicking the second copy of clip.mp4 must flip THAT one, not the first"
    )


@requires_node
def test_flipping_is_reversible_and_touches_nothing_else():
    out = _run_js("""(() => {
        const videos = [
            { file: "a.mp4", use_soundtrack: true, crop: [0, 0, 1, 1] },
            { file: "a.mp4", use_soundtrack: false },
        ];
        const once = flipSoundtrackAt(videos, 0);
        const back = flipSoundtrackAt(once, 0);
        return {
            once: once.map((v) => v.use_soundtrack),
            back: back.map((v) => v.use_soundtrack),
            cropSurvived: JSON.stringify(back[0].crop),
            originalUntouched: videos[0].use_soundtrack,
        };
    })()""")
    assert out["once"] == [False, False]
    assert out["back"] == [True, False]
    assert out["cropSurvived"] == "[0,0,1,1]", "the flip must not drop other fields"
    assert out["originalUntouched"] is True, (
        "it must not mutate its input - the caller clones and compares"
    )


@requires_node
@pytest.mark.parametrize("index", [-1, 2, 99, "0", None, 1.5])
def test_an_index_that_is_not_a_slot_changes_nothing(index):
    """A repaint landing between the click and the handler can leave a stale index, and a
    hit region is only as trustworthy as the last frame. Returning null is what lets the
    caller do nothing rather than flip an arbitrary video."""
    out = _run_js(f"""(() => {{
        const videos = [{{ file: "a.mp4", use_soundtrack: false }},
                        {{ file: "b.mp4", use_soundtrack: false }}];
        return {{ result: flipSoundtrackAt(videos, {json.dumps(index)}) }};
    }})()""")
    assert out["result"] is None


@requires_node
def test_a_missing_list_is_refused_rather_than_thrown():
    out = _run_js("""(() => ({
        nullList: flipSoundtrackAt(null, 0),
        notAList: flipSoundtrackAt("videos", 0),
        empty: flipSoundtrackAt([], 0),
    }))()""")
    assert out["nullList"] is None
    assert out["notAList"] is None
    assert out["empty"] is None
