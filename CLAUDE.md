# Working in a memvara plugin repository

These repos are thin. Each one is an install surface — a manifest, a vendored skill, some
tests — wrapped around a library that lives somewhere else. Almost every mistake made here
comes from forgetting that, so this file is about the habits that follow from it rather
than about the code.

`memvara/memvara` is the core. This repo packages it.

## Read the core repository before proposing anything to it

Not skim: read. The design decisions are written down, at length, in three places, and all
three are load-bearing:

- **`docs/INTERNALS.md`** states the invariants and *why* each one is the way it is.
- **`docs/ROADMAP.md`** has a section called **Deliberately deferred** and another called
  **What is still missing**. They exist so that considered-and-declined stops reading as
  not-yet-done. If your proposal is in either, the question is settled and the burden is on
  new evidence.
- **The tests are the design document.** `tests/test_server.py` and `tests/test_pipeline.py`
  explain reasoning in docstrings that runs to paragraphs. Test *names* alone will tell you
  whether a behaviour is deliberate.

This has a measured cost. A predicate-router design was written in this repo and then cut
by three quarters on a second pass, because reading the core would have shown that:

- the mechanism already existed — `Memvara(...)` has taken a `registry` parameter all along;
- the MANY default was already deliberate and already documented in `INTERNALS.md`
  ("Wrongly retiring a true fact is worse than keeping two competing ones");
- the contradiction report already shipped, as `types.Accumulation` plus `_receipt_summary`;
- and the inference the plan was built on had been **explicitly rejected** in a test, with a
  better argument than the plan had: two live values in one slot can be a contradiction
  (`quota_gate/status`) or perfectly correct (`agent-memory/rejected`), the rows are
  identical, "the difference is intent and intent is not a property of the row".

None of that needed new machinery. It needed twenty lines of server plumbing. Two checks
would have caught it before a word was written:

1. **grep the constructor** for the parameter you are about to propose adding;
2. **read the test names** for the behaviour you are about to propose changing.

## The skill is vendored. Do not edit it here.

The source of truth is `memvara/skills/memvara/` in `memvara/memvara`. `skill.lock` pins the
commit, CI diffs the vendored copy against that commit, and every plugin repo pins the same
sha. Edit the copy here and two things happen: sync overwrites you, and CI fails first.

Fix the skill upstream, then let sync bring it across.

There is exactly one sanctioned local transform, in `claude-memvara`: the frontmatter
`name: memvara` becomes `name: memory`, so the client renders `/memvara:memory` rather than
`/memvara:memvara`. It is applied during sync, and the drift test compensates for that one
line and no other — every remaining byte still has to match.

**Documentation ships in the same commit as the code.** Inherited from the core repo's own
CLAUDE.md, and it means the README here too: a README that oversells the install is how
someone finds a background process they were told would not exist.

## Runtime facts that cost hours to find

Each of these was measured, not reasoned about, and each fails silently.

- **`MEMVARA_LLM=none` means `NullLLM`.** `memory_add` accepts prose, stores nothing, and
  reports no-fact. Write triples with `memory_remember` instead. Server `memory_stats` says
  `fast-path-only` when this is the case — check it before assuming a store is empty.
- **Triple writes never register a predicate.** `remember()` bypasses extraction, and
  predicate acquisition lives only on the extraction path. With 23 builtins and a 200-slot
  learned cap, anything you write is MANY (nothing supersedes it) and SLOW (a **730-day**
  half-life). The receipt's accumulation note reports the cardinality half. Nothing reports
  the volatility half, because a mis-ranked fact produces no event at all.
- **Cloudflare rejects the stdlib User-Agent.** `app.memvara.dev` answers
  `Python-urllib/3.13` with error 1010 — a 403 at the edge, before the request reaches the
  application. `curl`, a browser string and `memvara-hook/0.1` all reach a real 401. Any
  stdlib HTTP client here must set an explicit User-Agent, and nothing in the 403 hints that
  the client's *name* is the problem.
- **python.org's macOS build ignores the system trust store.** `CERTIFICATE_VERIFY_FAILED`
  on a certificate `curl` accepts. Use `certifi` when present, `ssl.create_default_context()`
  otherwise. "Standard library only" is not the same as "no dependencies" on macOS.
- **`claude -p` costs ~21k tokens of Claude Code's own preamble per run**, regardless of
  input size — measured at 16.3k cache-read plus 4.9k cache-creation on a two-sentence
  input. Batch the work; the overhead is per-run, not per-token. `--bare` is not a cost
  lever: it skips auth loading and returns "Not logged in".
- **Use `http.client`, not `urllib`, for anything repeated.** `urlopen` cannot reuse a
  connection. On the hosted endpoint the same call is 609ms cold and 177ms warm.

## If this repo ships hooks

Today that is `claude-memvara` only. The rules are general.

- **A hook must never fail a prompt.** No store, no library, no credentials, bad config:
  print nothing, exit 0.
- **But silence hides breakage, so verify bytes and never timings.** `python3 -S` looked
  like a 55% speedup. It was the hook returning zero bytes — numpy lives in site-packages,
  and the hook's own degrade-to-silence swallowed the ImportError. *The fastest
  configuration was the broken one.* Diff output length against a known-good run before
  believing any performance result.
- **A daemon is an optimisation, never a dependency.** Every route must return the same
  text; only latency differs. Assert that byte-for-byte.
- **Address a daemon by what it serves and what it runs.** The socket name digests both the
  store identity and the hook sources, so a second store cannot reach it and edited code
  strands it rather than being served stale. Neither problem then needs managing.
- **Know the budget.** Interpreter startup is ~21ms and is the floor. `import memvara` is
  ~95ms; `pathlib` is ~10.5ms. Keep both off the per-prompt path.
- **Scenario-test the lifecycle; do not assert it.** Killing a daemon with `-9`, racing two
  starts, and editing a hook mid-flight found two real bugs that unit tests did not —
  including one where the fallback quietly held while the optimisation was entirely broken.

## Before proposing new machinery

1. `grep` the constructor or signature for the parameter you want to add.
2. Read the test names for the behaviour you want to change.
3. Check `docs/ROADMAP.md` — *Deliberately deferred*, then *What is still missing*.
4. Check `docs/INTERNALS.md` for the invariant you are about to cross.
5. Then write the plan, and say which of the four you checked.

---

# Karpathy guidelines

Behavioural guidelines for reducing common LLM coding mistakes, from
[multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
(declared MIT in the skill's frontmatter), derived from
[Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876).
They are merged here rather than vendored as a second skill: they govern how work is done
*in* this repository, and shipping them inside the plugin would hand every memvara user a
third-party skill they did not install.

**Tradeoff:** these bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think before coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity first

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports, variables and functions that *your* changes orphaned; leave
  pre-existing dead code alone unless asked.

The test: every changed line should trace directly to the request.

## 4. Goal-driven execution

**Define success criteria. Loop until verified.**

- "Add validation" → "write tests for invalid inputs, then make them pass"
- "Fix the bug" → "write a test that reproduces it, then make it pass"
- "Refactor X" → "ensure tests pass before and after"

For multi-step work, state the plan as steps with their checks, then run it.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due
to overcomplication, and clarifying questions arriving before implementation rather than
after mistakes.

## Where they bite hardest in this project

Not decoration — each of these has already cost time here.

- **§1 and §2 against the core repository.** The predicate-router episode is the worked
  example above: a design was written before the core was read, and the second pass cut it
  by three quarters because the mechanism already existed and the inference it rested on had
  been explicitly rejected upstream. "Think before coding" here means *read `INTERNALS.md`,
  the roadmap's deferred list, and the test names* — not merely pause.
- **§3 against a vendored tree.** `plugin/skills/` is not yours to improve. Style, wording
  and formatting there are upstream's; the only sanctioned local edit is the one line
  `skill.lock` and the drift test know about.
- **§4 against silent failures.** Most defects in this repository do not raise. "Verify"
  therefore has to mean comparing output — bytes, counts, a diff against a known-good run —
  never that a command exited 0 or ran fast. A hook that returns nothing is the fastest hook
  there is.

One local amendment to §3, because this repository's own rule is stricter, not looser:
**documentation ships in the same commit as the code.** Updating the README, `CHANGELOG.md`
or a tool description alongside a behaviour change *is* the surgical change, not scope creep.
