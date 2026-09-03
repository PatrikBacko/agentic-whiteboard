# Agentic Whiteboard

> Chat gives AI a voice. Agentic Whiteboard gives it a visual workspace.

Agentic Whiteboard is an experimental conversational interface where an AI can answer with normal text **and** create persistent, interactive web artifacts on a shared board.

Instead of explaining every result as another long message, the AI can generate the representation that fits the task:

- a decision matrix with adjustable priorities;
- an architecture explorer;
- a timeline or implementation plan;
- an interactive simulation;
- an evidence map;
- a custom dashboard or calculator;
- or a small interface nobody designed in advance.

The user can interact with the artifact, refer to it in later messages, and ask the AI to revise it. The browser becomes the AI's second communication medium.

## The product idea

```text
Traditional AI chat
User ↔ chronological text stream

Agentic Whiteboard
User ↔ chat + persistent interactive artifacts
```

The board is capped at **15 top-level elements**. This forces the AI to curate, replace, group, and summarize instead of filling an infinite canvas with noise. A top-level element may be a simple note—or an entire generated React mini-app.

## MVP promise

A user describes a complex problem. The AI responds in text and generates an interactive React artifact beside the conversation. The user manipulates it, continues the conversation with that artifact as context, and asks the AI to revise it without losing the previous version.

### Hero demo

1. Ask: “Compare three architectures for this product and let me change how much I value cost, speed, and isolation.”
2. The AI explains its recommendation in chat.
3. It creates a React comparison app with sliders, scores, trade-offs, and expandable evidence.
4. The user changes the weights and selects an option.
5. Ask: “Turn the selected option into a four-week implementation plan.”
6. The AI creates a second interactive artifact while preserving the first.

## MVP scope

### Included

- one local single-user workspace;
- multiple persistent conversations;
- text chat with a configurable AI provider;
- AI-generated React artifacts;
- artifact cards on a draggable/resizable board;
- maximum 15 top-level board elements;
- sandboxed iframe execution;
- expand and open-artifact views;
- artifact IDs included in conversation context;
- version history for generated source;
- retry, revise, restore, delete, and undo;
- local SQLite persistence;
- a visible activity/error state while an artifact is generated or compiled.

### Explicitly excluded

- multiplayer collaboration;
- arbitrary npm package installation;
- generated artifact network access;
- autonomous privileged actions;
- mobile-grade canvas editing;
- polished infinite-canvas diagram tooling;
- production authentication, billing, or cloud deployment;
- agent-generated backend services.

## Proposed architecture

```text
┌─────────────────────────────────────────────────────────────┐
│ Trusted host application                                    │
│                                                             │
│  Chat ──► AI turn ──► text response + artifact proposal     │
│                                  │                          │
│                                  ▼                          │
│                         React source compiler               │
│                                  │                          │
│                                  ▼                          │
│  SQLite ◄── versions/state ── sandboxed iframe on board     │
└─────────────────────────────────────────────────────────────┘
```

Generated React is treated as an expressive but untrusted artifact:

- it receives only explicit serializable inputs;
- it runs with scripts enabled but without same-origin privileges;
- it cannot read host cookies, storage, tokens, or DOM;
- it has no network access in the MVP;
- it can emit a small set of typed events through `postMessage`;
- the trusted host validates and persists supported state changes;
- a broken artifact can be stopped and restored independently.

## Initial technical direction

- TypeScript
- Next.js + React
- SQLite + Drizzle ORM
- a server-side React/TSX compilation step using a fixed dependency allowlist
- sandboxed iframe artifact runtime
- Zod schemas for model output and host/artifact messages
- Vitest for domain and protocol tests
- Playwright for the complete generation → render → interact → revise flow

The exact AI model/provider should remain configurable. The MVP should depend on structured output and tool calling, not one vendor-specific model.

## Product principles

1. **Visual output must add structure, not decorate text.**
2. **Generated React is first-class.** The agent may invent task-specific interaction rather than only compose predefined widgets.
3. **Generated code is untrusted.** Freedom of expression does not imply access to credentials or privileged APIs.
4. **The user owns the board.** User edits, locks, deletion, and version restoration are authoritative.
5. **Artifacts persist beyond one message.** They are addressable objects, not disposable previews.
6. **Keep the board small.** Fifteen meaningful objects are better than an unlimited visual transcript.
7. **Failure is local.** A broken generated app must not crash the conversation or other artifacts.
8. **Every artifact has a text fallback.** Important conclusions remain understandable without executing generated code.

## Success criteria for the prototype

The MVP is successful if a tester can complete the hero demo and:

- the generated app renders without modifying host state directly;
- interaction state survives conversation turns;
- the AI can correctly refer to the selected artifact and its current state;
- revision creates a new restorable version;
- malformed or looping artifact code fails locally and recoverably;
- the board enforces its 15-element limit;
- the result feels clearer than receiving the same information as prose alone.

## Plan

The implementation plan is in:

[`/.hermes/plans/2026-09-03_073952-agentic-whiteboard-mvp.md`](.hermes/plans/2026-09-03_073952-agentic-whiteboard-mvp.md)

## Status

Concept and MVP planning only. No application code has been implemented yet.
