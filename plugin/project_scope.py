"""The git project for a directory, as the MCP header and as a memory subject.

Two uses, one answer. Both work the project out with the vendored hooks' `lib/project.py`,
so the MCP server, the hooks and `/memvara:index` all agree on one name for a repository:
`github.com/memvara/memvara` for a repository with an `origin` remote, `path:` and 16 hex
characters for one without, and nothing outside a git repository.

``python3 project_scope.py headers <directory>``
    Prints the JSON object Claude Code's `headersHelper` expects, for example
    `{"Memvara-Project": "github.com/memvara/memvara"}`. `.mcp.json` runs this when Claude
    Code connects to the memvara server, passing the project directory, so every MCP tool
    call carries the same project the hooks send. It prints `{}` when there is no project,
    when the `project_scope` switch is off, and on any failure, and it always exits 0. An
    empty object means the connection goes ahead without the header, which is how every
    call was made before the header existed. The header never replaces the
    `Authorization` header Claude Code's own OAuth adds, so nobody has to log in again.

``python3 project_scope.py subject [<directory>]``
    Prints the memory subject for the repository, `project:github.com/memvara/memvara`,
    for `/memvara:index`. The directory defaults to the current one. Exits 1 with a
    sentence on stderr outside a git repository. It ignores the `project_scope` switch:
    the subject names the repository a fact is about, which does not change when the
    scope of MCP calls is switched off.
"""

from __future__ import annotations

import json
import os
import sys

#: The header name the hooks send in lower case. HTTP header names are case-insensitive;
#: this spelling is the one the design documents use.
HEADER = "Memvara-Project"

#: The prefix `docs/SUBJECT-CONVENTIONS.md` in memvara/memvara settles for a repository.
SUBJECT_PREFIX = "project:"


def _project_module():
    from memvara_hooks import hooks_lib

    return hooks_lib("project")[0]


def headers(directory: str) -> dict:
    """The headers for an MCP connection made from `directory`. Never raises."""
    try:
        if not directory or not os.path.isdir(directory):
            return {}
        project = _project_module()
        value = project.resolve(directory)
        # `resolve` can answer from its cache file, which is outside this process's
        # control. A value the server would refuse must not reach the header, because a
        # refused header costs every MCP call in the session.
        if value and project.is_canonical(value):
            return {HEADER: value}
    except Exception:  # noqa: BLE001 -- a helper that fails must not block the connection
        return {}
    return {}


def subject(directory: str) -> "str | None":
    """`project:<name>` for the repository `directory` is in, or `None` outside one."""
    value = _project_module().canonical_project(os.path.abspath(directory))
    return f"{SUBJECT_PREFIX}{value}" if value else None


def main(argv: "list[str]") -> int:
    command = argv[0] if argv else ""
    if command == "headers":
        print(json.dumps(headers(argv[1] if len(argv) > 1 else "")))
        return 0
    if command == "subject":
        directory = argv[1] if len(argv) > 1 else os.getcwd()
        found = subject(directory)
        if found is None:
            print(f"{directory} is not inside a git repository, so it has no project "
                  "subject. Run this from inside the repository you want to index.",
                  file=sys.stderr)
            return 1
        print(found)
        return 0
    print("usage: project_scope.py headers <directory> | subject [<directory>]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
