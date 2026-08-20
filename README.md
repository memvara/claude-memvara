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

This marketplace is for **Claude Code**. Cursor, Codex, Grok and VS Code
have their own repos when those exist. Claude Desktop and ChatGPT paste
the same URL: see [memvara.dev/docs/agents](https://memvara.dev/docs/agents).

A loop you wrote is not this path. Python: `pip install memvara`.

## CLI equivalent

```
claude plugin marketplace add memvara/claude-memvara
claude plugin install memvara@claude-memvara
```

## License

Apache-2.0. The skill is vendored from [memvara/memvara](https://github.com/memvara/memvara);
a test fails if the copy drifts.
