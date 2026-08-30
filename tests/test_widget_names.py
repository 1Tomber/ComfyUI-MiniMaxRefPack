"""Every widget name the browser half reaches for must exist in the Python half.

A stale name is not an error anywhere. `widgetByName` returns undefined, the caller's
guard treats it as "absent" and moves on, and the feature is silently dead. That is what
happened to the config's model field: the widget was renamed `openrouter_model` in 0.3.3
and Save/Load kept asking for `model`, so every config written since carried an empty
model and Load restored nothing - without even reporting it as an unavailable setting,
because the empty value returned early before the lookup that would have failed.

README.md still promised Load restored the model. Nothing caught it because nothing
compared the two lists.
"""

import pathlib
import re

import pytest

from minimax_refpack import nodes

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REFPACK_JS = REPO_ROOT / "web" / "refpack.js"

# Not widgets: the DOM widget this pack adds itself, and the legacy names the migration
# block deliberately reads out of OLD saved workflows (they no longer exist by design -
# remapWidgetValues translates them, which is the whole point of that code).
NOT_WIDGETS = {"mmrp_block"}
LEGACY_MIGRATION_NAMES = {"model", "model_override", "use_openrouter"}


def _declared() -> set[str]:
    spec = nodes.MiniMaxH3ReferencePack.INPUT_TYPES()
    return set(spec.get("required", {})) | set(spec.get("optional", {}))


def _js() -> str:
    return REFPACK_JS.read_text(encoding="utf-8")


def test_every_widget_the_browser_asks_for_exists():
    """widgetByName with a literal - the direct lookups."""
    declared = _declared()
    asked = set(re.findall(r'widgetByName\(\s*\w+\s*,\s*"([^"]+)"', _js()))
    stale = sorted(asked - declared - NOT_WIDGETS - LEGACY_MIGRATION_NAMES)
    assert not stale, (
        f"web/refpack.js asks for widget(s) that nodes.py does not declare: {stale}. "
        f"widgetByName returns undefined and the caller quietly does nothing, so whatever "
        f"feature this belongs to is dead."
    )


def test_every_widget_the_browser_writes_exists():
    """setWidget with a literal - the write side, which fails just as quietly."""
    declared = _declared()
    written = set(re.findall(r'setWidget\(\s*\w+\s*,\s*"([^"]+)"', _js()))
    stale = sorted(written - declared - NOT_WIDGETS - LEGACY_MIGRATION_NAMES)
    assert not stale, f"web/refpack.js writes widget(s) that do not exist: {stale}"


def test_the_provider_visibility_groups_name_real_widgets():
    """PROVIDER_FIELDS decides what is shown per provider. A stale name there means a
    setting that is never hidden, or never shown."""
    declared = _declared()
    block = _js()
    start = block.find("const PROVIDER_FIELDS = {")
    assert start != -1, "PROVIDER_FIELDS moved"
    end = block.find("};", start)
    stale = sorted(set(re.findall(r'"([a-z_]+)"', block[start:end])) - declared
                   - {"openrouter", "local", "none"})
    assert not stale, f"PROVIDER_FIELDS names widget(s) that do not exist: {stale}"


def test_the_config_format_key_and_the_widget_it_restores_are_both_named():
    """The config file's key is `model` and the widget is `openrouter_model`. They differ
    on purpose - the file format stays stable so an older build can still read a config
    written here - so this pins that the pairing is deliberate rather than the old bug
    reintroduced."""
    js = _js()
    assert 'model: w("openrouter_model")' in js, "Save must read the widget, not the key"
    assert 'restore("openrouter_model", data.model)' in js, (
        "Load must write the widget from the file's key"
    )


@pytest.mark.parametrize("name", sorted(LEGACY_MIGRATION_NAMES))
def test_the_legacy_names_really_are_gone(name):
    """The exemption list above is only honest while these are genuinely absent. If one
    comes back as a real widget, the exemption would start hiding a live mistake."""
    assert name not in _declared()
