"""What one coding host's hook I/O looks like, written down as data.

Every hook body in this package used to spell Claude Code's protocol inline: the stdin
key a session id arrives under, the reply key a status line has to be printed against,
the tag a machine-generated prompt is wrapped in. Six sibling plugin repos are about to
vendor these same bodies for six other hosts, and a literal repeated in seven places
fails the way this repository's CLAUDE.md describes at length -- by doing nothing, in a
copy nobody is reading. So the literals move here, one `Host` record per client, and the
bodies read them.

**The record says what a host cannot do, not only what it does.** A canonical hook name
absent from `events` is a hook that host has no event for; `context_key = ""` is a host
with no per-turn injection channel at all; `transcript = None` is a host where capture
cannot run. Those are the states a port gets wrong, and an absent key is the one spelling
that cannot be mistaken for a working default.

`collections.namedtuple` rather than `typing.NamedTuple`, and that is a cost decision
rather than a style one: `typing` is not otherwise imported anywhere on the per-prompt
path and costs 3-5ms measured to import, against a client budget of ~30ms. This
repository already refuses `pathlib` there for 10.5ms. `collections` is already loaded by
the time any hook runs, so this is free. Field names are exactly what the class-syntax
version would have had.
"""

from __future__ import annotations

from collections import namedtuple

#: One hook invocation, in the shape the bodies read. `raw` is the undecoded stdin object,
#: kept so a body can reach a field no host record has been taught yet -- and so that a
#: reader debugging a port can see what actually arrived rather than what we extracted.
Event = namedtuple(
    "Event",
    "hook session cwd prompt transcript_path tool_name reentrant raw",
)

#: One hook's answer, before any host has been asked how to spell it. `status` is the line
#: a person at the terminal sees; `context` is text put in front of the model;
#: `decision`/`reason` are the permission verdict a pre-tool event carries. Empty means
#: "this reply does not carry that", which is not the same as a host that cannot carry it
#: -- `Host.status_key` and `Host.context_key` decide that, and they decide it once.
Reply = namedtuple("Reply", "hook status context decision reason",
                   defaults=("", "", "", ""))

#: How a host keeps the conversation on disk, and therefore whether `capture` can mine it.
#: Presence is the capability flag: `Host.transcript = None` means this host does not hand
#: a hook anything to read back, so the Stop-equivalent body must not run at all.
TranscriptSpec = namedtuple("TranscriptSpec", "format")

#: Everything the pre-tool auto-approve needs that differs by host. Deliberately NOT the
#: read-only tool list: which memory_* tools are safe to run unprompted is a fact about
#: our own MCP server, identical everywhere, and duplicating it per host would let one
#: copy start approving a forget.
ApproveSpec = namedtuple("ApproveSpec", "matcher separators decision_key reason_key allow")

#: The headless CLI `capture` shells out to in order to mine a turn: the command without
#: the prompt, which is appended. The recursion sentinel is deliberately absent -- that
#: environment variable is ours, identical on every host, and lives in `lib.ipc` for the
#: same reason `READ_ONLY` and `RECALL_MARKERS` do.
ExtractorSpec = namedtuple("ExtractorSpec", "argv")

Host = namedtuple(
    "Host",
    "id plugin_root_env events fields envelope context_key status_key "
    "context_token_cap supports_async timeouts client_configs config_format "
    "transcript tools noise machine_prompt_prefixes reentry_field approve "
    "extractor extractor_label",
)

#: Canonical hook names. The bodies and `run.py` speak these; `Host.events` maps each to
#: whatever the host calls the event it fires.
HOOKS = ("session_start", "recall", "capture", "approve")

_ACTIVE: "Host | None" = None


def use(host: "Host") -> None:
    """Bind this process to one host. `run.py` calls this before importing a body.

    Before, not after: `lib.transcript` resolves the host's noise markers at import time,
    so a body imported ahead of this call would be built against the wrong client.
    """
    global _ACTIVE
    _ACTIVE = host


def active() -> "Host":
    """The bound host, defaulting to Claude Code.

    The default is what makes `python3 hooks/recall.py` -- with no arguments, the way this
    plugin has always been invoked and the way its tests drive it -- keep meaning exactly
    what it meant before. `hosts.default` names it, not this module: `core/` is meant to
    be the same bytes in every repository that vendors these hooks, and the identity of
    the client is exactly what is not. Imported lazily so a host resolved by `run.py`
    never pays for a second one.
    """
    if _ACTIVE is None:
        from hosts import default

        return default()
    return _ACTIVE
