# claude-memvara

Give Claude Code a memory it can prove — hosted MCP and the skill that
says how to use it, in one install.

```
/plugin marketplace add memvara/claude-memvara
/plugin install memvara
```

The first connection opens a browser so you can click Allow. That grant
lasts 90 days. Nothing is installed on the machine: there is no local
Python process and we do not use an API key.

## What you get

Ten tools on `https://app.memvara.dev/mcp`: `memory_recall`,
`memory_search`, `memory_since`, `memory_add`, `memory_remember`,
`memory_forget`, `memory_end`, `memory_history`, `memory_why`,
`memory_stats`.

The `memvara` skill is the judgment a single tool description cannot
carry: which surface to use, the sequence when a stored fact is
disputed, scope, clocks, and that `memory_forget` is not erasure.

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
