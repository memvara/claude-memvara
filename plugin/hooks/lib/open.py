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

from .ipc import emit, payload, server_env  # noqa: F401  (re-exported; they live
# in ipc so the fast path can use them without importing pathlib)

#: Written by `memvara-mcp login`, read when there is no local store to open.
_CREDENTIALS = Path.home() / ".memvara" / "credentials.json"


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


def open_store(*, recalls: bool = True) -> Any | None:
    """The `Memvara` a hook should read, or `None` to do nothing at all.

    `None` is a normal outcome, not an error: no store is configured, the library is not
    installed, the embedder does not match, the credentials expired, or the deployment is
    hosted and belongs to `lib.hosted` instead. Every one of those means this prompt gets
    no memory block *from here*, and the last one means it gets a better one elsewhere.

    `recalls` is what the caller intends to *do* with the handle, and it exists because the
    answer differs. A caller that will call `recall()` needs `header=` and `budget=`, which
    the library's hosted client does not serve; a caller that will only write needs
    `sources=`, which it serves and `lib.hosted` does not. Collapsing the two costs one of
    them, and the first attempt at this fix collapsed them silently in the writer's
    direction -- every captured fact stored unlinked, with `capture.log` still reporting
    `stored=N`. Asked as a question rather than sniffed off the object, for the reason
    `store_facts` gives at length about `hosted`: what the caller means cannot rot.
    """
    env = dict(os.environ)
    # The client's block loses to a real environment variable. Someone who exports
    # MEMVARA_DB to point a session at a scratch store means it.
    for key, value in server_env().items():
        env.setdefault(key, value)

    if not env.get("MEMVARA_DB") and env.get("MEMVARA_MODE") != "cloud":
        # No local store named. Cloud mode is still possible if a key was written by
        # `memvara-mcp login`, and is the only case where we invent configuration.
        if _CREDENTIALS.is_file():
            env["MEMVARA_MODE"] = "cloud"
        else:
            return None

    if recalls and env.get("MEMVARA_MODE") == "cloud":
        # The same refusal as below, reached before paying for it. `import memvara` is
        # ~95ms and this function runs whenever the daemon is not warm -- the first prompt
        # of every session, and every prompt after a daemon dies -- so importing the whole
        # library to then discard it is the one cost here worth avoiding.
        #
        # Deliberately the raw string, matching the check above that may have just set it,
        # rather than a second copy of the library's normalisation. An unnormalised value
        # like `Cloud` misses this and is caught by the normalised check below, so this can
        # only ever be an early exit for a case that was already decided, never a decision
        # of its own.
        return None

    try:
        _import_memvara(env)
        from memvara.server.config import ServerConfig, build_memvara

        config = ServerConfig.from_env(env)
        if recalls and config.mode != "local":
            # Not a local engine, and this caller is going to call `recall()` on it.
            # `build_memvara` has returned a `RemoteMemvara` for a cloud config since
            # memvara/memvara@2a3bb48, and a recalling hook cannot use one: its `recall()`
            # takes no `header=` at all and *refuses* a `budget=` rather than dropping it
            # -- deliberately, because it cannot re-derive the local truncation from a
            # server-rendered string. `lib.hosted` is this repo's client for exactly that
            # deployment and does both, so answering None is how a caller is sent
            # somewhere that can serve the call.
            #
            # Only recalling callers. A writer wants the opposite handle: `RemoteMemvara`
            # takes the `sources=` that carries a claim back to the turn it came from, and
            # `lib.hosted` cannot until the server renders episode ids.
            #
            # This is not a new rule; it is the one every docstring here already states
            # ("open_store() answers None on a hosted install"). It stopped being true by
            # accident, upstream, and cost every prompt its memory block for a day while
            # the fallback chain sat intact and unreached.
            #
            # Spelled `!= "local"` rather than `== "cloud"`: a mode added later is far
            # likelier to be another remote than another engine, and this direction
            # degrades to the route that works instead of to a silent outage.
            return None
        return build_memvara(config)
    except Exception:
        # Deliberately bare. ConfigError, ImportError, EmbedderMismatchError, a corrupt
        # SQLite file and a revoked API key are all the same event from here: no memory
        # this turn.
        return None
