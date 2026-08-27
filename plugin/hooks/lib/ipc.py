"""Addressing and framing for the recall daemon. Shared by both ends.

The socket's *name* does most of the safety work here, so it is worth saying why it is
built the way it is.

It contains a digest of two things: the store the daemon opened, and the source code it
opened it with.

* **The store**, because a daemon is a warm handle on one specific database. A second
  project pointing at a different `MEMVARA_DB` must not reach it — it would be answering
  one store's questions out of another store's memory, which is a privacy failure, not a
  cache miss.
* **The code**, because a long-lived process keeps running whatever it was started with.
  Edit a hook and the daemon serves the old logic indefinitely, and nothing looks wrong.
  Folding a digest of the sources into the name means changed code simply addresses a
  different socket: the new client starts a new daemon, and the old one idles out and
  exits on its own. No version negotiation, no restart command to remember, no way to be
  silently served by stale code.

The directory is `0700` and the socket `0600`. A unix socket carrying recall output is a
read interface to everything the user has ever stored, and the default umask would have
left it readable by every account on the machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import os.path
import socket

# `pathlib` is deliberately absent. Importing it costs 10.5ms measured, against a client
# whose entire budget is ~30ms, and every path here is a string join and a stat. `open.py`
# still uses it freely — that module is only reached on the fallback path, where 10ms is
# already lost in the noise of a 148ms in-process query.
_HOME = os.path.expanduser("~")

#: Private by construction: created 0700, and re-chmod'd on every call because an
#: existing directory from an older version may predate that rule.
RUNTIME_DIR = os.path.join(_HOME, ".memvara", ".hooks", "run")

#: Files whose contents decide what a daemon actually does. A change to any of them must
#: strand the old daemon rather than let it keep serving.
CODE_FILES = ("daemon.py", "lib/ipc.py", "lib/open.py", "recall.py")

#: Set in the environment of the `claude -p` child that `capture.py` spawns to mine a turn.
#: A hook that finds it is running underneath an extraction rather than in front of a
#: person, and must stand down.
#:
#: It lives here, in the one module every hook already imports, because the alternative is
#: the literal string in each of them -- and a sentinel that has drifted apart across four
#: files fails by doing nothing, which is the failure this whole guard exists to stop.
#: `lib.extract` imports it from here rather than declaring its own.
CAPTURE_SENTINEL = "MEMVARA_CAPTURE_ACTIVE"


def under_extraction() -> bool:
    """Whether this hook is running inside the extractor's own child process.

    `capture.py` launches that child with `--settings '{"hooks":{}}'`, and the comment
    there long claimed the empty hook set was what kept the child inert. It is not: that
    clears the hooks a *settings file* declares and does not touch the ones a **plugin**
    registers, so the child ran this plugin's hooks like any other session. Measured rather
    than assumed -- a `claude -p` run fires `SessionStart` and `UserPromptSubmit`, both
    confirmed with a marker file, and `recall-sample.log` caught the result in the act:
    41 of 77 sampled prompts were the extractor's own "Extract durable facts from the
    exchange below", each answered with a retrieval query and a standing block.

    Two costs, and the second is the one that matters. A retrieval query is spent per
    extraction against an allowance that is not per-session. And the child gets a session
    id it has never used before, so nothing is deduplicated and the whole standing block is
    injected -- into the one prompt whose entire job is to decide which sentences in front
    of it are facts worth storing. `capture.py` already passes `injected` to `triples()` to
    stop the store's own output being mined back in as new; letting the child recall in the
    first place hands that filter a problem it should never have been given.
    """
    return bool(os.environ.get(CAPTURE_SENTINEL))


#: How long a client waits. Generous next to a 6ms query and mean next to a 148ms cold
#: fallback: past this the daemon is wedged and the in-process path is the faster answer.
CLIENT_TIMEOUT_SEC = 2.0

#: A daemon with no client for this long has outlived its session and exits. This is what
#: stops abandoned processes accumulating after Claude Code quits, since nothing sends a
#: shutdown on exit.
IDLE_TIMEOUT_SEC = 30 * 60


#: The brand mark, as one character.
#:
#: `public/brand/mark-dark.svg` is two arcs on one axis meeting at a node -- valid time
#: closed, transaction time still open, which is the product's whole idea. A
#: `systemMessage` is a string the client renders into its own terminal UI, so there is no
#: image channel to put that in: whatever stands in for it has to be one glyph.
#:
#: BOWTIE is the closest honest one. Two strokes meeting at a node is the mark's own
#: geometry, and it is the relational-algebra join -- which is what a store of facts about
#: one subject does. It is BMP rather than an emoji on purpose: a glyph a terminal font
#: lacks renders as a tofu box, which is worse than no mark at all, and BMP maths symbols
#: are carried nearly everywhere a monospace font is.
MARK = "\u22c8"


def status(text: str) -> str:
    """The one line a person watching the terminal actually sees.

    Composed here rather than at each call site because there were eight of them, every one
    repeating both the mark and the word. A status line that says `Memvara` in seven places
    and something else in the eighth is the kind of drift nobody notices until a screenshot.
    """
    return f"{MARK} Memvara \u00b7 {text}"


def runtime_dir() -> str:
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    try:
        os.chmod(RUNTIME_DIR, 0o700)
    except OSError:
        pass
    return RUNTIME_DIR


def _code_digest(root: str) -> str:
    h = hashlib.sha256()
    for name in CODE_FILES:
        try:
            with open(os.path.join(root, *name.split("/")), "rb") as fh:
                h.update(fh.read())
        except OSError:
            # A missing file is itself a distinguishing state: it must not collide with
            # the complete install's address.
            h.update(b"\0missing\0" + name.encode())
    return h.hexdigest()


def socket_path(store_key: str, root: "str | None" = None) -> str:
    """Where the daemon for this store, running this code, listens."""
    here = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    digest = hashlib.sha256(
        f"{store_key}\0{_code_digest(here)}".encode()
    ).hexdigest()[:16]
    # Unix socket paths are length-limited (~104 bytes on macOS), hence the truncation
    # rather than a readable name.
    return os.path.join(runtime_dir(), f"recall-{digest}.sock")


#: Where MCP clients keep the server block we mine for configuration. Checked in order;
#: the first one that names a `memvara` server wins.
_CLIENT_CONFIGS = (
    os.path.join(_HOME, ".claude.json"),
    os.path.join(_HOME, ".claude", "settings.json"),
)


def server_env() -> "dict[str, str]":
    """The `env` block the client launches the memvara MCP server with.

    Lives here rather than in `open.py` because both halves need it and only one of them
    can afford `pathlib`: the client computes the socket address from this before it opens
    anything, so config discovery has to sit on the cheap side of the import graph.

    Empty when no client config names a memvara server. This is discovery, not validation
    — whatever is found goes to `ServerConfig.from_env`, which decides if it is usable.
    """
    for path in _CLIENT_CONFIGS:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
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


def store_key() -> str:
    """Identity of the store this process would open, without opening it.

    Derived from configuration rather than from a live handle, because the client must
    compute the same address as the daemon *before* paying to open anything.
    """
    env = {**server_env(), **{k: v for k, v in os.environ.items() if k.startswith("MEMVARA_")}}
    db = env.get("MEMVARA_DB") or ""
    if db and db != ":memory:":
        try:
            db = os.path.realpath(os.path.expanduser(db))
        except OSError:
            db = os.path.expanduser(db)
    hosted = ""
    if not db:
        # A hosted install has no MEMVARA_DB, so without this every hosted account on the
        # machine would hash to the same address and share one daemon -- one account's
        # memories answering another's prompts.
        try:
            with open(os.path.join(_HOME, ".memvara", "credentials.json"), encoding="utf-8") as fh:
                creds = json.load(fh)
            hosted = f"{creds.get('server_url','')}|{creds.get('project','')}"
        except (OSError, ValueError):
            hosted = ""

    return "\0".join([
        db,
        hosted,
        env.get("MEMVARA_MODE", ""),
        env.get("MEMVARA_TENANT", ""),
        env.get("MEMVARA_USER", ""),
        env.get("MEMVARA_AGENT", ""),
        env.get("MEMVARA_SESSION", ""),
        env.get("MEMVARA_EMBEDDER", ""),
    ])


def send(path: str, request: dict, timeout: float = CLIENT_TIMEOUT_SEC) -> "str | None":
    """One request, one reply. `None` means "no daemon" — the caller must fall back.

    Every failure collapses to `None` on purpose. A refused connection, a stale socket
    file left by a killed daemon, a hung server, a half-written reply: from the caller's
    side these are one condition, "this path did not work", and the response to all of
    them is the in-process query.
    """
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(path)
        conn.sendall(json.dumps(request).encode("utf-8"))
        # Half-close so the server reads a clean EOF instead of guessing a length.
        conn.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", "replace")
    except (OSError, socket.timeout, ValueError):
        return None
    finally:
        try:
            conn.close()
        except OSError:
            pass


# -- hook stdio ---------------------------------------------------------------
#
# These live beside the socket code rather than in `open.py` for one reason: every hook
# needs them, and `open.py` imports `pathlib`. Reaching for `payload()` must not drag a
# 10.5ms import onto a path whose whole budget is ~30ms.


def payload() -> "dict":
    """The hook's stdin JSON, or `{}` when there is nothing readable there."""
    import sys

    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


#: Bounded by truncation rather than rotation, like the capture log beside it: a debugging
#: aid that needs its own maintenance is worse than no aid.
LOG_MAX_BYTES = 64 * 1024


def log_line(name: str, text: str) -> None:
    """Append one line to `~/.memvara/.hooks/<name>.log`, or give up quietly.

    A second logger, deliberately, rather than reusing `lib.write.log`: that module imports
    `pathlib` for the writing hooks that can afford it, and this one is called from the
    per-prompt path where the same import costs 10.5ms measured. `os.path` and a plain
    `open` do the whole job.

    It exists because the write path has had a token ledger since 0.1.2 and the read path
    has had none -- so the hook that spends context on every single prompt was the one
    nobody could measure, which is exactly how it came to spend four times what it needed
    to without anyone noticing.
    """
    import time

    directory = os.path.join(_HOME, ".memvara", ".hooks")
    path = os.path.join(directory, f"{name}.log")
    try:
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            with open(path, "w", encoding="utf-8"):
                pass
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


def plural(n: int, word: str = "memory", many: str = "memories") -> str:
    """`1 memory`, `2 memories`. Shared so the three hooks cannot drift apart on it."""
    return f"{n} {word if n == 1 else many}"


def emit_json(reply: dict) -> None:
    """Print one JSON object: the hook protocol's structured reply.

    Plain stdout from a hook is either context for the model or nothing at all, depending
    on the event, and neither is visible to the person watching the terminal. `systemMessage`
    is, on both of the events this plugin answers, which is the only reason to prefer this
    over `emit`.
    """
    import sys

    sys.stdout.write(json.dumps(reply) + "\n")


def emit(text: str) -> None:
    """Print a block for the model, or print nothing.

    Whitespace-only output is suppressed rather than printed: a lone newline still reads
    as an injected context block to anyone debugging the transcript.
    """
    import sys

    if text and text.strip():
        sys.stdout.write(text.rstrip() + "\n")
