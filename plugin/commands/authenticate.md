---
description: Get a memvara credential for this machine, or report the one it already has.
argument-hint: "[project-id]"
---

Run this and show the user everything it prints:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/auth/memvara_auth.py" authenticate $ARGUMENTS
```

Give the Bash call a timeout of 600000ms. It waits for a person to approve the login in a
browser, and a shorter timeout kills the command while they are still reading the page.

It asks the deployment about this machine's credential before it does anything. If that
credential already works it says so and stops. **That is the successful outcome**, not a
failure and not something to retry with different arguments: minting a second key leaves
the first one live on the deployment with nothing here pointing at it.

If it does start a login it prints a short code and a URL. Both are meant for the person at
the keyboard, so pass them on exactly as printed and do not summarise them.

Exit codes: 0 is a working credential, 2 means the project id is not the dashed UUID form
the console shows, and anything else is a failure the output names.
