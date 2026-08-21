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

NOISE = (
    "<command-message>",
    "<command-name>",
    "<system-reminder>",
    "<local-command-stdout>",
    "Recalled from Memvara",
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
