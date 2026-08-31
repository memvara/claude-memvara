---
description: Replace this machine's memvara credential with a freshly minted one.
argument-hint: "[project-id]"
---

Run this and show the user everything it prints:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/auth/memvara_auth.py" login $ARGUMENTS
```

Give the Bash call a timeout of 600000ms, for the same reason `/memvara:authenticate` does.

**Exit code 3 means a working credential is already on this machine and nothing was
replaced.** The output above it describes that credential. Show it to the user, ask whether
they want to replace it, and re-run with `--confirm` appended only if they say yes. A minted
key is returned exactly once, so replacing one destroys the only copy of it.

Exit code 0 means a new key is on disk. Anything else is a failure the output names.
