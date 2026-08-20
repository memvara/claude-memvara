"""Resolve the store a hook should read, or decide there isn't one.

Hooks run in the *client's* environment, not the MCP server's. Claude Code launches
`python3 -m memvara.server` with an env block — `MEMVARA_DB`, `PYTHONPATH` and the rest —
and none of that reaches a hook process. So the hook has to rediscover the same
configuration, and the only way to guarantee it lands on the same store, with the same
embedder, is to read the client's own server block and reuse the library's
`ServerConfig.from_env()` rather than re-deriving any of it here.

Everything in this module is written so a failure is silent. A hook that raises on a
missing store, an unreadable settings file or a half-installed library turns every prompt
into an error banner; the memory is an enhancement, and its absence must look like
nothing at all.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

#: Where MCP clients keep the server block we mine for configuration. Checked in order;
#: the first one that names a `memvara` server wins.
_CLIENT_CONFIGS = (
    Path.home() / ".claude.json",
    Path.home() / ".claude" / "settings.json",
)

#: Written by `memvara-mcp login`, read when there is no local store to open.
_CREDENTIALS = Path.home() / ".memvara" / "credentials.json"


def _server_env() -> dict[str, str]:
    """The `env` block the client launches the memvara MCP server with.

    Empty when no client config names one. This is configuration discovery, not
    validation: whatever is found is handed to `ServerConfig.from_env`, which is the
    component that gets to decide whether it is usable.
    """
    for path in _CLIENT_CONFIGS:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        for name, block in servers.items():
            if "memvara" not in name.lower() or not isinstance(block, dict):
                continue
            env = block.get("env")
            if isinstance(env, dict):
                return {str(k): str(v) for k, v in env.items()}
    return {}


def _import_memvara(env: Mapping[str, str]) -> Any:
    """Import the library, honouring a PYTHONPATH that only the server block knows.

    A source checkout is a normal way to run this — the server block carries the path —
    and without this the hook would import nothing while the server imports fine, which
    presents as memory that works in tool calls and is silently absent from prompts.
    """
    extra = env.get("PYTHONPATH", "")
    for part in reversed([p for p in extra.split(os.pathsep) if p]):
        if part not in sys.path:
            sys.path.insert(0, part)
    import memvara  # noqa: F401  (imported for its side effect of being importable)

    return memvara


def open_store() -> Any | None:
    """The `Memvara` a hook should read, or `None` to do nothing at all.

    `None` is a normal outcome, not an error: no store is configured, the library is not
    installed, the embedder does not match, the credentials expired. Every one of those
    means this prompt gets no memory block, and nothing else.
    """
    env = dict(os.environ)
    # The client's block loses to a real environment variable. Someone who exports
    # MEMVARA_DB to point a session at a scratch store means it.
    for key, value in _server_env().items():
        env.setdefault(key, value)

    if not env.get("MEMVARA_DB") and env.get("MEMVARA_MODE") != "cloud":
        # No local store named. Cloud mode is still possible if a key was written by
        # `memvara-mcp login`, and is the only case where we invent configuration.
        if _CREDENTIALS.is_file():
            env["MEMVARA_MODE"] = "cloud"
        else:
            return None

    try:
        _import_memvara(env)
        from memvara.server.config import ServerConfig, build_memvara

        return build_memvara(ServerConfig.from_env(env))
    except Exception:
        # Deliberately bare. ConfigError, ImportError, EmbedderMismatchError, a corrupt
        # SQLite file and a revoked API key are all the same event from here: no memory
        # this turn.
        return None


def payload() -> dict[str, Any]:
    """The hook's stdin JSON, or `{}` when there is nothing readable there."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(text: str) -> None:
    """Print a block for the model, or print nothing.

    Whitespace-only output is suppressed rather than printed: a lone newline still reads
    as an injected context block to anyone debugging the transcript.
    """
    if text and text.strip():
        sys.stdout.write(text.rstrip() + "\n")
