#!/usr/bin/env python3
"""Versioned whiteboard lifecycle and a deliberately narrow local viewer."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import unquote, urlsplit

MAX_ELEMENTS = 15
MAX_HTML_BYTES = 1_000_000
MAX_ARCHIVE_FILES = 32
MAX_ARCHIVE_BYTES = 2_000_000
SCHEMA_VERSION = 1
VIEWER_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; connect-src 'none'; media-src data: blob:; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class WhiteboardError(RuntimeError):
    """Raised when a whiteboard transition would violate the protocol."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_session_id(value: str | None) -> str:
    """Return a readable id, hashing whenever normalization could collide."""
    if value is None or value == "":
        return "default"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    if normalized == value and len(value) <= 80:
        return value
    stem = (normalized or "default")[:67].rstrip("-._") or "default"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{suffix}"


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    session_id: str

    @property
    def whiteboard_root(self) -> Path:
        return self.root / ".whiteboard"

    @property
    def session_dir(self) -> Path:
        return self.whiteboard_root / "sessions" / self.session_id

    @property
    def current(self) -> Path:
        return self.session_dir / "current"

    @property
    def current_html(self) -> Path:
        return self.current / "index.html"

    @property
    def manifest(self) -> Path:
        return self.current / "manifest.json"

    @property
    def pending(self) -> Path:
        return self.session_dir / "pending.json"

    @property
    def versions(self) -> Path:
        return self.session_dir / "versions"

    @property
    def history(self) -> Path:
        return self.session_dir / "history.jsonl"

    @property
    def lock(self) -> Path:
        return self.session_dir / ".lock"


STARTER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent whiteboard</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101412; color: #ecf3ee; }
    main { width: min(42rem, calc(100% - 2rem)); padding: 2rem; border: 1px solid #34463b; border-radius: 1.25rem; background: #18201b; }
    p { color: #aebdb4; line-height: 1.6; }
  </style>
</head>
<body>
  <main id="welcome" data-whiteboard-element>
    <h1>Whiteboard ready</h1>
    <p>The coding agent will replace this with a visual companion to the conversation.</p>
  </main>
</body>
</html>
"""


def _assert_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise WhiteboardError(f"Missing whiteboard directory: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise WhiteboardError(f"Whiteboard path must be a non-symlink directory: {path}")


def _assert_regular(path: Path, *, allow_missing: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise WhiteboardError(f"Missing regular file: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise WhiteboardError(f"Whiteboard path must be a regular non-symlink file: {path}")


def _safe_mkdir(path: Path, parent: Path | None = None) -> None:
    if parent is not None:
        _assert_directory(parent)
    try:
        path.mkdir()
    except FileExistsError:
        pass
    _assert_directory(path)


def _validate_protocol_tree(root: Path) -> None:
    """Reject special files and links anywhere in the bounded protocol tree."""
    seen = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        _assert_directory(directory)
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise WhiteboardError(f"Cannot inspect whiteboard directory: {directory}") from exc
        for child in children:
            seen += 1
            if seen > 1024:
                raise WhiteboardError("Whiteboard protocol tree exceeds 1024 entries")
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise WhiteboardError(f"Cannot inspect whiteboard path: {child.path}") from exc
            mode = info.st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise WhiteboardError(
                    f"Whiteboard path must be regular and non-symlink: {child.path}"
                )
            if stat.S_ISDIR(mode):
                pending.append(Path(child.path))


def _bootstrap(paths: SessionPaths) -> None:
    _safe_mkdir(paths.whiteboard_root, paths.root)
    sessions = paths.whiteboard_root / "sessions"
    _safe_mkdir(sessions, paths.whiteboard_root)
    _safe_mkdir(paths.session_dir, sessions)
    _safe_mkdir(paths.current, paths.session_dir)
    _safe_mkdir(paths.versions, paths.session_dir)
    _assert_regular(paths.lock, allow_missing=True)
    _validate_protocol_tree(paths.whiteboard_root)


@contextlib.contextmanager
def _session_lock(paths: SessionPaths) -> Iterator[None]:
    _bootstrap(paths)
    descriptor = os.open(paths.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WhiteboardError(f"Session lock is not a regular file: {paths.lock}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _bootstrap(paths)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    _assert_directory(path.parent)
    _assert_regular(path, allow_missing=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, (json.dumps(value, indent=2) + "\n").encode())


def _load_json(path: Path) -> dict[str, Any]:
    _assert_regular(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WhiteboardError(f"Cannot read valid JSON from {path}") from exc
    if not isinstance(loaded, dict):
        raise WhiteboardError(f"Expected a JSON object in {path}")
    return loaded


def paths_for(root: Path | str, session_id: str | None) -> SessionPaths:
    resolved_root = Path(root).expanduser().resolve()
    _assert_directory(resolved_root)
    return SessionPaths(resolved_root, safe_session_id(session_id))


def _ensure_unlocked(paths: SessionPaths) -> None:
    _bootstrap(paths)
    _assert_regular(paths.current_html, allow_missing=True)
    _assert_regular(paths.manifest, allow_missing=True)
    _assert_regular(paths.pending, allow_missing=True)
    _assert_regular(paths.history, allow_missing=True)
    if not paths.current_html.exists():
        _atomic_bytes(paths.current_html, STARTER_HTML.encode("utf-8"))
    if not paths.manifest.exists():
        _atomic_json(
            paths.manifest,
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": paths.session_id,
                "revision": 0,
                "title": "Whiteboard ready",
                "summary": "Initial empty whiteboard",
                "source_prompt_id": None,
                "updated_at": now_iso(),
                "elements": ["welcome"],
                "content_sha256": hashlib.sha256(STARTER_HTML.encode()).hexdigest(),
            },
        )
    active = paths.whiteboard_root / "active.json"
    _assert_regular(active, allow_missing=True)
    _atomic_json(active, {"session_id": paths.session_id, "updated_at": now_iso()})


def ensure_session(root: Path | str, session_id: str | None) -> SessionPaths:
    paths = paths_for(root, session_id)
    with _session_lock(paths):
        _ensure_unlocked(paths)
    return paths


def _content_digest(path: Path) -> str:
    _assert_regular(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mark_prompt(
    root: Path | str, session_id: str | None, prompt_id: str | None, prompt: str
) -> SessionPaths:
    paths = paths_for(root, session_id)
    prompt_identifier = (
        safe_session_id(prompt_id)
        if prompt_id
        else hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
    )
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    with _session_lock(paths):
        _ensure_unlocked(paths)
        if paths.pending.exists():
            pending = _load_json(paths.pending)
            if (
                pending.get("prompt_id") == prompt_identifier
                and pending.get("prompt_sha256") == prompt_digest
            ):
                return paths
            raise WhiteboardError(
                f"A different prompt ({pending.get('prompt_id')!r}) is already pending"
            )
        manifest = _load_json(paths.manifest)
        if (
            manifest.get("source_prompt_id") == prompt_identifier
            and manifest.get("content_sha256") == _content_digest(paths.current_html)
        ):
            return paths
        _atomic_json(
            paths.pending,
            {
                "schema_version": SCHEMA_VERSION,
                "prompt_id": prompt_identifier,
                "prompt_sha256": prompt_digest,
                "started_at": now_iso(),
                "prepared_revision": None,
            },
        )
    return paths


def _bounded_tree(directory: Path) -> tuple[list[tuple[Path, str, int]], str, int]:
    _assert_directory(directory)
    entries: list[tuple[Path, str, int]] = []
    total = 0
    for candidate in sorted(directory.rglob("*")):
        relative = candidate.relative_to(directory).as_posix()
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise WhiteboardError(f"Artifact tree contains a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise WhiteboardError(f"Artifact tree contains a non-regular file: {relative}")
        if relative == "archive.json":
            continue
        total += info.st_size
        entries.append((candidate, relative, info.st_size))
        if len(entries) > MAX_ARCHIVE_FILES or total > MAX_ARCHIVE_BYTES:
            raise WhiteboardError("Artifact tree exceeds archive file/byte limits")
    digest = hashlib.sha256()
    for candidate, relative, size in entries:
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(size.to_bytes(8, "big"))
        with candidate.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
    return entries, digest.hexdigest(), total


def _verify_archive(directory: Path, revision: int) -> dict[str, Any]:
    metadata = _load_json(directory / "archive.json")
    entries, digest, total = _bounded_tree(directory)
    expected_files = [relative for _, relative, _ in entries]
    if (
        metadata.get("revision") != revision
        or metadata.get("sha256") != digest
        or metadata.get("files") != expected_files
        or metadata.get("total_bytes") != total
    ):
        raise WhiteboardError(f"Archived revision {revision} failed integrity verification")
    manifest = _load_json(directory / "manifest.json")
    if manifest.get("content_sha256") != _content_digest(directory / "index.html"):
        raise WhiteboardError(f"Archived revision {revision} content digest does not match")
    return metadata


def _archive_current(paths: SessionPaths, revision: int) -> Path:
    destination = paths.versions / f"{revision:06d}"
    if destination.exists():
        _assert_directory(destination)
        _verify_archive(destination, revision)
        return destination
    entries, digest, total = _bounded_tree(paths.current)
    temporary = Path(tempfile.mkdtemp(prefix=".archive-", dir=paths.versions))
    try:
        for source, relative, _ in entries:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
        _atomic_json(
            temporary / "archive.json",
            {
                "schema_version": SCHEMA_VERSION,
                "revision": revision,
                "sha256": digest,
                "files": [relative for _, relative, _ in entries],
                "total_bytes": total,
            },
        )
        _verify_archive(temporary, revision)
        for candidate in sorted(temporary.rglob("*"), reverse=True):
            os.chmod(candidate, 0o555 if candidate.is_dir() else 0o444)
        os.chmod(temporary, 0o555)
        try:
            temporary.rename(destination)
        except FileExistsError as exc:
            raise WhiteboardError(f"Archive destination was pre-created: {destination}") from exc
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def prepare(root: Path | str, session_id: str | None) -> dict[str, Any]:
    paths = paths_for(root, session_id)
    with _session_lock(paths):
        _ensure_unlocked(paths)
        manifest = _load_json(paths.manifest)
        revision = int(manifest.get("revision", 0))
        archived_to: str | None = None
        if revision > 0:
            if manifest.get("content_sha256") != _content_digest(paths.current_html):
                raise WhiteboardError("Finalized artifact was modified before it could be archived")
            archived_to = str(_archive_current(paths, revision))
        if paths.pending.exists():
            pending = _load_json(paths.pending)
        else:
            pending = {
                "schema_version": SCHEMA_VERSION,
                "prompt_id": "manual",
                "prompt_sha256": hashlib.sha256(b"manual").hexdigest(),
                "started_at": now_iso(),
            }
        prepared = pending.get("prepared_revision")
        if prepared not in (None, revision):
            raise WhiteboardError("Pending prompt was prepared against another revision")
        pending["prepared_revision"] = revision
        _atomic_json(paths.pending, pending)
        return {
            "session_id": paths.session_id,
            "current_revision": revision,
            "archived_to": archived_to,
            "edit": str(paths.current_html),
        }


class _ArtifactInspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_seen = False
        self.element_ids: list[str] = []
        self.remote_assets: list[str] = []

    @staticmethod
    def _embedded(value: str) -> bool:
        lowered = value.strip().lower()
        return not lowered or lowered.startswith(("data:", "blob:", "#"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered_tag = tag.lower()
        values = {key.lower(): value for key, value in attrs}
        if lowered_tag == "html":
            self.html_seen = True
        if lowered_tag == "meta" and (values.get("name") or "").lower() == "agentic-whiteboard-element":
            identifier = values.get("content")
            if not identifier:
                raise WhiteboardError("React element metadata requires a stable content id")
            self.element_ids.append(identifier)
        if "data-whiteboard-element" in values:
            identifier = values.get("id")
            if not identifier:
                raise WhiteboardError("Every data-whiteboard-element requires a stable id")
            self.element_ids.append(identifier)
        for attribute in ("src", "href", "action", "formaction", "poster", "data"):
            value = values.get(attribute) or ""
            if not self._embedded(value):
                self.remote_assets.append(value)
        srcset = values.get("srcset") or ""
        if srcset:
            # srcset has a deliberately complex grammar (including commas in data URLs).
            # A single-file artifact can use src=data: instead, so reject it entirely.
            self.remote_assets.append(srcset)
        if lowered_tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            self.remote_assets.append(values.get("content") or "refresh")


def inspect_html(path: Path) -> list[str]:
    _assert_regular(path)
    size = path.stat().st_size
    if size > MAX_HTML_BYTES:
        raise WhiteboardError(f"Artifact is {size} bytes; maximum is {MAX_HTML_BYTES} bytes")
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WhiteboardError("Artifact must be UTF-8 HTML") from exc
    parser = _ArtifactInspector()
    try:
        parser.feed(source)
    except WhiteboardError:
        raise
    except Exception as exc:
        raise WhiteboardError("Artifact is not parseable HTML") from exc
    css_dependency = re.search(
        r"(?is)(?:url\(\s*['\"]?\s*(?!(?:data:|blob:|#))|@import\b)",
        source,
    )
    js_dependency = re.search(
        r"(?is)\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|require)\s*\(|"
        r"\bnavigator\s*\.\s*sendBeacon\s*\(|"
        r"\bimport\s*(?:\(|(?:[^<>;]*?\bfrom\s*)?['\"])",
        source,
    )
    if parser.remote_assets or css_dependency or js_dependency:
        raise WhiteboardError(
            "Artifact must be self-contained; external dependencies and networking are not allowed"
        )
    if not parser.html_seen:
        raise WhiteboardError("Artifact must contain an html document")
    if len(parser.element_ids) > MAX_ELEMENTS:
        raise WhiteboardError(f"Whiteboard has {len(parser.element_ids)} top-level elements; maximum is {MAX_ELEMENTS}")
    if len(set(parser.element_ids)) != len(parser.element_ids):
        raise WhiteboardError("Whiteboard element ids must be unique")
    return parser.element_ids


def finalize(
    root: Path | str,
    session_id: str | None,
    prompt_id: str,
    title: str,
    summary: str,
) -> dict[str, Any]:
    paths = paths_for(root, session_id)
    normalized_prompt = safe_session_id(prompt_id)
    with _session_lock(paths):
        _ensure_unlocked(paths)
        previous = _load_json(paths.manifest)
        if not paths.pending.exists():
            if (
                previous.get("source_prompt_id") == normalized_prompt
                and previous.get("content_sha256") == _content_digest(paths.current_html)
            ):
                return previous
            raise WhiteboardError("No pending prompt to finalize")
        pending = _load_json(paths.pending)
        expected_prompt = str(pending.get("prompt_id"))
        if expected_prompt != normalized_prompt:
            raise WhiteboardError(f"Pending prompt is {expected_prompt!r}, not {normalized_prompt!r}")
        previous_revision = int(previous.get("revision", 0))
        if pending.get("prepared_revision") not in (None, previous_revision):
            raise WhiteboardError("Pending prompt was prepared against another revision")
        if previous_revision > 0:
            archived = paths.versions / f"{previous_revision:06d}"
            if not archived.exists():
                raise WhiteboardError("Run prepare before replacing a finalized whiteboard")
            _verify_archive(archived, previous_revision)
        elements = inspect_html(paths.current_html)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "session_id": paths.session_id,
            "revision": previous_revision + 1,
            "title": title.strip()[:160] or "Untitled whiteboard",
            "summary": summary.strip()[:1000],
            "source_prompt_id": normalized_prompt,
            "updated_at": now_iso(),
            "elements": elements,
            "content_sha256": _content_digest(paths.current_html),
        }
        _atomic_json(paths.manifest, manifest)
        history = b""
        if paths.history.exists():
            _assert_regular(paths.history)
            history = paths.history.read_bytes()
        history += (json.dumps(manifest, separators=(",", ":")) + "\n").encode()
        _atomic_bytes(paths.history, history)
        paths.pending.unlink()
        return manifest


def status(root: Path | str, session_id: str | None) -> dict[str, Any]:
    paths = paths_for(root, session_id)
    with _session_lock(paths):
        _ensure_unlocked(paths)
        manifest = _load_json(paths.manifest)
        versions = 0
        for item in paths.versions.iterdir():
            if item.name.startswith(".archive-"):
                continue
            _assert_directory(item)
            revision = int(item.name) if item.name.isdigit() else -1
            if revision < 0:
                raise WhiteboardError(f"Unexpected archive entry: {item}")
            _verify_archive(item, revision)
            versions += 1
        return {
            "session_id": paths.session_id,
            "revision": int(manifest.get("revision", 0)),
            "title": manifest.get("title"),
            "pending": paths.pending.exists(),
            "current": str(paths.current_html),
            "versions": versions,
        }


def artifact_directory(
    root: Path | str, session_id: str | None, revision: int | None = None
) -> Path:
    paths = ensure_session(root, session_id)
    if revision is None:
        _assert_regular(paths.current_html)
        return paths.current
    selected = paths.versions / f"{revision:06d}"
    if not selected.exists():
        raise WhiteboardError(f"Archived revision {revision} does not exist for session {paths.session_id}")
    _assert_directory(selected)
    _verify_archive(selected, revision)
    return selected


def viewer_handler(index: Path) -> type[BaseHTTPRequestHandler]:
    _assert_regular(index)

    class ViewerHandler(BaseHTTPRequestHandler):
        def _serve(self, include_body: bool) -> None:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if parsed.query or parsed.fragment or path not in ("/", "/index.html"):
                self.send_error(404)
                return
            try:
                _assert_regular(index)
                content = index.read_bytes()
            except (OSError, WhiteboardError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Security-Policy", VIEWER_CSP)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802
            self._serve(True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(False)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ViewerHandler


def serve(
    root: Path | str,
    session_id: str | None,
    port: int,
    open_browser: bool,
    revision: int | None = None,
) -> None:
    paths = ensure_session(root, session_id)
    directory = artifact_directory(root, session_id, revision)
    server = ThreadingHTTPServer(("127.0.0.1", port), viewer_handler(directory / "index.html"))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Serving {directory / 'index.html'} at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain a versioned agent whiteboard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "prepare", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--root", default=".")
        child.add_argument("--session", default="default")
    prompt = subparsers.add_parser("prompt")
    prompt.add_argument("--root", default=".")
    prompt.add_argument("--session", required=True)
    prompt.add_argument("--prompt-id")
    prompt.add_argument("--summary", required=True)
    finish = subparsers.add_parser("finalize")
    finish.add_argument("--root", default=".")
    finish.add_argument("--session", required=True)
    finish.add_argument("--prompt-id", required=True)
    finish.add_argument("--title", required=True)
    finish.add_argument("--summary", required=True)
    server = subparsers.add_parser("serve")
    server.add_argument("--root", default=".")
    server.add_argument("--session", default="default")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--open", action="store_true")
    server.add_argument("--revision", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            result: Any = status(args.root, args.session)
        elif args.command == "prompt":
            paths = mark_prompt(args.root, args.session, args.prompt_id, args.summary)
            fallback = hashlib.sha256(args.summary.encode()).hexdigest()[:24]
            result = {
                "session_id": paths.session_id,
                "prompt_id": safe_session_id(args.prompt_id) if args.prompt_id else fallback,
                "pending": str(paths.pending),
            }
        elif args.command == "prepare":
            result = prepare(args.root, args.session)
        elif args.command == "finalize":
            result = finalize(args.root, args.session, args.prompt_id, args.title, args.summary)
        elif args.command == "status":
            result = status(args.root, args.session)
        else:
            serve(args.root, args.session, args.port, args.open, args.revision)
            return 0
    except WhiteboardError as exc:
        print(f"whiteboard: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
