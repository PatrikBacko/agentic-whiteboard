import subprocess
import os
import tempfile
import unittest
from pathlib import Path


class ReactBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = Path(__file__).resolve().parents[1]
        self.source = self.root / "App.jsx"
        self.output = self.root / "index.html"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_builder(
        self,
        *,
        cwd: Path | None = None,
        source: Path | None = None,
        output: Path | None = None,
        max_output: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                str(self.repo / "scripts" / "build-react.mjs"),
                "--source",
                str(source or self.source),
                "--output",
                str(output or self.output),
                "--title",
                "Interactive plan",
            ],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                **(
                    {"AGENTIC_WHITEBOARD_TEST_MAX_OUTPUT_BYTES": str(max_output)}
                    if max_output is not None
                    else {}
                ),
            },
        )

    def test_builds_self_contained_react_artifact_from_unrelated_cwd(self) -> None:
        self.source.write_text(
            """import React, { useState } from 'react';
export default function App() {
  const [count, setCount] = useState(0);
  return <main id="counter" data-whiteboard-element>
    <button onClick={() => setCount(count + 1)}>Count {count}</button>
  </main>;
}
""",
            encoding="utf-8",
        )
        attacker = self.root / "node_modules" / "react"
        attacker.mkdir(parents=True)
        attacker.joinpath("package.json").write_text(
            '{"name":"react","main":"index.js"}', encoding="utf-8"
        )
        attacker.joinpath("index.js").write_text(
            "throw new Error('attacker runtime loaded')", encoding="utf-8"
        )

        result = self.run_builder(cwd=self.root)

        self.assertEqual(result.returncode, 0, result.stderr)
        html = self.output.read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("Interactive plan", html)
        self.assertIn("data-whiteboard-element", html)
        self.assertIn(
            '<meta name="agentic-whiteboard-element" content="counter">', html
        )
        self.assertNotIn("attacker runtime loaded", html)
        self.assertNotIn('<script src="http', html)
        self.assertNotIn('<link href="http', html)

    def test_rejects_packages_outside_allowlist(self) -> None:
        self.source.write_text(
            """import axios from 'axios';
export default function App() { return <main>{String(axios)}</main>; }
""",
            encoding="utf-8",
        )
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_oversized_source(self) -> None:
        self.source.write_text(" " * 200_001, encoding="utf-8")
        result = self.run_builder()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("200000", result.stderr)

    def test_rejects_relative_imports_and_dynamic_import_bypasses(self) -> None:
        self.root.joinpath("helper.js").write_text("export default 1")
        sources = (
            "import value from './helper.js'; export default () => <main>{value}</main>",
            "const name = './helper.js'; import(name); export default () => <main />",
            "const name = 'axios'; require(name); export default () => <main />",
        )
        for source in sources:
            with self.subTest(source=source):
                self.source.write_text(source)
                self.output.unlink(missing_ok=True)
                result = self.run_builder()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())

    def test_rejects_paths_outside_same_canonical_artifact_directory(self) -> None:
        artifact = self.root / "artifact"
        artifact.mkdir()
        source = artifact / "App.jsx"
        source.write_text("export default () => <main />")
        result = self.run_builder(source=source, output=self.output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same canonical artifact directory", result.stderr)

    def test_rejects_symlinks_and_source_beneath_node_modules(self) -> None:
        real = self.root / "Real.jsx"
        real.write_text("export default () => <main />")
        linked = self.root / "Linked.jsx"
        linked.symlink_to(real)
        result = self.run_builder(source=linked)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)

        nested = self.root / "node_modules" / "evil"
        nested.mkdir(parents=True, exist_ok=True)
        source = nested / "App.jsx"
        source.write_text("export default () => <main />")
        result = self.run_builder(source=source, output=nested / "index.html")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("node_modules", result.stderr)

    def test_rejects_output_over_bounded_limit(self) -> None:
        self.source.write_text("export default () => <main>bounded</main>")
        result = self.run_builder(max_output=1_000)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maximum is 1000", result.stderr)
        self.assertFalse(self.output.exists())

        builder = (self.repo / "scripts" / "build-react.mjs").read_text()
        self.assertIn("const MAX_OUTPUT_BYTES = 1_000_000", builder)


if __name__ == "__main__":
    unittest.main()
