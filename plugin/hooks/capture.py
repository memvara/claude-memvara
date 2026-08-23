#!/usr/bin/env python3
"""Stop — mine the reply Claude just gave for anything worth knowing next week.

This runs once per turn and looks at one turn: what Claude said and did in answer to the
prompt that was just handled. Nothing earlier, because the earlier turns were mined when
they happened.

That is a deliberate reversal of how this hook used to work, and the reason is a defect
rather than a preference. It used to batch — hold text back until 2000 characters had
accumulated, then mine the tail of it — because a headless extraction costs about 21k
tokens of Claude Code's own preamble whatever it is handed, so batching amortised the
overhead. But it kept only the last 48 formatted lines of whatever it read while advancing
its watermark past *all* of it, so on a session with large tool outputs most of the
transcript was skipped unread and could never be reconsidered. Measured on one session:
630KB consumed, six extractions paid for, and only the tail of each batch ever seen.

Per-turn costs more and loses nothing. The two guards that remain:

* **It writes.** A hosted install has no local store, so writes go over the MCP endpoint
  and a refusal raises rather than returning quietly. See `lib/write.py`.
* **It repeats.** `Stop` can fire more than once over one reply, so the size of the
  transcript at the last run is recorded and an unchanged size means there is nothing new.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.extract import triples  # noqa: E402
from lib.ipc import emit_json  # noqa: E402
from lib.open import payload  # noqa: E402
from lib.transcript import last_reply  # noqa: E402
from lib.write import log, open_writer, store_facts  # noqa: E402

#: How much of the tail to parse looking for the turn boundary. One turn is far smaller
#: than this; the margin is for a turn with a lot of tool traffic in it.
TAIL_BYTES = 512 * 1024

#: Characters of reply handed to the extractor. A very long turn is truncated from the
#: front, keeping the end, because the conclusion of a turn carries the decisions.
MAX_REPLY_CHARS = 20_000

#: Where the per-transcript sizes live. Beside the store, not in the plugin, which is
#: replaced wholesale on update.
STATE = Path.home() / ".memvara" / ".hooks" / "capture-state.json"


def _read_state() -> dict:
    import json

    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    import json

    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # A lost marker costs one repeated extraction, not correctness.
        pass


def _reply(transcript: Path) -> str:
    """The formatted text of the turn that just ended, or an empty string."""
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return ""
    text = last_reply(raw)
    return text[-MAX_REPLY_CHARS:] if len(text) > MAX_REPLY_CHARS else text


def main() -> int:
    data = payload()
    if data.get("stop_hook_active"):
        # Re-entry from a hook-triggered continuation. Mining here would double-count.
        return 0

    raw_path = data.get("transcript_path")
    if not raw_path:
        return 0
    transcript = Path(str(raw_path)).expanduser()
    if not transcript.is_file():
        return 0

    key = str(transcript.resolve())
    try:
        size = transcript.stat().st_size
    except OSError:
        return 0
    state = _read_state()
    if state.get(key) == size:
        # Stop fired twice over one reply. Nothing has been added since the last run.
        return 0

    reply = _reply(transcript)
    if not reply.strip():
        emit_json({"systemMessage": "Memvara · nothing in this reply to record"})
        return 0

    # Recorded before extraction, not after: a run that dies mid-way must not leave the
    # same reply queued for the next turn to pay for again.
    state[key] = size
    _write_state(state)

    facts = triples(reply)
    if not facts:
        emit_json({"systemMessage": "Memvara · no durable facts in this reply"})
        return 0

    store, close = open_writer()
    if store is None:
        log(f"reply={len(reply)}c facts={len(facts)} stored=0 failed=no store or login")
        emit_json({"systemMessage": "Memvara · no store to write to — see capture.log"})
        return 0

    try:
        stored, failed = store_facts(store, facts)
    finally:
        if close is not None:
            close()

    log(f"reply={len(reply)}c facts={len(facts)} stored={stored}"
        + (" failed=" + "; ".join(failed) if failed else ""))

    if failed:
        emit_json({"systemMessage":
                   f"Memvara · {stored} stored, {len(failed)} failed — see capture.log"})
    else:
        emit_json({"systemMessage": f"Memvara · {stored} fact(s) stored from this reply"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
