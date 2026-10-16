---
description: Record 10 to 30 facts about the current repository in memvara and, in a coding project, its architecture, what each main folder and each file is for, and the decisions in its recent commits, without repeating facts already stored.
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

- **The same fact is already stored:** do not write it, even if you would word it differently
  today. Rewording a value that is still right ends a true fact and adds churn.
- **A stored fact now has a different value** (a new version, a changed test command):
  write the new value with `replaces` set to the stored fact's claim id, and `reason`
  saying what changed, for example `moved from Jest to Vitest`. That ends the old fact
  and records that this one replaced it. Do not call `memory_end` or `memory_forget`
  first: ending the old fact separately loses the link between the two.
- **A new fact:** write it.

If memvara refuses a write because of its rate limit, retry it after a short wait, and do not
move on to the next fact until it is stored. After five refusals in a row, stop the run and
say in the report which facts were not stored; a later run picks them up, because it looks
each one up before writing.

**6. In a coding project, record its architecture and what each folder and file is for.**
Skip steps 6 and 7, and say so in the report, unless the repository has a manifest from
step 4 and source files that git tracks (`git ls-files`).

Before anything else in this step, find the `indexed_through` marker, as the start of step 7
describes, because it decides whether this step reads every folder and file or only the ones
that changed.

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
each component, call `memory_search` with its subject as the query and `k` of 50.
Keep only the rows whose subject is exactly that component's, because a search for one
folder also finds folders with similar names. Compare with those rows as in step 5 before writing these,
with the same `memory_type` and `extractor` as in step 5:

- `purpose`: what the folder is for, in one sentence, from its README, docstrings and entry
  points;
- `belongs_to`: the subject from step 2.

On a later run, when step 7 finds an `indexed_through` fact, re-read only the components whose
files changed since that commit, because a purpose can change only when its folder does:

```bash
git log --name-only --format= <last>..origin/HEAD
```

A component with no changed files keeps its stored facts. A new folder is a new component.

A stored component whose folder no longer exists is not ended here. Name it in the report,
and suggest ending its facts with `memory_end`, which asks the user before it runs.

**The architecture.** Write between 3 and 8 `architecture` facts about the step 2 subject,
each one sentence on how the main parts fit together: where requests or commands come in,
which components they pass through, where data is kept, and which outside services are
called. Base them on the components above and their entry points. First call
`memory_search` with the query `architecture` followed by the step 2 subject, keep the rows
with exactly that subject and predicate, and compare as in step 5, so that a sentence that
changed replaces the old one by id.

**Every file.** Write one `purpose` fact for every file that git tracks,
except lock files, generated files and binary files such as images, fonts and archives. Its subject is `file:`, then the step 2 subject without
`project:`, then `:` and the file's path, for example
`file:github.com/memvara/memvara:memvara/core.py`. The object says in one sentence what the
file is for, from its first lines, docstring or exports; read only as much of the file as
that needs. Skip a file whose subject would be longer than 128 characters, and name it in
the report. Before writing, call `memory_search` with the file's subject as the query, keep
only the rows whose subject is exactly that file's, and compare as in step 5.
Do this search for every file on every run, even when an earlier search found no file facts:
a run that stopped part way, or another run at the same time, leaves some files stored.

If you hand the files to subagents to go faster, give each subagent its own folders, so that
no file goes to two of them, and give each one these rules in full: the subject format, one
sentence per file, the files to skip, and the search before every write.

On a later run, re-read only the files changed since `<last>`, from the same `git log`
output as the components, and any new file. Name the files that were deleted in the report,
and suggest ending their facts with `memory_end`. Do the full pass anyway when a search for
`file:` followed by the step 2 subject without `project:` finds no file facts, because then
the repository was indexed before files were recorded. Finish the files before step 7: its
`indexed_through` marker is what tells a later run that every file was read.

**7. Record the decisions in the commit history.** Decisions are read from the default
branch, `origin/HEAD`, because every clone and worktree of the repository shares these facts,
and only merged history is true for all of them. If `origin/HEAD` does not exist, use `HEAD`
in its place in the commands below, which reads the current branch, and say so in the report.

First find where the last run stopped. Call `memory_search` with the query `indexed_through`
followed by the step 2 subject, and keep the row whose subject is the step 2 subject and whose
predicate is `indexed_through`. Its object is the last commit an earlier run read; call it
`<last>`.

Then list the commits after it, oldest first. A run reads at most the newest 100 commits
after `<last>`, so a busy repository is not read back to its beginning:

```bash
git log --reverse -100 --no-merges --format=%h <last>..origin/HEAD
```

On the first run there is no `<last>`, so leave out `<last>..`: the run then reads the
newest 100 commits of the whole history. If git cannot find `<last>`, because the history
was rewritten, do the same: the search by sha below keeps a decision from being written
twice.

Read the listed commits 10 at a time, oldest first, with their messages and the files they
changed, naming the commits of each group:

```bash
git log --no-walk --name-only --format='--%n%h %cs %s%n%b' <sha> <sha> ...
```

Do not shorten this output with `head` or any other command, because a commit left unread
is a decision lost. If a group's output comes back cut off, read the same commits again in
smaller groups. Read every listed commit before you report.

Keep a commit only when its message states a choice and the reason for it. Skip commits
with no body, dependency bumps, releases and reverts.
Write every qualifying decision. There is no limit.
For each kept commit, write one fact with `memory_remember`, with the same `memory_type`
and `extractor` as in step 5:

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

Before writing a decision, call `memory_search`
with the commit's short sha as the query. If a stored `decided` fact already ends with that
sha, do not write it again. The search in step 3 is not enough for this, because it returns
at most 50 rows.
Do this search on every run, the first one included: a run that stopped before writing
`indexed_through` leaves its decisions stored without the marker that says so.

**Keep your place.** After every 5 groups (50 commits), and once more after the last group,
record the newest commit read so far: the last listed commit you have read. Get its full sha
with `git log -1 --format=%H <sha>`, and write it as the object of an `indexed_through` fact
about the step 2 subject, with the same `memory_type` and `extractor` as in step 5. When an
earlier one exists, found above or written earlier in this run, write it with
`replaces` set to the previous `indexed_through` fact's claim id
and `reason` `indexed up to a newer commit`, so that only one stays live. A later run then
reads only the commits after it, and a run that stops early, at its budget or for any other
reason, has not lost its place.

**8. Report.** Tell the user the subject, how many facts were written, how many were already
stored, and which ones replaced an older value. List the facts written, one per line. In a
coding project, also give the number of components, `architecture` facts and files
written, how many commits were read and how many decisions were written, and name the
components and files that were skipped and the stored ones that no longer exist.

Store only what the repository states. Do not record secrets, credentials, personal data or
guesses; if something is unclear, leave it out.
