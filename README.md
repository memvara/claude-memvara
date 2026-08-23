# claude-memvara

Give Claude Code a memory it can prove — hosted MCP and the skill that
says how to use it, in one install.

```
/plugin marketplace add memvara/claude-memvara
/plugin install memvara
```

The first connection opens a browser so you can click Allow. That grant
lasts 90 days, and no API key is involved.

The hooks below do run locally, and one of them starts a short-lived
background process to keep recall fast. Nothing is installed to do it:
they use the standard library, and the process exits after 30 minutes
idle. Delete the `hooks/` directory and the plugin is still a working
MCP server plus the skill.

## What you get

Ten tools on `https://app.memvara.dev/mcp`: `memory_recall`,
`memory_search`, `memory_since`, `memory_add`, `memory_remember`,
`memory_forget`, `memory_end`, `memory_history`, `memory_why`,
`memory_stats`.

The `memory` skill is the judgment a single tool description cannot
carry: which surface to use, the sequence when a stored fact is
disputed, scope, clocks, and that `memory_forget` is not erasure.
Claude Code shows it as `/memvara:memory`.

Four hooks, so memory happens without being asked for:

| Event | What it does |
|---|---|
| `SessionStart` | Opens the session with standing facts, and names the scope it is bound to |
| `UserPromptSubmit` | Recalls against every prompt |
| `Stop` | Ingests the turn that just ended |
| `PreToolUse` | Auto-allows read-only `memory_*` tools; writes still ask |

The hooks try three routes and return the same text from each; only the
latency differs. A resident daemon answers in ~38 ms. Without one, a
local install queries in-process in ~148 ms. A hosted install with no
`memvara` package installed goes over plain stdlib HTTP — no
`pip install`, since the hosted install story is pasting a URL.

| Install | Cold | With the daemon |
|---|---|---|
| Local (SQLite) | 148 ms | **38 ms** |
| Hosted (stdlib HTTP) | ~390 ms | **~177 ms** |

The daemon earns more on hosted than on local, because only a resident
process can hold the TLS connection open: the same request measured
609 ms on a fresh connection and 177 ms on a warm one. It exits after 30
minutes idle, and its socket address digests both the store and the hook
sources, so a second store can never reach it and edited code strands it
rather than being served stale.

Two things the hosted path needs that look like nothing when missing.
It must send a **User-Agent**: Cloudflare refuses the stdlib default with
error 1010 before the request reaches the application at all. And it
needs a **CA bundle**, because python.org's macOS build does not use the
system trust store and fails with `CERTIFICATE_VERIFY_FAILED` on a
certificate every other tool accepts.

Configuration is discovered from the `memvara` server block in your own
client settings, so the hooks open the store the MCP server writes to
rather than one of their own choosing, and they fall silent when there is
no store, no library and no login.

Capture needs a model, and uses the one you already pay for. It shells
out to `claude -p` against your existing Claude Code login, so there is
no `ANTHROPIC_API_KEY` and no second bill. It does not go through
`MEMVARA_LLM`, which stays `none`: the library's `NullLLM` accepts prose
and stores nothing, so facts are written as triples instead. On a hosted
install there is no local store to open, so capture writes those triples
over the same MCP endpoint recall reads; a write the endpoint refuses is
logged as failed rather than counted as stored.

A headless run costs about 21k tokens of Claude Code's own preamble
before it reads any of your transcript — roughly $0.018 and 12–14s on
Haiku, whether it is handed one sentence or twenty. So capture batches:
nothing runs until 2000 characters of unprocessed conversation have
accumulated, and below that the watermark stays put so the text joins the
next batch. Each run appends a line to `~/.memvara/.hooks/capture.log`
saying how many facts were found and how many were stored, which is the
only place a failed write is distinguishable from a quiet transcript.

The child is launched with an empty hook set, and refuses to start if it
finds itself already inside an extraction. Without both, a `Stop` hook
that spawns Claude would fire the child's `Stop` hook, forever.

Every extraction reports what it cost to
`~/.memvara/.hooks/usage.jsonl`, under the library's own
`write.tokens_in` and `write.tokens_out` — the series its metric
catalogue names as the one to bill on. `python3 hooks/lib/usage.py`
prints the running totals. Cache reads and cache writes are counted as
input and tagged so they stay separable, which is the whole picture:
a measured run spent **9 fresh input tokens against 21,130 cached**.
Counting only `input_tokens` would report that run at a two-thousandth
of its real size.

The ledger sits beside the store, never inside it. Operational
accounting is not a fact about you, and must not surface in a recall
block.

## Teach it your vocabulary

The built-in predicates are a personal-assistant vocabulary. A store of engineering facts
matches none of them, and an unknown predicate takes the safe default twice over:
multi-valued, so nothing supersedes it, and slow-decaying, so this morning's deploy still
ranks as fresh in two years. The first half shows up on the write receipt. The second is
silent.

Server-side configuration, so it is set where the server is launched:

```bash
MEMVARA_PREDICATES=engineering        # or: engineering,./ours.toml
```

A declaration outranks a guess, so a pack corrects a store that already classified
something wrongly rather than only shaping a fresh one.

## Coming from another memory product

```python
from memvara.compat import import_mem0, import_supermemory
```

mem0 records what changed and when, so that import rebuilds supersession. Supermemory
records current state, so its documents arrive as episodes on their original timestamps
and nothing invents a history it was never told — which means plain recall answers from
claims and looks empty until you ask for `include_episodes`. The skill says this at the
point of use.

## Other clients

This marketplace is for **Claude Code**. Cursor, Codex, Grok, VS Code and
OpenCode have their own repos (see [memvara.dev/docs/agents](https://memvara.dev/docs/agents)).
Claude Desktop and ChatGPT paste the same URL.

OpenClaw 2026.7 can install this as a Claude **bundle**:

```
openclaw plugins install memvara --marketplace memvara/claude-memvara
```

That loads the skill. Hosted HTTP MCP is listed but not started on 2026.7.1
("stdio only today"). Do not use an OpenClaw `slots.memory` capture plugin
as a workaround. A native HTTP MCP plugin is a follow-up if OpenClaw still
cannot run `type: http` from a bundle.

A loop you wrote is not this path. Python: `pip install memvara`.

## CLI equivalent

```
claude plugin marketplace add memvara/claude-memvara
claude plugin install memvara@claude-memvara
```

## License

Apache-2.0. The skill is vendored from [memvara/memvara](https://github.com/memvara/memvara);
a test fails if the copy drifts.
