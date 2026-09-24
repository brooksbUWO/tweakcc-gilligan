#!/usr/bin/env node
// Build a merged carry-forward seed for unnerfcc's gen-catalog: every entry of
// OUR previous catalog (ids win), plus the entries of an UPSTREAM catalog for
// the target version whose content hash and id are both absent from ours.
// gen-catalog carries a seed entry only on an exact identity-hash match, so an
// upstream entry names a prompt only when the target binary contains that
// exact text. Prompts that no seed names remain anonymous for relabel.
//
// Usage:
//   node merge-seed-catalog.mjs <repo> <ours.json> <upstream.json> <out.json> [--max-seconds N] [--watchdog-probe N]
//
//   <repo>          the unnerfcc checkout; scripts/prompt-index.mjs supplies identityHash
//   <ours.json>     the fork's previous catalog, for example data/prompts/prompts-2.1.235.json
//   <upstream.json> the upstream catalog for the target, for example the output of
//                   `git show <upstream sync commit>:data/prompts/prompts-<target>.json`
//   <out.json>      the merged seed; pass it to `./upgrade.sh --seed <out.json>`
//
// Substitute for another project: any two catalogs with a top-level
// `prompts[]` array whose entries carry `id`, `pieces`, and `identifiers`,
// and an identityHash(entry) function importable from <repo>.
//
// Exit codes: 0 merged, 1 nothing added or bad catalog shape, 2 usage error,
// 3 terminated at the wall-clock ceiling (recipe-skill-script-hardening).
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const USAGE = "usage: node merge-seed-catalog.mjs <repo> <ours.json> <upstream.json> <out.json> [--max-seconds N] [--watchdog-probe N]";
const args = process.argv.slice(2);
const positional = [];
let maxSeconds = 120, probeSeconds = 0;
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--max-seconds") maxSeconds = Number(args[++i]);
  else if (args[i] === "--watchdog-probe") probeSeconds = Number(args[++i]);
  else if (args[i].startsWith("--")) { console.error(`unknown flag: ${args[i]}\n${USAGE}`); process.exit(2); }
  else positional.push(args[i]);
}
if (!(maxSeconds > 0)) { console.error("error: --max-seconds must be greater than 0"); process.exit(2); }
if (!(probeSeconds >= 0)) { console.error("error: --watchdog-probe must be at least 0"); process.exit(2); }
// Deterministic termination: the ceiling fires exit 3; unref() keeps the timer
// from holding the event loop open after a normal fast exit.
setTimeout(() => process.exit(3), maxSeconds * 1000).unref();
if (probeSeconds > 0) await new Promise((r) => setTimeout(r, probeSeconds * 1000));

const [repo, oursPath, upPath, outPath] = positional;
if (!repo || !oursPath || !upPath || !outPath) { console.error(USAGE); process.exit(2); }

const { identityHash } = await import(pathToFileURL(join(repo, "scripts", "prompt-index.mjs")).href);
const ours = JSON.parse(readFileSync(oursPath, "utf8"));
const up = JSON.parse(readFileSync(upPath, "utf8"));
if (!Array.isArray(ours.prompts) || !Array.isArray(up.prompts)) {
  console.error("both catalogs must have a top-level prompts[] array");
  process.exit(1);
}

const ourIds = new Set(ours.prompts.map((p) => p.id).filter(Boolean));
const seenHash = new Set(ours.prompts.map(identityHash));
const merged = { version: ours.version, prompts: [...ours.prompts] };
let added = 0, skippedId = 0, skippedHash = 0, skippedAnon = 0;
for (const p of up.prompts) {
  if (!p.id) { skippedAnon++; continue; }
  const h = identityHash(p);
  if (seenHash.has(h)) { skippedHash++; continue; }
  if (ourIds.has(p.id)) { skippedId++; continue; }
  seenHash.add(h); ourIds.add(p.id);
  merged.prompts.push({ ...p });
  added++;
}
writeFileSync(outPath, JSON.stringify(merged, null, 2) + "\n");
console.log(`ours=${ours.prompts.length} upstream=${up.prompts.length} added=${added} ` +
  `skipped: same-hash=${skippedHash} id-collision=${skippedId} anonymous=${skippedAnon} -> ${merged.prompts.length} entries`);
if (added === 0) { console.error("nothing added: the upstream catalog contributes no new entries"); process.exit(1); }
