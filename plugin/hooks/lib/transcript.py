"""Turn a Claude Code JSONL transcript into the text capture should mine.

The Stop hook used to keep only `type == user` string messages. That is what the
user *said*. SuperMemory's capture is useful because it also keeps what the
agent *did* — assistant prose and a compact record of Edit/Write/Bash — so a
later prompt can recall "we put the store in plugin/" without anyone having
typed that as a preference.

Thinking blocks, Memvara's own recall injection, and memory_* tool calls are
dropped: they are the plumbing of this plugin, not facts about the project.
"""

from __future__ import annotations

import json
from typing import Any

#: Tools whose *use* is a durable event (a file changed, a command ran).
INCLUDE_TOOLS = frozenset({"Edit", "Write", "Bash", "NotebookEdit"})

#: Prefixes of tool names that are this plugin talking to itself.
SKIP_TOOL_PREFIXES = ("mcp__",)

MAX_TOOL_ARG = 120
MAX_TOOL_RESULT = 240

#: Text that is this plugin talking to itself. Anything carrying one of these is dropped
#: whole rather than mined.
#:
#: The last two are the SessionStart block, and they matter more than they look. Capture
#: mines the turn it just watched; SessionStart injects a block of already-stored memories
#: into the very first turn of a session. Without these markers that block is read back as
#: conversation, re-extracted, and written again under whatever predicate the model picks
#: this time -- a feedback loop that manufactures duplicates of facts already in the store,
#: and one that gets worse every session rather than settling.
#:
#: It has never fired, because SessionStart produced no output at all on a hosted install
#: until 0.1.4. Fixing that hook without adding these two lines in the same commit would
#: have turned a dead hook into an actively harmful one.
NOISE = (
    "<command-message>",
    "<command-name>",
    "<system-reminder>",
    "<local-command-stdout>",
    "Recalled from Memvara",
    "Memvara — what is already known about this user",
    "Memvara — how this user wants work done",
    "Memvara scope:",
)


def _clean(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if any(marker in text for marker in NOISE):
        return ""
    return text


def _truncate(text: str, n: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= n:
        return text
    return text[: n - 3] + "..."


def _skip_tool(name: str) -> bool:
    if name in INCLUDE_TOOLS:
        return False
    return name.startswith(SKIP_TOOL_PREFIXES) or name not in INCLUDE_TOOLS


def _tool_args(inp: Any) -> str:
    if not isinstance(inp, dict):
        return _truncate(str(inp), MAX_TOOL_ARG)
    parts = []
    for key in ("file_path", "command", "path", "notebook_path"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(f"{key}={_truncate(val, MAX_TOOL_ARG)}")
    return " ".join(parts)


def format_user(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    out: list[str] = []
    if isinstance(content, str):
        cleaned = _clean(content)
        if cleaned:
            out.append(f"User: {cleaned}")
        return out
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            cleaned = _clean(str(block.get("text") or ""))
            if cleaned:
                out.append(f"User: {cleaned}")
        elif block.get("type") == "tool_result":
            name = str(block.get("name") or "tool")
            if _skip_tool(name):
                continue
            raw = block.get("content")
            if isinstance(raw, str):
                snippet = _truncate(_clean(raw), MAX_TOOL_RESULT)
            else:
                snippet = ""
            status = "error" if block.get("is_error") else "ok"
            if snippet:
                out.append(f"Tool result ({name}, {status}): {snippet}")
    return out


def format_assistant(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    out: list[str] = []
    if isinstance(content, str):
        cleaned = _clean(content)
        if cleaned:
            out.append(f"Claude: {cleaned}")
        return out
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "thinking":
            continue
        if kind == "text":
            cleaned = _clean(str(block.get("text") or ""))
            if cleaned:
                out.append(f"Claude: {cleaned}")
        elif kind == "tool_use":
            name = str(block.get("name") or "")
            if _skip_tool(name):
                continue
            args = _tool_args(block.get("input"))
            line = f"Claude used {name}"
            if args:
                line += f" {args}"
            out.append(line)
    return out


def format_entry(entry: dict) -> list[str]:
    kind = entry.get("type")
    message = entry.get("message")
    if kind == "user":
        return format_user(message)
    if kind == "assistant":
        return format_assistant(message)
    return []


def last_turn(raw: bytes) -> str:
    """The exchange that just ended: the last typed prompt and the reply to it.

    Both halves, because they carry different things and neither is enough on its own. A
    standing instruction is stated in the prompt — "always open a PR", "stop asking me
    about X" — while what was actually decided, and where it landed, is in the reply.

    Mining the reply alone was tried and does not work. It asks a model to find durable
    facts *about the user* in Claude's own words, and the model correctly answers that
    there are none: measured over one session, fifteen extractions in an hour returned an
    empty list every time while costing a full run each.

    The boundary is the last entry that formats to a `User:` line. Tool results are also
    entries of type `user`, so the naive boundary cuts the turn in half; and a prompt that
    survives the noise filter is a prompt somebody typed.
    """
    entries = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)

    start = None
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry.get("type") != "user":
            continue
        if any(line.startswith("User: ") for line in format_entry(entry)):
            start = index
            break
    if start is None:
        # No typed prompt in the window. Mining everything from here would re-mine turns
        # that were already handled when they happened.
        return ""

    out: list[str] = []
    for entry in entries[start:]:
        out.extend(format_entry(entry))
    return "\n".join(out)


def span_from_bytes(raw: bytes) -> str:
    """Decode a JSONL slice (the bytes after the watermark) into mineable text."""
    lines_out: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        lines_out.extend(format_entry(entry))
    return "\n".join(lines_out)
