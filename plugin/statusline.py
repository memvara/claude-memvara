"""The memvara status line: what memory did in this session, in one line.

Claude Code runs this after each turn and shows what it prints under the prompt:

    ⋈ memvara · 12 recalled · 3 searched · 5 captured

`recalled` is how many memory lines the hooks put into prompts, `searched` how many
read-only memory tools the model called, and `captured` how many facts were stored from
finished turns, all for the session Claude Code names on stdin. The hooks keep the numbers
in `~/.memvara/.hooks/counts/<session>.json`, and this reads them with the vendored hooks'
`lib/counts.py`, which imports nothing else from the hooks.

When the `status_line` switch is off the hooks stop counting, and this prints
`⋈ memvara · off` so the line does not show numbers that have stopped moving.

It must finish in well under 50 ms and must never show an error in the status bar. So it
imports only `json`, `os` and `sys` besides the two small hook modules, and on any failure
it prints nothing and exits 0. `setup.py` installs it into `~/.claude/settings.json` and
removes it again.
"""

import json
import os
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hooks")

#: The same glyph the hooks put in front of every recalled line.
GLYPH = "⋈"

#: The switch that turns counting off.
FEATURE = "status_line"


def render(session_id: str) -> str:
    """The line for `session_id`. Raises on a broken install; `main` catches that."""
    sys.path.insert(0, HOOKS)
    try:
        from lib import counts, settings
    finally:
        sys.path.pop(0)
    if not settings.enabled(FEATURE):
        return f"{GLYPH} memvara · off"
    got = counts.read(session_id)
    return (f"{GLYPH} memvara · {got['recalled']} recalled · "
            f"{got['searched']} searched · {got['captured']} captured")


def main() -> int:
    try:
        data = json.load(sys.stdin)
        session = data.get("session_id") if isinstance(data, dict) else None
        if not isinstance(session, str) or not session:
            # Without a session there is no count to show, and zeros would be a claim.
            return 0
        line = render(session)
        # Bytes, because the glyph is not in every locale's encoding and a
        # UnicodeEncodeError would cost the whole line.
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    except Exception:  # noqa: BLE001 -- the status bar must never show a traceback
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
