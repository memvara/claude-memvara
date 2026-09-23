---
description: List memvara's features and switch one on or off, or remove memvara's status line.
argument-hint: "[<feature> on|off | remove-status-line]"
---

Run this and show the user everything it prints, exactly as printed:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/setup.py" $ARGUMENTS
```

With no arguments it lists every feature with its current value and its default, which is
on for all of them, and says whether memvara's status line is installed.

`/memvara:setup <feature> on|off` writes the switch to `~/.memvara/settings.json`. A name
that is not a feature is refused, and the output lists the names that are. Exit code 0 means
the change was made, 2 means the arguments were refused, and 1 means a settings file could
not be read, in which case nothing was changed.

`/memvara:setup remove-status-line` takes memvara's status line out of
`~/.claude/settings.json` and switches `status_line` off so it is not added back. A status
line that belongs to another tool is never changed.

Do not edit either settings file yourself; this command is the one place that writes them.
