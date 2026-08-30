"""Claude Code, as a `Host` record.

Every value here is the literal the hook bodies used to carry inline. Nothing was
tidied on the way across, and one of them must not be tidied later either:
`extractor_label` is written into users' stores and rendered back by `memory_why`, so
changing the string re-labels history that was recorded under the old one.
"""

from __future__ import annotations

# Absolute, not relative: every entry point puts `plugin/hooks/` on `sys.path` and
# imports both `core` and `hosts` as top-level packages from there.
from core.host import ApproveSpec, ExtractorSpec, Host, TranscriptSpec

HOST = Host(
    id="claude",
    plugin_root_env=("CLAUDE_PLUGIN_ROOT",),
    #: Canonical hook name -> the event this client fires. A canonical name absent from
    #: this mapping is a hook the host has no event for; Claude Code has all four.
    events={
        "session_start": "SessionStart",
        "recall": "UserPromptSubmit",
        "capture": "Stop",
        "approve": "PreToolUse",
    },
    #: `Event` field -> the stdin keys it may arrive under, first match wins. A tuple
    #: rather than a string so a client that renames a key can be followed without a
    #: release that breaks everyone still on the old one.
    fields={
        "session": ("session_id",),
        "cwd": ("cwd",),
        "prompt": ("prompt",),
        "transcript_path": ("transcript_path",),
        "tool_name": ("tool_name",),
    },
    #: Replies are `{"systemMessage": ..., "hookSpecificOutput": {"hookEventName": ...}}`.
    envelope="nested",
    #: The only field on these events that reaches the model, and the only one that
    #: reaches the person at the terminal. A host with "" for either has no such channel,
    #: and the renderer drops that half of the reply rather than inventing a key.
    context_key="additionalContext",
    status_key="systemMessage",
    #: Uncapped: this client imposes no ceiling of its own on injected context, so the
    #: only budget is the one `recall.BUDGET` sets for cost reasons.
    context_token_cap=0,
    supports_async=True,
    timeouts={"session_start": 20, "recall": 10, "capture": 120, "approve": 5},
    client_configs=("~/.claude.json", "~/.claude/settings.json"),
    config_format="json",
    transcript=TranscriptSpec(format="jsonl"),
    #: The tools whose use is evidence that a turn did something. See
    #: `lib.transcript.INCLUDE_TOOLS`, which reads this.
    tools=frozenset({"Edit", "Write", "Bash", "NotebookEdit"}),
    #: Markup this client wraps around text that is not conversation. Only the host's own
    #: tags belong here: the markers this plugin injects are in `transcript.RECALL_MARKERS`
    #: and are the same on every host, because mining our own output back in is a bug
    #: everywhere.
    noise=("<command-message>", "<command-name>", "<system-reminder>",
           "<local-command-stdout>"),
    #: A finished background task and a message from another session arrive through the
    #: prompt event wrapped in these. Answering one spends a retrieval query on a task id.
    machine_prompt_prefixes=("<task-notification", "<cross-session-message"),
    #: Set when the Stop event is a hook-triggered continuation rather than a real end of
    #: turn. Mining it would double-count the reply.
    reentry_field="stop_hook_active",
    approve=ApproveSpec(
        matcher="mcp__.*memvara.*",
        #: How a namespaced tool name splits into its leaf. `mcp__memvara__memory_search`
        #: and `mcp__plugin_memvara_memvara__memory_search` both end in the leaf.
        separators=("__",),
        decision_key="permissionDecision",
        reason_key="permissionDecisionReason",
        allow="allow",
    ),
    #: Declared, not yet read: `lib/extract.py` still builds this argv itself, and
    #: `test_extraction_cannot_recurse` pins the `--settings` literal in that file. The
    #: two must be reconciled by whichever change teaches `lib/extract.py` to ask the host
    #: -- until then `lib/extract.py` is the live copy and this one is a description of it.
    extractor=ExtractorSpec(
        argv=("claude", "-p", "--settings", '{"hooks":{}}',
              "--model", "claude-haiku-4-5-20251001", "--output-format", "json"),
    ),
    #: Written into every claim this plugin stores and rendered back by `memory_why`.
    #: Changing it for tidiness re-labels history.
    extractor_label="claude-code-hook",
)
