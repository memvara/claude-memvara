# Code project memory for `/memvara:index`: design

Status: the simple version described here is built, in `plugin/commands/index.md`. First
written 2026-09-30. Cut down on 2026-10-15, when a larger design with new hooks and a
measurement phase was dropped in favour of this one.

## 1. Summary

When `/memvara:index` runs in a coding project, it records what a coding agent cannot learn
quickly by reading the code: what each main folder is for, and the decisions behind the code,
taken from recent commit messages. Nothing new reads these facts back. The hook that runs on
every prompt already shows Claude the memories that best match the user's message, so a
question about one part of the code can bring that part's facts in first.

It does not store a summary of every file. Section 2 explains why.

## 2. Why not a summary of every file

The first idea was to summarize each file, update the summary when the file changes, and have
the agent read memory before it reads code. It was dropped for these reasons.

1. A file summary mostly repeats the file, and the agent can read the file itself with one
   tool call. Summaries are weakest for the largest files, and memvara's `core.py` is about
   80k tokens.
2. Files change in ways no hook sees: `git pull`, rebases, branch switches and the user's own
   editor. Worktrees of one repository share one project scope, so two branches would
   overwrite each other's summary of the same file.
3. The recall on each prompt shows at most 4 memories in about 300 tokens, so file summaries
   would push the user's preferences out.
4. A model would have to read every file: about 2.8M tokens for memvara and 3.4M for
   memvara-cloud.
5. A description of every file in a private codebase would be sent to the hosted store.

Hindsight's coding-agent integration reached the same conclusion: it stores decisions from
git history and a survey of the repository's structure, not per-file summaries.

## 3. What the code mode does

The steps are in `plugin/commands/index.md`, and the README describes them for users.

- **When.** The repository has a manifest (`pyproject.toml`, `package.json` and so on) and
  source files that git tracks. Otherwise the command does what it did before, and its report
  says so.
- **Components.** A component is a main source folder: a top-level source folder, a package
  that a manifest declares as a workspace member, or a subpackage of the main package, as
  `memvara/store` is in memvara. The files directly inside a split package form one more
  component, named after the package's folder. Tests, docs and CI folders are not
  components. At most 25 are recorded, keeping the ones with the most tracked files.
- **Facts per component.** The subject is `component:<host>/<owner>/<repo>:<folder>`, built
  from the repository's own subject so that every clone and worktree spells it the same. Each
  component gets a one-sentence `purpose` and a `belongs_to` fact naming the repository.
- **Decisions.** The first run reads every commit on the default branch that is not a
  merge, oldest first and 10 at a time, with the files each one changed. Every 50 commits it
  records the newest commit it has read as an `indexed_through` fact, so later runs read only
  the commits after it, and a run that stops early keeps its place. The first version read
  pages of 100, newest first, and recorded the marker only at the end. Its first run on
  memvara-cloud shortened a page itself, read about 16 of 579 commits, and stopped without
  the marker, so every later run would have stopped at the same place. Only merged history is read, because every clone
  and worktree shares these facts. A decision is found again by its sha, so a second run does
  not write it twice. A commit whose message states a choice and its reason becomes a `decided`
  fact. The fact's `true_since` is the commit's date, and its text ends with the short sha.
  It is filed under the component that holds more than half of the commit's changed paths,
  counting only paths inside components, or under the repository when no component does.
  Every such decision is written. The first version capped a run at 40 and read only the last
  100 commits; the user removed both limits on 2026-10-15.
- **Keeping it current.** A second run compares with what is stored, as the command already
  did: a fact already stored is not written again, and a changed purpose is written with
  `replaces`. It re-reads only the folders whose files changed since `indexed_through`,
  and it looks up every decision by its sha before writing it, on every run, because a run
  that stopped early leaves decisions stored without the marker. A folder that was deleted or moved keeps its old facts. The report names it and
  suggests `memory_end`, which asks the user before it runs, because the command's
  `allowed-tools` still excludes `memory_end` and `memory_forget`.
- **Privacy.** The command reads code and commit messages inside the user's session. Only the
  short facts are sent to memvara, never source code or diffs.

## 4. Decisions

- **The plugin owns the decisions taken from git history, for now** (2026-10-15).
  memvara-code's `why` explains symbols from commits too, but memvara-code is not ready. The
  question is reopened when it is. The Confluence page is "2026-10-15 The claude-memvara
  plugin, not memvara-code, owns the decisions taken from git history for now".
- **Only paths inside a component decide where a decision goes** (2026-10-15). Writing the
  notes for the measurement by hand showed why. Counting every changed path left 38 of 40
  memvara decisions on the repository, because most commits also change tests, docs and the
  changelog. Counting only paths inside components put 26 of 40 on a component.
- **The simple version first** (2026-10-15). The larger design added a brief at session
  start, notes injected when the agent touches a file, both built as new hooks in the core
  repository, and a measurement phase before any of it. The user's call was that it had to be
  much simpler. The code mode is one prompt change in this repository and relies on the hooks
  that already exist.

## 5. What was measured before the change of plan

A measurement harness asked real questions through `claude -p` in three setups: memvara off
(A), memvara as installed (B), and memvara off with hand-written notes shown at session start
and on file touch (C). A pilot of six runs on Sonnet, one question about each repository,
gave these numbers.

| Run | Tool calls | First right file at call | Cost |
|---|---|---|---|
| A, memvara question | 4 | 1 | $0.30 |
| B, memvara question | 2 | 1 | $0.30 |
| C, memvara question | 4 | 1 | $0.17 |
| A, memvara-cloud question | 2 | never | $0.22 |
| B, memvara-cloud question | 5 | 5 | $0.25 |
| C, memvara-cloud question | 2 | never | $0.16 |

With notes, both questions cost less: 43% and 27% less than without memory. Six runs are a
hint, not a result. The full run of 120 was not made. The harness, its tests and its plan
were removed from the repository and remain in git history at commit `cfb0db8`, in
`bench/code_memory.py` and `docs/superpowers/plans/2026-10-15-code-project-memory-phase-0.md`.

## 6. Left out, and when to add each

Each of these waits for real use to show that the facts are not reaching Claude.

- **A brief at session start** listing the components and recent decisions. Add it if
  questions about a part of the code do not bring that part's facts into the recall.
- **Notes when the agent touches a file**, through a pre-tool hook. Claude Code's
  `PreToolUse` can add context since version 2.1.9. Add it if the per-prompt recall is too
  crowded for code facts.
- **Running the code mode automatically** on a repository that has never been indexed.
- **Pull request descriptions** as a source of decisions, read with `gh`.

## 7. Open questions

1. **Component naming.** Naming a component by its folder is a simplification:
   `docs/SUBJECT-CONVENTIONS.md` section 7 in the core repository wants component identity
   kept apart from filesystem location, and leaves the naming open. A moved folder becomes a
   new component.
2. **The `decisions` pack on the hosted server.** If it is not loaded, `decided` is stored as
   an unregistered predicate: it keeps many values and decays slowly. That suits decisions.
3. **Citing the commit.** The short sha in the fact's text is the only link to the commit.
   The core has no way to cite a commit as a source.

## 8. References

- Hindsight coding-agent integration: https://hindsight.vectorize.io/sdks/integrations/coding-agents
- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- memvara/memvara: `docs/SUBJECT-CONVENTIONS.md`, `docs/INTERNALS.md`, `docs/ROADMAP.md`
- This repository: `plugin/commands/index.md`, `plugin/hooks/recall.py`, `test/test_plugin.py`
  (`IndexCommand`)
