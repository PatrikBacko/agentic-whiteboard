import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class PluginContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[1]

    def test_manifest_declares_installable_plugin(self) -> None:
        manifest = json.loads(
            (self.repo / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "agentic-whiteboard")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertIn("whiteboard", manifest["description"].lower())

    def test_marketplace_catalog_supports_gui_installation(self) -> None:
        marketplace = json.loads(
            (self.repo / ".claude-plugin/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "patrikbacko-plugins")
        entries = marketplace["plugins"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "agentic-whiteboard")
        self.assertEqual(entries[0]["source"], "./")

    def test_hooks_register_prompt_and_stop_lifecycle(self) -> None:
        hooks = json.loads(
            (self.repo / "hooks/hooks.json").read_text(encoding="utf-8")
        )["hooks"]
        self.assertEqual(set(hooks), {"UserPromptSubmit", "Stop"})
        for event, mode in (("UserPromptSubmit", "prompt"), ("Stop", "stop")):
            handlers = hooks[event][0]["hooks"]
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0]["type"], "command")
            self.assertIn("${CLAUDE_PLUGIN_ROOT}", handlers[0]["command"])
            self.assertTrue(handlers[0]["command"].endswith(f" {mode}"))

    def test_skill_describes_versioned_visual_companion(self) -> None:
        skill = (self.repo / "skills/whiteboard/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        for requirement in (
            "after every meaningful turn",
            ".whiteboard/sessions/",
            "data-whiteboard-element",
            "15",
            "self-contained",
            "prepare",
            "finalize",
        ):
            self.assertIn(requirement, skill)

    def test_codex_instructions_are_portable_without_hooks(self) -> None:
        instructions = (
            self.repo / "integrations/codex/AGENTS.md"
        ).read_text(encoding="utf-8")
        self.assertIn("visual companion", instructions.lower())
        self.assertIn("WHITEBOARD_SESSION_ID", instructions)
        self.assertIn("prepare", instructions)
        self.assertIn("finalize", instructions)
        self.assertIn("15", instructions)
        self.assertLess(instructions.index("2. Create the pending turn"), instructions.index("3. Archive"))
        self.assertLess(instructions.index("3. Archive"), instructions.index("4. Update"))
        self.assertLess(instructions.index("4. Update"), instructions.index("7. Finalize"))

    def test_claude_fallback_uses_prompt_before_prepare(self) -> None:
        instructions = (self.repo / "integrations/claude/CLAUDE.md").read_text()
        prompt_command = "whiteboard.py prompt"
        prepare_command = "whiteboard.py prepare"
        self.assertIn(prompt_command, instructions)
        self.assertLess(instructions.index(prompt_command), instructions.index(prepare_command))

    def test_claude_hook_process_runs_from_outside_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            payload = {
                "session_id": "integration-session",
                "prompt_id": "prompt-1",
                "cwd": str(target),
                "prompt": "Design the feature",
                "hook_event_name": "UserPromptSubmit",
            }
            result = subprocess.run(
                [
                    "python3",
                    str(self.repo / "scripts/whiteboard_hook.py"),
                    "prompt",
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                cwd=target,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertIn("additionalContext", output["hookSpecificOutput"])
            self.assertTrue(
                target.joinpath(
                    ".whiteboard/sessions/integration-session/pending.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
