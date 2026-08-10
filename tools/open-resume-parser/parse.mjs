#!/usr/bin/env node
/**
 * CLI: node parse.mjs <path-to-resume.pdf>
 * Prints one JSON line: { ok: true, resume } | { ok: false, error }
 *
 * Registers tsx so vendored Open Resume TypeScript (lib/) can run under Node 18+.
 */
import { register } from "tsx/esm/api";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const unregister = register({
  tsconfig: path.join(__dirname, "tsconfig.json"),
});

try {
  await import(pathToFileURL(path.join(__dirname, "cli.ts")).href);
} finally {
  unregister();
}
