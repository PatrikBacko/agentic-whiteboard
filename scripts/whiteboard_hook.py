#!/usr/bin/env python3
"""Claude Code lifecycle hooks for the Agentic Whiteboard plugin."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import whiteboard

MAX_CONTEXT_CHARS = 16_000
_LIMITS = {
    "session_id": 256,
    "prompt_id": 256,
    "cwd": 4096,
    "prompt": 100_000,
    "last_assistant_message": 100_000,
    "hook_event_name": 64,
}


def _command(plugin_root: Path, arguments: str) -> str:
    script = shlex.quote(str(plugin_root / "scripts" / "whiteboard.py"))
    return f"python3 {script} {arguments}"


def _scalar(payload: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"{key} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    if len(value) > _LIMITS[key]:
        raise ValueError(f"{key} exceeds {_LIMITS[key]} characters")
    if key != "prompt" and any(ord(character) < 32 for character in value):
        raise ValueError(f"{key} contains control characters")
    return value


def _validate(payload: dict[str, Any], expected_event: str) -> None:
    event = _scalar(payload, "hook_event_name", required=True)
    if event != expected_event:
        raise ValueError(f"expected hook_event_name {expected_event!r}")
    _scalar(payload, "session_id")
    _scalar(payload, "prompt_id")
    _scalar(payload, "cwd")
    if expected_event == "UserPromptSubmit":
        _scalar(payload, "prompt", required=True)
    else:
        _scalar(payload, "last_assistant_message")
        if type(payload.get("stop_hook_active")) is not bool:
            raise ValueError("stop_hook_active must be a boolean")


def _workspace_root(payload: dict[str, Any]) -> Path:
    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured is not None:
        if len(configured) > _LIMITS["cwd"] or any(ord(char) < 32 for char in configured):
            raise ValueError("CLAUDE_PROJECT_DIR is invalid")
        value = configured
    else:
        value = _scalar(payload, "cwd") or "."
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("workspace root must be an existing directory")
    return root


def handle_prompt(payload: dict[str, Any], plugin_root: Path) -> dict[str, Any]:
    _validate(payload, "UserPromptSubmit")
    root = _workspace_root(payload)
    session_value = _scalar(payload, "session_id") or "default"
    prompt_id = _scalar(payload, "prompt_id")
    prompt = _scalar(payload, "prompt", required=True) or ""
    paths = whiteboard.mark_prompt(root, session_value, prompt_id, prompt)
    pending = whiteboard._load_json(paths.pending) if paths.pending.exists() else whiteboard._load_json(paths.manifest)
    effective_prompt = str(
        pending.get("prompt_id")
        or hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
    )
    quoted_root = shlex.quote(str(root))
    quoted_session = shlex.quote(paths.session_id)
    quoted_prompt = shlex.quote(effective_prompt)
    prepare = _command(plugin_root, f"prepare --root {quoted_root} --session {quoted_session}")
    finalize = _command(
        plugin_root,
        "finalize "
        f"--root {quoted_root} --session {quoted_session} "
        f"--prompt-id {quoted_prompt} --title '<short title>' "
        "--summary '<what this revision visualizes>'",
    )
    react_source = shlex.quote(str(paths.current / "App.jsx"))
    react_output = shlex.quote(str(paths.current_html))
    react_builder = shlex.quote(str(plugin_root / "scripts" / "build-react.mjs"))
    build_react = (
        f"node {react_builder} --source {react_source} --output {react_output} "
        "--title '<artifact title>'"
    )
    context = f"""Maintain the visual companion for this turn after completing the user's core task and before your final response.

Current artifact: {paths.current_html.relative_to(root)}
Current manifest: {paths.manifest.relative_to(root)}
Prompt id: {effective_prompt}

Workflow:
1. Read the current manifest and HTML so spatial structure and useful context remain stable.
2. Run: {prepare}
3. Update current/index.html as a self-contained interactive web app. Plain HTML/CSS/JS is valid. For React, write `current/App.jsx`, then run: {build_react}
   Mark at most 15 meaningful top-level objects with unique `id` and `data-whiteboard-element` attributes. Do not include remote scripts, styles, images, fonts, fetches, or analytics.
4. Preserve user-authored or still-relevant elements; remove, merge, or de-emphasize stale ones. Visualize state, relationships, decisions, evidence, progress, blockers, and next steps rather than copying the transcript.
5. Run: {finalize}
6. Mention the updated whiteboard revision briefly in your final response.

If the turn genuinely adds no useful visual information, preserve the HTML and still prepare/finalize a new revision with an honest summary. Never skip the user's actual task in order to work on the whiteboard."""
    if len(context) > MAX_CONTEXT_CHARS:
        raise ValueError("generated hook context exceeds safe size")
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def handle_stop(payload: dict[str, Any], plugin_root: Path) -> dict[str, Any]:
    _validate(payload, "Stop")
    root = _workspace_root(payload)
    session_id = _scalar(payload, "session_id") or "default"
    paths = whiteboard.paths_for(root, session_id)
    if not paths.pending.exists():
        return {}
    if payload["stop_hook_active"] is True:
        return {
            "systemMessage": (
                "Agentic Whiteboard reminder was already issued; allowing stop to avoid a loop. "
                f"Pending state remains at {paths.pending}."
            )
        }
    pending = whiteboard._load_json(paths.pending)
    prompt_id = str(pending.get("prompt_id") or "manual")
    quoted_root = shlex.quote(str(root))
    quoted_session = shlex.quote(paths.session_id)
    quoted_prompt = shlex.quote(prompt_id)
    prepare = _command(plugin_root, f"prepare --root {quoted_root} --session {quoted_session}")
    finish = _command(
        plugin_root,
        "finalize "
        f"--root {quoted_root} --session {quoted_session} "
        f"--prompt-id {quoted_prompt} --title '<short title>' "
        "--summary '<what this revision visualizes>'",
    )
    reason = (
        "The current turn has not finalized its visual companion. Complete the user's "
        f"work first, then run `{prepare}`, update `{paths.current_html}`, and run "
        f"`{finish}`. If no visual change is warranted, preserve the HTML and finalize "
        "an unchanged revision. Do not discuss this internal reminder at length."
    )
    if len(reason) > MAX_CONTEXT_CHARS:
        raise ValueError("generated stop context exceeds safe size")
    return {"decision": "block", "reason": reason}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if len(arguments) != 1 or arguments[0] not in {"prompt", "stop"}:
        print("usage: whiteboard_hook.py prompt|stop", file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        plugin_root = Path(
            os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[1])
        ).resolve()
        result = (
            handle_prompt(payload, plugin_root)
            if arguments[0] == "prompt"
            else handle_stop(payload, plugin_root)
        )
    except Exception as exc:
        result = {"systemMessage": f"Agentic Whiteboard hook failed safely: {exc}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
