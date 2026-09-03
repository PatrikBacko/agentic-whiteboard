# Agentic Whiteboard Plugin MVP Implementation Plan

> **For Hermes:** Implement with strict test-first development and independent review before commit.

**Goal:** Extend existing Claude Code and Codex conversations with a versioned interactive visual companion, without creating another chat application or agent harness.

**Architecture:** Claude Code loads this repository as a plugin. A `UserPromptSubmit` hook establishes session-specific whiteboard state and injects workflow instructions; a `Stop` hook reminds the agent once if the revision remains pending. Standard-library lifecycle scripts maintain `.whiteboard/sessions/<id>/current`, immutable archived versions, and history. The agent writes self-contained HTML directly or compiles generated React through a fixed local bundler. Codex uses the same protocol through `AGENTS.md` instructions.

**Tech Stack:** Claude Code plugin manifest, Claude lifecycle hooks, Markdown skill/instructions, Python 3 standard library, Node.js, esbuild, React.

---

## Product boundary

This repository is:

- an installable Claude Code plugin;
- a reusable visual-output protocol;
- a versioned filesystem convention;
- deterministic lifecycle tooling;
- a static artifact viewer;
- a portable Codex instruction template.

It is not:

- a new chat frontend;
- an LLM provider integration;
- a conversation database;
- an autonomous agent wrapper;
- a replacement for Claude Code or Codex;
- an infinite-canvas editing suite.

## Canonical turn flow

```text
User prompt
   ↓
UserPromptSubmit hook
   ├── selects Claude session ID
   ├── initializes .whiteboard session if absent
   ├── writes pending prompt metadata
   └── injects exact workflow commands
   ↓
Claude/Codex performs normal requested work
   ↓
prepare
   └── copies current finalized revision to versions/<revision>/
   ↓
agent updates current/index.html
   ├── plain self-contained HTML/CSS/JS; or
   └── App.jsx → fixed React bundler → index.html
   ↓
finalize
   ├── validates size, remote assets, IDs, and 15-element cap
   ├── increments manifest revision
   ├── appends history
   └── clears pending marker
   ↓
normal final text response
```

## MVP acceptance criteria

- [x] Claude Code recognizes the repository as a valid plugin.
- [x] A prompt hook creates isolated state using Claude's session and prompt IDs.
- [x] Hook context tells Claude exactly where and how to update the board.
- [x] A Stop hook blocks once when a turn remains pending.
- [x] Stop-hook continuation cannot loop indefinitely.
- [x] Previous finalized versions are archived before edits.
- [x] `prepare` is idempotent for one revision.
- [x] Finalization preserves immutable history and clears pending state.
- [x] Artifacts enforce at most 15 stable top-level element IDs.
- [x] Remote HTML assets are rejected so artifacts remain self-contained.
- [x] Generated React can be bundled into one static HTML file.
- [x] Arbitrary package imports are rejected.
- [x] Current and archived artifacts can be served locally.
- [x] Codex receives a portable `AGENTS.md` workflow.
- [x] Tests run without an LLM key or provider integration.

## Implemented files

```text
.claude-plugin/plugin.json
hooks/hooks.json
skills/whiteboard/SKILL.md
commands/whiteboard-status.md
scripts/__init__.py
scripts/whiteboard.py
scripts/whiteboard_hook.py
scripts/build-react.mjs
integrations/codex/AGENTS.md
integrations/claude/CLAUDE.md
tests/test_whiteboard.py
tests/test_react_builder.py
tests/test_plugin_contract.py
package.json
package-lock.json
README.md
```

## Verification

Run:

```bash
npm test
claude plugin validate .
npm audit
```

Manual smoke test:

1. Create a temporary target repository.
2. Send a synthetic `UserPromptSubmit` hook payload.
3. Verify session and pending files.
4. Run `prepare`.
5. Generate `App.jsx` containing several marked top-level concepts.
6. Build it to `index.html`.
7. Run `finalize`.
8. Send a synthetic `Stop` payload and verify it allows stopping.
9. Start the viewer and inspect the actual rendered artifact in a browser.
10. Repeat a second turn and verify revision 1 remains archived while current becomes revision 2.

## Deferred ideas

- automatic browser/IDE panel displaying the active board;
- two-way interaction state between artifact and agent context;
- a visual revision-history navigator;
- richer but still fixed React dependency packs;
- Codex lifecycle integration if a stable native hook surface becomes available;
- explicit user-authored/locked regions inside generated artifacts;
- semantic diffs between manifest revisions.
