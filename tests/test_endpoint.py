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


# ---- describe() is user-facing, not just log-facing ----------------------------------


def test_describe_does_not_carry_url_credentials():
    """describe() is inlined into PromptError messages, which ComfyUI shows in the UI and
    which people paste into bug reports. prompt.py's RequestException handler already
    suppresses str(e) to keep the Authorization header out of that text, and was leaking
    the equivalent basic credentials through this in the same f-string."""
    resolved = ep.resolve("local", api_base="http://user:s3cret@127.0.0.1:1234/v1")
    described = resolved.describe()
    assert "s3cret" not in described
    assert "user" not in described
    assert "127.0.0.1:1234" in described, "which server it was is the point of describe()"


def test_describe_is_unchanged_without_credentials():
    resolved = ep.resolve("local", api_base="http://127.0.0.1:1234/v1")
    assert "127.0.0.1:1234" in resolved.describe()
    assert "***" not in resolved.describe()


def test_the_error_text_a_user_sees_carries_no_credential():
    """The whole point: the string that reaches the UI."""
    resolved = ep.resolve("local", api_base="http://user:s3cret@127.0.0.1:1234/v1")
    message = f"{resolved.describe()} request failed: ConnectionError"
    assert "s3cret" not in message
