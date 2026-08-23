#!/usr/bin/env python3
"""Stop — mine the turn that just ended for anything worth knowing next week.

Recall without capture is a store that only ever gets less useful. This is the half that
fills it without anyone remembering to.

Three things separate this hook from the recall pair, and each is a way it could go wrong
quietly:

* **It writes.** So it refuses to run against a store that cannot extract. Under
  `MEMVARA_LLM=none` the library's `NullLLM` accepts prose and stores nothing — a hook
  that ignored that would burn a pass over the transcript every turn and look successful
  while the store stayed empty. It writes over the hosted endpoint when there is no local
  store, the same route recall takes, and a write that the endpoint refuses raises rather
  than returning nothing, so the log below can count it as failed instead of stored.
* **It repeats.** `Stop` fires at the end of every turn against an append-only transcript,
  so without a watermark the same opening exchange is re-ingested all session. The
  watermark is a byte offset per transcript, exactly as durable as the file it describes.
* **It costs money.** Extraction is a model call per run. The turn cap below is a spend
  ceiling, not a correctness knob.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.extract import triples  # noqa: E402
from lib.open import open_store, payload  # noqa: E402
from lib.transcript import span_from_bytes  # noqa: E402

#: Most user turns to ingest in one run. A long turn is normal; a thousand-turn backlog
#: means the watermark was lost, and replaying all of it is a bill, not a recovery.
MAX_TURNS = 12

#: Characters of unprocessed transcript before extraction is worth running.
#:
#: This threshold is what makes the hook affordable. Extraction boots a headless Claude
#: session that costs ~21k tokens of its own preamble no matter how much transcript it is
#: handed, so running it on a single short turn spends the same overhead as running it on
#: twenty. Under this, the hook returns having done nothing and — crucially — leaves the
#: watermark alone, so the text stays pending and joins the next batch instead of being
#: dropped.
MIN_SPAN_CHARS = 2000

#: Where the per-transcript byte offsets live. Beside the store, not in the plugin: the
#: plugin directory is replaced wholesale on update.
STATE = Path.home() / ".memvara" / ".hooks" / "capture-state.json"

#: Capture is the one hook whose success is invisible from the transcript, so it keeps a
#: one-line-per-run record. Bounded by truncation rather than rotation: it is a debugging
#: aid, and a log that needs its own maintenance is worse than no log.
LOG = STATE.parent / "capture.log"
LOG_MAX_BYTES = 64 * 1024


def _log(line: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
            LOG.write_text("")
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def _read_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # A lost watermark costs a replay, not correctness. Never fail the turn for it.
        pass


def _new_span(transcript: Path, offset: int) -> "tuple[str, int]":
    """Mineable text after `offset`, and the offset to record next time.

    Opened in binary and seeked rather than re-read: the point of the watermark is to not
    pay for the whole file on every turn of a long session. A lost watermark (truncated
    file, or offset past EOF) rereads from the start and keeps only the tail, so a
    thousand-turn backlog does not become one extraction bill.
    """
    try:
        size = transcript.stat().st_size
    except OSError:
        return "", offset
    if size < offset:
        # Truncated or replaced — a different conversation reusing the path. Start over.
        offset = 0

    try:
        with transcript.open("rb") as fh:
            fh.seek(offset)
            raw = fh.read()
            end = offset + len(raw)
    except OSError:
        return "", offset

    span = span_from_bytes(raw)
    lines = [line for line in span.splitlines() if line]
    # ~4 formatted lines per turn (user, assistant, an edit, a result).
    cap = MAX_TURNS * 4
    if len(lines) > cap:
        span = "\n".join(lines[-cap:])
    return span, end


def main() -> int:
    data = payload()
    if data.get("stop_hook_active"):
        # Re-entry from a hook-triggered continuation. Ingesting here would double-count.
        return 0

    raw_path = data.get("transcript_path")
    if not raw_path:
        return 0
    transcript = Path(str(raw_path)).expanduser()
    if not transcript.is_file():
        return 0

    store = open_store()
    if store is None:
        # A hosted install has no local store to open, and that is its normal state
        # rather than a broken one: MEMVARA_MODE=cloud cannot build an engine, because
        # the REST facade exposes none of the low-level calls one needs. Without this
        # fallback capture returns here on every turn, writes nothing, logs nothing, and
        # is reported as a hook that succeeded — the exact silence this file's docstring
        # warns about, arriving through the door next to the one it guards.
        from lib.hosted import open_hosted

        store = open_hosted()
    if store is None:
        return 0

    key = str(transcript.resolve())
    state = _read_state()
    span, end = _new_span(transcript, int(state.get(key, 0) or 0))
    if len(span) < MIN_SPAN_CHARS:
        # Not enough new material to justify the fixed overhead of a headless run. Return
        # without touching the watermark so this text is reconsidered with the next turn.
        return 0

    # Extraction goes through the user's own Claude Code login rather than the library's
    # extractor. `store.add()` would be the natural call, but it routes through whichever
    # LLM the server was configured with, and this store is deliberately MEMVARA_LLM=none:
    # under NullLLM prose is accepted and silently stores nothing. Writing the triples
    # directly also sidesteps that store's known defect around unregistered predicates,
    # where a fact recorded as prose and the same fact recorded as a triple never merge.
    facts = triples(span)
    if not facts:
        # Either nothing durable was said or the run failed. Both are "no facts"; the
        # watermark still advances, because retrying the same span would pay the same
        # overhead for the same answer.
        state[key] = end
        _write_state(state)
        return 0

    stored, failed = 0, []
    for subject, predicate, obj in facts:
        try:
            # Confidence below 1.0 on purpose: a model inferred this from a transcript,
            # and it should not outrank something the user stated outright.
            store.remember(subject, predicate, obj, confidence=0.7)
            stored += 1
        except Exception as exc:
            failed.append(f"{subject}/{predicate}: {type(exc).__name__}: {exc}")

    close = getattr(store, "close", None)
    if close is not None:
        close()

    # Without this line, "the model found one fact" and "two facts were found and one
    # write failed" are the same observation from outside: a store with one new claim and
    # a hook that printed nothing. The log is the only place that difference survives.
    _log(f"span={len(span)}c facts={len(facts)} stored={stored}"
         + (" failed=" + "; ".join(failed) if failed else ""))

    if stored:
        state[key] = end
        _write_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
