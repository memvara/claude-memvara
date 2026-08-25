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
| `UserPromptSubmit` | Recalls against every prompt, skipping what it has already injected |
| `Stop` | Keeps the turn that just ended, and mines it for facts |
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
rather than one of their own choosing, and they degrade to doing nothing
when there is no store, no library and no login.

The hooks say so, but not all in the same place. Plain stdout from a hook
is either context for the model or nothing at all, depending on the
event, and neither is visible to the person watching — so a hook that had
silently stopped working looked exactly like one with nothing to say.

Recall and session start are synchronous and print a one-line
`systemMessage`: `⋈ Memvara · 4 memories recalled` before the turn (and
`1 memory`, singular, when there is one).

Capture prints nothing, because it runs `async`. Extraction shells out to
`claude -p` and takes 12–14 seconds, and a synchronous `Stop` hook holds
the turn open for all of it; async hands the turn straight back. The
client discards an async hook's output, so the report moves to
`~/.memvara/.hooks/capture.log` rather than being dropped — every path
that reaches a decision writes a line there, including the ones that
decide to do nothing.

Recall distinguishes three outcomes, and two of them used to read the
same. `⋈ Memvara · no matching memories` means the store was asked and had
nothing; `⋈ Memvara · recall failed — see capture.log` means it could not
be asked at all. Collapsing those into one message is how a hosted client
whose session id had gone stale went on reporting an empty store for a
whole session — from the terminal that is indistinguishable from a store
that is genuinely empty, and nobody investigates an empty store. Three
separate defects had to line up for it, and each is now closed: the
client re-handshakes instead of holding a dead session id, the daemon
answers `{"ok": false}` instead of an empty string, and the client only
treats `ok: true` as authoritative.

**Storage is rich; injection is clipped.** They are different jobs. A
memory worth keeping carries its reasoning — that is what stopped captured
facts being useless one-liners — and it made each about four times bigger.
Measured over eight real prompts against a 222-claim store: median
injected memory 48 tokens, p90 237, max 503, and four lines over 150
tokens accounted for **39% of every token injected**.

So the block now carries excerpts of at most 160 characters and one line
saying so, while the whole note stays stored, stays embedded, and is what
`memory_search` returns. With `k=4` and a 300-token budget that is a **75%
cut in what recall costs**, measured end to end on the same eight prompts:
3,073 fresh tokens before, 731 after.

When the structured layer comes back thin, a second pass asks for the raw
turns as well. That pass **selects wider than the claims pass and injects
no wider** — `k` is the candidate cap episodes have to win a slot inside,
and episodes are down-weighted against claims, so a narrower cap means
none ever places. Measured against the deployed server, `k=2` returned an
episode at no budget at all. What bounds the cost is the clip: a
1,853-character median episode arrives as a 160-character pointer.

Standing preferences moved with it. Procedural memories apply to every
turn, so they are asked for once in the opening block — where they are
paid for once and then cached — instead of being retrieved again on each
prompt, where they also crowded out the incidental facts that prompt was
actually about.

What recall spends is now written to `~/.memvara/.hooks/recall.log`, per
prompt. The write path has had a token ledger since 0.1.2 and the read
path had none, which is how the hook that spends context on every single
prompt became the one nobody could measure.

A prompt that is purely a reply — "yes please", "go ahead" — is searched
together with the last prompt that had a topic in it, not on its own. The
query used to be the prompt verbatim, and two function words retrieve
arbitrary neighbours: measured on a real store, a turn approving a memory
cleanup was handed notes about pricing tiers and an unrelated project's
zip layout. Never an error, just the whole block's budget spent on noise.
The carried topic is prepended rather than substituted, so "yes, add that
fix to #7" still carries the `#7`, and it only advances when you say
something with a topic in it.

`⋈ Memvara · 3 memories recalled · 4 already in context` is the fourth
shape. A memory injected on turn 1 is still in the conversation on turn
5, so re-injecting it spends budget a genuinely new memory could have
had. Hashes of what has already gone in are kept per session under
`~/.memvara/.hooks/recalled/`.

Capture keeps two things per turn, because they fail differently. The
turn itself goes in as an episode — searchable prose, the reasoning and
the sentence you actually typed, which is the part a triple loses. On a
`fast-path-only` server, which is what the hosted endpoint reports, that
costs one round trip and no tokens at all: the episode is committed
before the extraction gate is consulted, and billing counts net-new
claims rather than episodes.

Then it mines the turn for facts. That half needs a model, and uses the
one you already pay for: it shells out to `claude -p` against your
existing Claude Code login, so there is no `ANTHROPIC_API_KEY` and no
second bill. It does not go through `MEMVARA_LLM`, which stays `none`.
On a hosted install there is no local store to open, so capture writes
over the same MCP endpoint recall reads; a write the endpoint refuses is
logged as failed rather than counted as stored.

**Facts are written from a closed vocabulary.** The model picks from the
core's registered predicates and returns nothing when none fit, rather
than inventing one per fact. This is not tidiness. `remember()`
normalizes a predicate but never registers one, and an unregistered
predicate is multi-valued forever — so an invented predicate can never
supersede an older value of the same fact. Measured on a real store: one
preference about file paths occupying four live claims under four
invented names, none replacing any other, all four competing for the same
recall budget. Project facts take the repository as their subject, keyed
on the git *remote* rather than the path, so two worktrees and a clone on
another machine file into one place instead of three.

**And the object has to stand on its own.** The old prompt asked for
"the value alone", which is right for `lives_in` ("Lisbon") and useless
for a preference, where the value *is* the instruction: the store still
holds `deployment_approach = verification_first`, which no later session
can act on. Predicates that carry instructions now require the reasoning
and the concrete detail with them, and a memory too thin to be applied is
dropped rather than stored.

**A fact is attributed to whoever actually said it.** The mined turn is
labelled — `User:` is what you typed, `Claude:` is what the assistant
wrote, `Tool result` is what a command returned — and the extractor is
told that a fact about *you* may come only from your own lines, and that
a project fact needs evidence rather than the assistant's analysis,
however confident that analysis sounds.

This closed a loop rather than tidying an edge case. An inference the
assistant made about a Postgres memory setting was mined out of its own
reply, stored as a project fact, recalled ninety minutes later into
another session under a header saying these were notes about the user,
and quoted back to them as their own note corroborating the inference.
Nothing errored at any step, and each retelling made the guess look
better supported.

Two mechanical checks back the instruction up, because a prompt can be
ignored. A memory this plugin injected into the turn, which the model
then merely repeated, is dropped unless your own half of the exchange
supports it — otherwise recall feeds capture and one guess becomes
several stored rows that agree with each other. And identifiers,
versions and measurements have to appear somewhere in the turn; prose
does not, because a memory worth keeping is composed rather than quoted.

Two limits worth stating plainly. `sources` is still not among the hosted
`memory_remember` arguments, so on a hosted install a captured fact has
no attached source turn — the episode above is the compensating
mechanism, searchable if not linked. `extractor` *is* sent now, so a
captured fact reports itself as `claude-code-hook` instead of
`Derived by user`, which is what let a mined inference read back as
something you had stated; but only against a server new enough to accept
the argument, and the client asks before sending, because argument
validation there is closed and a wrong guess costs the whole write rather
than the one field. And the engineering predicate pack
is loaded server-side through `MEMVARA_PREDICATES`, which
`app.memvara.dev` does not set, so project predicates land unregistered
there however this plugin writes them.

Capture is scoped to one turn and runs once per turn. The `Stop` hook
mines the whole exchange — the prompt you typed and the reply it got —
because the two halves hold different things: a standing instruction is
stated in the prompt, while what was actually decided and where it landed
is in the reply.

Mining them separately was tried first and is worth recording as a
failure. Asking a model for durable facts *about the user* in Claude's
own reply returns nothing, correctly: measured over one session, fifteen
extractions in an hour returned an empty list every time and paid a full
run for each. One run over both halves is half the cost and sees all the
evidence.

A headless run is about 21k tokens of Claude Code's own preamble before
it reads a word of your conversation — roughly $0.018 and 12–14s on
Haiku, whether handed one sentence or twenty — so **budget one run per
turn**. Batching was cheaper still and lost data: the old hook kept the last 48 formatted lines of
each batch while advancing its watermark past everything it had read, so
on a session with large tool outputs most of the transcript was skipped
unread and could never be reconsidered. Measured on one session: 630 KB
consumed, six extractions paid for, only the tail of each ever seen.

Because the bill is per run and not per token — 239 runs measured over
four days spent 2,491 fresh input tokens against 8.0M of preamble — the
only lever left is running less often. Capture skips a turn whose whole
typed prompt is a continuation (`yes`, `do it`, `continue`) or is very
short, unless it contains a word that suggests a decision. The filter is
deliberately timid: a skipped turn is a fact lost with no trace, and the
saving is one run.

Each run appends a line to `~/.memvara/.hooks/capture.log` saying how
many facts were found, how many were stored, whether the turn itself was
kept, and what was dropped for falling outside the vocabulary — which is
where a failed write is distinguishable from a quiet turn, and where a
vocabulary that is too narrow is distinguishable from a model ignoring
it.

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
