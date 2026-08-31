"""Which model id each provider sends, and the guarantee that they cannot cross.

The bug this file exists to prevent, hit live against OpenRouter 2026-08-17:

    prompt generation failed: openrouter returned 400:
    google/gemma-4-e2b is not a valid model ID

`model_override` was a single generic field that won over the dropdown for EVERY
provider. Set up a local run, switch back to openrouter to compare, and the local slug
was still sitting there and still won. The fix is structural rather than defensive: each
provider reads its own field and cannot see the other's, so there is no ordering or
precedence rule left to get wrong.
"""

import sys
import types

import pytest

from minimax_refpack import endpoint, nodes, refs


@pytest.fixture
def env(tmp_path, monkeypatch):
    module = types.ModuleType("folder_paths")
    module.get_input_directory = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "folder_paths", module)
    return tmp_path


@pytest.fixture
def sent(monkeypatch):
    """The kwargs write_prompt was called with."""
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return "a prompt"

    monkeypatch.setattr(nodes.prompt, "write_prompt", fake, raising=False)
    return seen


def _build(**kw):
    return nodes.MiniMaxH3ReferencePack().build(**kw)


# ---- the two fields never cross --------------------------------------------------


def test_openrouter_sends_the_dropdown_and_ignores_the_local_slug(env, sent):
    """The exact live failure: a leftover local slug must not reach OpenRouter."""
    _build(
        direction="d", prompt_provider="openrouter",
        openrouter_model="google/gemini-3-flash-preview",
        local_model_slug="google/gemma-4-e2b",
        api_base="http://127.0.0.1:1234/v1",
    )
    assert sent["model"] == "google/gemini-3-flash-preview"


def test_local_sends_the_slug_and_ignores_the_dropdown(env, sent):
    _build(
        direction="d", prompt_provider="local",
        openrouter_model="google/gemini-3-flash-preview",
        local_model_slug="google/gemma-4-e2b",
        api_base="http://127.0.0.1:1234/v1",
    )
    assert sent["model"] == "google/gemma-4-e2b"


def test_switching_provider_back_and_forth_never_carries_a_model_across(env, sent):
    """The user's actual workflow: configure local, switch to openrouter, run."""
    common = dict(
        direction="d", openrouter_model="google/gemini-3-flash-preview",
        local_model_slug="google/gemma-4-e2b", api_base="http://127.0.0.1:1234/v1",
    )
    _build(prompt_provider="local", **common)
    assert sent["model"] == "google/gemma-4-e2b"
    sent.clear()
    _build(prompt_provider="openrouter", **common)
    assert sent["model"] == "google/gemini-3-flash-preview"
    assert "gemma" not in sent["model"]


# ---- local with nothing typed ----------------------------------------------------


def test_local_with_no_slug_fails_with_a_readable_error(env, sent):
    """The dropdown lists OpenRouter models a local server has never heard of, so
    silently falling back to it would 404 with someone else's model id."""
    with pytest.raises(ValueError) as e:
        _build(
            direction="d", prompt_provider="local",
            openrouter_model="google/gemini-3-flash-preview",
            local_model_slug="", api_base="http://127.0.0.1:1234/v1",
        )
    msg = str(e.value)
    assert "local_model_slug" in msg
    assert not sent, "no call may be made when there is no model to call"


def test_whitespace_only_slug_counts_as_empty(env, sent):
    with pytest.raises(ValueError):
        _build(
            direction="d", prompt_provider="local", openrouter_model="m",
            local_model_slug="   ", api_base="http://127.0.0.1:1234/v1",
        )


# ---- none makes no call at all ---------------------------------------------------


def test_provider_none_needs_no_model_of_either_kind(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("provider=none must not call write_prompt")

    monkeypatch.setattr(nodes.prompt, "write_prompt", boom, raising=False)
    out = _build(direction="straight through", prompt_provider="none",
                 openrouter_model="", local_model_slug="")
    assert out[refs.slot_index("prompt")] == "straight through"


# ---- old names still work --------------------------------------------------------


def test_a_legacy_model_kwarg_still_drives_openrouter(env, sent):
    """An API client posting a stored 0.3.1 prompt sends `model`, not `openrouter_model`."""
    _build(direction="d", prompt_provider="openrouter", model="google/gemini-3-flash-preview")
    assert sent["model"] == "google/gemini-3-flash-preview"


def test_a_legacy_model_override_still_drives_local(env, sent):
    _build(direction="d", prompt_provider="local", model="ignored",
           model_override="google/gemma-4-e2b", api_base="http://127.0.0.1:1234/v1")
    assert sent["model"] == "google/gemma-4-e2b"


def test_the_new_name_beats_the_legacy_one_when_both_arrive(env, sent):
    _build(direction="d", prompt_provider="openrouter",
           openrouter_model="new/model", model="old/model")
    assert sent["model"] == "new/model"


# ---- the widget contract ---------------------------------------------------------


def test_the_local_group_text_fields_are_single_line():
    """local_extra_body used to be a multiline widget, and a growable textarea fights the
    node's own resizing - reported live on the pod, where it grew and pushed the layout
    around. It is a one-line JSON field like api_base now, and this pins it there: the
    fix is one dropped flag that is trivial to re-add without noticing what it breaks."""
    optional = nodes.MiniMaxH3ReferencePack.INPUT_TYPES()["optional"]
    for name in ("api_base", "local_model_slug", "local_extra_body"):
        opts = optional[name][1] if len(optional[name]) > 1 else {}
        assert not opts.get("multiline"), (
            f"{name} is multiline again; a growable text box breaks the responsive node"
        )


def test_the_widgets_are_named_and_ordered_as_declared():
    spec = nodes.MiniMaxH3ReferencePack.INPUT_TYPES()
    assert list(spec["required"]) == ["direction"]
    # Grouped by decision flow (Aviv, 2026-08-17): the mode, then that mode's settings,
    # then what to write, then the target video, then reference prep. This order is a
    # WIRE FORMAT - widgets_values is positional - so changing it means updating
    # ORDER_0_3_3 in web/refpack.js too, which test_migration.py asserts.
    assert list(spec["required"]) + list(spec["optional"]) == [
        "direction", "references_json", "system_prompt", "prompt_provider",
        "openrouter_api_key", "openrouter_model", "reasoning_effort", "api_base",
        "local_model_slug", "job_type", "width", "height", "length_seconds",
        "max_reference_edge",
        "local_ttl", "local_server", "local_send_reasoning", "local_extra_body",
    ]


def test_the_debug_header_names_the_field_that_was_actually_used(env, sent):
    out = _build(direction="d", prompt_provider="local", openrouter_model="dropdown/model",
                 local_model_slug="local/model", api_base="http://127.0.0.1:1234/v1")
    debug = out[refs.slot_index("debug")]
    assert "local/model" in debug
    assert "dropdown/model" not in debug


# ---- the local server's own request fields reach the writer ----------------------


def test_the_local_settings_are_handed_to_the_writer(env, sent):
    _build(direction="d", prompt_provider="local", local_model_slug="m",
           api_base="http://127.0.0.1:1234/v1",
           local_ttl=1, local_server="lmstudio", local_send_reasoning=True,
           local_extra_body='{"top_k": 40}')
    assert sent["local_ttl"] == 1
    assert sent["local_server"] == "lmstudio"
    assert sent["local_send_reasoning"] is True
    assert sent["local_extra_body"] == '{"top_k": 40}'


def test_build_accepts_the_new_widgets_without_them_being_passed(env, sent):
    """build() has no **kwargs, unlike IS_CHANGED. Every declared widget must be a real
    parameter with a default or an API client replaying a stored prompt breaks at queue
    time - and so does a workflow saved before these widgets existed."""
    _build(direction="d", prompt_provider="local", local_model_slug="m",
           api_base="http://127.0.0.1:1234/v1")
    assert sent["local_ttl"] == endpoint.LOCAL_TTL_OFF
    assert sent["local_send_reasoning"] is False


def test_a_broken_extra_body_reads_as_a_user_error_not_a_crash(env):
    """It reaches endpoint.resolve as a ValueError. build() re-raises it wrapped, the
    same way it already does for "local with no api_base", so ComfyUI shows a sentence
    naming the widget instead of a traceback."""
    with pytest.raises(ValueError) as e:
        _build(direction="d", prompt_provider="local", local_model_slug="m",
               api_base="http://127.0.0.1:1234/v1", local_extra_body="{not json")
    assert "prompt generation failed" in str(e.value)
    assert "local_extra_body" in str(e.value)


def test_the_debug_header_states_the_auto_guess_and_the_field_it_picked(env, sent):
    out = _build(direction="d", prompt_provider="local", local_model_slug="m",
                 api_base="http://127.0.0.1:11434/v1", local_ttl=0, local_server="auto")
    debug = out[refs.slot_index("debug")]
    assert "auto:ollama" in debug
    assert "keep_alive" in debug


def test_the_debug_header_says_when_the_ttl_will_be_dropped(env, sent):
    """local_server=generic takes no idle-unload field, so a value typed into local_ttl
    goes nowhere. Saying so beats letting the user conclude their server ignored it."""
    out = _build(direction="d", prompt_provider="local", local_model_slug="m",
                 api_base="http://127.0.0.1:1234/v1", local_ttl=30, local_server="generic")
    assert "the value is dropped" in out[refs.slot_index("debug")]


def test_the_local_block_stays_out_of_an_openrouter_debug_header(env, sent):
    """Four settings that do nothing on this path would just be noise in the one output
    a user pastes into a bug report."""
    out = _build(direction="d", prompt_provider="openrouter", openrouter_model="m",
                 local_ttl=30, local_server="ollama")
    debug = out[refs.slot_index("debug")]
    assert "local_ttl" not in debug and "local_server" not in debug
