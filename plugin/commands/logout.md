---
description: Delete the memvara credential stored on this machine.
disable-model-invocation: true
---

Run this and show the user everything it prints:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/auth/memvara_auth.py" logout
```

It deletes `~/.memvara/credentials.json` and nothing else. Every other place a key may
still be — an exported `MEMVARA_API_KEY`, an `Authorization` header in an MCP
configuration — is named in the output and left exactly as it was.

If the output names a file that holds a memvara `Authorization` header, that header is
still live. Tell the user which file it is, show them the block, and ask whether to remove
it. Edit the file only if they say yes: this host's own OAuth client writes that file too,
and an edit nobody asked for leaves neither of you able to say whose token is in there.

Do not offer to revoke the key. Revocation happens in the console at
https://app.memvara.dev and there is nothing to run here for it.
