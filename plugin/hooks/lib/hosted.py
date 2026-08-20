"""Hosted recall over the MCP endpoint, using nothing but the standard library.

This exists so a hosted install needs no `pip install memvara`. The plugin's install story
is "paste a URL", and a hook that silently did nothing until someone also installed a
Python package would be worse than no hook: it fails the same way a working hook over an
empty store looks.

Three things here were found by measurement rather than reasoning, and each one is a
silent failure if you skip it.

**Set a User-Agent.** Cloudflare rejects the default `Python-urllib/3.13` with error 1010
before the request reaches the application at all. Measured side by side: the stock agent
gets 403/1010, and `curl/8.7.1`, a browser string and `memvara-hook/0.1` all get through to
a genuine 401. Nothing in that 403 hints that the client's name is the problem.

**Bring a CA bundle.** python.org's macOS build does not use the system trust store, so
verification fails with CERTIFICATE_VERIFY_FAILED on a certificate every other tool on the
machine accepts. `certifi` is used when present and the default context otherwise.

**Use `http.client`, not `urllib`.** `urlopen` builds a fresh connection per call, which
throws away the TLS handshake every prompt — about 170ms of the ~390ms a cold request
costs. `HTTPSConnection` is the stdlib object that can be held open, and holding it is the
entire reason the daemon pays for itself on a hosted install: ~390ms cold against
~162-287ms warm.

Everything fails to `None`. A hosted store that cannot be reached is a prompt without a
memory block, never a prompt with an error in it.
"""

from __future__ import annotations

import json
import os
import os.path
import ssl

#: Anything but the stdlib default. See the module docstring: this single header is the
#: difference between reaching the application and being refused at the edge.
USER_AGENT = "memvara-hook/0.1"

#: Written by `memvara-mcp login`.
CREDENTIALS = os.path.join(os.path.expanduser("~"), ".memvara", "credentials.json")

DEFAULT_BASE = "https://app.memvara.dev"
MCP_PATH = "/mcp"

#: Long enough for a cold TLS handshake on a slow link, short enough that a wedged
#: endpoint does not hold a prompt hostage.
TIMEOUT_SEC = 6.0

PROTOCOL_VERSION = "2025-06-18"


def credentials() -> "dict | None":
    """`{'api_key': ..., 'server_url': ...}` or None when not logged in."""
    try:
        with open(CREDENTIALS, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("api_key"):
        return None
    return data


def _context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class HostedRecall:
    """One kept-alive connection to the hosted MCP endpoint.

    Constructed cheaply and connected lazily: a client that dials on __init__ would pay
    the handshake even when the daemon it belongs to is never asked anything.
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._conn = None
        self._session: "str | None" = None
        self._id = 0

    # -- transport -------------------------------------------------------------

    def _connect(self):
        import http.client
        import urllib.parse

        parts = urllib.parse.urlsplit(self._base)
        host = parts.hostname or "app.memvara.dev"
        port = parts.port
        if parts.scheme == "http":
            return http.client.HTTPConnection(host, port, timeout=TIMEOUT_SEC)
        return http.client.HTTPSConnection(host, port, timeout=TIMEOUT_SEC,
                                           context=_context())

    def _rpc(self, method: str, params: "dict | None" = None,
             retry: bool = True) -> "dict | None":
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params

        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {self._key}",
            "user-agent": USER_AGENT,
        }
        if self._session:
            headers["mcp-session-id"] = self._session

        try:
            if self._conn is None:
                self._conn = self._connect()
            self._conn.request("POST", MCP_PATH, json.dumps(body), headers)
            response = self._conn.getresponse()
            raw = response.read()
        except Exception:
            # A kept-alive connection the server has since closed raises on reuse. That is
            # normal and recoverable exactly once: reconnect and try again, so a daemon
            # that has idled does not answer the first prompt after a gap with silence.
            self.close()
            if retry:
                return self._rpc(method, params, retry=False)
            return None

        session = response.getheader("mcp-session-id")
        if session:
            self._session = session
        if response.status != 200:
            return None
        return _decode(raw)

    def close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    # -- the one call a hook makes ---------------------------------------------

    def _ensure_session(self) -> bool:
        if self._session:
            return True
        reply = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "memvara-hook", "version": "0.1"},
        })
        if reply is None:
            return False
        # Some servers issue no session id and are stateless. Treat a successful
        # initialize as sufficient rather than requiring the header.
        self._session = self._session or "stateless"
        self._rpc("notifications/initialized")
        return True

    def recall(self, query: str, *, k: int = 6, budget: int = 700,
               header: "str | None" = None) -> "str | None":
        """Recall text, or None if the hosted store could not answer."""
        if not query.strip() or not self._ensure_session():
            return None
        args = {"query": query, "k": k, "budget": budget}
        reply = self._rpc("tools/call", {"name": "memory_recall", "arguments": args})
        if not isinstance(reply, dict):
            return None
        result = reply.get("result")
        if not isinstance(result, dict):
            return None
        text = "\n".join(
            block.get("text", "") for block in result.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not text:
            return None
        return f"{header}\n{text}" if header else text


def _decode(raw: bytes) -> "dict | None":
    """A JSON-RPC reply, whether it arrived as JSON or as one SSE frame."""
    body = raw.decode("utf-8", "replace").strip()
    if not body:
        return None
    if body.startswith("{"):
        try:
            return json.loads(body)
        except ValueError:
            return None
    # text/event-stream: the payload is on `data:` lines.
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                parsed = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def open_hosted() -> "HostedRecall | None":
    """A hosted client if this machine is logged in, else None."""
    creds = credentials()
    if creds is None:
        return None
    return HostedRecall(str(creds["api_key"]),
                        str(creds.get("server_url") or DEFAULT_BASE))
