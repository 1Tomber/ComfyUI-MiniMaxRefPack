"""Tests for minimax_refpack.logs - the structured line format and the timing helper.

The format is the contract: one line, `[MiniMaxRefPack] event=<name> k=v k=v`, greppable
in a ComfyUI console that is already noisy. Everything here is pure string work plus
`logging`, so no ComfyUI and no torch.
"""

import logging

import pytest

from minimax_refpack import logs


# ---- the line format ---------------------------------------------------------


def test_the_event_leads_and_fields_keep_their_order():
    line = logs.format_event("build", refs=3, images=1, videos=2)

    assert line == "[MiniMaxRefPack] event=build refs=3 images=1 videos=2"


def test_floats_are_trimmed_rather_than_printed_raw():
    """0.1+0.2 in a log line is noise, and 8.0 reads better than 8.000."""
    line = logs.format_event("trim", start=0.30000000000000004, end=8.0, ms=12.3456)

    assert line == "[MiniMaxRefPack] event=trim start=0.3 end=8 ms=12.346"


def test_lists_are_compact_and_keep_their_numbers():
    line = logs.format_event("crop", crop=[0.52, 0.3, 0.30000000000000004, 0.68])

    assert line == "[MiniMaxRefPack] event=crop crop=[0.52,0.3,0.3,0.68]"


def test_a_value_with_spaces_is_quoted_so_the_line_stays_parseable():
    line = logs.format_event("prompt", model="a model", file="my sheet.png")

    assert line == '[MiniMaxRefPack] event=prompt model="a model" file="my sheet.png"'


def test_none_fields_are_dropped_not_printed():
    line = logs.format_event("load", file="a.png", crop=None, trim=None)

    assert line == "[MiniMaxRefPack] event=load file=a.png"


def test_booleans_read_as_true_false():
    line = logs.format_event("call", use_openrouter=True, cached=False)

    assert line == "[MiniMaxRefPack] event=call use_openrouter=true cached=false"


@pytest.mark.parametrize("field", ["api_key", "openrouter_api_key", "token", "access_token", "secret", "password"])
def test_anything_that_smells_like_a_credential_is_redacted(field):
    line = logs.format_event("call", **{field: "sk-or-v1-deadbeef"})

    assert "deadbeef" not in line
    assert f"{field}=***" in line


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens", "reasoning_tokens", "keyframes"])
def test_the_redaction_rule_does_not_eat_ordinary_fields(field):
    """`prompt_tokens` is not a credential. The guard matches whole name segments, so a
    field merely containing "token" or "key" keeps its value."""
    line = logs.format_event("call", **{field: 1200})

    assert f"{field}=1200" in line


# ---- emitting ----------------------------------------------------------------


def test_log_writes_one_info_line_on_the_named_logger(caplog):
    with caplog.at_level(logging.INFO, logger=logs.LOGGER_NAME):
        logs.log("build", refs=0)

    assert len(caplog.records) == 1
    assert caplog.records[0].name == logs.LOGGER_NAME
    assert caplog.records[0].levelno == logging.INFO
    assert caplog.records[0].getMessage() == "[MiniMaxRefPack] event=build refs=0"


def test_debug_lines_stay_out_of_an_info_console(caplog):
    """Route hits are per-tile chatter - they must not drown the console at INFO."""
    with caplog.at_level(logging.INFO, logger=logs.LOGGER_NAME):
        logs.debug("thumb", file="a.png")

    assert caplog.records == []


def test_warn_writes_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=logs.LOGGER_NAME):
        logs.warn("bad_request", reason="crop out of range")

    assert caplog.records[0].levelno == logging.WARNING


# ---- timed() -----------------------------------------------------------------


def test_timed_logs_once_on_success_with_a_duration(caplog):
    with caplog.at_level(logging.INFO, logger=logs.LOGGER_NAME):
        with logs.timed("load_image", file="a.png") as fields:
            fields["out"] = "100x50"

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert msg.startswith("[MiniMaxRefPack] event=load_image file=a.png out=100x50 ms=")


def test_timed_reports_the_failure_and_reraises(caplog):
    with caplog.at_level(logging.INFO, logger=logs.LOGGER_NAME):
        with pytest.raises(ValueError):
            with logs.timed("load_video", file="clip.mp4"):
                raise ValueError("too short")

    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    msg = record.getMessage()
    assert "event=load_video" in msg
    assert "ok=false" in msg
    assert "error=ValueError" in msg
    assert "ms=" in msg


def test_timed_fields_added_inside_the_block_are_logged_after_the_openers(caplog):
    with caplog.at_level(logging.INFO, logger=logs.LOGGER_NAME):
        with logs.timed("call", model="m") as fields:
            fields["status"] = 200

    assert "event=call model=m status=200 ms=" in caplog.records[0].getMessage()
