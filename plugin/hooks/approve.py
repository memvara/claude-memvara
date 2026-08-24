#!/usr/bin/env python3
"""PreToolUse — let read-only memory_* tools run without a permission prompt.

SuperMemory auto-allows search; writes still ask. Same split here. A silent
no-op on any other tool, so this matcher can be wide (`mcp__.*memvara.*`)
without approving a forget.
"""

from __future__ import annotations

import json
import sys

#: Every memory_* tool the server marks `readOnlyHint`. A read that prompts is a read the
#: model learns to avoid, and the two graph tools were missing for no reason other than
#: that they were added after this list.
READ_ONLY = frozenset({
    "memory_recall",
    "memory_search",
    "memory_since",
    "memory_history",
    "memory_why",
    "memory_stats",
    "memory_neighborhood",
    "memory_paths",
})


def _tool_leaf(name: str) -> str:
    # mcp__memvara__memory_search or mcp__plugin_memvara_memvara__memory_search
    if "__" in name:
        return name.rsplit("__", 1)[-1]
    return name


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    leaf = _tool_leaf(str(data.get("tool_name") or ""))
    if leaf not in READ_ONLY:
        return 0
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Memvara recall is read-only.",
        }
    }, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
