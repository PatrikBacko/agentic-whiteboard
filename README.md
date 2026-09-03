# Agentic Whiteboard

> A versioned visual companion plugin for Claude Code conversations, with portable instructions for Codex.

Agentic Whiteboard does **not** replace Claude Code, Codex, their CLI, IDE extension, or conversation storage. It teaches the coding agent to maintain an additional visual artifact after each meaningful turn.

```text
normal coding-agent conversation
          │
          ├── code, tools, tests, final text answer
          │
          └── versioned visual companion
              .whiteboard/sessions/<conversation-id>/
```

The companion is a self-contained static web app. The agent can use plain HTML/CSS/JavaScript or generate a React component and bundle it into one offline `index.html`. It can visualize architecture, progress, decisions, alternatives, evidence, blockers, and next steps in whatever interactive form fits the work.

## How it works

For Claude Code, the plugin combines a skill with two lifecycle hooks:

1. `UserPromptSubmit` creates a pending visual revision and injects the exact session-specific paths and commands.
2. Claude performs the user's normal task.
3. Claude reads the existing whiteboard and archives its previous revision.
4. Claude updates the static web artifact, preserving useful visual continuity.
5. Claude finalizes the revision before answering.
6. `Stop` reminds Claude once if it forgot, without creating an infinite loop.

Claude Code remains the agent and conversation interface. The plugin is only instructions, hooks, version-management helpers, a React bundler, and a tiny local viewer.

## Result inside a target repository

```text
.whiteboard/
├── active.json
└── sessions/
    └── <claude-session-id>/
        ├── current/
        │   ├── index.html
        │   ├── manifest.json
        │   └── App.jsx             # optional React source
        ├── versions/
        │   ├── 000001/
        │   └── 000002/
        ├── history.jsonl
        └── pending.json            # only while a turn is unfinished
```

Each finalized revision records its source prompt ID, title, summary, update time, content digest, and stable visual element IDs. Archived revisions are digest-verified, tamper-evident, and made read-only as defense in depth; they are not cryptographically immutable against the workspace owner.

## Install and use with Claude Code

### VS Code extension or Claude Code desktop app

The repository is also a Claude plugin marketplace, so no `--plugin-dir` launch flag is needed:

1. **VS Code:** type `/plugins` in the Claude Code prompt, then open **Marketplaces** and add `PatrikBacko/agentic-whiteboard`.
2. **Claude Code desktop:** in a local or SSH project session, click the **+** beside the prompt, choose **Plugins → Add plugin**, and install **Agentic Whiteboard** from the configured `patrikbacko-plugins` marketplace.
3. In VS Code, open the **Plugins** tab and install **Agentic Whiteboard** from `patrikbacko-plugins`.
4. Restart/reload Claude Code when prompted.
5. Open a trusted local project and confirm `/agentic-whiteboard:whiteboard-status` appears in `/help`.

The desktop plugin browser displays plugins from marketplaces already configured for Claude Code. If the custom marketplace does not yet appear there, add it once through the VS Code `/plugins` marketplace screen, then reopen the desktop app.

The desktop integration is intended for a local **Claude Code** project session. Regular Claude Chat and remote/cloud sessions do not provide the local project filesystem used by `.whiteboard/`.

### Local development installation

Requirements:

- Python 3.10+
- Node.js 20+ only when generated React artifacts are desired
- Claude Code with plugin support

Clone and install the fixed React bundler dependencies:

```bash
git clone https://github.com/PatrikBacko/agentic-whiteboard.git
cd agentic-whiteboard
npm install
```

Validate the plugin:

```bash
claude plugin validate .
```

Start Claude Code inside any target project:

```bash
cd /path/to/your-project
claude --plugin-dir /absolute/path/to/agentic-whiteboard
```

Then talk to Claude normally. No API keys, provider integration, database, or separate chat application are required by this plugin.

The plugin hooks work wherever Claude Code supports its standard lifecycle hooks, including the CLI and IDE-hosted Claude Code sessions. The target workspace must be trusted because hooks execute local commands.

## View the whiteboard

Read the active session:

```bash
python3 -c 'import json; print(json.load(open(".whiteboard/active.json"))["session_id"])'
```

Serve the latest artifact:

```bash
python3 /absolute/path/to/agentic-whiteboard/scripts/whiteboard.py serve \
  --root "$PWD" --session '<session-id>' --open
```

Serve an archived revision:

```bash
python3 /absolute/path/to/agentic-whiteboard/scripts/whiteboard.py serve \
  --root "$PWD" --session '<session-id>' --revision 1 --open
```

Claude also exposes the plugin command `/agentic-whiteboard:whiteboard-status`.

## React artifacts

The agent may directly generate `current/index.html`, which is usually sufficient. When a richer React interface is useful, it writes `current/App.jsx` and runs:

```bash
node /absolute/path/to/agentic-whiteboard/scripts/build-react.mjs \
  --source .whiteboard/sessions/<session-id>/current/App.jsx \
  --output .whiteboard/sessions/<session-id>/current/index.html \
  --title 'Current work'
```

The bundler:

- permits React and its fixed runtime only;
- rejects arbitrary package imports;
- resolves its exact pinned React/esbuild runtime from the plugin, independent of the target working directory;
- requires regular, non-symlink source/output paths in one canonical artifact directory;
- embeds the application and React runtime into one HTML file;
- adds an offline content-security policy with `connect-src 'none'`;
- rejects source larger than 200 KB or output larger than 1 MB.

Static HTML checks reject referenced files plus common remote URL and networking forms, but are necessarily best-effort rather than a JavaScript sandbox. The local viewer applies CSP as runtime enforcement and serves only `index.html`; directly opening the file bypasses viewer response headers and is appropriate only for trusted generated content.

## Whiteboard rules

- Maximum **15 meaningful top-level elements**.
- Every top-level element uses a unique stable ID and `data-whiteboard-element`.
- Internal DOM complexity is unrestricted.
- Preserve useful elements and approximate spatial layout between revisions.
- Update, merge, or remove stale information instead of appending forever.
- Do not copy the conversation into decorative cards.
- No remote scripts, styles, images, fonts, analytics, or API calls.
- The user's actual task always has priority over the visualization.

## Codex integration

Codex reads `AGENTS.md` instructions but does not use the Claude plugin hooks. Copy or merge:

```text
integrations/codex/AGENTS.md
```

into the target repository and configure:

```bash
export AGENTIC_WHITEBOARD_PLUGIN=/absolute/path/to/agentic-whiteboard
export WHITEBOARD_SESSION_ID=codex-default
```

The instructions give Codex explicit `prompt → prepare → edit/build → finalize` commands. Use a distinct `WHITEBOARD_SESSION_ID` for each conversation.

A fallback `CLAUDE.md` snippet is also available at `integrations/claude/CLAUDE.md` when plugin loading is unavailable.

## Development and verification

```bash
npm install
npm test
claude plugin validate .
```

The test suite exercises:

- session isolation and safe IDs;
- prompt markers without storing raw prompt text;
- archive-before-edit with digest verification and read-only snapshots;
- idempotent preparation;
- 15-element validation and stable unique IDs;
- rejection of remote artifact dependencies;
- Claude prompt and Stop hook behavior;
- hook execution from outside the plugin directory;
- React bundling and package-import rejection;
- selection of archived revisions for viewing;
- plugin manifest and integration contracts.

## Current MVP limitations

- Codex integration is instruction-driven because it has no equivalent plugin hook in this MVP.
- The viewer serves one selected session/revision; it is not a visual history browser yet.
- The plugin reminds Claude once rather than blocking forever if finalization repeatedly fails.
- Interactive state inside a generated page is not synchronized back into the conversation automatically.
- The React allowlist intentionally contains no charting or UI libraries.
- `.whiteboard/` is local working state by default; add it to the target project's ignore rules if it should not be committed.
- A hostile workspace owner can replace files and locks, and filesystem power-loss durability varies; protection against owner-level mutation and fully durable multi-file transactions are post-MVP limits.
- Static offline checks are best-effort. Use the CSP-enforcing local viewer for untrusted rendering; direct file opening assumes the generated artifact is trusted.

## Repository layout

```text
.claude-plugin/plugin.json        Claude Code plugin manifest
hooks/hooks.json                  prompt and Stop lifecycle hooks
skills/whiteboard/SKILL.md        agent behavior and visual protocol
commands/whiteboard-status.md     inspection command
scripts/whiteboard_hook.py        Claude hook adapter
scripts/whiteboard.py             lifecycle, validation, history, viewer
scripts/build-react.mjs           fixed offline React bundler
integrations/codex/AGENTS.md      portable Codex instructions
integrations/claude/CLAUDE.md     non-plugin Claude fallback
tests/                            standard-library integration tests
```

## License

MIT
