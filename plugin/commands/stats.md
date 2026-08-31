---
description: Report what the memvara store holds at this credential's scope.
---

Run this and show the user everything it prints:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/auth/memvara_auth.py" stats
```

It asks about the credential before it asks about the store, so a credential that has
expired or been revoked says so rather than reporting a store with nothing in it. Exit code
0 is a reading; anything else is a credential or a deployment, and the output says which.

This works when the memvara MCP server is not authenticated, which is when it is worth
running. When the MCP server is connected, `memory_stats` answers the same question without
a subprocess.
