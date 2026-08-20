#!/usr/bin/env python3
"""Stop — mine the turn that just ended for anything worth knowing next week.

Recall without capture is a store that only ever gets less useful. This is the half that
fills it without anyone remembering to.

Three things separate this hook from the recall pair, and each is a way it could go wrong
quietly:

* **It writes.** So it refuses to run against a store that cannot extract. Under
  `MEMVARA_LLM=none` the library's `NullLLM` accepts prose and stores nothing — a hook
  that ignored that would burn a pass over the transcript every turn and look successful
  while the store stayed empty.
* **It repeats.** `Stop` fires at the end of every turn against an append-only transcript,
  so without a watermark the same opening exchange is re-ingested all session. The
  watermark is a byte offset per transcript, exactly as durable as the file it describes.
* **It costs money.** Extraction is a model call per run. The turn cap below is a spend
  ceiling, not a correctness knob.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.open import open_store, payload  # noqa: E402

#: Most user turns to ingest in one run. A long turn is normal; a thousand-turn backlog
#: means the watermark was lost, and replaying all of it is a bill, not a recovery.
MAX_TURNS = 12

#: Shortest turn worth a model call. "ok", "yes", "do it" carry nothing durable.
MIN_CHARS = 24

#: Where the per-transcript byte offsets live. Beside the store, not in the plugin: the
#: plugin directory is replaced wholesale on update.
STATE = Path.home() / ".memvara" / ".hooks" / "capture-state.json"

#: Synthetic user turns. The transcript records slash-command expansions, injected
#: reminders and tool results as `role: user`; none of them are the user talking, and
#: ingesting them teaches the store about its own plumbing.
NOISE = (
    "<command-message>",
    "<command-name>",
    "<system-reminder>",
    "<local-command-stdout>",
    "Recalled from Memvara",
)


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


def _new_turns(transcript: Path, offset: int) -> "tuple[list[str], int]":
    """User turns after `offset`, and the offset to record next time.

    Opened in binary and seeked rather than re-read: the point of the watermark is to not
    pay for the whole file on every turn of a long session.
    """
    try:
        size = transcript.stat().st_size
    except OSError:
        return [], offset
    if size < offset:
        # Truncated or replaced — a different conversation reusing the path. Start over.
        offset = 0

    turns: "list[str]" = []
    try:
        with transcript.open("rb") as fh:
            fh.seek(offset)
            raw = fh.read()
            end = offset + len(raw)
    except OSError:
        return [], offset

    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "user":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            # Tool results arrive as block lists. Nothing the user said is in there.
            continue
        text = content.strip()
        if len(text) < MIN_CHARS or any(marker in text for marker in NOISE):
            continue
        turns.append(text)

    return turns[-MAX_TURNS:], end


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
        return 0

    try:
        from memvara.llm import NullLLM

        if isinstance(getattr(store, "llm", None), NullLLM):
            # Offline extraction. Recall still works; capture degrades to nothing rather
            # than to a silent no-op that looks like it worked.
            return 0
    except Exception:
        return 0

    key = str(transcript.resolve())
    state = _read_state()
    turns, end = _new_turns(transcript, int(state.get(key, 0) or 0))

    if turns:
        try:
            store.add(turns, role="user")
        except Exception:
            # Leave the watermark where it is so the next Stop retries this span.
            return 0

    state[key] = end
    _write_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
