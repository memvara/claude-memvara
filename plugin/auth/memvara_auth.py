"""Ask the deployment what this machine's credential actually is, and obtain one.

Two halves, in that order, because acting before asking is how a working install gets
replaced by a fresh one nobody needed.

`probe` reports which of six states this machine is in, without writing anything.
`authenticate` runs an RFC 8628 device-code login and writes the key it is handed.

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

**One file is ever written, `~/.memvara/credentials.json`, at `0600`, and only by
`write_credentials`.** Every credential *source* stays read-only, including this host's
own MCP configuration: the host's OAuth client already writes that file, and a second
writer to it leaves nobody able to say whose token is live. `probe` writes nothing at
all -- asking is not acting, and a probe that quietly refreshed a file would answer a
question by changing the answer.

**`device_code` is a secret and never leaves this module.** The authorize call returns it
exactly once; `poll` sends it back and nothing else ever sees it -- not standard output,
not the credentials file, not an error message. `user_code` is the half meant for a
person, and is printed on purpose, along with the verification URI, whether or not a
browser opens. A machine with no browser is the case this whole flow exists for.
"""

from __future__ import annotations

import http.client
import json
import os
import os.path
import re
import ssl
import sys
import tempfile
import time
import urllib.parse
import webbrowser

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


# -- the device-code flow, RFC 8628 ---------------------------------------------------

AUTHORIZE_PATH = "/api/auth/device/authorize"
TOKEN_PATH = "/api/auth/device/token"

#: The deployment names an interval and a lifetime in every grant, and both are honoured.
#: These are only what to do when it names neither.
POLL_INTERVAL_SEC = 5.0
POLL_CEILING_SEC = 900.0

#: RFC 8628 §3.5: on `slow_down` the client increases its interval by five seconds and
#: keeps the wider one. The server also sends `Retry-After`, which is not read here --
#: `request` returns a status and a body and teaching it to return headers for one hint
#: buys nothing the RFC's own rule does not already give.
SLOW_DOWN_STEP_SEC = 5.0

#: A project id exactly as the console shows it, and nothing else. See `authenticate`.
PROJECT_ID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

#: One example, used in the message that refuses a malformed argument. A refusal that
#: only says no leaves the user guessing at the shape it wanted.
PROJECT_ID_EXAMPLE = "3c04449a-3d99-4c0e-9f0a-1b2c3d4e5f60"


def authorize(project: "str | None" = None, *, timeout: float = TIMEOUT_SEC) -> dict:
    """Start a device grant and return what the deployment handed back.

    `project` absent means the request names no project at all and the approver chooses
    one in the console. A deployment that has not taken that change refuses, and refuses
    two different ways -- measured live on 2026-08-31:

        400  the route's own `project must be a project id (uuid)`
        422  the schema's, because `project` is still a required field there

    Both get the same sentence, naming the argument form that works today. Only those two
    statuses do: a 429 or a 503 is a deployment that is busy or down, and telling that
    user to name a project id is advice they will follow and that cannot help.

    The returned dict holds `device_code`, which is a **secret**. It is passed to `poll`
    and goes nowhere else -- not to a terminal, not to a file, not into an error message.
    """
    body: dict = {} if project is None else {"project": project}
    status, reply = request("POST", AUTHORIZE_PATH, body=body, timeout=timeout)
    if status in (200, 201):
        if not reply.get("device_code") or not reply.get("user_code"):
            raise AuthError(
                f"{_base_url()} answered HTTP {status} to a device login but returned no "
                "code to poll with, so there is nothing to wait for.")
        return reply

    detail = _message(reply)
    said = f": {detail}" if detail else ""
    if status in (400, 422):
        raise AuthError(
            f"{_base_url()} refused this login with HTTP {status}{said}\n"
            f"Name the project yourself: /memvara authenticate <project-id>\n"
            f"<project-id> is the project's id in the console -- a dashed UUID like "
            f"{PROJECT_ID_EXAMPLE}, not its slug and not its tenant id.")
    raise AuthError(f"{_base_url()} refused to start a device login with HTTP "
                    f"{status}{said}.")


def poll(grant: dict, *, timeout: float = TIMEOUT_SEC) -> dict:
    """Wait for a human to decide the grant, and return the minted key when one does.

    RFC 8628 §3.4 and §3.5. Four words the token route answers, all at HTTP 400 and all
    in the RFC's flat envelope rather than this API's own:

        authorization_pending  nobody has decided yet -- wait `interval` and ask again
        slow_down              asking too fast; widen the interval and keep it widened
        access_denied          a human said no; a one-way door, so stop
        expired_token          the grant died before anyone decided it

    Bounded twice. The grant's own `expires_in` is the real limit, and the 900-second
    ceiling covers a deployment that names a longer one than any person will sit at a
    terminal for. Either bound alone leaves a loop that can run until the process dies.

    Sleeps before the first poll, deliberately: nobody has had time to approve anything in
    the instant after the code was printed, and an immediate poll only spends the
    server's rate limit to be told so.
    """
    device_code = str(grant.get("device_code") or "")
    if not device_code:
        raise AuthError("there is no device code to poll with.")

    interval = float(grant.get("interval") or POLL_INTERVAL_SEC)
    lifetime = min(float(grant.get("expires_in") or POLL_CEILING_SEC), POLL_CEILING_SEC)
    deadline = time.monotonic() + lifetime

    while time.monotonic() < deadline:
        time.sleep(interval)
        status, reply = request("POST", TOKEN_PATH, body={"device_code": device_code},
                                timeout=timeout)
        if status == 200 and reply.get("api_key"):
            return reply

        # RFC 8628 fixes the word in `error` itself, so it is read from there rather
        # than through `_message` -- which would answer with `error_description` if this
        # route ever grew one, and turn a retryable `slow_down` into an unrecognised
        # refusal that stops the flow.
        raw = reply.get("error")
        outcome = raw if isinstance(raw, str) else _message(reply)
        if outcome == "authorization_pending":
            continue
        if outcome == "slow_down":
            interval += SLOW_DOWN_STEP_SEC
            continue
        if outcome == "access_denied":
            raise AuthError("the login was denied in the console. Nothing was minted, "
                            "and polling this code again can only say the same thing; "
                            "run the command again to start a new one.")
        if outcome == "expired_token":
            raise AuthError("this code expired before anyone approved it. Run the "
                            "command again for a fresh one.")
        raise AuthError(f"{_base_url()} refused the poll with HTTP {status}: "
                        f"{outcome or 'no reason given'}.")

    raise AuthError(f"nobody approved this login within {int(lifetime)} seconds. Nothing "
                    "was minted; run the command again for a fresh code.")


def write_credentials(minted: dict, *, server_url: "str | None" = None) -> str:
    """Put the minted key in `~/.memvara/credentials.json` at `0600`, and return the path.

    **The only file anything in this module writes.** A host's own MCP configuration is
    read, never written: the host's OAuth client already writes that file, and a second
    writer to it leaves nobody able to say whose token is live.

    Written to a temporary file in the same directory and moved into place. The file being
    replaced may hold the only copy of a key the API will never show again -- it is
    returned exactly once, at the poll that mints it -- so a plain `open(path, "w")` would
    truncate a working credential on its first byte and leave nothing to fall back to if
    anything after that failed. `os.replace` is atomic on one filesystem: either the old
    key or the new one, never neither.

    The mode is set on the descriptor before a byte is written, not on the path
    afterwards, so the plaintext is never briefly readable by anyone else on the machine.
    """
    path = _expand(CREDENTIALS)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    payload = {"api_key": minted["api_key"], "server_url": server_url or _base_url()}
    for name in ("project", "privilege"):
        if minted.get(name):
            payload[name] = minted[name]

    handle_fd, temporary = tempfile.mkstemp(dir=directory, prefix=".credentials-",
                                            suffix=".json")
    try:
        # `mkstemp` already creates at 0600; this restates it at the point a reader looks
        # for the file's mode. Inheriting a security property from a helper's documented
        # default is how it stops being checked when the helper is swapped out.
        os.fchmod(handle_fd, 0o600)
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def _announce(grant: dict, out) -> str:
    """Show the human half of the grant and return the URI to open, if there is one.

    `user_code` and the verification URI are printed **whether or not** a browser opens.
    A machine with no browser is the case this whole flow exists for, and a client that
    only opens one leaves that machine with nothing on screen to act on.
    """
    uri = str(grant.get("verification_uri_complete")
              or grant.get("verification_uri") or "")
    print(f"Your code is {grant.get('user_code')}", file=out)
    if uri:
        print(f"Open {uri} and approve it there.", file=out)
    print("Waiting for approval...", file=out)
    return uri


def authenticate(project: "str | None" = None, *, out=None) -> int:
    """Run the whole flow and return a process exit code. 0 is a key on disk.

    `project` is a dashed UUID when given, and is sent as-is. When omitted the request
    carries no project at all and the approver chooses it in the console.

    A bare 32-hex tenant id (`prj_3c04449a3d99...`) is **not** silently reshaped into a
    UUID. That derivation works, but guessing at an id format on the user's behalf turns
    a clear 400 into a credential minted against the wrong project -- and that is not an
    error anyone ever sees. It is refused here rather than at the endpoint, because a
    round trip only buys the same answer in the endpoint's words instead of ours.

    Every failure is a sentence and a non-zero code. Nothing here raises at a caller: the
    thing on the other side of this function is a person who typed a command.
    """
    out = sys.stdout if out is None else out
    # Surrounding whitespace comes off a pasted argument and nothing else does. Trimming
    # what a terminal added is not the same as reshaping what the user meant.
    project = None if project is None else project.strip()
    if project is not None and not PROJECT_ID.match(project):
        print(f"{project!r} is not a project id.\n"
              f"/memvara authenticate <project-id> takes the dashed UUID the console "
              f"shows, like {PROJECT_ID_EXAMPLE}.\n"
              "A slug and a tenant id are refused rather than converted: a credential "
              "minted against the wrong project is not an error anyone ever sees.",
              file=out)
        return 2

    try:
        grant = authorize(project)
    except AuthError as exc:
        print(str(exc), file=out)
        return 1

    uri = _announce(grant, out)
    if uri:
        try:
            webbrowser.open(uri)
        except Exception:
            # A machine with no browser is the case this flow exists for. The code and
            # the URI are already on screen, so there is nothing to report and nothing
            # to do differently.
            pass

    try:
        minted = poll(grant)
    except AuthError as exc:
        print(str(exc), file=out)
        return 1

    path = write_credentials(minted)
    where = f" to {minted['project']}" if minted.get("project") else ""
    how = f", {minted['privilege']} privilege" if minted.get("privilege") else ""
    # What the key's lifetime is, this does not say. The token route does not report one
    # and `probe` reads `whoami` on every run, which answers it from the deployment
    # rather than from an assumption made here at minting time.
    print(f"Authenticated{where}{how}. The key is in {path}, readable only by you.",
          file=out)
    return 0
