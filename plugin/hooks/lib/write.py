"""Open something that can be written to, write triples to it, and say what happened.

Two hooks write now — `UserPromptSubmit` records what the user just said, `Stop` records
what Claude just did — and both need the same three things: a store that may be local or
hosted, a loop that counts failures instead of swallowing them, and a log, because a write
that fails leaves no other trace.

The store is opened the way recall opens it. `open_store()` answers None on a hosted
install, which is the normal state rather than a broken one: `MEMVARA_MODE=cloud` cannot
build an engine, since the REST facade exposes none of the low-level calls the pipeline
makes. The hosted client is the route in that case, and it raises on a failed write rather
than returning nothing, which is what lets `store_facts` tell a refusal from a quiet turn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .open import open_store

#: Beside the store, not in the plugin: the plugin directory is replaced wholesale on
#: update, and a log that disappears on upgrade is not a log.
LOG = Path.home() / ".memvara" / ".hooks" / "capture.log"

#: Bounded by truncation rather than rotation. It is a debugging aid, and a log that needs
#: its own maintenance is worse than no log.
LOG_MAX_BYTES = 64 * 1024


def log(line: str) -> None:
    """Append one line, or give up quietly. Never raises into a hook."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
            LOG.write_text("")
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def open_writer() -> "tuple[Any, Any] | tuple[None, None]":
    """`(store, close)` for whichever backend answers, or `(None, None)`.

    `close` is None for a local store, which has no connection to give back, and the
    hosted client's `close` otherwise. Callers close after their last write and not
    before: the hosted client connects lazily, so an early return costs nothing.
    """
    store = open_store()
    if store is not None:
        return store, None

    from .hosted import open_hosted

    hosted = open_hosted()
    if hosted is None:
        return None, None
    return hosted, hosted.close


def store_facts(store: Any, facts: Iterable["tuple[str, str, str]"]) -> "tuple[int, list[str]]":
    """Write each triple. Returns how many landed and why the rest did not.

    Confidence is below 1.0 deliberately: a model inferred these from a transcript, and
    they should not outrank something the user stated outright.
    """
    stored, failed = 0, []
    for subject, predicate, obj in facts:
        try:
            store.remember(subject, predicate, obj, confidence=0.7)
            stored += 1
        except Exception as exc:
            failed.append(f"{subject}/{predicate}: {type(exc).__name__}: {exc}")
    return stored, failed
