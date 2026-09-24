// Black-box checks for merge-seed-catalog.mjs (node:test, subprocess only).
// Run: node --test test_merge_seed_catalog.mjs
// Pins: the kill path exits 3 under the ceiling, range validation exits 2,
// and a merge on fixtures adds exactly the new-hash new-id entries.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "merge-seed-catalog.mjs");
const run = (argv) => spawnSync(process.execPath, [SCRIPT, ...argv], { encoding: "utf8", timeout: 60000 });

// A fake repo whose identityHash is the joined pieces, enough to exercise the merge rules.
function fakeRepo() {
  const dir = mkdtempSync(join(tmpdir(), "merge-seed-"));
  mkdirSync(join(dir, "scripts"));
  writeFileSync(join(dir, "scripts", "prompt-index.mjs"),
    'export const identityHash = (p) => (p.pieces ?? []).join("\\u0000");\n');
  return dir;
}
const cat = (entries) => ({ version: "x", prompts: entries.map(([id, text]) => ({ id, pieces: [text], identifiers: [] })) });

test("kill path exits 3 before the probe ends", () => {
  const t0 = performance.now();
  const r = run(["--watchdog-probe", "10", "--max-seconds", "1", "a", "b", "c", "d"]);
  assert.equal(r.status, 3);
  assert.ok(performance.now() - t0 < 5000, "ceiling fired late");
});

test("range validation exits 2 with a message", () => {
  for (const argv of [["--max-seconds", "0"], ["--max-seconds", "-5"], ["--watchdog-probe", "-1"]]) {
    const r = run([...argv, "a", "b", "c", "d"]);
    assert.equal(r.status, 2);
    assert.ok(r.stderr.length > 0);
  }
});

test("missing positionals exit 2", () => {
  assert.equal(run([]).status, 2);
});

test("merge adds only entries whose hash and id are both new", () => {
  const repo = fakeRepo();
  const ours = join(repo, "ours.json"), up = join(repo, "up.json"), out = join(repo, "out.json");
  writeFileSync(ours, JSON.stringify(cat([["a", "text-a"], ["b", "text-b"]])));
  writeFileSync(up, JSON.stringify(cat([
    ["a", "text-a"],      // same hash: skipped
    ["b", "text-b2"],     // id collision: skipped (relabel reuses the removed id)
    ["c", "text-c"],      // new: added
  ])));
  const r = run([repo, ours, up, out]);
  assert.equal(r.status, 0, r.stderr);
  const merged = JSON.parse(readFileSync(out, "utf8"));
  assert.deepEqual(merged.prompts.map((p) => p.id), ["a", "b", "c"]);
  assert.match(r.stdout, /added=1 skipped: same-hash=1 id-collision=1/);
});

test("nothing added exits 1", () => {
  const repo = fakeRepo();
  const ours = join(repo, "ours.json"), up = join(repo, "up.json"), out = join(repo, "out.json");
  writeFileSync(ours, JSON.stringify(cat([["a", "text-a"]])));
  writeFileSync(up, JSON.stringify(cat([["a", "text-a"]])));
  assert.equal(run([repo, ours, up, out]).status, 1);
});
