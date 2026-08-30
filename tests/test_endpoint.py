"""Tests for minimax_refpack.endpoint — the one place that knows where a call goes.

This module is deliberately pure: no network, no exceptions, no key lookup. It answers
"given a provider and a base URL, what URL do I post to, what may I send, and how long do
I wait". Key POLICY stays in prompt.py because `_resolve_api_key` is a pinned public
surface with its own tests, and because raising from here would mean importing PromptError
out of prompt.py, which imports this module.

The security-relevant rule under test: a `local` endpoint never inherits the OpenRouter
URL, so a run the user believes is local cannot silently reach openrouter.ai.
"""

import pytest

from minimax_refpack import endpoint as ep


def test_openrouter_is_the_default_and_keeps_todays_urls():
    e = ep.resolve("openrouter", "")
    assert e.chat_url == "https://openrouter.ai/api/v1/chat/completions"
    assert e.models_url == "https://openrouter.ai/api/v1/models"
    assert e.is_openrouter is True


def test_openrouter_accepts_every_modality_and_sends_reasoning():
    e = ep.resolve("openrouter", "")
    assert e.accepts == frozenset({"text", "image", "audio", "video"})
    assert e.sends_reasoning is True
    assert e.requires_key is True


def test_openrouter_keeps_todays_timeouts():
    e = ep.resolve("openrouter", "")
    assert (e.chat_timeout, e.classify_timeout, e.models_timeout) == (120, 20, 5)


def test_local_builds_its_urls_from_the_base():
    e = ep.resolve("local", "http://localhost:1234/v1")
    assert e.chat_url == "http://localhost:1234/v1/chat/completions"
    assert e.models_url == "http://localhost:1234/v1/models"
    assert e.is_openrouter is False


@pytest.mark.parametrize("base", [
    "http://localhost:1234/v1/",
    "http://localhost:1234/v1//",
    "  http://localhost:1234/v1  ",
])
def test_a_trailing_slash_or_stray_whitespace_does_not_double_up(base):
    """Users paste URLs. A '//chat/completions' 404s on some servers and not others."""
    assert ep.resolve("local", base).chat_url == "http://localhost:1234/v1/chat/completions"


def test_local_takes_text_and_images_only():
    """Verified against Ollama's docs 2026-08-17: /v1/chat/completions documents vision
    via base64 images; video and audio are not part of the OpenAI-compatible surface."""
    e = ep.resolve("local", "http://localhost:1234/v1")
    assert e.accepts == frozenset({"text", "image"})
    assert "video" not in e.accepts
    assert "audio" not in e.accepts


def test_local_does_not_send_reasoning_and_does_not_demand_a_key():
    e = ep.resolve("local", "http://localhost:1234/v1")
    assert e.sends_reasoning is False
    assert e.requires_key is False


def test_local_waits_much_longer():
    """A 7B model on CPU blows straight through 120s."""
    e = ep.resolve("local", "http://localhost:1234/v1")
    assert e.chat_timeout >= 900
    assert e.classify_timeout >= 120


def test_a_local_endpoint_never_points_at_openrouter():
    """The whole privacy claim rests on this."""
    e = ep.resolve("local", "http://localhost:1234/v1")
    assert "openrouter.ai" not in e.chat_url
    assert "openrouter.ai" not in e.models_url


def test_local_with_no_base_url_is_an_error_the_caller_can_report():
    """Returning a half-built endpoint would post to '/chat/completions' on nothing."""
    with pytest.raises(ValueError) as exc:
        ep.resolve("local", "   ")
    assert "api_base" in str(exc.value)


def test_an_unknown_provider_falls_back_to_openrouter():
    """Mirrors prompt.py:510-511 - an unrecognised widget value must never become a 400."""
    assert ep.resolve("banana", "").is_openrouter is True


@pytest.mark.parametrize("legacy,expected", [(True, "openrouter"), (False, "none")])
def test_a_legacy_boolean_maps_to_a_provider(legacy, expected):
    """Workflows saved at 0.3.1 carry use_openrouter's True/False in this slot."""
    assert ep.normalize_provider(legacy) == expected


@pytest.mark.parametrize("raw,expected", [
    ("openrouter", "openrouter"),
    ("LOCAL", "local"),
    (" none ", "none"),
    ("true", "openrouter"),
    ("False", "none"),
    ("", "openrouter"),
    (None, "openrouter"),
    ("nonsense", "openrouter"),
])
def test_provider_normalisation_is_total(raw, expected):
    """Every value that can reach the widget resolves to something sendable."""
    assert ep.normalize_provider(raw) == expected


# ---- idle-unload, and why it is not one field ------------------------------------
#
# A prompt writer that keeps a 27B Q4 vision model resident after answering costs the
# user the generation it was writing the prompt FOR - the two cannot share one card. Both
# popular local servers can unload on idle and they disagree about the field AND about
# what zero means, which is the whole reason this is not a single normalised number.

LOCAL = "http://127.0.0.1:1234/v1"


def test_by_default_nothing_extra_is_sent():
    """Every local_* argument defaults to today's behaviour, so an existing caller
    resolves exactly the endpoint it used to."""
    e = ep.resolve("local", LOCAL)
    assert e.extra_body == {}
    assert e.sends_reasoning is False
    assert e.reasoning_style == ""


@pytest.mark.parametrize("server,expected", [
    ("lmstudio", "ttl"),
    ("ollama", "keep_alive"),
    ("generic", None),
])
def test_the_server_decides_what_the_ttl_is_called(server, expected):
    e = ep.resolve("local", LOCAL, local_ttl=30, local_server=server)
    assert e.extra_body == ({expected: 30} if expected else {})


@pytest.mark.parametrize("base,expected", [
    ("http://127.0.0.1:1234/v1", "ttl"),          # LM Studio's port
    ("http://127.0.0.1:11434/v1", "keep_alive"),  # Ollama's port
    ("http://127.0.0.1:8080/v1", None),           # llama.cpp - takes neither
    ("http://127.0.0.1:9999/v1", None),           # anything else
])
def test_auto_guesses_from_the_port_and_sends_nothing_when_it_cannot(base, expected):
    """A /v1 surface does not report what is behind it, so the port is all there is. An
    unrecognised one sends NO field rather than picking one: `ttl` posted to a server that
    has never heard of it is how a working setup becomes a 400 nobody can explain."""
    e = ep.resolve("local", base, local_ttl=5, local_server="auto")
    assert e.extra_body == ({expected: 5} if expected else {})


def test_the_auto_guess_is_reported_rather_than_made_silently():
    assert ep.resolve("local", LOCAL, local_server="auto").flavor == "auto:lmstudio"
    assert ep.resolve("local", "http://127.0.0.1:11434/v1",
                      local_server="auto").flavor == "auto:ollama"
    assert ep.resolve("local", "http://127.0.0.1:9999/v1",
                      local_server="auto").flavor == "auto:unrecognised"


@pytest.mark.parametrize("ttl", [-1, -5])
def test_a_negative_ttl_sends_no_field_at_all(ttl):
    """-1 is "off" precisely so that 0 stays available as a value the user can send.
    LM Studio reads ttl:0 as UNSET and falls back to 60 minutes; Ollama reads
    keep_alive:0 as unload immediately. Opposite meanings for the same number, so
    neither can be borrowed as our off-switch."""
    assert ep.resolve("local", LOCAL, local_ttl=ttl, local_server="lmstudio").extra_body == {}


def test_zero_is_sent_verbatim_and_not_reinterpreted():
    """The trap this design exists to avoid: whatever 0 means to that server is what the
    user gets. The node does not rewrite it into 1 for LM Studio or anything else."""
    assert ep.resolve("local", LOCAL, local_ttl=0, local_server="lmstudio").extra_body == {"ttl": 0}
    assert ep.resolve("local", LOCAL, local_ttl=0, local_server="ollama").extra_body == {"keep_alive": 0}


# ---- the escape hatch -------------------------------------------------------------


def test_extra_body_merges_top_level_fields():
    e = ep.resolve("local", LOCAL, local_extra_body='{"top_k": 40, "mirostat": 2}')
    assert e.extra_body == {"top_k": 40, "mirostat": 2}


def test_extra_body_wins_over_the_ttl_the_node_worked_out():
    """It is the escape hatch for a server this node has not been taught about, and an
    escape hatch a guess could override would not be one."""
    e = ep.resolve("local", LOCAL, local_ttl=30, local_server="lmstudio",
                   local_extra_body='{"ttl": 1}')
    assert e.extra_body == {"ttl": 1}


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_a_blank_extra_body_is_simply_empty(raw):
    assert ep.resolve("local", LOCAL, local_extra_body=raw).extra_body == {}


@pytest.mark.parametrize("raw,fragment", [
    ("{not json", "not valid JSON"),
    ("[1, 2]", "must be a JSON object"),
    ('"a string"', "must be a JSON object"),
    ("42", "must be a JSON object"),
])
def test_a_broken_extra_body_is_refused_by_name_not_dropped(raw, fragment):
    """Silently dropping it would look exactly like a server that ignored the field, and
    the user would be debugging the wrong end. The message names the widget."""
    with pytest.raises(ValueError) as e:
        ep.resolve("local", LOCAL, local_extra_body=raw)
    assert fragment in str(e.value)
    assert "local_extra_body" in str(e.value)


# ---- reasoning, and the two spellings ---------------------------------------------


def test_openrouter_keeps_the_nested_spelling():
    assert ep.resolve("openrouter", "").reasoning_style == "nested"


def test_local_asks_for_the_flat_spelling_when_it_is_turned_on():
    """OpenRouter normalises `reasoning: {effort}` across providers; a plain
    OpenAI-compatible server takes `reasoning_effort` and has never heard of the other.
    Sending the wrong one is not an error the user sees - it is ignored, so the model
    reasons anyway and all they notice is a slow call and a truncated answer."""
    e = ep.resolve("local", LOCAL, local_send_reasoning=True)
    assert e.sends_reasoning is True
    assert e.reasoning_style == "flat"


def test_openrouter_ignores_every_local_setting():
    """They are local-only knobs. Leaking one onto the hosted path is the same class of
    bug as the shared model field that posted a local slug to OpenRouter."""
    e = ep.resolve("openrouter", "", local_ttl=30, local_server="ollama",
                   local_send_reasoning=True, local_extra_body='{"ttl": 1}')
    assert e.extra_body == {}
    assert e.reasoning_style == "nested"


@pytest.mark.parametrize("raw", ['{"messages": []}', '{"model": "other"}',
                                 '{"ttl": 1, "model": "other"}'])
def test_extra_body_may_not_set_the_fields_the_node_owns(raw):
    with pytest.raises(ValueError) as e:
        ep.resolve("local", LOCAL, local_extra_body=raw)
    assert "may not set" in str(e.value)
