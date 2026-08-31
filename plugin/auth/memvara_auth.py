"""Ask the deployment what this machine's credential actually is.

Six answers, and merging any two of them is the failure this module exists to end. On
2026-08-30 a host's own OAuth minted a token that lived fifty-nine minutes; when it died,
every surface said some version of "not authenticated", and an evening went into
re-authenticating a credential that had worked perfectly and then expired. Nothing
anywhere said "expired". So:

    authenticated  the deployment recognises this credential right now
    expired        it did recognise it, until a named instant
    revoked        it was disabled deliberately -- re-authenticating will not help
    unknown        the deployment does not recognise it: it is wrong, not stale
    absent         this machine holds no credential at all
    unreachable    nothing was learned about any credential, because nothing answered

`unreachable` comes first and costs a round trip on every run, deliberately. `/v1/health`
takes no credential, so it is the only thing that can tell a dead deployment from a dead
key -- and without it every outage reads as a login problem and sends the user to fix
something that was never broken.

Four requirements below were measured rather than reasoned about, and every one of them
fails while naming something else. They are already paid for once in
`../hooks/lib/hosted.py`; this module inherits them rather than rediscovering them.

**Set a User-Agent.** Cloudflare refuses the stdlib default `Python-urllib/3.13` with
error 1010 -- a 403 at the edge, before the request reaches the application, with nothing
in it hinting that the client's *name* is the problem.

**Bring a CA bundle.** python.org's macOS build does not read the system trust store:
`ssl.create_default_context()` there loads zero roots and fails CERTIFICATE_VERIFY_FAILED
against a certificate `curl` accepts. `certifi` is used when importable and the default
context otherwise -- never a hard dependency, because this plugin installs with no pip
step and must keep doing so.

**Send `X-Memvara-CSRF`.** Its absence is `403 csrf_failed` on the device routes, which
reads as an authentication failure and is not one. Presence is the whole check; the value
is free. Sent on every call rather than only the ones that need it, so there is one code
path to be wrong about.

**Use `http.client`, not `urllib`.** `urlopen` cannot hold a connection open, and a probe
makes two calls before it can say anything at all.

Two error envelopes, and code that parses one misreads the other as silence:

    /v1/*                     {"error": {"code": ..., "message": ..., "detail": ...}}
    /mcp, the RFC 8628 routes {"error": ..., "error_description": ...}

Misreading is not a crash here -- it lands every refusal in the fallback state, which is
worse, because the fallback is a confident answer. `_message` reads both.

**Nothing here writes anything.** Every credential source is read-only: obtaining a
credential is a separate act with a separate command, and a probe that quietly rewrote a
file would be a second writer to it with no way to tell whose token is live.
"""

from __future__ import annotations

import http.client
import json
import os
import os.path
import ssl
import urllib.parse

#: One of "authenticated" | "expired" | "revoked" | "unknown" | "absent" | "unreachable".
CredentialState = str

#: Anything but the stdlib default. See the module docstring: this single header is the
#: difference between reaching the application and being refused at the edge.
USER_AGENT = "memvara-cli/0.1"

CSRF_HEADER = "x-memvara-csrf"
CSRF_VALUE = "cli"

DEFAULT_BASE = "https://app.memvara.dev"
HEALTH_PATH = "/v1/health"
WHOAMI_PATH = "/v1/whoami"

#: Long enough for a cold TLS handshake on a slow link, short enough that a wedged
#: endpoint does not hold a command open indefinitely.
TIMEOUT_SEC = 10.0

ENV_KEY = "MEMVARA_API_KEY"
ENV_URL = "MEMVARA_SERVER_URL"

#: Held unexpanded and expanded at the moment of use. A path resolved at import time is a
#: path that cannot be redirected, which makes the read paths untestable without touching
#: the developer's own credentials.
CREDENTIALS = "~/.memvara/credentials.json"

#: Where this host keeps the MCP configuration a user may have pasted a key into. Read to
#: report *which* credential is live, never written. Ordered, and consulted last: a key
#: the user set explicitly should win over one a client wrote for them.
HOST_CONFIGS = (
    "~/.claude.json",
    "~/.claude/settings.json",
    "~/.claude/settings.local.json",
)


class AuthError(RuntimeError):
    """Something went wrong that a person needs to be told about."""


class Unreachable(AuthError):
    """Nothing answered. Never a statement about a credential.

    Distinct from a refusal for the same reason `HostedError` is distinct from an empty
    recall: `except Unreachable` and `if status == 401` are different questions, and a
    caller that cannot tell them apart reports an outage as a login problem.
    """


def _context() -> ssl.SSLContext:
    """A context that trusts what `curl` trusts, on machines where the default does not."""
    try:
        import certifi  # noqa: PLC0415

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _connect(host: str, port: "int | None", timeout: float, *, scheme: str = "https"):
    if scheme == "http":
        return http.client.HTTPConnection(host, port, timeout=timeout)
    return http.client.HTTPSConnection(host, port, timeout=timeout, context=_context())


#: One connection per (scheme, host, port) for the life of the process. A probe makes two
#: calls and the device poll makes one every five seconds; `urlopen` would throw away the
#: TLS handshake each time -- about 170ms per call against this endpoint.
_CONN: dict = {}


def close() -> None:
    """Drop every kept connection. Safe to call twice, and on a process that made none."""
    for conn in list(_CONN.values()):
        try:
            conn.close()
        except Exception:
            pass
    _CONN.clear()


def _base_url() -> str:
    """The deployment to talk to: the environment, then the credentials file, then ours.

    Read in that order for the same reason `hosted.credentials()` reads it there -- a
    machine that sets both must not reach a different store depending on which client
    happened to look. A self-hosted deployment named only in the credentials file would
    otherwise be probed at `app.memvara.dev`, and answer perfectly about the wrong server.
    """
    url = (os.environ.get(ENV_URL) or "").strip()
    if not url:
        data = _read_json(_expand(CREDENTIALS))
        if isinstance(data, dict):
            url = str(data.get("server_url") or "").strip()
    return (url or DEFAULT_BASE).rstrip("/")


def request(method: str, path: str, *, body=None, auth=None,
            timeout: float = TIMEOUT_SEC) -> "tuple[int, dict]":
    """One HTTPS call to the deployment. The only network primitive in this module.

    Returns `(status, parsed body)`; a body that will not parse is `{}`, because plenty of
    statuses arrive with none or with HTML from something in front of the API, and the
    status alone is still an answer. **Raises `Unreachable` when nothing answered at all** --
    that is the distinction the whole probe is built on, so it cannot be collapsed into a
    status code here.
    """
    base = _base_url()
    parts = urllib.parse.urlsplit(base)
    scheme = parts.scheme or "https"
    host = parts.hostname or "app.memvara.dev"
    key = (scheme, host, parts.port)

    payload = None if body is None else json.dumps(body)
    headers = {
        "accept": "application/json",
        "user-agent": USER_AGENT,
        CSRF_HEADER: CSRF_VALUE,
    }
    if payload is not None:
        headers["content-type"] = "application/json"
    if auth:
        headers["authorization"] = f"Bearer {auth}"

    conn = _CONN.get(key)
    reused = conn is not None
    try:
        # Building the connection is inside the try, not before it. `HTTPSConnection`
        # does not dial, so this looks like it cannot fail -- but resolving the CA bundle
        # does run here, and anything that raises outside this block leaves the caller
        # holding a raw `ssl` or socket error instead of the one exception this module
        # promises. The probe then has no branch for it and the command dies with a
        # traceback where it owed the user a sentence.
        if conn is None:
            conn = _CONN[key] = _connect(host, parts.port, timeout, scheme=scheme)
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        raw = response.read()
    except Exception as exc:
        _CONN.pop(key, None)
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        if reused:
            # A kept-alive connection the server has since closed raises on reuse. That is
            # normal and recoverable exactly once: the retry builds a fresh connection, so
            # `reused` is False there and a real outage still raises.
            return request(method, path, body=body, auth=auth, timeout=timeout)
        raise Unreachable(f"{base} did not answer: {exc}") from exc
    return response.status, _decode(raw)


def _decode(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message(body: dict) -> str:
    """Whatever the deployment said about a refusal, from either envelope.

    See the module docstring. A reader of only the `/v1` shape returns `""` for every
    `/mcp` refusal, and `""` classifies as unrecognised -- so both the revoked key and the
    wrong key would report as the same state, confidently.
    """
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")
    description = body.get("error_description")
    if description:
        return str(description)
    return str(error or "")


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _bearer(value) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text


def _header_key(node) -> "str | None":
    """A memvara `Authorization` header anywhere in a host config, depth first.

    Depth first because the host nests one `mcpServers` map per project --
    `~/.claude.json` holds `projects.<absolute path>.mcpServers` -- so a reader that only
    looks at the top level finds nothing on exactly the machine it was written to answer
    for.
    """
    if isinstance(node, dict):
        servers = node.get("mcpServers")
        if isinstance(servers, dict):
            for name, server in servers.items():
                if not isinstance(server, dict):
                    continue
                url = str(server.get("url") or "")
                if "memvara" not in str(name).lower() and "memvara" not in url.lower():
                    continue
                headers = server.get("headers")
                if not isinstance(headers, dict):
                    continue
                for header, value in headers.items():
                    if str(header).lower() == "authorization":
                        token = _bearer(value)
                        if token:
                            return token
        for value in node.values():
            found = _header_key(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _header_key(value)
            if found:
                return found
    return None


def credential() -> "tuple[str | None, str | None]":
    """`(api_key, source)` for the credential this machine would actually use, or
    `(None, None)`.

    Three sources in the order every memvara client resolves them, and `source` names the
    one that won. That name is the point: a user holding an environment variable, a
    credentials file and a host config that disagree cannot otherwise answer "which key is
    the host using", and holding all three is the normal state of a machine that has been
    logged in twice.
    """
    key = (os.environ.get(ENV_KEY) or "").strip()
    if key:
        return key, ENV_KEY

    path = _expand(CREDENTIALS)
    data = _read_json(path)
    if isinstance(data, dict):
        key = str(data.get("api_key") or "").strip()
        if key:
            return key, path

    for template in HOST_CONFIGS:
        path = _expand(template)
        key = _header_key(_read_json(path)) or ""
        if key:
            return key, path
    return None, None


def _result(state: CredentialState, detail: str, source: "str | None" = None,
            **extra) -> dict:
    result = {"state": state, "detail": detail, "source": source, "scope": None,
              "privilege": None, "expires_at": None, "read_only": None}
    result.update(extra)
    return result


def probe(*, timeout: float = TIMEOUT_SEC) -> dict:
    """`{'state', 'detail', 'source', 'scope', 'privilege', 'expires_at', 'read_only'}`.

    Health first, then the credential, then `whoami`. The order is the design: a probe
    that asks `whoami` first cannot tell a refused key from a deployment that refuses
    everything, and answers the user's question with the wrong noun.
    """
    base = _base_url()
    try:
        status, _body = request("GET", HEALTH_PATH, timeout=timeout)
    except Unreachable as exc:
        return _result("unreachable", f"{base} could not be reached: {exc}. "
                                      "Nothing was learned about your credential.")
    if status != 200:
        return _result("unreachable",
                       f"{base} answered HTTP {status} to a health check that carries no "
                       "credential, so this is the deployment and not your key.")

    key, source = credential()
    if not key:
        return _result("absent",
                       f"no credential on this machine: {ENV_KEY} is unset, "
                       f"{_expand(CREDENTIALS)} does not hold one, and neither does any "
                       "MCP configuration this host reads.")

    try:
        status, body = request("GET", WHOAMI_PATH, auth=key, timeout=timeout)
    except Unreachable as exc:
        return _result("unreachable", f"{base} answered a health check and then stopped "
                                      f"answering: {exc}", source)

    if status == 200:
        expires = body.get("expires_at")
        privilege = body.get("effective_privilege") or body.get("granted_privilege")
        when = f"expires at {expires}" if expires else "never expires"
        return _result(
            "authenticated",
            f"the credential from {source} is live: {privilege or 'unknown'} privilege, "
            f"{when}.",
            source,
            scope=body.get("scope"),
            privilege=privilege,
            expires_at=expires,
            read_only=body.get("read_only"),
        )

    message = _message(body)
    lowered = message.lower()
    if "expired at" in lowered:
        instant = message.split("expired at", 1)[1].strip() or "an unstated time"
        return _result("expired",
                       f"the credential from {source} expired at {instant}. It "
                       "authenticated correctly until then; re-authenticate to replace it.",
                       source)
    if "disabled" in lowered:
        return _result("revoked",
                       f"the credential from {source} has been disabled. It did not "
                       "expire -- someone revoked it, and re-authenticating mints a new "
                       "one rather than restoring this one.",
                       source)
    if status != 401:
        return _result("unknown",
                       f"{base} refused the credential from {source} with HTTP {status}: "
                       f"{message or 'no reason given'}.",
                       source)
    # Everything else the deployment refuses is a credential it does not accept: wrong,
    # not missing. Never `absent` -- telling a user with a bad key that they have no key
    # sends them into a re-login that cannot fix it, and they will run it twice before
    # doubting the message. The wording matched above is the thing most likely to change
    # server-side, so this fallback is where a wording change lands.
    #
    # "the bearer token is not recognised" arrives here rather than at a branch of its
    # own. A branch would reach the same state by a different sentence, and the sentence
    # this writes quotes the deployment verbatim, which is what a reader needs when the
    # wording is one nobody here anticipated. Folding the known wording in means the
    # recorded fixture exercises the path a wording change will actually take.
    return _result("unknown",
                   f"{base} does not recognise the credential from {source}"
                   + (f": {message}." if message else "."),
                   source)
