---
description: List memvara's features and switch one on or off, check the model key for query rewrite, or remove memvara's status line.
argument-hint: "[<feature> on|off | verify-key | remove-status-line]"
---

Run this and show the user everything it prints, exactly as printed:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/setup.py" $ARGUMENTS
```

With no arguments it lists every feature with its current value, its default, one sentence
on what it does and, for the newer features, what it costs. Every feature is on by default
except `extraction_chunks`. The list ends with the features that arrive in the next
release, which cannot be set yet, and says whether memvara's status line is installed.

`/memvara:setup <feature> on|off` writes the switch to `~/.memvara/settings.json`. A name
that is not a feature is refused, and the output lists the names that are. Exit code 0 means
the change was made, 2 means the arguments were refused, and 1 means a settings file could
not be read, in which case nothing was changed.

`/memvara:setup verify-key` is how query rewrite reaches the recall hook. Without `--yes`
it prints what a rewrite adds to every prompt, in time and in model calls, and where to
look up the model's price, and it changes nothing. Show that to the user and ask whether
they want it. Only when they say yes, run `/memvara:setup verify-key --yes`: it makes one
test call to the configured model through the memvara library and records the result, and
the recall hook starts rewriting each prompt's query only if the model answered. Exit code
0 means the model answered; 1 means it did not, and the output says why and what to fix.
`/memvara:setup query_rewrite on` also prints the cost and changes nothing when turning the
switch on would start rewrites over a key that was already checked; the user confirms with
`/memvara:setup query_rewrite on --yes`. Never add `--yes` yourself. The user types it, or
tells you to, after reading the cost.

`/memvara:setup remove-status-line` takes memvara's status line out of
`~/.claude/settings.json` and switches `status_line` off so it is not added back. A status
line that belongs to another tool is never changed.

Do not edit either settings file yourself; this command is the one place that writes them.
