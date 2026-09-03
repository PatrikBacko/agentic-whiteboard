# Agentic Whiteboard MVP Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a local single-user conversational workspace where an AI produces text responses and persistent interactive React artifacts that run safely beside the chat.

**Architecture:** A trusted Next.js host owns conversations, artifact metadata, source versions, board placement, and durable interaction state in SQLite. AI-generated TSX is compiled against a fixed runtime and rendered in a sandboxed iframe; generated code communicates with the host only through a typed `postMessage` bridge. The first vertical slice proves generation, interaction, persistence, revision, recovery, and the 15-element board limit.

**Tech Stack:** TypeScript, Next.js, React, SQLite, Drizzle ORM, Zod, server-side TSX bundling, sandboxed iframe runtime, Vitest, Testing Library, Playwright.

---

## 1. MVP outcome

The MVP must support this complete scenario:

1. A user creates a conversation and asks for an interactive architecture comparison.
2. The AI returns a normal text explanation plus a structured React-artifact proposal.
3. The host validates and compiles the artifact.
4. The artifact appears as a draggable and resizable card beside the chat.
5. The user changes sliders and selects an architecture inside the generated app.
6. The artifact reports its serializable state to the host.
7. The user asks a follow-up referring to the selected artifact.
8. The AI receives the artifact ID, current state, and compact source summary.
9. The AI creates a revised artifact version or a related planning artifact.
10. The user can restore the previous version, undo board changes, or delete an artifact.

A prerecorded or hardcoded artifact does not satisfy this outcome. At least one artifact must be generated from an actual model response, compiled, rendered, and exercised in a browser test.

## 2. Product boundaries

### In scope

- local, single-user application;
- persistent conversations and messages;
- text plus generated React artifact output;
- one board per conversation;
- up to 15 top-level elements;
- artifact card positioning and resizing;
- artifact source and state versioning;
- sandboxed execution and host bridge;
- revision, restoration, deletion, and turn-level undo;
- provider-configurable structured model call;
- clear compile/runtime failures with a recoverable fallback.

### Out of scope

- arbitrary package installation;
- generated network calls;
- access to host APIs except the typed bridge;
- generated server code;
- collaborative editing;
- production authentication or public hosting;
- general-purpose Figma/Miro feature parity;
- multiple nested boards;
- automatic migration of state between incompatible artifact revisions beyond a simple validated state object.

## 3. Proposed repository shape

```text
agentic-whiteboard/
├── .env.example
├── .github/workflows/ci.yml
├── .hermes/plans/
├── drizzle/
├── e2e/
│   ├── conversation-artifact.spec.ts
│   └── artifact-failure.spec.ts
├── public/
├── src/
│   ├── app/
│   │   ├── api/
│   │   │   ├── conversations/[id]/turns/route.ts
│   │   │   ├── artifacts/[id]/state/route.ts
│   │   │   └── artifacts/[id]/versions/route.ts
│   │   ├── conversations/[id]/page.tsx
│   │   ├── layout.tsx
│   │   └── page.tsx
│   ├── components/
│   │   ├── board/
│   │   ├── chat/
│   │   └── artifacts/
│   ├── db/
│   │   ├── client.ts
│   │   ├── schema.ts
│   │   └── migrations.ts
│   ├── domain/
│   │   ├── conversations.ts
│   │   ├── board.ts
│   │   ├── artifacts.ts
│   │   └── versions.ts
│   ├── agent/
│   │   ├── provider.ts
│   │   ├── prompt.ts
│   │   └── output-schema.ts
│   ├── artifact-runtime/
│   │   ├── compiler.ts
│   │   ├── dependency-allowlist.ts
│   │   ├── host-protocol.ts
│   │   ├── iframe-document.ts
│   │   └── runtime-client.ts
│   └── test/
├── package.json
├── playwright.config.ts
└── vitest.config.ts
```

Paths may change slightly during the initial framework scaffold, but domain boundaries should remain explicit.

## 4. Minimal domain model

### Conversation

- `id`
- `title`
- `createdAt`
- `updatedAt`

### Message

- `id`
- `conversationId`
- `role`: `user | assistant`
- `content`
- `createdAt`
- optional `turnId`

### BoardElement

- `id`
- `conversationId`
- `kind`: initially only `react_artifact | note`
- `title`
- integer `x`, `y`, `width`, `height`
- `zIndex`
- `locked`
- `createdByTurnId`
- `deletedAt`
- optimistic-concurrency `revision`

### Artifact

- `id`
- `boardElementId`
- `activeVersionId`
- `runtimeStatus`: `proposed | compiling | ready | compile_error | runtime_error`
- validated JSON `state`
- integer `stateRevision`

### ArtifactVersion

- `id`
- `artifactId`
- `versionNumber`
- TSX source
- compiled bundle or deterministic bundle cache key
- model/source metadata
- compile diagnostics
- `createdAt`

### Turn

- `id`
- `conversationId`
- model/provider metadata
- lifecycle status
- compact record of board mutations for undo

## 5. Agent output contract

The model should not return free-form source mixed into prose. Validate a structured response shaped approximately as:

```ts
type AgentTurnOutput = {
  message: string;
  operations: Array<
    | {
        type: "create_react_artifact";
        clientRequestId: string;
        title: string;
        description: string;
        source: string;
        initialState: unknown;
      }
    | {
        type: "revise_react_artifact";
        artifactId: string;
        source: string;
        nextState: unknown;
      }
    | {
        type: "create_note";
        title: string;
        body: string;
      }
    | {
        type: "delete_element";
        elementId: string;
      }
  >;
};
```

The host validates IDs, permissions, object limits, source size, and operation count before persisting anything. Invalid visual operations must not discard the valid text response.

## 6. Artifact runtime contract

Generated TSX receives a deliberately small API:

```ts
type ArtifactProps<State> = {
  initialState: State;
  emitState(nextState: State): void;
  emitEvent(event: {
    type: string;
    payload?: unknown;
  }): void;
};
```

The iframe runtime sends messages to the host:

```ts
type ArtifactToHostMessage =
  | { type: "artifact_ready"; artifactId: string; runtimeToken: string }
  | { type: "state_changed"; artifactId: string; runtimeToken: string; state: unknown }
  | { type: "artifact_event"; artifactId: string; runtimeToken: string; event: unknown }
  | { type: "runtime_error"; artifactId: string; runtimeToken: string; message: string };
```

The host must validate message origin/source, artifact ID, runtime token, schema, payload size, and state revision before applying a message.

## 7. Implementation tasks

### Task 0: Validate the generated-artifact runtime

**Objective:** Prove the riskiest technical assumption before scaffolding the full product.

**Create:** A disposable spike outside production source that compiles one TSX component, renders it in a sandboxed iframe, receives a state-change message, blocks direct parent DOM access, and recovers from a runtime exception.

**Verification:**

- Run the spike in Chromium through Playwright.
- Assert a React counter renders and sends state to the parent.
- Assert access to `window.parent.document` fails.
- Assert a deliberately broken component shows a host-owned error boundary/fallback.
- Record exact findings and the selected compiler strategy in `docs/architecture/runtime-spike.md`.
- Delete disposable spike code before Task 1; preserve only the conclusions and tests that define desired behavior.

**Commit:** `docs: validate sandboxed React artifact runtime`

### Task 1: Scaffold the trusted host

**Objective:** Create a minimal responsive Next.js application with quality gates.

**Files:** Initialize the app, TypeScript strict mode, linting, formatting, Vitest, Testing Library, and Playwright. Add a two-pane desktop shell and stacked mobile shell with placeholder chat and board.

**TDD/verification:**

1. Write component tests for chat/board landmark accessibility and responsive state.
2. Observe RED before implementing the shell.
3. Implement the minimum shell.
4. Run unit tests, typecheck, lint, formatting, build, and desktop/mobile Playwright smoke tests.

**Commit:** `feat: scaffold agentic whiteboard host`

### Task 2: Add SQLite persistence

**Objective:** Persist conversations, messages, turns, board elements, artifacts, and artifact versions with ownership and revision constraints.

**Files:** Create `src/db/*`, initial Drizzle migration, temporary-file integration tests, and service-layer modules under `src/domain/`.

**TDD/verification:**

- Write failing tests for creating a conversation, appending ordered messages, enforcing 15 active elements, ownership checks, optimistic revision conflicts, artifact version restoration, and transactional turn undo.
- Test real file-backed SQLite with foreign keys and integrity checks.
- Implement only through domain services; route handlers must not issue ad hoc database mutations.

**Commit:** `feat: add persistent conversation and artifact model`

### Task 3: Implement the board

**Objective:** Render persistent note and artifact cards and support move, resize, lock, delete, selection, and undo.

**TDD/verification:**

- Test the pure integer geometry/reducer layer first.
- Test the 15-element boundary and locked-element behavior.
- Use browser tests for pointer movement, resizing, selection context, and reload persistence.
- Keep the board deliberately basic; do not build connectors, nested frames, zoom, or full diagram tooling.

**Commit:** `feat: add bounded persistent artifact board`

### Task 4: Build and secure the React artifact runtime

**Objective:** Compile generated TSX against fixed dependencies and run it in an isolated iframe.

**TDD/verification:**

- Write failing tests for allowed imports, rejected imports, source-size limits, compilation diagnostics, iframe sandbox attributes, CSP, and bundle creation.
- Port the validated spike design into `src/artifact-runtime/`.
- Do not install dependencies requested by generated source.
- Permit React and a deliberately tiny reviewed allowlist only.
- Verify generated code cannot access the trusted host DOM or credentials and cannot make network requests under the MVP CSP.
- Verify one broken/looping artifact cannot destroy chat or sibling artifact state; define a practical recovery mechanism for hangs.

**Commit:** `feat: run generated React artifacts in isolation`

### Task 5: Add the typed artifact state bridge

**Objective:** Persist user interaction state from generated apps and restore it after reload.

**TDD/verification:**

- Test forged artifact IDs, stale revisions, oversized payloads, malformed messages, wrong window source, and duplicate messages before the happy path.
- Debounce state writes without losing the final state.
- Browser-test counter/slider state across reload.
- Display the current serialized state in a developer inspection panel for the MVP.

**Commit:** `feat: persist generated artifact interaction state`

### Task 6: Integrate a configurable AI turn

**Objective:** Send conversation and selected-artifact context to an AI provider and validate text plus artifact operations.

**TDD/verification:**

- Define the Zod output schema first.
- Test valid text-only output, artifact creation, revision, unknown IDs, excess operations, malformed source, and partial failure.
- Implement a provider interface plus one real configured provider.
- Persist a turn atomically where possible; a compile failure must retain the assistant message and artifact diagnostics.
- Redact secrets from logs and stored model metadata.

**Commit:** `feat: generate conversational React artifacts`

### Task 7: Add artifact lifecycle and revision UX

**Objective:** Let users inspect, revise, restore, delete, expand, and retry artifacts.

**TDD/verification:**

- Test version numbering and active-version switching in domain services.
- Test that restoration does not destroy later versions.
- Test delete and turn undo while respecting locked elements.
- Add source/diagnostic inspection as an advanced panel, not the primary interface.
- Add an expanded host page that still embeds the artifact in the same sandbox rather than granting it a trusted top-level origin.

**Commit:** `feat: add artifact version and recovery controls`

### Task 8: Prove the hero flow end to end

**Objective:** Exercise the real differentiating workflow with an actual model-generated artifact.

**Verification:**

- Create the architecture-comparison prompt fixture and deterministic mock-provider equivalent for CI.
- In local/manual verification, use a real configured provider to generate the artifact.
- Use Playwright to modify weights, select an option, reload, ask the follow-up, generate/revise the plan artifact, restore a version, and undo a board mutation.
- Capture screenshots for the README.
- Verify desktop usability and a readable mobile fallback; advanced board manipulation may remain desktop-only.

**Commit:** `test: prove conversational artifact workflow`

### Task 9: Quality and release candidate

**Objective:** Produce a reproducible local MVP suitable for user testing.

**Verification:**

- Fresh-install test from the documented Node version.
- Migration from an empty data directory.
- Full unit, integration, and browser suite.
- Typecheck, lint, formatting, production build, dependency audit, and secret scan.
- Independent security review of iframe policy, bridge validation, compilation inputs, and generated-code boundaries.
- Independent product review against the hero demo and explicit exclusions.
- Add local setup, provider configuration, backup, reset, and troubleshooting instructions.

**Commit:** `docs: prepare agentic whiteboard MVP`

## 8. Testing strategy

### Unit tests

- board reducer and geometry;
- 15-element enforcement;
- artifact operation validation;
- source/import policy;
- message protocol schemas;
- state revision and version-selection logic;
- prompt/context construction.

### Integration tests

- SQLite services and transaction rollback;
- ownership and optimistic concurrency;
- compile pipeline with representative generated TSX;
- turn persistence when compilation succeeds or fails;
- artifact state persistence and restoration.

### Browser tests

- create conversation and submit prompt;
- render generated artifact in sandbox;
- interact and persist state;
- select artifact and issue contextual follow-up;
- revise and restore versions;
- enforce board limit;
- survive malformed and runtime-failing artifacts;
- verify desktop and mobile presentation.

### Security checks

- no same-origin privilege in generated iframe;
- restrictive CSP and no generated network access;
- no arbitrary package resolution;
- source, bundle, state, and message size limits;
- strict host-side schema and ownership validation;
- no trust based only on message-declared artifact ID;
- secrets absent from client bundles, logs, and stored prompts;
- visible distinction between trusted host controls and generated content.

## 9. Key risks and decisions

### Generated code hangs

A React error boundary cannot catch infinite loops. The spike must determine whether iframe replacement, compilation instrumentation, worker-assisted execution, or another watchdog gives acceptable recovery. Do not claim strong resource isolation from iframe sandboxing alone.

### State compatibility across revisions

The MVP preserves the prior version and attempts to pass the current JSON state to the new version. If validation fails, the user chooses between the previous state and the new version's default. Automatic schema migration is out of scope.

### Model reliability

Structured output may still contain uncompilable source. Compilation errors are expected product states with retry/revise controls, not exceptional server failures.

### Security versus openness

The MVP allows arbitrary component logic but only fixed dependencies, no network, and no direct host access. Broader capabilities should later use explicit grants rather than weakening the default sandbox.

### Board complexity

Do not build an infinite-canvas platform before proving that generated interactive artifacts improve comprehension. Basic positioning, resizing, selection, and persistence are enough.

## 10. MVP acceptance checklist

- [ ] A real AI call generates at least one non-hardcoded React artifact.
- [ ] The artifact is compiled and rendered in a sandboxed iframe.
- [ ] User interaction state survives reload and a follow-up turn.
- [ ] The AI receives selected artifact context and state.
- [ ] Artifact source has restorable version history.
- [ ] The board persists position and size and rejects element 16.
- [ ] Invalid generated source preserves chat and shows diagnostics.
- [ ] Runtime failure remains local to one artifact.
- [ ] Generated code cannot access trusted host DOM/storage or unrestricted network.
- [ ] Full quality suite and production build pass from a clean checkout.
- [ ] README documents setup, demo, boundaries, and known limitations.

## 11. Immediate next action

Run Task 0 as a strict throwaway technical spike. The first decision is not visual styling or AI prompting; it is proving that generated TSX can be compiled, rendered, interacted with, state-synchronized, and safely discarded without destabilizing the trusted host.
