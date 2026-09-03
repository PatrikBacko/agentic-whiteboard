# Agentic Whiteboard instructions for Codex

Maintain a persistent, versioned **visual companion** to this conversation after every meaningful turn. This is additional output, not a replacement for normal coding work or the final text response.

## Configuration

`AGENTIC_WHITEBOARD_PLUGIN` must point to the cloned Agentic Whiteboard repository. Set `WHITEBOARD_SESSION_ID` to a stable identifier for this Codex thread; if unavailable, use `codex-default` and do not mix unrelated conversations in it.

```bash
export AGENTIC_WHITEBOARD_PLUGIN=/absolute/path/to/agentic-whiteboard
export WHITEBOARD_SESSION_ID=codex-default
```

Initialize once:

```bash
python3 "$AGENTIC_WHITEBOARD_PLUGIN/scripts/whiteboard.py" init \
  --root "$PWD" --session "$WHITEBOARD_SESSION_ID"
```

## Required workflow

After completing the user's core task and before each final response:

1. Read `.whiteboard/sessions/$WHITEBOARD_SESSION_ID/current/manifest.json` and `index.html`.
2. Create the pending turn with an ID unique to this turn:

```bash
python3 "$AGENTIC_WHITEBOARD_PLUGIN/scripts/whiteboard.py" prompt \
  --root "$PWD" --session "$WHITEBOARD_SESSION_ID" \
  --prompt-id "<unique-turn-id>" \
  --summary "<short non-secret prompt summary>"
```

3. Archive the current revision:

```bash
python3 "$AGENTIC_WHITEBOARD_PLUGIN/scripts/whiteboard.py" prepare \
  --root "$PWD" --session "$WHITEBOARD_SESSION_ID"
```

4. Update `current/index.html` as a self-contained static web app that visually communicates the current state, relationships, progress, decisions, evidence, blockers, alternatives, or next steps.
5. Mark no more than **15** top-level concepts with unique stable IDs and `data-whiteboard-element` attributes.
6. Do not use remote scripts, styles, images, fonts, fetches, analytics, or a backend.
7. Finalize using the same prompt ID:

```bash
python3 "$AGENTIC_WHITEBOARD_PLUGIN/scripts/whiteboard.py" finalize \
  --root "$PWD" --session "$WHITEBOARD_SESSION_ID" \
  --prompt-id "<unique-turn-id>" \
  --title "<short title>" \
  --summary "<what this revision visualizes>"
```

The order for Codex is therefore: prompt → prepare → edit → finalize.

## React option

The artifact may be plain HTML/CSS/JavaScript or an interactive React app. For React, write `current/App.jsx` with a default export and build it with:

```bash
node "$AGENTIC_WHITEBOARD_PLUGIN/scripts/build-react.mjs" \
  --source ".whiteboard/sessions/$WHITEBOARD_SESSION_ID/current/App.jsx" \
  --output ".whiteboard/sessions/$WHITEBOARD_SESSION_ID/current/index.html" \
  --title "<title>"
```

Only React imports are permitted. Never install packages requested by generated artifact code.

## Preservation rules

- Preserve useful elements and approximate positions between versions.
- Update rather than regenerate everything when possible.
- Never edit archived `versions/`.
- Do not turn the board into a transcript.
- If no visual change is valuable, preserve the current app and finalize an honest unchanged revision.
- Briefly mention the revision in the final answer.
