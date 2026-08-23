#!/usr/bin/env python3
"""Mine one user message for durable facts. Spawned by the recall hook, never registered.

This is not a hook. `recall.py` starts it detached and returns immediately, because the
work here takes as long as a headless model run and nothing about a prompt should wait on
that. It reads the message on stdin and exits.

Recording what the user *said* is a different job from recording what Claude *did*, which
is why it is a different process. A preference stated in a prompt — "always use the
worktree", "stop asking about X" — is the most valuable thing in a session and the least
likely to survive in the reply, where it appears at most as compliance.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.extract import triples  # noqa: E402
from lib.write import log, open_writer, store_facts  # noqa: E402


def main() -> int:
    try:
        prompt = sys.stdin.read().strip()
    except (OSError, ValueError):
        return 0
    if not prompt:
        return 0

    facts = triples(prompt)
    if not facts:
        return 0

    store, close = open_writer()
    if store is None:
        log(f"prompt={len(prompt)}c facts={len(facts)} stored=0 failed=no store or login")
        return 0

    try:
        stored, failed = store_facts(store, facts)
    finally:
        if close is not None:
            close()

    log(f"prompt={len(prompt)}c facts={len(facts)} stored={stored}"
        + (" failed=" + "; ".join(failed) if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
