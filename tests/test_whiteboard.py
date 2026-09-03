import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import whiteboard
from scripts import whiteboard_hook


VALID_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Board</title></head>
<body>
  <main id="status" data-whiteboard-element>Ready</main>
</body></html>
"""


class WhiteboardLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = "session/with unsafe chars"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_session_id_is_safe_and_stable(self) -> None:
        expected_hash = __import__("hashlib").sha256(self.session.encode()).hexdigest()[:12]
        self.assertEqual(
            whiteboard.safe_session_id(self.session),
            f"session-with-unsafe-chars-{expected_hash}",
        )
        self.assertNotEqual(
            whiteboard.safe_session_id("***"), whiteboard.safe_session_id("???")
        )
        self.assertEqual(whiteboard.safe_session_id(None), "default")

    def test_prompt_initializes_session_and_pending_turn(self) -> None:
        paths = whiteboard.mark_prompt(
            self.root, self.session, "prompt-1", "Explain the architecture"
        )

        self.assertTrue(paths.current_html.exists())
        self.assertTrue(paths.session_dir.name.startswith("session-with-unsafe-chars-"))
        pending = json.loads(paths.pending.read_text(encoding="utf-8"))
        self.assertEqual(pending["prompt_id"], "prompt-1")
        self.assertNotIn("Explain the architecture", pending)
        self.assertEqual(len(pending["prompt_sha256"]), 64)

    def test_finalize_archives_previous_revision_before_replacement(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "first")
        paths.current_html.write_text(VALID_HTML.replace("Ready", "Version one"))
        first = whiteboard.finalize(
            self.root, self.session, "p1", "First", "Initial visual state"
        )
        self.assertEqual(first["revision"], 1)

        whiteboard.mark_prompt(self.root, self.session, "p2", "second")
        whiteboard.prepare(self.root, self.session)
        archived = paths.versions / "000001" / "index.html"
        self.assertIn("Version one", archived.read_text(encoding="utf-8"))

        paths.current_html.write_text(VALID_HTML.replace("Ready", "Version two"))
        second = whiteboard.finalize(
            self.root, self.session, "p2", "Second", "Updated visual state"
        )

        self.assertEqual(second["revision"], 2)
        self.assertIn("Version two", paths.current_html.read_text(encoding="utf-8"))
        self.assertIn("Version one", archived.read_text(encoding="utf-8"))
        history = [
            json.loads(line)
            for line in paths.history.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([entry["revision"] for entry in history], [1, 2])

    def test_prepare_is_idempotent_for_the_same_revision(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "first")
        paths.current_html.write_text(VALID_HTML)
        whiteboard.finalize(self.root, self.session, "p1", "First", "Summary")
        whiteboard.mark_prompt(self.root, self.session, "p2", "second")

        whiteboard.prepare(self.root, self.session)
        whiteboard.prepare(self.root, self.session)

        self.assertEqual(
            [path.name for path in paths.versions.iterdir()], ["000001"]
        )

    def test_finalize_rejects_more_than_fifteen_top_level_elements(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "many")
        elements = "".join(
            f'<section id="item-{index}" data-whiteboard-element></section>'
            for index in range(16)
        )
        paths.current_html.write_text(f"<!doctype html><html><body>{elements}</body></html>")

        with self.assertRaisesRegex(whiteboard.WhiteboardError, "15"):
            whiteboard.finalize(
                self.root, self.session, "p1", "Too many", "Invalid board"
            )

        self.assertTrue(paths.pending.exists())

    def test_finalize_requires_unique_element_ids(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "duplicate")
        paths.current_html.write_text(
            "<!doctype html><html><body>"
            '<section id="same" data-whiteboard-element></section>'
            '<section id="same" data-whiteboard-element></section>'
            "</body></html>"
        )

        with self.assertRaisesRegex(whiteboard.WhiteboardError, "unique"):
            whiteboard.finalize(
                self.root, self.session, "p1", "Duplicate", "Invalid board"
            )

    def test_finalize_rejects_remote_assets_in_static_artifact(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "remote")
        paths.current_html.write_text(
            '<!doctype html><html><body><script src="https://example.com/x.js"></script></body></html>'
        )

        with self.assertRaisesRegex(whiteboard.WhiteboardError, "self-contained"):
            whiteboard.finalize(
                self.root, self.session, "p1", "Remote", "Invalid board"
            )

    def test_finalize_reads_react_element_metadata(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "react")
        paths.current_html.write_text(
            '<!doctype html><html><head>'
            '<meta name="agentic-whiteboard-element" content="architecture">'
            '<meta name="agentic-whiteboard-element" content="progress">'
            '</head><body><div id="root"></div></body></html>'
        )

        manifest = whiteboard.finalize(
            self.root, self.session, "p1", "React", "Compiled app"
        )

        self.assertEqual(manifest["elements"], ["architecture", "progress"])

    def test_status_reports_pending_and_current_revision(self) -> None:
        whiteboard.mark_prompt(self.root, self.session, "p1", "first")
        status = whiteboard.status(self.root, self.session)
        self.assertTrue(status["pending"])
        self.assertEqual(status["revision"], 0)

    def test_archived_revision_can_be_selected_for_viewing(self) -> None:
        paths = whiteboard.mark_prompt(self.root, self.session, "p1", "first")
        paths.current_html.write_text(VALID_HTML.replace("Ready", "Old view"))
        whiteboard.finalize(self.root, self.session, "p1", "Old", "First")
        whiteboard.mark_prompt(self.root, self.session, "p2", "second")
        whiteboard.prepare(self.root, self.session)

        selected = whiteboard.artifact_directory(self.root, self.session, 1)

        self.assertIn("Old view", selected.joinpath("index.html").read_text())

    def test_unknown_archived_revision_is_rejected(self) -> None:
        whiteboard.ensure_session(self.root, self.session)
        with self.assertRaisesRegex(whiteboard.WhiteboardError, "revision 99"):
            whiteboard.artifact_directory(self.root, self.session, 99)

    def test_prompt_cli_creates_pending_marker(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = whiteboard.main(
                [
                    "prompt",
                    "--root",
                    str(self.root),
                    "--session",
                    "manual-session",
                    "--prompt-id",
                    "turn-7",
                    "--summary",
                    "Plan the API",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["prompt_id"], "turn-7")
        pending = self.root / ".whiteboard/sessions/manual-session/pending.json"
        self.assertEqual(json.loads(pending.read_text())["prompt_id"], "turn-7")
    def test_rejects_symlink_protocol_root(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.root.joinpath(".whiteboard").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(whiteboard.WhiteboardError, "non-symlink"):
            whiteboard.ensure_session(self.root, "unsafe")

    def test_atomic_writes_do_not_use_predictable_tmp_name(self) -> None:
        paths = whiteboard.paths_for(self.root, "safe")
        paths.session_dir.mkdir(parents=True)
        paths.current.mkdir()
        paths.versions.mkdir()
        predictable = paths.manifest.with_suffix(".json.tmp")
        predictable.write_text("sentinel")
        paths = whiteboard.ensure_session(self.root, "safe")
        self.assertTrue(paths.manifest.is_file())
        self.assertEqual(predictable.read_text(), "sentinel")

    def test_rejects_offline_validation_bypasses(self) -> None:
        bypasses = (
            '<script src="HtTpS://example.test/x"></script>',
            '<img src="//example.test/x">',
            '<img srcset="data:image/png,x 1x, HTTPS://example.test/x 2x">',
            '<style>.x{background:url(HTTP://example.test/x)}</style>',
            '<style>@IMPORT "//example.test/x";</style>',
            '<meta http-equiv="refresh" content="0; URL=HTTPS://example.test">',
            '<form action="//example.test"><button>go</button></form>',
            '<script>fetch("/secret")</script>',
            '<script>new XMLHttpRequest()</script>',
            '<script>navigator.sendBeacon("/x")</script>',
            '<script src="app.js"></script>',
            '<link rel="stylesheet" href="styles.css">',
            '<img src="image.png">',
            '<video poster="poster.jpg"><source src="movie.mp4"></video>',
            '<iframe src="panel.html"></iframe>',
            '<style>.x{background:url(icons/check.svg)}</style>',
            '<style>@import "theme.css";</style>',
            '<script type="module">import "./feature.js"</script>',
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass):
                path = self.root / "bypass.html"
                path.write_text(f"<!doctype html><html><body>{bypass}</body></html>")
                with self.assertRaisesRegex(whiteboard.WhiteboardError, "self-contained"):
                    whiteboard.inspect_html(path)

    def test_prompt_locking_rejects_different_pending_prompt(self) -> None:
        barrier = threading.Barrier(2)
        results: list[str] = []

        def mark(identifier: str) -> None:
            barrier.wait()
            try:
                whiteboard.mark_prompt(self.root, "locked", identifier, identifier)
                results.append("ok")
            except whiteboard.WhiteboardError:
                results.append("rejected")

        threads = [threading.Thread(target=mark, args=(identifier,)) for identifier in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(results, ["ok", "rejected"])

    def test_same_prompt_and_finalize_are_idempotent_under_concurrency(self) -> None:
        for _ in range(2):
            whiteboard.mark_prompt(self.root, "shared", "p1", "same")
        paths = whiteboard.paths_for(self.root, "shared")
        paths.current_html.write_text(VALID_HTML)
        barrier = threading.Barrier(4)
        revisions: list[int] = []

        def finish() -> None:
            barrier.wait()
            revisions.append(
                whiteboard.finalize(self.root, "shared", "p1", "Same", "Same")["revision"]
            )

        threads = [threading.Thread(target=finish) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(revisions), [1, 1, 1, 1])
        self.assertEqual(len(paths.history.read_text().splitlines()), 1)

    def test_locks_are_per_session(self) -> None:
        barrier = threading.Barrier(2)
        completed: list[str] = []

        def mark(session: str) -> None:
            barrier.wait()
            whiteboard.mark_prompt(self.root, session, "p", "same")
            completed.append(session)

        threads = [threading.Thread(target=mark, args=(session,)) for session in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(completed, ["one", "two"])

    def test_archive_is_digest_verified_and_read_only(self) -> None:
        paths = whiteboard.mark_prompt(self.root, "archive", "p1", "first")
        paths.current_html.write_text(VALID_HTML)
        whiteboard.finalize(self.root, "archive", "p1", "First", "First")
        whiteboard.mark_prompt(self.root, "archive", "p2", "second")
        whiteboard.prepare(self.root, "archive")
        archived = paths.versions / "000001"
        metadata = json.loads(archived.joinpath("archive.json").read_text())
        self.assertEqual(len(metadata["sha256"]), 64)
        self.assertEqual(archived.joinpath("index.html").stat().st_mode & 0o222, 0)
        archived.joinpath("index.html").chmod(0o644)
        archived.joinpath("index.html").write_text("tampered")
        with self.assertRaisesRegex(whiteboard.WhiteboardError, "integrity|digest"):
            whiteboard.artifact_directory(self.root, "archive", 1)

    def test_prepare_detects_precreated_archive(self) -> None:
        paths = whiteboard.mark_prompt(self.root, "precreated", "p1", "first")
        paths.current_html.write_text(VALID_HTML)
        whiteboard.finalize(self.root, "precreated", "p1", "First", "First")
        whiteboard.mark_prompt(self.root, "precreated", "p2", "second")
        paths.versions.joinpath("000001").mkdir()
        with self.assertRaises(whiteboard.WhiteboardError):
            whiteboard.prepare(self.root, "precreated")

    def test_viewer_serves_only_index_with_security_headers(self) -> None:
        paths = whiteboard.ensure_session(self.root, "viewer")
        paths.current.joinpath("App.jsx").write_text("secret source")
        server = whiteboard.ThreadingHTTPServer(
            ("127.0.0.1", 0), whiteboard.viewer_handler(paths.current_html)
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            response = urllib.request.urlopen(base + "/index.html")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
            for path in ("/App.jsx", "/manifest.json", "/..%2findex.html"):
                with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base + path)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class HookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugin_root = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prompt_hook_marks_turn_and_injects_exact_workflow(self) -> None:
        result = whiteboard_hook.handle_prompt(
            {
                "session_id": "claude-session",
                "prompt_id": "prompt-42",
                "cwd": str(self.root),
                "prompt": "Implement the parser",
                "hook_event_name": "UserPromptSubmit",
            },
            self.plugin_root,
        )

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(
            result["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
        )
        self.assertIn(".whiteboard/sessions/claude-session/current/index.html", context)
        self.assertIn("prompt-42", context)
        self.assertIn("prepare", context)
        self.assertIn("finalize", context)
        self.assertIn("build-react.mjs", context)
        self.assertIn("App.jsx", context)
        self.assertTrue(
            self.root.joinpath(
                ".whiteboard/sessions/claude-session/pending.json"
            ).exists()
        )

    def test_prompt_hook_falls_back_to_plain_html_without_bundler_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as plugin_directory:
            result = whiteboard_hook.handle_prompt(
                {
                    "session_id": "marketplace-session",
                    "prompt_id": "prompt-1",
                    "cwd": str(self.root),
                    "prompt": "Visualize this",
                    "hook_event_name": "UserPromptSubmit",
                },
                Path(plugin_directory),
            )

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("React bundler dependencies are unavailable", context)
        self.assertIn("plain HTML", context)
        self.assertNotIn("node ", context)

    def test_prompt_hook_prefers_claude_project_root_over_changed_cwd(self) -> None:
        nested = self.root / "packages" / "app"
        nested.mkdir(parents=True)
        with mock.patch.dict(
            os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}, clear=False
        ):
            whiteboard_hook.handle_prompt(
                {
                    "session_id": "claude-session",
                    "prompt_id": "prompt-42",
                    "cwd": str(nested),
                    "prompt": "work",
                    "hook_event_name": "UserPromptSubmit",
                },
                self.plugin_root,
            )

        self.assertTrue(
            self.root.joinpath(
                ".whiteboard/sessions/claude-session/pending.json"
            ).exists()
        )
        self.assertFalse(nested.joinpath(".whiteboard").exists())

    def test_stop_hook_blocks_once_when_whiteboard_is_stale(self) -> None:
        whiteboard.mark_prompt(
            self.root, "claude-session", "prompt-42", "Implement the parser"
        )
        result = whiteboard_hook.handle_stop(
            {
                "session_id": "claude-session",
                "prompt_id": "prompt-42",
                "cwd": str(self.root),
                "stop_hook_active": False,
                "last_assistant_message": "Implemented.",
                "hook_event_name": "Stop",
            },
            self.plugin_root,
        )

        self.assertEqual(result["decision"], "block")
        self.assertIn("whiteboard", result["reason"].lower())
        self.assertIn("finalize", result["reason"])

    def test_stop_hook_does_not_loop_after_reminder(self) -> None:
        whiteboard.mark_prompt(self.root, "claude-session", "prompt-42", "work")
        result = whiteboard_hook.handle_stop(
            {
                "session_id": "claude-session",
                "prompt_id": "prompt-42",
                "cwd": str(self.root),
                "stop_hook_active": True,
                "last_assistant_message": "Done.",
                "hook_event_name": "Stop",
            },
            self.plugin_root,
        )

        self.assertNotEqual(result.get("decision"), "block")

    def test_stop_hook_allows_current_whiteboard(self) -> None:
        paths = whiteboard.mark_prompt(
            self.root, "claude-session", "prompt-42", "work"
        )
        paths.current_html.write_text(VALID_HTML)
        whiteboard.finalize(
            self.root,
            "claude-session",
            "prompt-42",
            "Current",
            "Reflects this turn",
        )

        result = whiteboard_hook.handle_stop(
            {
                "session_id": "claude-session",
                "prompt_id": "prompt-42",
                "cwd": str(self.root),
                "stop_hook_active": False,
                "last_assistant_message": "Done.",
                "hook_event_name": "Stop",
            },
            self.plugin_root,
        )

        self.assertEqual(result, {})

    def test_hook_validates_event_and_payload_types(self) -> None:
        invalid_payloads = (
            ({"hook_event_name": "Stop", "prompt": "x"}, "prompt"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": [], "session_id": "s"}, "prompt"),
            ({"hook_event_name": "Stop", "stop_hook_active": "false"}, "stop"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": "x", "session_id": "s" * 257}, "prompt"),
        )
        for payload, mode in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    if mode == "prompt":
                        whiteboard_hook.handle_prompt(payload, self.plugin_root)
                    else:
                        whiteboard_hook.handle_stop(payload, self.plugin_root)

    def test_prompt_hook_missing_prompt_id_uses_content_hash_fallback(self) -> None:
        prompt = "No event-level prompt id"
        result = whiteboard_hook.handle_prompt(
            {
                "session_id": "fallback",
                "cwd": str(self.root),
                "prompt": prompt,
                "hook_event_name": "UserPromptSubmit",
            },
            self.plugin_root,
        )
        expected = __import__("hashlib").sha256(prompt.encode()).hexdigest()[:24]
        pending = json.loads(
            self.root.joinpath(".whiteboard/sessions/fallback/pending.json").read_text()
        )
        self.assertEqual(pending["prompt_id"], expected)
        self.assertIn(expected, result["hookSpecificOutput"]["additionalContext"])

    def test_hook_main_always_emits_json(self) -> None:
        payload = {
            "session_id": "claude-session",
            "prompt_id": "prompt-42",
            "cwd": str(self.root),
            "prompt": "work",
            "hook_event_name": "UserPromptSubmit",
        }
        output = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with redirect_stdout(output):
                exit_code = whiteboard_hook.main(["prompt"])

        self.assertEqual(exit_code, 0)
        self.assertIsInstance(json.loads(output.getvalue()), dict)


if __name__ == "__main__":
    unittest.main()
