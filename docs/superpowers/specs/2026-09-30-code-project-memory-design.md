# Code project memory for `/memvara:index`: design

Status: draft for review, 2026-09-30. Updated 2026-10-15: the plugin, not memvara-code,
owns the decisions taken from git history for now (section 7, Phase 0). Nothing here is
built yet.

## 1. Summary

When `/memvara:index` runs in a coding project, it should store what a coding agent cannot
learn by reading the code: a map of the project's components, the conventions it follows,
and the decisions behind it, taken from the git history. Later sessions then show that
knowledge to the agent at two moments: once when the session starts, and again when the
agent opens or edits a file in a component that has notes.

We will **not** store a summary of every file. Section 2 explains why.

The work ships in five phases. Phase 0 is a measurement that decides whether the rest is
worth building. Phase 1 changes only the index command in this repository. Phases 2 and 3
add hooks, which have to be built in the core repository (memvara/memvara) and synced here.
Phase 4 is optional.

## 2. Why not a summary of every file

The first idea was to summarize each file, update the summary when the file changes, and
have the agent read memory before it reads code. We dropped it for these reasons.

1. **A file summary mostly repeats the file.** The agent can read the file itself with one
   tool call. Anthropic's Claude Code team has said that early versions of Claude Code used
   RAG over a local vector database, and that grep and glob searches run by the model
   worked better, partly because indexes go stale. Summaries are also weakest for the
   largest files: memvara's `core.py` is 327 KB, about 80k tokens.
2. **Files change in ways a hook cannot see.** `git pull`, rebases, branch switches and the
   user's own editor all change files. None of the plugin's hook events fires on those.
   All worktrees of a repository share one project scope, so two worktrees on different
   branches would overwrite each other's summary of the same file.
3. **The existing recall path cannot carry them.** The per-prompt hook injects at most 4
   memories in 300 tokens (`plugin/hooks/recall.py`, `K` and `BUDGET`). File summaries
   would compete with the user's preferences for those slots. The same file records that
   after 434 clipped injections the model called `memory_search` only 4 times, so the
   model rarely goes to memory on its own.
4. **Cost.** A model would have to read every file. The source of memvara core is about
   2.8M tokens and memvara-cloud about 3.4M. The hosted store has no extraction model.
5. **Privacy.** A description of every file in a private codebase would be sent to the
   hosted store.
6. **It goes against positions already written down.** memvara/code's design spec lists
   "LLM extraction of claims from code" and "vector search over code" as non-goals. Its
   productization plan requires every ingested document to attach to a durable entity or
   a dated event. The core roadmap defers model-written summaries because nothing ends a
   derived claim when the claims under it end.

Hindsight's coding-agent integration reached the same conclusion. It does not summarize
source files. It stores decisions taken from git history and past conversations, a survey
of the repository's structure, and five fixed knowledge pages. We take that shape and
change three things: memvara shows notes when a file is touched, uses git to end facts as
well as to add them, and keeps a model off the per-prompt path.

Hindsight is installed on the development machine where this was written, and its log
there is a warning about silent failure. Since 2026-09-12 it has logged 1,306 session starts
and about 11,000 failed calls, all "connection refused". It injected memory twice. Every
session was still told that it was "still learning this repo". Section 5.8 exists because
of this.

## 3. Goals and non-goals

**Goals**

- A new session in an indexed repository starts knowing the project's components, its
  conventions and its recent decisions.
- When the agent opens or edits a file, it sees the decisions and known problems recorded
  for that part of the codebase, without having to ask.
- Knowledge stays correct as the code changes, and the agent can tell how current it is.
- Every step reports what it did, including when it did nothing and when it failed.
- Each phase is measured against a session with no memory before the next phase starts.

**Non-goals**

- A summary of every file.
- Embeddings or vector search over source code.
- Identity for individual symbols, such as functions and classes, and their history across
  renames. That is memvara-code's job.
- Reading diffs. Version 1 reads commit messages and the list of changed paths only.
- Indexing work that is not yet merged. Only the default branch's history is read.

## 4. What the user sees

**Running the index.** The user runs `/memvara:index` in a repository. The command checks
for a coding project. If it finds one and this is the first code run for the project, it
says what it will store and where, and asks once before writing anything, for example:

> This is a Python project. I will store a map of up to 25 components and up to 40
> decisions taken from the last 300 commits on `main`, in the hosted memvara store for
> `github.com/acme/api`. Commit messages are read; code and diffs are not sent. Continue?

At the end it reports what it wrote, what it skipped and why, what it proposes to end, and
which commit the map was built from.

**Starting a session (Phase 2).** The status line says one of three things: that a brief
was loaded, with its component and decision counts and how many commits old it is; that
this repository has no code map yet; or that the brief could not be loaded, and why. The
model receives a short brief: one line per component, a few conventions and the most
recent decisions.

**Opening a file (Phase 3).** The first time in a session that the agent reads or edits a
file in a component that has notes, the notes for that component are added to its
context, once. Nothing is added for a component with no notes.

## 5. Design

### 5.1 Detecting a coding project

The index command already reads manifest files in its step 4. The code mode runs when the
repository has at least one manifest from that list (`pyproject.toml`, `package.json`,
`Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `Gemfile`, `requirements*.txt` and
similar) and tracked source files (`git ls-files`). A repository without both gets
today's behaviour only, and the report says so.

### 5.2 Data model

No new predicates are invented. Everything uses the `engineering` and `decisions` packs
that the core already ships.

**Subjects**

| Subject | Example | Meaning |
|---|---|---|
| `project:<host/owner/repo>` | `project:github.com/memvara/memvara` | The repository. Already used by the index command. |
| `component:<host/owner/repo>:<directory>` | `component:github.com/memvara/memvara:memvara/store` | One part of the codebase, named by its directory relative to the repository root. |

The component name is taken from the path, so every clone and worktree produces the same
subject. `docs/SUBJECT-CONVENTIONS.md` section 7 wants component identity kept apart from
filesystem location and leaves open how components are named. Naming them by path is a
deliberate simplification: a moved directory becomes a new component, and section 5.5
says how the move is recorded. Subjects are limited to 128 characters. A component whose
subject would be longer is skipped and named in the report.

**Predicates**

| Predicate | Pack | Cardinality | Used for |
|---|---|---|---|
| `purpose` | engineering | one | What a component is for, in one sentence |
| `entry_point` | engineering | many | A file or command where the component starts |
| `depends_on` | engineering | many | Another component this one uses |
| `convention` | engineering | many | A rule contributors follow in this component |
| `decided` | decisions | many | A decision and its reason, from a commit |
| `rejected` | engineering | many | An alternative that was considered and dropped, and why |
| `known_defect` | engineering | many | A known problem stated in a commit or document |
| `belongs_to` | none | many (default) | Links a component to its project |
| `indexed_through` | none | many (default) | The last commit the code mode has read |

`belongs_to` and `indexed_through` are not declared in any pack, so the store treats them
as many-valued. `indexed_through` must therefore always be written with `replaces` naming
the previous value, or two values would stay live.

**Time and provenance**

- Every claim is written with `extractor: "memvara-index"`.
- A claim taken from a commit is written with `true_since` set to that commit's committer
  date, so it is dated when the decision landed, not when the index ran.
- The object of a git-derived claim ends with the short commit sha, for example
  `Recall injects at most 4 memories in 300 tokens; both were measured (3f2a1bc)`.
- Component purposes are the model's reading of the code, so they are written with
  `confidence: 0.8`. Decisions quoted from commit messages are written at `0.9`.

### 5.3 The structure map

A component is a source directory. That is a top-level source directory, a package that a
manifest declares as a workspace member (npm or pnpm workspaces, Cargo workspace members, Go
modules), or a subpackage of the repository's main Python package, as `memvara/store` is in
memvara. When a package is split into components, the files directly inside it form one more
component, named after the package directory. Directories that hold no product source, such
as tests, docs and CI configuration, are not components. At most 25 components are recorded.
When a repository has more, the command keeps the largest by tracked file count and reports
the rest by name.

For each component the command writes `belongs_to`, one `purpose`, and any `entry_point`,
`depends_on` and `convention` facts that the repository itself states. It reads READMEs,
manifests, entry points and a small sample of files. It does not read every file.

### 5.4 Decisions from git history

- **Range.** Only the default branch (`origin/HEAD`, for example `origin/main`) is read,
  because branches and worktrees share one project scope and only merged history is
  true for all of them. The first run reads at most the last 300 commits. Later runs read
  the commits after `indexed_through`. A repository with no remote uses its current
  branch, and the report says so.
- **Input.** For each commit: the message (subject and body) and the list of changed
  paths (`git show --name-only --format=`). Code and diffs are not read.
- **What to skip.** Commits without a body, dependency bumps, release commits, and
  reverts, which section 5.5 handles.
- **What to write.** A `decided` fact only when the message states both a choice and a
  reason. A `rejected` fact when it names an alternative and why it was dropped.
  `known_defect` and `convention` when the message states them. Anything else is skipped.
  Secrets, credentials and personal data are never written, as the command already
  requires.
- **Anchoring.** Only the changed paths that fall inside a component are counted, each in
  the longest component path that contains it. Tests, docs and the changelog fall outside
  every component, so they are not counted. The fact goes to the component that holds more
  than half of the counted paths. When no component does, it goes to the project. Phase 0
  found why the paths outside components must not count: most commits also change tests
  and docs, so counting every path left almost every decision on the project, where the
  brief shows only five of them.
- **Limit.** At most 40 decisions per run. The first run takes the newest qualifying
  commits first, so that the map describes the code as it is now. When more commits
  qualify, the report says how many were left. How a later run reaches the older ones is
  open question 7, because later runs read only the commits after `indexed_through`.
- **No duplicates.** Before writing, the command searches the stored facts for that
  subject and predicate, as step 3 of the index command already does.

### 5.5 Keeping it current

Git decides what has changed. Each run compares the stored map with the default branch.

| What git shows | What the command does |
|---|---|
| New commits after `indexed_through` | Reads them as in 5.4, then replaces `indexed_through` |
| A component's directory was moved | Writes the component's live facts under the new subject, each with `replaces` and a `reason` such as `moved from memvara/store to memvara/storage in 9c1e2d4` |
| A component's directory was deleted | Proposes to end the component's facts at the deleting commit's date |
| A commit reverts another (`This reverts commit <sha>`) | Proposes to end the decision that cites the reverted sha, at the revert's date |
| A component's purpose no longer fits | Writes the new purpose with `replaces` |

Proposals to end facts go through `memory_end`, and each call asks the user for
permission. That is deliberate: the index command's `allowed-tools` excludes
`memory_end`, and `test_it_may_write_without_a_prompt_per_fact_and_may_not_end_or_retire`
guards that. This design keeps the rule. `memory_end` with `subject` and `predicate` ends
every current value in one call, so ending a deleted component takes one call per
predicate.

The brief in Phase 2 shows how many commits old the map is, so a stale map can be seen.
When it is 20 or more commits behind, the brief suggests running `/memvara:index` again.

### 5.6 The brief at session start (Phase 2, core repository)

- **When.** At session start in a repository whose project has an `indexed_through`
  fact.
- **What.** One line per component (path and purpose), up to five conventions, and the
  five most recent decisions, within a budget of about 400 tokens. The budget will be
  measured the way `recall.py` measured its own before it ships.
- **How it reads.** One call that narrows on the server by predicate, such as
  `memory_profile` with buckets for `purpose`, `convention`, `decided` and `rejected`.
  Core invariant 7 says a filter and a limit must run in the same layer, so the hook must
  not fetch a limited list and then filter it on the client. With at most 25 components,
  a bucket limit of 50 is not reached. If a bucket comes back full, the brief says it was
  cut. Phase 2 must check that the rows come back newest first. If they do not, the
  recent decisions are taken from `memory_since` instead.
- **Where it writes.** It saves the component list to a session file under
  `~/.memvara/.hooks/`. Phase 3 reads that file, so it needs no network call to map a
  file to a component.

### 5.7 Notes when a file is touched (Phase 3, core repository)

- **Event.** A pre-tool hook on `Read`, `Edit`, `MultiEdit` and `Write`. Claude Code's
  `PreToolUse` can add context to the model since version 2.1.9. Each host record in
  `plugin/hooks/hosts/` must say whether its pre-tool event can carry context. Where it
  cannot, the hook is not registered for that host, and the README says so.
- **Lookup.** The file path maps to a component by the longest matching directory, using
  the session file from 5.6. The first touch of a component in a session makes one search
  anchored to that component's subject, which narrows on the server, for `decided`,
  `rejected`, `known_defect` and `convention`. At most 4 notes and 300 tokens are added,
  using the same limits and clipping as `recall.py`.
- **Once per component.** A component's notes are added at most once per session. Later
  touches of any file in it add nothing.
- **Latency.** One hosted call per component per session, measured at 177 ms warm and
  609 ms cold. The hook gives up after 1.5 seconds. It never blocks or denies the tool: it
  always exits 0 and never returns a permission decision.

### 5.8 Every step reports what happened

Hindsight's log on this machine shows what happens otherwise. Each part of this design has
separate outcomes for "did something", "had nothing to do" and "failed":

- **The index command** reports written, skipped with reason, waiting for the next run,
  proposed to end, and failed.
- **The brief** has three status lines: loaded (with counts and age), no map for this
  repository, and could not load (with the reason).
- **File-touch notes** count what they injected for the status line (`lib.counts`). In
  the hook log they record "no notes for this component" separately from "lookup failed".

### 5.9 Privacy and cost

- **What leaves the machine.** Component purposes, conventions, and decisions distilled
  from commit messages, with the paths they apply to. Source code and diffs do not leave
  it. The first code run in a repository says this and asks before writing.
- **Model cost.** The index command runs inside the user's session, so the session's
  model does the reading. The caps are 300 commits on the first run, only new commits
  after that, 25 components and 40 decisions per run.
- **Hook cost.** No model call happens at session start, per prompt or on file touch.
  There is one hosted read at session start and one per touched component.

## 6. Where each piece lives

| Piece | Repository | Files |
|---|---|---|
| Code mode of the index command | this repository | `plugin/commands/index.md`, `test/test_plugin.py`, `README.md`, `CHANGELOG.md` |
| Brief at session start | memvara/memvara | `plugin/hooks/session_start.py`, core tests, then synced here |
| File-touch notes | memvara/memvara | a new hook body under `plugin/hooks/`, `plugin/hooks/hosts/*.py`, manifests from `plugin/hooks/tools/generate.py`, core tests, then synced |
| Any new feature switch (Phase 4 only) | memvara/memvara | `memvara.server.config.FEATURE_DEFAULTS` and its copy in `plugin/hooks/lib/settings.py`. Tests require the two to match. |

`plugin/hooks/` and `plugin/skills/` are vendored in this repository and must not be
edited here. Only the command, its tests and the docs change here. `/memvara:index`
exists only in this repository, so no other plugin repository needs the Phase 1 change.

## 7. Delivery plan

Each phase gets its own implementation plan, written after this spec is approved. Each
pull request follows the repositories' rules: open the PR, run `/code-review high` on it,
fix what it finds, run the gate again, and keep AI attribution out of commits and PR
text. Each decision is recorded in Confluence under "Decisions" and in memvara.

### Phase 0: measure first

1. **Ownership decision, made 2026-10-15.** memvara-code's `why` explains symbols from
   commits and pull requests, so the two could overlap. The plugin stores decisions per
   component, as this spec proposes, because memvara-code is not ready. The question is
   reopened when memvara-code is ready. The step that overlaps is the git-history step in
   section 5.4.
2. **Question set.** Take 10 real questions from past sessions in each of memvara and
   memvara-cloud. Before any run, write down for each question the files that answer it
   and the decision it depends on, if any.
3. **Four ways to run each question.**
   - A: memory off.
   - B: memvara as it is today.
   - C: hand-written notes (a component map and the relevant decisions), shown at session
     start and on file touch.
   - D: added later, the automated version after Phases 1 to 3.
4. **Measures.** Tool calls and tokens until the first expected file is opened, total
   tokens, and whether the answer is right, judged against the written expectation
   without knowing which setup produced it. Each question runs twice in each setup.
5. **Cost.** About 120 headless runs for A, B and C. Each run carries 30k to 34k tokens
   of fixed preamble, as measured in this repository's CLAUDE.md.
6. **Gate.** Build Phase 1 if C, compared with A, cuts tool calls to the first right file
   by at least 30%, or gets at least 2 more of 10 decision-dependent questions right, and
   costs no more than 20% more total tokens. If hand-written notes do not help, automated
   notes will not help either, and we stop here.

   The decision bar is a share: 2 more right for every 10 decision questions. Only 4 real
   "why" questions passed the selection rule (3 about memvara and 1 about memvara-cloud),
   so with them the bar is 0.8 more right. Decided on 2026-10-15 when the question set was
   approved.

### Phase 1: code mode in the index command (this repository)

1. Detection step, as in 5.1, with the no-coding-project outcome in the report.
2. A confirmation before the first code run in a repository, as shown in section 4.
3. The structure map, as in 5.3.
4. Decisions from git history, as in 5.4, including `true_since` and the sha in the
   object.
5. Incremental runs and git-based upkeep, as in 5.5, with `indexed_through` always
   written through `replaces`.
6. The report, as in 5.8.
7. `allowed-tools` gains only read-only git commands (`git show`, `git ls-files`,
   `git rev-parse`). `memory_end` stays out, so every end is asked for.
8. Tests in `IndexCommand`:
   - the detection step runs before any write;
   - git history is read from the default branch;
   - `true_since` is set from the commit date;
   - `indexed_through` is written with `replaces`;
   - the caps are stated;
   - the confirmation comes before the first write;
   - `memory_end` is still not auto-approved.

   `test_every_write_is_semantic_and_says_what_derived_it` becomes a narrower rule,
   because `decided` is episodic in the decisions pack. The test is changed to say which
   predicates are episodic and why. It is not deleted. Each new guard is broken once on
   purpose to show that it can fail.
9. An end-to-end check, run by hand rather than in CI: run `/memvara:index` headless on a
   small fixture repository with a local store. Check the subjects, predicates, dates and
   shas that were written.
10. README section: what code mode stores, what leaves the machine, and how to turn the
    command off. Add a CHANGELOG entry.
11. Run Phase 0's harness again with the real Phase 1 output shown to the agent by hand
    (arm D, partial).

### Phase 2: the brief at session start (core repository, then synced)

1. The brief in `session_start.py`, as in 5.6, with the three status lines.
2. The session file of components for Phase 3.
3. Measure the budget on real repositories before fixing it, as `recall.py` did.
4. Core tests for the three states, the budget, the "cut" notice, and that no
   client-side filtering follows a server-side limit.
5. Sync the change to this repository, regenerate the hooks manifest, and release.

### Phase 3: notes when a file is touched (core repository, then synced)

1. A new pre-tool hook body and host records, as in 5.7.
2. Core tests: longest-prefix mapping, once-per-component, the 1.5-second give-up, the
   tool is never blocked, output byte for byte per host, and hosts that cannot carry
   context are not registered.
3. Scenario tests: server down, a session file missing or corrupt, two sessions at once.
   Every failure must end as an ordinary tool call with no notes and a log line.
4. Run Phase 0's harness in full with arm D, and compare it with A and C.

### Phase 4 (optional): run code mode automatically on a new repository

This happens only if Phases 0 to 3 show a gain. At session start in a coding repository
with no code map, the hook would start a background headless run of the code mode, with a
cheap model and a spending cap, as Hindsight's survey does. It needs a new feature switch
that is off by default, in both core locations listed in section 6.

## 8. Risks

| Risk | What reduces it |
|---|---|
| Decisions crowd preferences out of the 4-slot per-prompt recall | Caps per run. Compare recall blocks on real prompts before and after Phase 1. If crowding shows, keep episodic facts out of the per-prompt recall and show decisions only in the brief and file-touch notes (open question 5). |
| The map goes stale between runs | The brief shows its age in commits and suggests a re-run at 20 commits. |
| A model-written purpose is wrong | Marked with `extractor` and a lower confidence. The next run replaces it. |
| Cost of an index run | Incremental runs after the first, and the caps in 5.9. |
| Duplicating memvara-code | The plugin owns git-derived decisions until memvara-code is ready (decided 2026-10-15). Only the git-history step in 5.4 overlaps, and that is the step to revisit then. |
| Branches and worktrees disagree | Only the default branch is read. |
| A host cannot carry context before a tool call | That host is not registered, and the README says so. |
| The feature fails silently | The three outcomes in 5.8 are tested for every step. |

## 9. Open questions

1. **Component naming.** Is a component named by its path acceptable for version 1? The
   subject conventions in section 7 leave component naming unsettled, so this may need a
   core decision.
2. **Decisions pack on the hosted server.** Is the `decisions` pack loaded there? If not,
   `decided` is stored with the default behaviour: many-valued and slow to decay. It still
   works, but ranks differently.
3. **Citing the commit.** Is a sha in the object text enough provenance? The core has no
   way to cite a commit as a source.
4. **Pull request descriptions.** They hold the best reasons. Should version 1 read them
   with `gh`, or should that wait?
5. **Per-prompt recall.** Should decisions stay out of the 4-slot per-prompt recall and
   appear only through the brief and file-touch notes?
6. **Phase 0 thresholds.** Are 30%, 2 of 10 and 20% the right bars?
7. **Older decisions after the first run.** The first run keeps the newest 40 decisions,
   and later runs read only the commits after `indexed_through`, so the older qualifying
   commits are never read. Either the command keeps a second marker for how far back it
   has read, or older history is out of scope.

## 10. Checks made before writing this

Following this repository's CLAUDE.md, "Before proposing new machinery":

1. **Signatures.**
   - `memory_remember` has no metadata field. It has `true_since`, `replaces`,
     `confidence` and `extractor`.
   - `memory_recall`'s `filepath_prefix` matches only memories that came from documents.
   - `memory_profile` narrows by predicate buckets.
   - `memory_end` can end every value of a subject and predicate in one call.
2. **Test names.**
   - `IndexCommand`: the switch is checked first, search comes before writes, every
     write is semantic, and the command may not end or retire facts without a prompt.
   - The markdown tool-count scan in `ToolCount`.
   - The measured `K`, `BUDGET` and clip in `recall.py`.
3. **Roadmap.**
   - Entity summaries are deferred because nothing ends a derived claim when the claims
     under it end.
   - memvara/code's non-goals and its rule that every document attaches to an entity or a
     dated event.
4. **Invariants in `docs/INTERNALS.md`.**
   - 2: unknown predicates are many-valued, hence `replaces`.
   - 4: every claim carries provenance, hence `extractor` and the sha.
   - 7: a filter and a limit run in one layer, hence server-side narrowing.
   - 8: `true_since` is the world clock only.

## 11. References

- Hindsight coding-agent integration: https://hindsight.vectorize.io/sdks/integrations/coding-agents
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- Building Claude Code with Boris Cherny (The Pragmatic Engineer):
  https://newsletter.pragmaticengineer.com/p/building-claude-code-with-boris-cherny
- memvara/memvara: `docs/SUBJECT-CONVENTIONS.md` (sections 6 and 7), `docs/INTERNALS.md`
  (design invariants), `docs/ROADMAP.md` (related work: entity summaries),
  `memvara/packs/engineering.toml`, `memvara/packs/decisions.toml`
- memvara/code: `docs/DESIGN-SPEC.md` (non-goals), `docs/PRODUCTIZATION-PLAN.md`
- This repository: `plugin/commands/index.md`, `plugin/hooks/recall.py`,
  `test/test_plugin.py` (`IndexCommand`, `ToolCount`)
