"""Open something that can be written to, write triples to it, and say what happened.

One hook writes — `Stop`, over the turn that just ended — and it needs three things: a
store that may be local or hosted, a loop that counts failures instead of swallowing them,
and a log, because a write that fails leaves no other trace.

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


def store_facts(store: Any, facts: Iterable[Any], turn: str = "",
                hosted: bool = False) -> "tuple[int, list[str]]":
    """Write each fact. Returns how many landed and why the rest did not.

    Three arguments here were being left at their defaults, and each omission is silent.

    `memory_type` — nothing on the write path infers one from the words of a fact, so an
    omitted type falls back to the predicate's registered default, and that default is
    `semantic`. Every standing instruction this hook has ever written was therefore filed
    as a durable fact rather than a procedural one, which makes the `procedural` filter --
    the one to use when you are about to do work and want the user's preferences -- unable
    to find any of them.

    `sources` — the turn the fact came from. Without it `why()` on the result has nothing
    to show, which in the one library whose pitch is that provenance always resolves is the
    difference between a provenance store and a dictionary. The text is right here in the
    caller's hand, and was being dropped.

    `extractor` — defaults to `"api"`, which is what an application asserting its own facts
    reports. A hook mining a transcript is not that, and an audit that cannot tell the two
    apart cannot review either.

    All three are library-only. The hosted `memory_remember` tool takes seven arguments and
    `sources` and `extractor` are not among them, and its schema is closed, so sending one
    is a hard rejection rather than a silent ignore. `memory_type` it does take. On a hosted
    install provenance is therefore unavailable, and `capture.py` stores the turn as its own
    episode instead — searchable, if not linked.

    `hosted` is passed in rather than sniffed off the object, and the first attempt did sniff
    it: "no `registry` attribute and no `add_episode`" looked like a safe capability test and
    is not. `ScopedMemvara` -- the scoped view, which `session_start` already builds -- has
    neither, so a perfectly local handle read as hosted and silently dropped the provenance
    this function exists to attach. It happens to be unreachable today only because
    `build_memvara` returns a bare `Memvara`, which is an implementation detail of a
    different repository. `open_writer` knows which backend it opened; asking it is the
    answer that cannot rot.

    Confidence is below 1.0 deliberately: a model inferred these from a transcript, and
    they should not outrank something the user stated outright.
    """
    stored, failed = 0, []
    for fact in facts:
        subject, predicate, obj = fact[0], fact[1], fact[2]
        memory_type = fact[3] if len(fact) > 3 else None
        kwargs: dict = {"confidence": 0.7}
        if memory_type:
            kwargs["memory_type"] = memory_type
        if not hosted:
            kwargs["extractor"] = "claude-code-hook"
            episode = _episode(turn)
            if episode is not None:
                kwargs["sources"] = [episode]
        try:
            store.remember(subject, predicate, obj, **kwargs)
            stored += 1
        except Exception as exc:
            failed.append(f"{subject}/{predicate}: {type(exc).__name__}: {exc}")
    return stored, failed


def _episode(turn: str) -> Any:
    """An `Episode` carrying the turn, or None when the library is not importable.

    Deliberately not pre-stored: passing the object rather than its id is what makes the
    claim and its source one transaction, so a crash between them cannot leave a claim
    citing a turn that does not exist.
    """
    if not turn.strip():
        return None
    try:
        from memvara.types import Episode

        return Episode(content=turn, role="user")
    except Exception:
        return None
