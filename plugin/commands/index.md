---
description: Record 10 to 30 facts about the current repository in memvara, without repeating facts already stored.
allowed-tools: Bash(python3:*), Bash(git log:*), Bash(git remote:*), Read, Glob, Grep, mcp__plugin_memvara_memvara__memory_search, mcp__plugin_memvara_memvara__memory_remember
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

**6. Report.** Tell the user the subject, how many facts were written, how many were already
stored, and which ones replaced an older value. List the facts written, one per line.

Store only what the repository states. Do not record secrets, credentials, personal data or
guesses; if something is unclear, leave it out.
