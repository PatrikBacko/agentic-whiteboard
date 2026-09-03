# Agentic Whiteboard fallback instructions for Claude Code

Prefer installing this repository as a Claude Code plugin because its hooks provide session and prompt IDs automatically. If plugin loading is unavailable, copy the workflow from `skills/whiteboard/SKILL.md` into the target project's `CLAUDE.md` and use a stable manual session ID.

Run after each meaningful turn:

```bash
python3 /absolute/path/to/agentic-whiteboard/scripts/whiteboard.py prompt \
  --root "$PWD" --session manual-claude --prompt-id '<turn-id>' \
  --summary '<short non-secret prompt summary>'
python3 /absolute/path/to/agentic-whiteboard/scripts/whiteboard.py prepare --root "$PWD" --session manual-claude
# update .whiteboard/sessions/manual-claude/current/index.html
python3 /absolute/path/to/agentic-whiteboard/scripts/whiteboard.py finalize \
  --root "$PWD" --session manual-claude --prompt-id '<turn-id>' \
  --title '<title>' --summary '<summary>'
```

A matching pending prompt must be created with the actual `prompt` CLI before `prepare`; plugin mode does this automatically.
