---
description: Show the active Agentic Whiteboard session, current revision, and viewer command.
allowed-tools: Bash(python3 *)
---

Read `.whiteboard/active.json` in the current project. Run the plugin lifecycle script's `status` command for that session and report the current revision, pending state, artifact path, archived version count, and this viewer command:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/whiteboard.py" serve --root "$PWD" --session '<session-id>' --open
```

Do not update or finalize the board as part of this status command.
