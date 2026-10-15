---
description: Record 10 to 30 facts about the current repository in memvara and, in a coding project, what each main folder is for and the decisions in its recent commits, without repeating facts already stored.
allowed-tools: Bash(python3:*), Bash(git log:*), Bash(git ls-files:*), Bash(git remote:*), Read, Glob, Grep, mcp__plugin_memvara_memvara__memory_search, mcp__plugin_memvara_memvara__memory_remember
---

Explore this repository and store what a new contributor would need to know about it as
memvara facts. Follow these steps in order.

**1. Check the switch.** Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/setup.py" check index_command
```

If it exits with anything other than 0, show the user what it printed and stop.

**2. Work out the subject.** Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/project_scope.py" subject
```

It prints the repository's subject, for example `project:github.com/memvara/memvara`. The
name comes from the git remote, so every clone and worktree of one repository gets the same
subject. Use that exact string as the subject of every fact below. If it exits with 1, the
directory is not a git repository: show the user the message and stop.

**3. Read what is already stored.** Call `memory_search` with the subject and the
repository's name as the query, and `k` of 50. Keep the rows whose subject is the one from
step 2, with their claim ids. You will compare against them in step 5 so that a second run
does not write the same facts again.

**4. Read the repository.** Look at, in this order and only as far as you need:

- the README and any `docs/` overview;
- the manifests: `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`,
  `build.gradle`, `Gemfile`, `requirements*.txt`, and whatever else the stack uses;
- CI files (`.github/workflows/`, `.gitlab-ci.yml`) and `Dockerfile` / compose files;
- the entry points the manifests name;
- `git log --oneline -30`, for what the project has been working on.

**5. Write the facts.** Record between 10 and 30 facts with `memory_remember`, one fact per
call, each with:

- `subject`: the string from step 2;
- `predicate`: one from the list below;
- `object`: the value, as short as it can be (`pytest`, not `the project uses pytest`);
- `memory_type`: `"semantic"`;
- `extractor`: `"memvara-index"`, so memvara records that this command derived the fact
  rather than that the user stated it.

Predicates to use:

| Predicate | For |
|---|---|
| `purpose` | What the repository is for, in one sentence |
| `depends_on` | A key dependency, one per fact |
| `version` | The version the project is at |
| `deploys_to` | Where it is deployed |
| `endpoint` | A URL it serves or publishes to |
| `owner` | The team or person who owns it |
| `entry_point` | A file or command where execution starts |
| `runs_with` | A command to build, test or run it: `make test`, `npm run dev` |
| `convention` | A rule contributors follow: formatting, branch naming, test layout |
| `known_defect` | A known problem the README or issues state |

`depends_on`, `version`, `deploys_to`, `endpoint`, `owner` and `known_defect` come from the
engineering predicate pack. Prefer them over new names, because an exact predicate is what
lets memvara tell a changed value from a new one.

Compare each fact with the rows from step 3 before writing it:

- **The same fact is already stored:** do not write it.
- **A stored fact now has a different value** (a new version, a changed test command):
  write the new value with `replaces` set to the stored fact's claim id, and `reason`
  saying what changed, for example `moved from Jest to Vitest`. That ends the old fact
  and records that this one replaced it. Do not call `memory_end` or `memory_forget`
  first: ending the old fact separately loses the link between the two.
- **A new fact:** write it.

**6. In a coding project, record what each main folder is for.** Skip steps 6 and 7, and
say so in the report, unless the repository has a manifest from step 4 and source files that
git tracks (`git ls-files`).

A component is one of the main source folders: a top-level source folder, a package that a
manifest declares as a workspace member, or a subpackage of the main package, as
`memvara/store` is in memvara. When a package is split into components, the files directly
inside it form one more component, named after the package's folder. Folders that hold no
product source, such as tests, docs and CI configuration, are not components.
Record at most 25 components. If there are more, keep the ones with the most tracked files,
and name the rest in the report.

A component's subject is `component:`, then the step 2 subject without its `project:`
prefix, then `:` and the folder's path from the repository root. For example,
`component:github.com/memvara/memvara:memvara/store`. Skip a component whose subject would be
longer than 128 characters, and name it in the report.

First call `memory_search` with the query `component:` followed by the step 2 subject
without `project:`, and `k` of 50, to see which components are already stored. Then, for
each component, call `memory_search` with its subject as the query and compare its facts as
in step 5 before writing these, with the same `memory_type` and `extractor` as in step 5:

- `purpose`: what the folder is for, in one sentence, from its README, docstrings and entry
  points;
- `belongs_to`: the subject from step 2.

A stored component whose folder no longer exists is not ended here. Name it in the report,
and suggest ending its facts with `memory_end`, which asks the user before it runs.

**7. Record the decisions in recent commits.** Run:

```bash
git log -100 --no-merges --name-only --format='--%n%h %cs %s%n%b'
```

Keep a commit only when its message states a choice and the reason for it. Skip commits
with no body, dependency bumps, releases and reverts. For each kept commit, write one fact
with `memory_remember`, with the same `memory_type` and `extractor` as in step 5:

- `predicate`: `decided`;
- `object`: the choice and its reason in one sentence, ending with the short sha in
  brackets, for example `Recall shows at most 4 memories, because more crowded out the
  user's preferences (3f2a1bc)`;
- `true_since`: the commit's date, so that the decision is dated when it was made, not when
  this command ran;
- `subject`: the component that holds more than half of the commit's changed paths,
  counting only the changed paths inside a component. Tests, docs and the changelog do not
  count, and a path belongs to the longest component folder that contains it. When no
  component holds more than half, use the subject from step 2.

Write at most 40 decisions in one run, newest first, and say in the report how many were
left out. A decision already stored for that subject is not written again.

**8. Report.** Tell the user the subject, how many facts were written, how many were already
stored, and which ones replaced an older value. List the facts written, one per line. In a
coding project, also give the number of components and decisions written, and name the
components that were skipped and the stored ones whose folder no longer exists.

Store only what the repository states. Do not record secrets, credentials, personal data or
guesses; if something is unclear, leave it out.
