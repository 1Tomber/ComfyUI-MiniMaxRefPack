"""One structured line per interesting event, on a logger of our own.

Format: `[MiniMaxRefPack] event=<name> key=value key=value`. Not JSON: these land in a
ComfyUI console that is already busy, where a human greps `MiniMaxRefPack` and reads the
line. Machines can still split on ` ` and `=` — values that contain a space are quoted.

Levels are the volume control, so nothing here needs a switch of its own: INFO is what
you want to see on a pod (what was loaded, what was sent, what it cost, what failed),
DEBUG is per-tile chatter (thumbnail and probe requests). ComfyUI runs at INFO by
default, so DEBUG stays out of the way until `--verbose DEBUG`.

Pure stdlib — no torch, no ComfyUI — so every other module can import it unconditionally.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

LOGGER_NAME = "MiniMaxRefPack"
PREFIX = f"[{LOGGER_NAME}]"

logger = logging.getLogger(LOGGER_NAME)

# A field whose name ENDS in one of these never has its value printed. The API key is
# passed around as a plain string and one accidental f-string in a log line would put it
# in a screenshot forever, so the guard lives here rather than at every call site.
# Matched on the last underscore-separated segment, not as a substring: `api_key` and
# `access_token` are credentials, `prompt_tokens` and `keyframes` are not.
_SECRET_SEGMENTS = frozenset({"key", "token", "secret", "password", "authorization"})


def _number(value: float) -> str:
    """3dp, trailing zeros dropped: 0.30000000000000004 -> 0.3, 8.0 -> 8."""
    text = f"{value:.3f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_value(v) for v in value) + "]"
    text = str(value)
    if not text:
        return '""'
    if any(c in text for c in ' "'):
        return '"' + text.replace('"', "'") + '"'
    return text


def format_event(event: str, **fields) -> str:
    """The line. `None` fields are dropped — an absent crop is not `crop=None`, it is
    simply not part of the event."""
    parts = [f"{PREFIX} event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        if key.lower().rsplit("_", 1)[-1] in _SECRET_SEGMENTS:
            parts.append(f"{key}=***")
            continue
        parts.append(f"{key}={_value(value)}")
    return " ".join(parts)


def log(event: str, **fields) -> None:
    logger.info(format_event(event, **fields))


def debug(event: str, **fields) -> None:
    logger.debug(format_event(event, **fields))


def warn(event: str, **fields) -> None:
    logger.warning(format_event(event, **fields))


@contextmanager
def timed(event: str, **fields):
    """One line when the block ends, carrying how long it took.

    Yields the field dict so the block can add what it learned — the output size, the
    frame count, the HTTP status — and have it appear after the fields the caller opened
    with. A raised exception is logged at WARNING with `ok=false error=<type>` and then
    re-raised untouched: this is instrumentation, it never swallows a failure.
    """
    started = time.perf_counter()
    try:
        yield fields
    except BaseException as e:
        fields["ok"] = False
        fields["error"] = type(e).__name__
        fields["ms"] = (time.perf_counter() - started) * 1000.0
        logger.warning(format_event(event, **fields))
        raise
    fields["ms"] = (time.perf_counter() - started) * 1000.0
    logger.info(format_event(event, **fields))
