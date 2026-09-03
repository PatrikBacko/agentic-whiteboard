---
name: whiteboard
description: Use after every meaningful turn to maintain a persistent, versioned, interactive visual companion for the current Claude Code conversation.
version: 0.1.0
---

# Agentic Whiteboard

## Purpose

The ordinary conversation remains authoritative. The whiteboard is a second, visual communication channel that shows the current shape of the work: architecture, progress, relationships, decisions, evidence, blockers, alternatives, or next steps.

The whiteboard is not a transcript and not a separate agent harness. Complete the user's requested work normally, then update the visual companion before the final response.

## Storage contract

Each Claude session owns an isolated board under:

```text
.whiteboard/sessions/<session-id>/
├── current/
│   ├── index.html
│   ├── manifest.json
│   └── App.jsx          # optional source when React is used
├── versions/
│   └── 000001/
│       ├── index.html
│       └── manifest.json
├── history.jsonl
└── pending.json         # exists until the current turn is finalized
```

The prompt hook provides the exact session ID, prompt ID, paths, and commands. Never invent a different path when hook context is present.

## Required turn workflow

Perform this after every meaningful turn:

1. Complete the user's primary task first.
2. Read `current/manifest.json` and `current/index.html`.
3. Run the exact `prepare` command from the hook context. This archives the previously finalized version before editing.
4. Update `current/index.html` to reflect the new canonical understanding.
5. Run the exact `finalize` command from the hook context.
6. Briefly mention the resulting revision in the final response.

If a turn adds no useful visual information, preserve the current HTML and still prepare/finalize an unchanged revision with an honest summary. Do not manufacture visual churn merely to look active.

## What to visualize

Prefer structures that are difficult to understand in chronological prose:

- current architecture and data flow;
- completed, active, blocked, and upcoming work;
- causal debugging hypotheses and evidence;
- alternatives and trade-offs;
- plans, dependencies, milestones, and decision points;
- user requirements and constraints;
- artifacts produced by tools and how they relate;
- uncertainties or questions that genuinely affect the work.

Do not copy whole assistant messages into boxes. Compress the useful state.

## Visual continuity

Treat positions and visual conventions as spatial memory:

- preserve still-valid elements and their approximate placement;
- update existing elements instead of replacing everything;
- add new elements only when they carry new meaning;
- merge or remove stale elements;
- preserve user-authored content unless the user asks to remove it;
- keep colors and shapes semantically consistent between revisions;
- make the latest change visually discoverable without destroying context.

## Artifact requirements

`current/index.html` must be a self-contained static web app:

- no remote scripts, stylesheets, fonts, images, analytics, or API calls;
- no dependence on a development server;
- responsive enough to work in an embedded panel or normal browser tab;
- usable with keyboard and readable without animation;
- all important information represented in visible text, not canvas pixels alone;
- at most **15 top-level semantic elements**.

Mark each top-level element like this:

```html
<section id="architecture" data-whiteboard-element>
  ...
</section>
```

Every marked element needs a unique, stable `id`. Internal DOM complexity does not count toward the limit; the cap applies to meaningful top-level concepts.

## HTML/CSS/JavaScript mode

For most turns, directly edit `current/index.html`. It may contain arbitrary local HTML, CSS, SVG, Canvas, and JavaScript. Keep everything in one file unless a React source file is useful for later revisions.

Interactive controls are encouraged when they improve comprehension: filters, tabs, expandable evidence, sliders, simulations, draggable objects, or animated flows. They must not require a backend.

## React mode

When React materially improves the artifact:

1. Write `current/App.jsx` with a default-exported component.
2. React and React hooks may be imported from `react`.
3. Mark the rendered top-level concepts with `data-whiteboard-element` and stable IDs.
4. Run the exact `build-react.mjs` command supplied by the prompt hook.
5. Inspect the generated `current/index.html` before finalizing.

The bundler permits only React and its fixed transitive runtime. Do not request or install packages. Styling should be embedded in the component or generated DOM. The output contains the runtime and application code in one offline HTML file with a restrictive content-security policy.

## Version semantics

- `prepare` archives the old `current/` directory under its existing revision number.
- Archived trees carry a verified digest and are set read-only for tamper evidence and defense in depth. They are not cryptographically immutable against the workspace owner; never edit anything under `versions/`.
- `finalize` validates the artifact, increments the revision, writes the manifest and history, and clears `pending.json`.
- If finalization fails, fix the current artifact and rerun it. The archived prior version remains available.
- Do not manually forge revision numbers or history entries.

## Stop-hook behavior

If `pending.json` remains when Claude attempts to stop, the plugin blocks stopping once and gives the exact recovery commands. Follow that reminder after the user's core work is complete. The hook allows the next stop even if still stale to avoid an infinite loop, so passing the hook is not proof by itself; successful `finalize` is the completion criterion.

## Verification checklist

Before finishing:

- [ ] User's primary task is actually complete.
- [ ] Previous whiteboard was read and prepared.
- [ ] Current artifact reflects the latest useful state.
- [ ] No more than 15 `data-whiteboard-element` objects exist.
- [ ] IDs are present, unique, and stable where concepts persisted.
- [ ] Artifact has no remote dependencies.
- [ ] Interactive controls work without a backend.
- [ ] `finalize` succeeded for the current prompt ID.
- [ ] Final response mentions the revision without narrating internal mechanics.
