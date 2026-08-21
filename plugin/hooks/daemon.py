#!/usr/bin/env python3
"""A resident store handle, so recall costs a socket round trip instead of an import.

The per-prompt hook spends 148ms, and only 6ms of that is the query: 95ms is
`import memvara`, the rest interpreter startup and opening the store. None of that work is
per-prompt work — it is the same every time — so this process does it once and answers on
a unix socket in about 12ms, measured end to end including the client.

It exists to be disposable. Nothing depends on it running, nothing breaks when it dies,
and every client falls back to querying in-process. That is the property that makes a
background process acceptable here: the worst case is the old speed, never a lost prompt.

How it stops running, since nothing tells it to:

* **Idle timeout.** Claude Code sends no shutdown on exit, so a daemon that has not been
  asked anything for 30 minutes exits by itself. This is what keeps abandoned processes
  from accumulating across days of sessions.
* **Superseded by new code.** The socket name embeds a digest of the hook sources, so
  edited code addresses a different socket. The old daemon is simply never contacted again
  and idles out. No restart step to remember, and no window where stale logic is served.
* **Singleton by bind.** Two sessions starting at once both try to bind; the loser sees
  `EADDRINUSE` and exits quietly, leaving the winner serving both.

Reads only. It never writes to the store, so a crash cannot corrupt anything, and the
worst a wedged daemon costs is a client timeout and a fallback.
"""

from __future__ import annotations

import json
import os
import socket
import os.path
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.ipc import IDLE_TIMEOUT_SEC, socket_path, store_key  # noqa: E402
from lib.open import open_store  # noqa: E402

#: Requests are small; anything larger is not a query we generated.
MAX_REQUEST_BYTES = 64 * 1024


class Daemon:
    def __init__(self, path: str, store: object) -> None:
        self.path = path
        self.store = store
        self.last_seen = time.monotonic()
        self._lock = threading.Lock()

    # -- serving ---------------------------------------------------------------

    def _answer(self, request: dict) -> str:
        query = str(request.get("q") or "").strip()
        if not query:
            return ""
        kwargs = {
            "k": int(request.get("k") or 6),
            "budget": int(request.get("budget") or 700),
        }
        header = request.get("header")
        if header:
            kwargs["header"] = str(header)
        try:
            # Serialised deliberately. The store is a read handle over SQLite and is not
            # documented as thread-safe; a per-prompt hook has no concurrency worth the
            # risk of finding out otherwise.
            with self._lock:
                # Both backends answer the same call. The local one is a `Memvara`; the
                # hosted one is a `HostedRecall` holding a kept-alive TLS connection,
                # which is the whole reason a hosted install wants a daemon: the same
                # request costs 609ms on a fresh connection and 177ms on a warm one.
                return str(self.store.recall(query, **kwargs) or "")
        except Exception:
            # A failed query is an empty answer, never a dead daemon.
            return ""

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5.0)
            try:
                chunks, size = [], 0
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_REQUEST_BYTES:
                        return
                    chunks.append(chunk)
                request = json.loads(b"".join(chunks).decode("utf-8", "replace"))
                if not isinstance(request, dict):
                    return
                conn.sendall(self._answer(request).encode("utf-8"))
            except (OSError, ValueError, socket.timeout):
                # Client vanished mid-exchange, or sent nonsense. Neither is fatal.
                return

    def run(self) -> int:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.path)
        except OSError:
            # Either a live daemon owns this address, or a dead one left the file behind.
            # Telling those apart by connecting is the only reliable test: a stale socket
            # refuses, a live one accepts.
            from lib.ipc import send

            if send(self.path, {"q": ""}, timeout=1.0) is not None:
                return 0  # someone else is already serving this exact address
            try:
                os.unlink(self.path)
                server.bind(self.path)
            except OSError:
                return 0
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

        server.listen(16)
        server.settimeout(30.0)
        try:
            while True:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    if time.monotonic() - self.last_seen > IDLE_TIMEOUT_SEC:
                        return 0
                    continue
                self.last_seen = time.monotonic()
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
        finally:
            server.close()
            try:
                os.unlink(self.path)
            except OSError:
                pass


def main() -> int:
    store = open_store()
    if store is None:
        # No library, or no local store. On a paste-the-URL hosted install that is the
        # normal state, not a broken one, so fall through to the stdlib HTTP client
        # rather than exiting.
        from lib.hosted import open_hosted

        store = open_hosted()
    if store is None:
        # Nothing to serve at all. Exiting is correct: a daemon with no backend would
        # accept connections and answer every one with silence, which is indistinguishable
        # from a working daemon over a store that happens to be empty.
        return 0
    try:
        # Pay the first-query costs -- imports, page cache, TLS handshake -- before any
        # prompt is waiting on them. For hosted this is the handshake that turns a 609ms
        # first call into a 177ms one.
        store.recall("warm", k=1)
    except Exception:
        pass
    return Daemon(socket_path(store_key()), store).run()


if __name__ == "__main__":
    raise SystemExit(main())
