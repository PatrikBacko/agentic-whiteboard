#!/usr/bin/env node

import { build } from "esbuild";
import {
  lstat,
  mkdir,
  readFile,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, isAbsolute, parse, resolve, sep } from "node:path";
import { createRequire } from "node:module";
import { randomBytes } from "node:crypto";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const PLUGIN_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_NODE_MODULES = resolve(PLUGIN_ROOT, "node_modules");
const MAX_SOURCE_BYTES = 200_000;
const MAX_OUTPUT_BYTES = 1_000_000;
const ALLOWED_PACKAGES = new Set([
  "react",
  "react-dom",
  "react-dom/client",
  "react/jsx-runtime",
  "react/jsx-dev-runtime",
]);

function parseArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("usage: build-react.mjs --source App.jsx --output index.html --title Title");
    }
    if (result[key.slice(2)] !== undefined) throw new Error(`duplicate argument ${key}`);
    result[key.slice(2)] = value;
  }
  if (!result.source || !result.output || !result.title) {
    throw new Error("source, output, and title are required");
  }
  return result;
}

function escapeHtml(value) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function extractElementIds(source) {
  const tags = source.match(/<[^>]*\bdata-whiteboard-element(?:\s*=\s*(?:\{true\}|["']?true["']?))?[^>]*>/gs) ?? [];
  const ids = tags.map((tag) => {
    const match = tag.match(/\bid\s*=\s*["']([^"']+)["']/s);
    if (!match) throw new Error("Every React data-whiteboard-element requires a literal, stable id");
    return match[1];
  });
  if (ids.length > 15) throw new Error(`React artifact has ${ids.length} top-level elements; maximum is 15`);
  if (new Set(ids).size !== ids.length) throw new Error("React whiteboard element ids must be unique");
  return ids;
}

function beneath(path, parent) {
  return path === parent || path.startsWith(`${parent}${sep}`);
}

async function rejectSymlinkComponents(path) {
  let current = resolve(path);
  const root = parse(current).root;
  while (current !== root) {
    try {
      const info = await lstat(current);
      if (info.isSymbolicLink()) throw new Error(`path must not contain a symlink: ${current}`);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    current = dirname(current);
  }
}

function rejectDynamicBypasses(source) {
  const dynamicImport = /\bimport\s*\(\s*(?!["'])/s;
  const dynamicRequire = /\brequire\s*\(\s*(?!["'])/s;
  if (dynamicImport.test(source) || dynamicRequire.test(source)) {
    throw new Error("Dynamic import/require expressions are not allowed");
  }
}

function dependencyGuard(sourcePath) {
  return {
    name: "agentic-whiteboard-dependency-allowlist",
    setup(buildApi) {
      buildApi.onResolve({ filter: /.*/ }, async (args) => {
        if (args.kind === "entry-point") return null;
        const entryImporter = basename(args.importer || "") === "agentic-whiteboard-entry.jsx";
        if (entryImporter && args.path === sourcePath) return { path: sourcePath };
        if (entryImporter && ALLOWED_PACKAGES.has(args.path)) {
          return { path: require.resolve(args.path, { paths: [PLUGIN_ROOT] }) };
        }
        const importer = args.importer ? resolve(args.importer) : "";
        if (importer === sourcePath) {
          if (!ALLOWED_PACKAGES.has(args.path)) {
            throw new Error(`Import ${JSON.stringify(args.path)} is not allowed. Generated artifacts may import React only and must otherwise be self-contained.`);
          }
          return { path: require.resolve(args.path, { paths: [PLUGIN_ROOT] }) };
        }
        if (beneath(importer, PLUGIN_NODE_MODULES)) {
          const resolved = require.resolve(args.path, { paths: [dirname(importer), PLUGIN_ROOT] });
          const canonical = await realpath(resolved);
          if (!beneath(canonical, PLUGIN_NODE_MODULES)) {
            throw new Error(`Runtime dependency escaped plugin node_modules: ${args.path}`);
          }
          return { path: canonical };
        }
        throw new Error(`Import ${JSON.stringify(args.path)} is not allowed`);
      });
    },
  };
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const sourcePath = resolve(args.source);
  const outputPath = resolve(args.output);
  await rejectSymlinkComponents(sourcePath);
  await rejectSymlinkComponents(outputPath);
  const sourceInfo = await lstat(sourcePath);
  if (!sourceInfo.isFile() || sourceInfo.isSymbolicLink()) throw new Error("source must be a regular non-symlink file");
  const sourceCanonical = await realpath(sourcePath);
  const sourceDirectory = await realpath(dirname(sourcePath));
  if (dirname(sourceCanonical) !== sourceDirectory) throw new Error("source must be in the canonical artifact directory");
  if (sourceCanonical.split(sep).includes("node_modules")) throw new Error("generated source must not be under node_modules");
  await mkdir(dirname(outputPath), { recursive: true });
  const outputDirectory = await realpath(dirname(outputPath));
  if (sourceDirectory !== outputDirectory) throw new Error("source and output must be in the same canonical artifact directory");
  try {
    const outputInfo = await lstat(outputPath);
    if (!outputInfo.isFile() || outputInfo.isSymbolicLink()) throw new Error("output must be a regular non-symlink file");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  const source = await readFile(sourceCanonical, "utf8");
  if (Buffer.byteLength(source, "utf8") > MAX_SOURCE_BYTES) throw new Error(`Generated React source exceeds ${MAX_SOURCE_BYTES} bytes`);
  rejectDynamicBypasses(source);
  const elementIds = extractElementIds(source);
  const elementMetadata = elementIds.map((id) => `  <meta name="agentic-whiteboard-element" content="${escapeHtml(id)}">`).join("\n");
  const entry = `import React from "react"; import { createRoot } from "react-dom/client"; import App from ${JSON.stringify(sourceCanonical)}; createRoot(document.getElementById("root")).render(React.createElement(App));`;
  const result = await build({
    stdin: { contents: entry, loader: "jsx", resolveDir: PLUGIN_ROOT, sourcefile: "agentic-whiteboard-entry.jsx" },
    absWorkingDir: PLUGIN_ROOT,
    bundle: true,
    write: false,
    minify: true,
    platform: "browser",
    format: "iife",
    target: ["es2020"],
    jsx: "automatic",
    plugins: [dependencyGuard(sourceCanonical)],
    logLevel: "silent",
  });
  const javascript = result.outputFiles[0]?.text;
  if (!javascript) throw new Error("esbuild produced no JavaScript output");
  const html = `<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; media-src data: blob:; object-src 'none'; base-uri 'none'; form-action 'none'">\n${elementMetadata}\n  <title>${escapeHtml(args.title)}</title>\n  <style>html,body,#root{min-height:100%;margin:0}*{box-sizing:border-box}</style>\n</head>\n<body>\n  <div id="root"></div>\n  <script>${javascript.replaceAll("</script", "<\\/script")}</script>\n</body>\n</html>\n`;
  const size = Buffer.byteLength(html, "utf8");
  const testLimit = Number.parseInt(process.env.AGENTIC_WHITEBOARD_TEST_MAX_OUTPUT_BYTES ?? "", 10);
  const outputLimit = Number.isSafeInteger(testLimit) && testLimit > 0
    ? Math.min(MAX_OUTPUT_BYTES, testLimit)
    : MAX_OUTPUT_BYTES;
  if (size > outputLimit) throw new Error(`Generated artifact is ${size} bytes; maximum is ${outputLimit} bytes`);
  const temporary = resolve(outputDirectory, `.${basename(outputPath)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  try {
    await writeFile(temporary, html, { encoding: "utf8", flag: "wx", mode: 0o600 });
    await rename(temporary, outputPath);
  } finally {
    await rm(temporary, { force: true });
  }
  process.stdout.write(`${outputPath}\n`);
}

main().catch((error) => {
  process.stderr.write(`build-react: ${error.message}\n`);
  process.exitCode = 1;
});
