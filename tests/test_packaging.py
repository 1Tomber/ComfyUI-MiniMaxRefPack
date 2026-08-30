"""What reaches the ComfyUI registry, and what must never be in it.

Background, 2026-08-17. 0.3.0 published and went Active. 0.3.3 and 0.3.4 published and
both sat at NodeVersionStatusPending. The only material difference in the uploaded zips
was `tests/`, and inside it `test_migration.py`, which builds a string and executes it
under node via `subprocess.run([node, "-e", script])`. That is a fine way to test shipped
JS and a textbook automated-scanner trigger, and it had no reason to be in a package a
user installs.

Two rules follow, and this file holds both:
  1. Development apparatus does not ship.
  2. The code that DOES ship contains no dynamic execution, so there is nothing for a
     scanner to find in the first place.
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMFYIGNORE = REPO_ROOT / ".comfyignore"
PACKAGE = REPO_ROOT / "minimax_refpack"

# `comfy node pack` ships git-tracked files minus .comfyignore patterns, so anything here
# that is git-tracked and unignored lands in the zip.
MUST_NOT_SHIP = ("tests/", "conftest.py")

# Patterns an automated malware scanner looks for. The shipped package has never needed
# any of them: it makes HTTP calls and decodes media, and nothing more.
DYNAMIC_EXECUTION = re.compile(
    r"\bsubprocess\b|\bos\.system\b|\bPopen\b|(?<![\w.])eval\s*\(|(?<![\w.])exec\s*\(",
)


def test_a_comfyignore_exists():
    assert COMFYIGNORE.exists(), (
        "without .comfyignore, `comfy node pack` ships every git-tracked file, which is "
        "how the test suite ended up inside a published package"
    )


@pytest.mark.parametrize("pattern", MUST_NOT_SHIP)
def test_development_apparatus_is_excluded_from_the_package(pattern):
    lines = [
        line.strip() for line in COMFYIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert pattern in lines, f".comfyignore must exclude {pattern!r}; it lists {lines}"


@pytest.mark.parametrize(
    "path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: p.name
)
def test_the_shipped_package_never_executes_anything_dynamically(path):
    """Not defensive style: it is what keeps the registry's scanner quiet.

    If a future change genuinely needs a subprocess, that is a deliberate decision worth
    making loudly rather than discovering when a release sits in Pending for a day.
    """
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        assert not DYNAMIC_EXECUTION.search(code), (
            f"{path.name}:{number} uses dynamic execution in SHIPPED code:\n  {line.strip()}"
        )


def test_the_web_assets_ship():
    """The inverse failure: over-broad ignores that drop something the node needs."""
    for needed in ("web/refpack.js", "web/refpack.css", "__init__.py", "requirements.txt"):
        lines = [
            line.strip() for line in COMFYIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert needed not in lines, f"{needed} is required at runtime and must ship"


# ---- the declared Python floor is real ------------------------------------------
#
# pyproject declares requires-python >= 3.10, and ComfyUI imports this package at
# startup. A SyntaxError is NOT an ImportError, so __init__.py's own try/except fallback
# cannot catch one - a construct that only parses on a newer Python takes the whole node
# pack down rather than degrading.
#
# This exists because that happened: a debug-header f-string whose replacement field
# spanned a line break (PEP 701, 3.12+) compiled fine on the dev machine's 3.13 and
# raised "unterminated string literal" on 3.11.
#
# It has to COMPILE under an old interpreter. ast.parse(feature_version=(3, 10)) accepts
# that f-string happily - checked - because feature_version does not model the f-string
# tokenizer change. So a real interpreter is the only honest check, and the test skips
# rather than lying when one is not available.

import re
import shutil
import subprocess
import sys


def _declared_floor() -> tuple[int, int]:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"[><=~^]*\s*(\d+)\.(\d+)', text)
    assert m, "requires-python is missing from pyproject.toml"
    return int(m.group(1)), int(m.group(2))


def _floor_interpreter(major: int, minor: int) -> list[str] | None:
    """A command that runs the declared minimum Python, or None if we have none."""
    if sys.version_info[:2] == (major, minor):
        return [sys.executable]
    if shutil.which("uv"):
        return ["uv", "run", "--quiet", "--no-project", "--python",
                f"{major}.{minor}", "python"]
    exact = shutil.which(f"python{major}.{minor}")
    return [exact] if exact else None


def test_the_package_compiles_on_the_oldest_python_it_claims_to_support():
    major, minor = _declared_floor()
    runner = _floor_interpreter(major, minor)
    if runner is None:
        pytest.skip(f"no Python {major}.{minor} available to check against")
    files = sorted(str(p) for p in (REPO_ROOT / "minimax_refpack").glob("*.py"))
    files.append(str(REPO_ROOT / "__init__.py"))
    script = (
        "import py_compile, sys\n"
        "bad = []\n"
        "for f in sys.argv[1:]:\n"
        "    try:\n"
        "        py_compile.compile(f, cfile=None, doraise=True)\n"
        "    except py_compile.PyCompileError as e:\n"
        "        bad.append(str(e).strip().splitlines()[-1])\n"
        "for b in bad:" + chr(10) + "    print(b)" + chr(10)
    )
    proc = subprocess.run(runner + ["-c", script] + files,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert not proc.stdout.strip(), (
        f"does not compile on Python {major}.{minor}, which pyproject.toml claims to "
        f"support:\n{proc.stdout}"
    )


# ---- the stylesheet is not silently truncated ------------------------------------
#
# Nothing in this suite parsed refpack.css, and a merge resolution swallowed two closing
# braces. Under CSS nesting - which every Chromium ComfyUI has - the rules that followed
# did not error: they parsed as rules NESTED inside `.mmrp-tab.mmrp-active` and inside
# `.mmrp-modal-check input`, selectors they can never match. Seven rules went inert
# without a single warning anywhere: the settings-modal checkbox row, the orientation
# buttons' width floor, the rotation transition, the free-angle slider's sizing and its
# jitter-free readout, and the fit-inside toggle including its dimmed state.
#
# A brace-depth walk is enough to catch that and needs no CSS parser.

CSS = REPO_ROOT / "web" / "refpack.css"


def _css_without_comments(text: str) -> str:
    """Braces inside /* ... */ are prose, not structure."""
    out = []
    i = 0
    while i < len(text):
        start = text.find("/*", i)
        if start == -1:
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find("*/", start + 2)
        if end == -1:
            break
        i = end + 2
    return "".join(out)


def test_every_css_rule_is_closed():
    text = _css_without_comments(CSS.read_text(encoding="utf-8"))
    depth = 0
    for number, line in enumerate(text.splitlines(), 1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"refpack.css:{number} closes a rule that was never opened"
        # One level is a plain rule; a media query would legitimately reach two, and there
        # are none in this file. Deeper than that means a `}` went missing above.
        assert depth <= 1, (
            f"refpack.css:{number} is nested {depth} deep - a closing brace is missing "
            f"above it, and every rule from here on is scoped to a selector that cannot "
            f"match"
        )
    assert depth == 0, f"refpack.css ends inside {depth} unclosed rule(s)"
