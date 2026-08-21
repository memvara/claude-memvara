"""The client half: ask the daemon, and never depend on there being one.

The contract this file exists to keep is that the daemon is an optimisation and not a
dependency. Every path through `recall()` returns the same text; only the latency differs.
If that stopped being true — if a missing daemon meant a missing memory block — a
background process would be trading a real risk for 136ms, which is not a trade worth
making on someone's prompt path.

Order of preference, and what each costs when it fails:

1. **Daemon.** ~38ms end to end, most of it this client's own interpreter startup. A
   missing or wedged one costs the connect attempt, which is sub-millisecond against a
   socket that is not there.
2. **In-process library.** ~148ms, the pre-daemon behaviour, always correct. Skipped
   entirely when the library is not installed, which is the normal hosted case.
3. **Hosted over stdlib HTTP.** ~390ms cold. Needs no `pip install`, which is the point:
   the hosted install story is "paste a URL", and a hook that waited for a Python package
   would be silently dead on exactly the machines this is aimed at.
4. **Nothing.** No store, no login: empty string, no output, no error.

Spawning is deliberately *after* answering. The first prompt of a session should not wait
on a process that cannot help it yet, so the daemon is started for the benefit of the next
one and this prompt takes the slow path.
"""

from __future__ import annotations

import os
import sys

from .ipc import send, socket_path, store_key

#: Set in a spawned daemon's environment so a daemon can never spawn a daemon.
SENTINEL = "MEMVARA_DAEMON"


def _spawn(root: str) -> None:
    """Start a daemon for next time. Best effort, and silent about failing."""
    if os.environ.get(SENTINEL):
        return
    env = dict(os.environ)
    env[SENTINEL] = "1"
    # Imported here, not at module scope: `subprocess` costs 5.8ms and is only ever needed
    # on the fallback path, which has already lost far more than that.
    import subprocess

    try:
        subprocess.Popen(
            [sys.executable, os.path.join(root, "daemon.py")],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach from this hook's process group: the daemon must outlive the hook,
            # and must not receive the signals Claude Code sends to its own children.
            start_new_session=True,
            env=env,
            cwd=root,
        )
    except (OSError, ValueError):
        pass


def recall(query: str, *, k: int = 6, budget: int = 700, header: str | None = None,
           spawn: bool = True) -> str:
    """Recall text for `query`, by whatever route is available."""
    if not query.strip():
        return ""

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        path = socket_path(store_key())
    except Exception:
        path = None

    if path is not None:
        request = {"q": query, "k": k, "budget": budget}
        if header:
            request["header"] = header
        answer = send(path, request)
        if answer is not None:
            # An empty string from a live daemon is a real answer — this store has nothing
            # relevant — and must not trigger the slow path to ask the same question again.
            return answer

    from .open import open_store

    store = open_store()
    if store is None:
        # No local library or no local store. Hosted is the remaining route, and on a
        # paste-the-URL install it is the only one there ever was.
        from .hosted import open_hosted

        client = open_hosted()
        if client is not None:
            try:
                text = client.recall(query, k=k, budget=budget, header=header)
            except Exception:
                text = None
            finally:
                client.close()
            if text:
                if spawn and path is not None:
                    _spawn(root)
                return text
        return ""

    try:
        kwargs = {"k": k, "budget": budget}
        if header:
            kwargs["header"] = header
        text = str(store.recall(query, **kwargs) or "")
    except Exception:
        text = ""

    if spawn and path is not None:
        _spawn(root)
    return text
