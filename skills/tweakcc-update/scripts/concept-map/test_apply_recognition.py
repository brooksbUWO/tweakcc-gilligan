"""Black-box tests for apply_recognition.py (stdlib unittest, subprocess only).

Each test builds a small store, a map, a claims table, and a transcript.
Each set lives in a temp directory. Each test runs the script as a
subprocess. It asserts the exit code, the standard output, and the files
the script writes.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apply_recognition.py")


def _find_workspace():
    """Return the .claude/workspace directory of the project that holds
    this test file. Walk up from HERE until a parent holds
    .claude/workspace/prompt-store. If no parent holds one, return
    None. A copy of the skill outside the project has no such parent."""
    d = HERE
    while True:
        candidate = os.path.join(d, ".claude", "workspace")
        if os.path.isdir(os.path.join(candidate, "prompt-store")):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


WORKSPACE = _find_workspace()


def _newest_recognition(directory):
    """Return the recognition-<version>.json file with the highest version
    in directory. Compare the version as numbers, so 2.1.1000 is higher
    than 2.1.999."""
    found = []
    for name in os.listdir(directory):
        m = re.fullmatch(r"recognition-(\d+(?:\.\d+)*)\.json", name)
        if m:
            found.append((tuple(int(x) for x in m.group(1).split(".")), name))
    if not found:
        raise FileNotFoundError("no recognition-<version>.json in " + directory)
    return os.path.join(directory, max(found)[1])


def run(*args, timeout=60):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8")


def run_parallel_cases(case_fns, max_workers=16):
    """Run each zero-argument callable in case_fns on its own worker
    thread. Return their results in the same order case_fns lists them.
    Each callable does all of its own file I/O and subprocess work. It
    must touch no shared state. Each mutation walk gives every case its
    own temp directory for this reason. This function raises nothing
    itself. A callable's own exception surfaces from the corresponding
    result via Future.result(), at the call site, on the main thread.
    The caller then decides how to report it."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fn) for fn in case_fns]
        return [f.result() for f in futures]


def trow(file="a.md", marker="m", state="carries-defect", provenance="recognition-first",
         line=2, quote="keep it short."):
    """A transcript row, defaulted to the a.md fixture line 2 and its stock
    quote. Override only the fields a test needs to vary."""
    return dict(file=file, marker=marker, state=state, provenance=provenance,
                line=line, quote=quote)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = os.path.join(self.d, "store")
        os.makedirs(self.store, exist_ok=True)
        self._write_prompt("a.md", "line one\nkeep it short.\nline three\n")
        self._write_prompt("b.md", "line one\nline two\nplan review here\n")
        self._write_prompt("c.md", "line one\nline two\nline three\n")

    def _write_prompt(self, name, body):
        with open(os.path.join(self.store, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(body)

    def _write_json(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)
            f.write("\n")
        return p

    def base_map(self, extra_concepts=None):
        row = dict(file="a.md", marker="old marker", fix_kind="body-rewrite",
                   provenance="recognition-first", fix_present="false")
        concept = dict(concept_id="k1", concept="Kept concept one.", tag="diffuse",
                       governed_files=[row])
        concepts = [concept] + (extra_concepts or [])
        return dict(schema="concept-to-prompt-file-map/1.1", store_dir="store",
                    concepts=concepts, reconciliation=dict(dropped_rows=[]))

    def base_claims(self):
        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        return dict(claims=[c1], decisions=[])

    def base_transcript(self, rows=None, dropped=None, extra_blocks=None):
        block = dict(concept_id="k1", notes_append="note text",
                     rows=rows if rows is not None else [],
                     dropped=dropped if dropped is not None else [])
        return dict(
            schema="recognition-transcript/1.0",
            binary=dict(claude_version="2.1.280 (Claude Code)"),
            session=dict(session_id="s1", date="2026-09-23", vantage="main"),
            store_dir="store",
            seed_store_dir="store",
            concepts=[block] + (extra_blocks or []),
            seed_dropped=[],
        )

    def one_kept_row_transcript(self):
        """The common fixture: k1's a.md row still carries-defect, unchanged."""
        return self.base_transcript(rows=[trow()])

    def paths(self, cmap, claims, transcript):
        return (self._write_json("map.json", cmap),
                self._write_json("claims.json", claims),
                self._write_json("transcript.json", transcript))

    def _read_json(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _read_text(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _read_bytes(self, path):
        with open(path, "rb") as f:
            return f.read()


class KeptRowTests(Base):
    def test_kept_row_gets_state_and_updates_marker(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(marker="new marker")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("APPLIED"), r.stdout)
        out_map = self._read_json(m)
        row = out_map["concepts"][0]["governed_files"][0]
        self.assertEqual(row["marker"], "new marker")
        self.assertEqual(row["state"], "carries-defect")
        self.assertEqual(row["fix_kind"], "body-rewrite")
        self.assertEqual(row["fix_present"], "false")
        self.assertEqual(list(row.keys())[-1], "state")

    def test_kept_conforms_row_forces_not_applicable(self):
        cmap = self.base_map()
        cmap["concepts"][0]["governed_files"][0]["fix_kind"] = "body-invariant"
        cmap["concepts"][0]["governed_files"][0]["fix_present"] = "not-applicable"
        transcript = self.base_transcript(rows=[trow(marker="conforms marker", state="conforms",
                                                      provenance="search-extended")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self._read_json(m)["concepts"][0]["governed_files"][0]
        self.assertEqual(row["fix_kind"], "body-invariant")
        self.assertEqual(row["fix_present"], "not-applicable")

    def test_carries_defect_on_body_invariant_becomes_body_rewrite(self):
        cmap = self.base_map()
        cmap["concepts"][0]["governed_files"][0]["fix_kind"] = "body-invariant"
        cmap["concepts"][0]["governed_files"][0]["fix_present"] = "not-applicable"
        transcript = self.base_transcript(rows=[trow(marker="now defective")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        row = self._read_json(m)["concepts"][0]["governed_files"][0]
        self.assertEqual(row["fix_kind"], "body-rewrite")
        self.assertEqual(row["fix_present"], "false")


class NewRowTests(Base):
    def test_new_row_gets_full_field_set(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[
            trow(),
            trow(file="b.md", marker="plan needs a field", line=3, quote="plan review here"),
        ])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self._read_json(m)["concepts"][0]["governed_files"]
        new_row = [x for x in rows if x["file"] == "b.md"][0]
        self.assertEqual(new_row["fix_kind"], "body-rewrite")
        self.assertEqual(new_row["fix_present"], "false")
        self.assertEqual(new_row["delivery_path"], "binary-splice")
        self.assertEqual(new_row["provenance"], "recognition-first")
        self.assertEqual(new_row["state"], "carries-defect")
        self.assertIn("main-session recognition 2026-09-23 on 2.1.280", new_row["verified"])
        self.assertEqual(rows[-1]["file"], "b.md")

    def test_new_conforms_row_gets_body_invariant(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[
            trow(),
            trow(file="c.md", marker="already fine", state="conforms", line=1, quote="line one"),
        ])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self._read_json(m)["concepts"][0]["governed_files"]
        new_row = [x for x in rows if x["file"] == "c.md"][0]
        self.assertEqual(new_row["fix_kind"], "body-invariant")
        self.assertEqual(new_row["fix_present"], "not-applicable")


class DropTests(Base):
    def test_dropped_prior_row_leaves_block_and_joins_dropped_rows(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[],
                                          dropped=[dict(file="a.md", reason="out of surface")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out_map = self._read_json(m)
        self.assertEqual(out_map["concepts"][0]["governed_files"], [])
        dropped = out_map["reconciliation"]["dropped_rows"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["concept"], "k1")
        self.assertEqual(dropped[0]["file"], "a.md")
        self.assertEqual(dropped[0]["reason"], "out of surface")

    def test_unaccounted_prior_row_fails_with_no_write(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[], dropped=[])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_text(m)
        before_claims = self._read_text(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self._read_text(m), before_map)
        self.assertEqual(self._read_text(c), before_claims)

    def test_empty_reason_drop_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[], dropped=[dict(file="a.md", reason="")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


class ClaimsTests(Base):
    def test_row_gets_one_claim_and_one_decision(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out_claims = self._read_json(c)
        row_claims = [x for x in out_claims["claims"] if x["id"] != "c1"]
        self.assertEqual(len(row_claims), 1)
        self.assertEqual(row_claims[0]["anchor"], "store/a.md:2")
        self.assertEqual(row_claims[0]["quote"], "keep it short.")
        decisions = out_claims["decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["concept"], "k1")
        self.assertEqual(decisions[0]["file"], "a.md")
        self.assertEqual(decisions[0]["state"], "carries-defect")
        self.assertIn("c1", decisions[0]["depends_on"])
        self.assertIn(row_claims[0]["id"], decisions[0]["depends_on"])

    def test_two_rows_same_anchor_and_quote_share_one_claim(self):
        concept_two = dict(concept_id="k2", concept="Kept concept two.", tag="diffuse",
                           governed_files=[])
        cmap = self.base_map(extra_concepts=[concept_two])
        block_two = dict(concept_id="k2", notes_append="n2",
                        rows=[trow(marker="m2")], dropped=[])
        transcript = self.base_transcript(rows=[trow(marker="m1")], extra_blocks=[block_two])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out_claims = self._read_json(c)
        row_claims = [x for x in out_claims["claims"] if x["id"] != "c1"]
        self.assertEqual(len(row_claims), 1)
        self.assertEqual(len(out_claims["decisions"]), 2)

    def test_check_claims_passes_on_written_table(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)


class IdempotencyTests(Base):
    def test_second_run_prints_no_change(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r1 = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)


class InputValidationTests(Base):
    def test_bad_state_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(state="unknown")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_empty_quote_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(quote="")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_line_below_one_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(line=0)])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_file_absent_from_store_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(file="missing.md", line=1, quote="x")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_missing_file_gives_exit_2(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", os.path.join(self.d, "nope.json"), "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 2)

    def test_bad_json_gives_exit_2(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        with open(t, "w", encoding="utf-8") as f:
            f.write("{not json")
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 2)

    def test_max_seconds_zero_gives_exit_2(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--max-seconds", "0")
        self.assertEqual(r.returncode, 2)

    def test_watchdog_kills(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        t0 = time.monotonic()
        r = run("--transcript", t, "--map", m, "--claims", c,
                 "--watchdog-probe", "10", "--max-seconds", "1")
        self.assertEqual(r.returncode, 3)
        self.assertLess(time.monotonic() - t0, 5)


class NoMapBlockTests(Base):
    def test_new_block_with_no_fields_fails(self):
        cmap = self.base_map()
        new_block = dict(concept_id="k-new", notes_append="n", rows=[], dropped=[])
        transcript = self.base_transcript(rows=[trow()], extra_blocks=[new_block])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


class NewBlockTests(Base):
    def test_new_block_gets_created_with_field_order(self):
        cmap = self.base_map()
        new_block = dict(
            concept_id="k-new", concept="New concept sentence.", tag="diffuse",
            markers=["m1"], notes_append="fresh notes",
            rows=[trow(file="b.md", marker="plan needs a field", line=3,
                       quote="plan review here")],
            dropped=[],
        )
        transcript = self.base_transcript(rows=[trow()], extra_blocks=[new_block])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        written = [x for x in self._read_json(m)["concepts"]
                  if x["concept_id"] == "k-new"][0]
        self.assertEqual(list(written.keys()),
                          ["concept_id", "concept", "tag", "markers",
                           "coverage_method", "notes", "governed_files"])
        self.assertEqual(written["coverage_method"], "read-verified")
        self.assertEqual(written["notes"], "fresh notes")
        self.assertEqual(len(written["governed_files"]), 1)

    def test_empty_new_block_keeps_verified_zero_note(self):
        cmap = self.base_map()
        new_block = dict(
            concept_id="k-empty", concept="Empty concept sentence.", tag="diffuse",
            markers=["m1"],
            notes_append="governed: 0 (verified: no prompt in this store matches)",
            rows=[], dropped=[],
        )
        transcript = self.base_transcript(rows=[trow()], extra_blocks=[new_block])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        written = [x for x in self._read_json(m)["concepts"]
                  if x["concept_id"] == "k-empty"][0]
        self.assertEqual(written["governed_files"], [])
        self.assertIn("governed: 0", written["notes"])
        self.assertIn("verified", written["notes"])


def seed_entry(seed_id="seed-1", verdict="prompt", governed=False, concept_id=None,
               catalog_recommendation="re-add"):
    return dict(
        id=seed_id, r0002_file="old.md", r0002_line=4,
        needle="the needle text", binary_module="chunk.js",
        binary_command="python needle_excerpt.py file 'the needle text'",
        binary_excerpt="...the needle text follows here...",
        verdict=verdict, governed=governed, concept_id=concept_id,
        catalog_recommendation=catalog_recommendation, reason="one-line reason",
    )


class SeedTests(Base):
    def test_seed_entry_gets_two_claims_and_one_decision(self):
        self._write_prompt("old.md", "l1\nl2\nl3\nthe needle text\nl5\n")
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        transcript["seed_dropped"] = [seed_entry()]
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out_claims = self._read_json(c)
        seed_decisions = [d for d in out_claims["decisions"] if d.get("seed_id")]
        self.assertEqual(len(seed_decisions), 1)
        self.assertEqual(seed_decisions[0]["seed_id"], "seed-1")
        self.assertEqual(seed_decisions[0]["verdict"], "prompt")
        binary_id = [x for x in out_claims["claims"]
                    if x["anchor"] == "cmd: claude --version"][0]["id"]
        self.assertIn(binary_id, seed_decisions[0]["depends_on"])
        out_map = self._read_json(m)
        verdicts = out_map["reconciliation"]["seed_dropped_verdicts"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["id"], "seed-1")
        self.assertEqual(verdicts[0]["anchor"],
                          f"cmd: {transcript['seed_dropped'][0]['binary_command']}")
        self.assertEqual(verdicts[0]["quote"], transcript["seed_dropped"][0]["binary_excerpt"])
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)

    def test_concepts_flag_skips_seed_entries(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        transcript["seed_dropped"] = [seed_entry()]
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out_map = self._read_json(m)
        self.assertNotIn("seed_dropped_verdicts", out_map.get("reconciliation", {}))

    def test_seed_missing_verdict_field_fails(self):
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[])
        entry = seed_entry()
        del entry["reason"]
        transcript["seed_dropped"] = [entry]
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


class FullRunTests(Base):
    def test_second_full_run_prints_no_change(self):
        self._write_prompt("old.md", "l1\nl2\nl3\nthe needle text\nl5\n")
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        transcript["seed_dropped"] = [seed_entry()]
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r1 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)
        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)


class AddendumRoundOneTests(Base):
    """Round 1 addendum items A1 to A7. See round1-addendum.txt for the
    contract text. Each test checks only the exit code, the output, and
    the file bytes. No test assumes how the script is implemented."""

    def test_addendum_a1_line_beyond_file_length_fails_and_names_file(self):
        # a.md has 3 lines. A row that claims line 4 is out of range.
        cmap = self.base_map()
        transcript = self.base_transcript(rows=[trow(line=4)])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("a.md", r.stdout)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a1b_quote_outside_window_fails_and_inside_window_applies(self):
        # A 7-line file. line 4 sits in the middle. The window for line 4
        # is lines 2 to 6 (line-2 to line+2), joined with single spaces.
        # A quote that only exists on line 1 or line 7 falls outside that
        # window and must fail. A quote from line 2 (the window's own
        # edge) must still apply.
        self._write_prompt(
            "wide.md",
            "unique row one\nrow two text\nrow three text\ntarget line four\n"
            "row five text\nrow six text\nunique row seven\n",
        )
        cmap = self.base_map()
        cmap["concepts"][0]["governed_files"][0]["file"] = "wide.md"

        # Negative case: quote from line 1, row claims line 4.
        bad_row = trow(file="wide.md", line=4, quote="unique row one")
        transcript = self.base_transcript(rows=[bad_row])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

        # Positive case: quote from line 2, still inside the window.
        good_row = trow(file="wide.md", line=4, quote="row two text")
        transcript2 = self.base_transcript(rows=[good_row])
        m2, c2, t2 = self.paths(cmap, self.base_claims(), transcript2)
        r2 = run("--transcript", t2, "--map", m2, "--claims", c2, "--concepts", "k1")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertTrue(r2.stdout.startswith("APPLIED"), r2.stdout)

    def test_addendum_a2_missing_claude_version_fails(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        del transcript["binary"]["claude_version"]
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a2_empty_claude_version_fails(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        transcript["binary"]["claude_version"] = ""
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_addendum_a3_absolute_file_path_fails_even_though_file_is_real(self):
        cmap = self.base_map()
        abs_path = os.path.join(self.store, "a.md")
        self.assertTrue(os.path.isfile(abs_path))
        # The map's own row uses the same bad file value as the transcript
        # row, so the only difference under test is the file path shape.
        cmap["concepts"][0]["governed_files"][0]["file"] = abs_path
        transcript = self.base_transcript(rows=[trow(file=abs_path, line=2, quote="keep it short.")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a3_file_holding_slash_fails_even_though_file_is_real(self):
        os.makedirs(os.path.join(self.store, "sub"), exist_ok=True)
        with open(os.path.join(self.store, "sub", "a.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("line one\nkeep it short.\nline three\n")
        cmap = self.base_map()
        cmap["concepts"][0]["governed_files"][0]["file"] = "sub/a.md"
        transcript = self.base_transcript(rows=[trow(file="sub/a.md", line=2, quote="keep it short.")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a3_file_holding_backslash_fails_even_though_file_is_real(self):
        os.makedirs(os.path.join(self.store, "sub"), exist_ok=True)
        with open(os.path.join(self.store, "sub", "a.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("line one\nkeep it short.\nline three\n")
        cmap = self.base_map()
        cmap["concepts"][0]["governed_files"][0]["file"] = "sub\\a.md"
        transcript = self.base_transcript(rows=[trow(file="sub\\a.md", line=2, quote="keep it short.")])
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_addendum_a4_transcript_root_not_object_fails_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        with open(t, "w", encoding="utf-8") as f:
            f.write("[]")
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a4_map_root_not_object_fails_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_claims = self._read_bytes(c)
        with open(m, "w", encoding="utf-8") as f:
            f.write("[]")
        before_map = self._read_bytes(m)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a4_claims_root_not_object_fails_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        with open(c, "w", encoding="utf-8") as f:
            f.write("[]")
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a5_seed_entry_missing_required_field_fails(self):
        cmap = self.base_map()
        for field in ("id", "binary_command", "binary_excerpt"):
            with self.subTest(field=field):
                transcript = self.one_kept_row_transcript()
                entry = seed_entry()
                del entry[field]
                transcript["seed_dropped"] = [entry]
                m, c, t = self.paths(cmap, self.base_claims(), transcript)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a5_seed_entry_empty_required_field_fails(self):
        cmap = self.base_map()
        for field in ("id", "binary_command", "binary_excerpt"):
            with self.subTest(field=field):
                transcript = self.one_kept_row_transcript()
                entry = seed_entry()
                entry[field] = ""
                transcript["seed_dropped"] = [entry]
                m, c, t = self.paths(cmap, self.base_claims(), transcript)
                r = run("--transcript", t, "--map", m, "--claims", c)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_addendum_a6_seed_entry_non_string_field_fails(self):
        cmap = self.base_map()
        for field in ("verdict", "catalog_recommendation", "reason"):
            for bad_value in (0, False):
                with self.subTest(field=field, bad_value=bad_value):
                    transcript = self.one_kept_row_transcript()
                    entry = seed_entry()
                    entry[field] = bad_value
                    transcript["seed_dropped"] = [entry]
                    m, c, t = self.paths(cmap, self.base_claims(), transcript)
                    before_map = self._read_bytes(m)
                    before_claims = self._read_bytes(c)
                    r = run("--transcript", t, "--map", m, "--claims", c)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertEqual(self._read_bytes(m), before_map)
                    self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_a7_no_relative_path_to_store_fails_cleanly(self):
        # A single-drive machine always has a relative path. Skip there.
        script_drive = os.path.splitdrive(SCRIPT)[0].lower()
        temp_drive = os.path.splitdrive(tempfile.gettempdir())[0].lower()
        if script_drive == temp_drive:
            self.skipTest("script and temp directory share one drive letter")

        if WORKSPACE is None:
            self.skipTest("no project workspace above this test file")
        runs_root = os.path.join(WORKSPACE, "runs")
        runs_root = os.path.abspath(runs_root)
        self.assertEqual(os.path.splitdrive(runs_root)[0].lower(), script_drive)
        self.assertTrue(runs_root.endswith(os.path.join("workspace", "runs")), runs_root)
        os.makedirs(runs_root, exist_ok=True)
        scratch_root = tempfile.mkdtemp(prefix="addendum-a7-store-", dir=runs_root)
        try:
            with open(os.path.join(scratch_root, "a.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write("line one\nkeep it short.\nline three\n")

            cmap = self.base_map()
            transcript = self.base_transcript(rows=[trow()])
            # Both the map and the transcript must name the same store dir.
            transcript["store_dir"] = scratch_root
            cmap["store_dir"] = scratch_root
            m, c, t = self.paths(cmap, self.base_claims(), transcript)
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(self._read_bytes(m), before_map)
            self.assertEqual(self._read_bytes(c), before_claims)
        finally:
            import shutil
            shutil.rmtree(scratch_root, ignore_errors=True)


class AddendumRoundTwoTests(Base):
    """Round 2 addendum items C1 to C4. See round2-addendum.txt for the
    contract text. Each test checks only the exit code, stderr content,
    and the file bytes. No test assumes how the script is implemented."""

    def test_addendum_c1_bad_max_seconds_and_watchdog_values_fail_cleanly(self):
        import threading as _threading
        over_ceiling = _threading.TIMEOUT_MAX * 10
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        bad_flag_values = [
            ("--max-seconds", "nan"),
            ("--max-seconds", "inf"),
            ("--watchdog-probe", "nan"),
            ("--watchdog-probe", "inf"),
            ("--max-seconds", repr(over_ceiling)),
        ]
        for flag, value in bad_flag_values:
            with self.subTest(flag=flag, value=value):
                r = run("--transcript", t, "--map", m, "--claims", c,
                        "--concepts", "k1", flag, value)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_c1_positive_control_small_max_seconds_still_runs(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        r = run("--transcript", t, "--map", m, "--claims", c,
                "--concepts", "k1", "--max-seconds", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_addendum_c2_boolean_transcript_row_line_fails(self):
        cmap = self.base_map()
        for bad_line in (True, False):
            with self.subTest(bad_line=bad_line):
                transcript = self.base_transcript(rows=[trow(line=bad_line)])
                m, c, t = self.paths(cmap, self.base_claims(), transcript)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_c3_bad_seed_entry_fields_fail_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        bad_variants = [
            dict(r0002_file=None),
            dict(r0002_file=""),
            dict(needle=None),
            dict(needle=""),
            dict(r0002_line=None),
            dict(r0002_line=True),
            dict(r0002_line=False),
            dict(r0002_line=0),
            dict(r0002_line=-1),
        ]
        for override in bad_variants:
            with self.subTest(override=override):
                t_copy = self.base_transcript(rows=[trow()])
                entry = seed_entry()
                if "r0002_file" in override and override["r0002_file"] is None:
                    del entry["r0002_file"]
                elif "needle" in override and override["needle"] is None:
                    del entry["needle"]
                elif "r0002_line" in override and override["r0002_line"] is None:
                    del entry["r0002_line"]
                else:
                    entry.update(override)
                t_copy["seed_dropped"] = [entry]
                m, c, t = self.paths(cmap, self.base_claims(), t_copy)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_c4_non_object_item_in_lists_fails_cleanly(self):
        cmap = self.base_map()
        cases = []

        # transcript.concepts itself holding a non-object item
        t2 = self.base_transcript(rows=[trow()])
        t2["concepts"].append(None)
        cases.append(("transcript.concepts", t2))

        t3 = self.base_transcript(rows=[trow(), None])
        cases.append(("transcript block rows", t3))

        t4 = self.base_transcript(rows=[trow()], dropped=[None])
        cases.append(("transcript block dropped", t4))

        t5 = self.base_transcript(rows=[trow()])
        t5["seed_dropped"] = [None]
        cases.append(("transcript.seed_dropped", t5))

        m6 = self.base_map()
        m6["concepts"].append(None)
        t6 = self.base_transcript(rows=[trow()])
        cases.append(("map.concepts", m6, t6))

        m7 = self.base_map()
        m7["concepts"][0]["governed_files"].append(None)
        t7 = self.base_transcript(rows=[trow()])
        cases.append(("map block governed_files", m7, t7))

        for case in cases:
            if len(case) == 2:
                label, transcript = case
                use_map = self.base_map()
            else:
                label, use_map, transcript = case
            with self.subTest(label=label):
                m, c, t = self.paths(use_map, self.base_claims(), transcript)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)


class AddendumRoundThreeTests(Base):
    """Round 3 addendum items D1 and D2. See round3-addendum.txt for the
    contract text. Each test checks only the exit code, stdout or stderr
    content, and the file bytes. No test assumes how the script is
    implemented."""

    def test_addendum_d1_row_reuses_prior_claim_but_check_claims_fails(self):
        # A non-observation claim ("prior") already holds the exact anchor
        # and quote the row claim needs. The run must still give exit 0
        # and APPLIED. The written claims table must pass check_claims.py.
        # Every claim id a written decision depends on must name a claim
        # whose category is "observation".
        cmap = self.base_map()
        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        c_prior_row = dict(id="cprior-row", category="prior",
                            anchor="store/a.md:2", quote="keep it short.")
        claims = dict(claims=[c1, c_prior_row], decisions=[])
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, claims, transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("APPLIED"), r.stdout)
        out_claims = self._read_json(c)
        by_id = {x["id"]: x for x in out_claims["claims"]}
        for d in out_claims["decisions"]:
            for dep in d["depends_on"]:
                self.assertEqual(by_id[dep]["category"], "observation",
                                 f"decision {d.get('id')} depends on {dep} ({by_id[dep]['category']})")
        # the pre-existing non-observation claim must still be in the
        # table, unchanged.
        self.assertIn(c_prior_row, out_claims["claims"])
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)
        # A second run on the same files must not append a duplicate
        # observation claim. It must print NO CHANGE and write nothing.
        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)

    def test_addendum_d1_binary_reuses_prior_claim_but_check_claims_fails(self):
        # A "prior" claim already holds the exact anchor and output the
        # binary claim needs.
        cmap = self.base_map()
        c_prior_bin = dict(id="cprior-bin", category="prior",
                            anchor="cmd: claude --version",
                            output="2.1.280 (Claude Code)")
        claims = dict(claims=[c_prior_bin], decisions=[])
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, claims, transcript)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("APPLIED"), r.stdout)
        out_claims = self._read_json(c)
        by_id = {x["id"]: x for x in out_claims["claims"]}
        for d in out_claims["decisions"]:
            for dep in d["depends_on"]:
                self.assertEqual(by_id[dep]["category"], "observation",
                                 f"decision {d.get('id')} depends on {dep} ({by_id[dep]['category']})")
        self.assertIn(c_prior_bin, out_claims["claims"])
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)
        # A second run on the same files must not append a duplicate
        # observation claim. It must print NO CHANGE and write nothing.
        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)

    def test_addendum_d1_seed_reuses_prior_claim_but_check_claims_fails(self):
        # A "prior" claim already holds the exact anchor and output the
        # seed's binary claim needs. old.md is placed in the store, so
        # the unrelated r0002 claim can ground. Only the seed's binary
        # claim is under test here.
        self._write_prompt("old.md", "l1\nl2\nl3\nthe needle text\nl5\n")
        cmap = self.base_map()
        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        entry = seed_entry()
        c_prior_seed = dict(id="cprior-seed", category="prior",
                             anchor=f"cmd: {entry['binary_command']}",
                             output=entry["binary_excerpt"])
        claims = dict(claims=[c1, c_prior_seed], decisions=[])
        transcript = self.base_transcript(rows=[], dropped=[dict(file="a.md", reason="not under test here")])
        transcript["seed_dropped"] = [entry]
        m, c, t = self.paths(cmap, claims, transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("APPLIED"), r.stdout)
        out_claims = self._read_json(c)
        by_id = {x["id"]: x for x in out_claims["claims"]}
        for d in out_claims["decisions"]:
            for dep in d["depends_on"]:
                self.assertEqual(by_id[dep]["category"], "observation",
                                 f"decision {d.get('id')} depends on {dep} ({by_id[dep]['category']})")
        self.assertIn(c_prior_seed, out_claims["claims"])
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)
        # A second run on the same files must not append a duplicate
        # observation claim. It must print NO CHANGE and write nothing.
        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)

    def test_addendum_d2_non_object_item_in_claims_list_fails_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        claims = dict(claims=[c1, None], decisions=[])
        m, c, t = self.paths(cmap, claims, transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Exception in thread", r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_d2_non_object_item_in_decisions_list_fails_cleanly(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        claims = dict(claims=[c1], decisions=[None])
        m, c, t = self.paths(cmap, claims, transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Exception in thread", r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)


class AddendumRoundFourTests(Base):
    """Round 4 addendum items E3 to E5. See round4-addendum.txt for the
    contract text. Each test checks only the exit code, stdout or stderr
    content, and the file bytes. No test assumes how the script is
    implemented."""

    def test_addendum_e3_non_string_transcript_row_fields_fail_cleanly(self):
        # A transcript row whose file, marker, or quote is not a string
        # (for example 7) must give exit 1, a clean error, and no write.
        cmap = self.base_map()
        for field in ("file", "marker", "quote"):
            with self.subTest(field=field):
                bad_row = trow()
                bad_row[field] = 7
                transcript = self.base_transcript(rows=[bad_row])
                m, c, t = self.paths(cmap, self.base_claims(), transcript)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_e4_rebalance_across_two_blocks_and_second_run_no_change(self):
        # Setup: map has block k1 with row a.md and block k2 with row b.md.
        # The claims table has a decision for (k1, a.md) listed before a
        # decision for (k2, b.md). Both decisions rest on real observation
        # claims, so the only fault under test is the rebalance itself.
        # The transcript drops a.md from k1, adds a new row c.md to k1,
        # and keeps b.md in k2 with a new state (conforms).
        cmap = self.base_map()
        cmap["concepts"].append(dict(
            concept_id="k2", concept="Kept concept two.", tag="diffuse",
            governed_files=[dict(file="b.md", marker="old b", fix_kind="body-rewrite",
                                  provenance="recognition-first", fix_present="false")],
        ))

        c1 = dict(id="c1", category="observation", anchor="cmd: claude --version",
                  output="2.1.280 (Claude Code)")
        ca = dict(id="ca", category="observation", anchor="store/a.md:2", quote="keep it short.")
        cb = dict(id="cb", category="observation", anchor="store/b.md:3", quote="plan review here")
        d_a = dict(id="d1", text="a.md in k1", concept="k1", file="a.md",
                   state="carries-defect", depends_on=["c1", "ca"])
        d_b = dict(id="d2", text="b.md in k2", concept="k2", file="b.md",
                   state="carries-defect", depends_on=["c1", "cb"])
        claims = dict(claims=[c1, ca, cb], decisions=[d_a, d_b])

        block_k1 = dict(concept_id="k1", notes_append="n1",
                        rows=[trow(file="c.md", marker="new c", line=1, quote="line one")],
                        dropped=[dict(file="a.md", reason="out of surface")])
        block_k2 = dict(concept_id="k2", notes_append="n2",
                        rows=[trow(file="b.md", marker="new b", state="conforms",
                                   line=3, quote="plan review here")],
                        dropped=[])
        transcript = self.base_transcript(rows=[])
        transcript["concepts"] = [block_k1, block_k2]

        m, c, t = self.paths(cmap, claims, transcript)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("APPLIED"), r.stdout)

        out_claims = self._read_json(c)
        decisions_k1 = [d for d in out_claims["decisions"] if d.get("concept") == "k1"]
        decisions_k2 = [d for d in out_claims["decisions"] if d.get("concept") == "k2"]
        self.assertEqual(len(decisions_k1), 1, decisions_k1)
        self.assertEqual(decisions_k1[0]["file"], "c.md")
        self.assertEqual(len(decisions_k2), 1, decisions_k2)
        self.assertEqual(decisions_k2[0]["file"], "b.md")
        self.assertEqual(decisions_k2[0]["state"], "conforms")
        self.assertFalse(any(d.get("concept") == "k1" and d.get("file") == "a.md"
                             for d in out_claims["decisions"]))

        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)

        map_bytes_1 = self._read_bytes(m)
        claims_bytes_1 = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(r2.stdout.strip(), "NO CHANGE")
        self.assertEqual(self._read_bytes(m), map_bytes_1)
        self.assertEqual(self._read_bytes(c), claims_bytes_1)

    def test_addendum_e5_same_file_for_two_path_args_fails_cleanly(self):
        # When any two of --transcript, --map, and --claims name the same
        # file, the run must give exit 2, a clean error, and no write.
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)

        with self.subTest(pair="map==claims"):
            before_m = self._read_bytes(m)
            r = run("--transcript", t, "--map", m, "--claims", m, "--concepts", "k1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(self._read_bytes(m), before_m)

        with self.subTest(pair="transcript==map"):
            before_m = self._read_bytes(m)
            before_c = self._read_bytes(c)
            r = run("--transcript", m, "--map", m, "--claims", c, "--concepts", "k1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(self._read_bytes(m), before_m)
            self.assertEqual(self._read_bytes(c), before_c)

        with self.subTest(pair="transcript==claims"):
            before_m = self._read_bytes(m)
            before_c = self._read_bytes(c)
            r = run("--transcript", c, "--map", m, "--claims", c, "--concepts", "k1")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(self._read_bytes(m), before_m)
            self.assertEqual(self._read_bytes(c), before_c)


class AddendumRoundFiveTests(Base):
    """Round 5 addendum item F4. See round5-addendum.txt for the contract
    text. F4 checks the same-file-different-case path guard. F3, the
    round 5 mutation walk, is gone. AddendumRoundSixTests holds its
    replacement, the G4 walk, per the round 6 addendum."""

    def test_addendum_f4_same_file_different_case_gives_exit_2(self):
        cmap = self.base_map()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, self.base_claims(), transcript)
        upper_m = m.upper()
        lower_exists = os.path.isfile(m)
        upper_exists_same = lower_exists and os.path.exists(upper_m) and os.path.samefile(m, upper_m)
        if not upper_exists_same:
            self.skipTest("the file system does not treat the two case "
                          "variants of this path as the same file")
        before_m = self._read_bytes(m)
        r = run("--transcript", t, "--map", m, "--claims", upper_m)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Exception in thread", r.stderr)
        self.assertEqual(self._read_bytes(m), before_m)


class G4HelpersMixin:
    """Shared G4 helpers for the round 6 and round 7 mutation walks. This
    mixin carries no test_ method. unittest never collects or runs it on
    its own. AddendumRoundSixTests and AddendumRoundSevenTests each
    derive from it separately, instead of one inheriting the other's
    test methods. Round 7 overrides g4_base_fixture and
    _collect_mutation_points in its own class body. Every other helper
    here is shared unchanged."""

    JSON_TYPE_SAMPLES = {
        "number": 1,
        "boolean": True,
        "null": None,
        "string": "x",
        "list": [1],
        "object": {"k": 1},
    }

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def g4_base_fixture(self):
        """The G4 base fixture. One valid transcript, map, and claims
        table. The transcript's one block holds a kept row, a new row,
        and a dropped row. A second, new block holds concept, tag,
        markers (a list of strings), and notes_append. One seed entry
        is present. The map holds one block with notes, markers, and
        governed_files, plus a reconciliation with one dropped_rows
        entry and one seed_dropped_verdicts entry. The claims table
        holds the binary claim plus one row claim (category
        observation), each with text, and two decisions left by an
        earlier run: one row decision for the kept row and one seed
        decision for the fixture's seed entry (seed_id, verdict, text,
        depends_on)."""
        self._write_prompt("old.md", "l1\nl2\nl3\nthe needle text\nl5\n")

        kept_row = dict(file="a.md", marker="old marker", fix_kind="body-rewrite",
                         provenance="recognition-first", fix_present="false",
                         delivery_path="binary-splice", state="carries-defect",
                         verified="orchestrator-read 2026-09-23")
        concept_k1 = dict(concept_id="k1", concept="Kept concept one.", tag="diffuse",
                          markers=["m1"], notes="prior notes", governed_files=[kept_row])
        entry = seed_entry()
        cmap = dict(
            schema="concept-to-prompt-file-map/1.1", store_dir="store",
            concepts=[concept_k1],
            reconciliation=dict(
                dropped_rows=[dict(concept="k1", file="c.md", reason="already dropped earlier")],
                seed_dropped_verdicts=[dict(id=entry["id"],
                                            anchor=f"cmd: {entry['binary_command']}",
                                            quote=entry["binary_excerpt"],
                                            verdict=entry["verdict"],
                                            governed=entry["governed"],
                                            concept_id="k1",
                                            catalog_recommendation=entry["catalog_recommendation"],
                                            reason=entry["reason"])],
            ),
        )

        c_binary = dict(id="c-binary", category="observation",
                         anchor="cmd: claude --version", output="2.1.280 (Claude Code)",
                         text="claude --version prints the Claude Code version.")
        c_row = dict(id="c-row", category="observation",
                      anchor="store/a.md:2", quote="keep it short.",
                      text="a.md line 2 reads keep it short.")
        d_row = dict(id="d-row", text="a.md in k1", concept="k1", file="a.md",
                     state="carries-defect", depends_on=["c-binary", "c-row"])
        d_seed = dict(id="d-seed", seed_id=entry["id"], verdict=entry["verdict"],
                     text=f"Seed {entry['id']} is {entry['verdict']}, per a main-session recognition.",
                     depends_on=["c-binary", "c-row"])
        claims = dict(claims=[c_binary, c_row], decisions=[d_row, d_seed])

        block_k1 = dict(
            concept_id="k1", notes_append="note text",
            rows=[trow(),
                  trow(file="b.md", marker="plan needs a field", line=3,
                       quote="plan review here")],
            dropped=[dict(file="c.md", reason="out of surface")],
        )
        block_new = dict(
            concept_id="k-new", concept="New concept sentence.", tag="diffuse",
            markers=["m1"], notes_append="fresh notes",
            rows=[], dropped=[],
        )
        transcript = self.base_transcript(rows=None)
        transcript["concepts"] = [block_k1, block_new]
        transcript["seed_dropped"] = [entry]
        return cmap, claims, transcript

    def _run_and_check(self, c, m, t, path):
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertNotIn("Traceback", r.stderr, f"{path}: {r.stdout} {r.stderr}")
        self.assertNotIn("Exception in thread", r.stderr, f"{path}: {r.stdout} {r.stderr}")
        if r.returncode in (1, 2):
            self.assertEqual(self._read_bytes(m), before_map, path)
            self.assertEqual(self._read_bytes(c), before_claims, path)
        else:
            self.fail(f"{path}: exited {r.returncode}, but the contract requires exit "
                     f"1 or 2 for every mutation: {r.stdout} {r.stderr}")

    def _write_case_files(self, directory, cmap, claims, transcript):
        """Write one case's store, map, claims, and transcript files into
        directory, a fresh temp directory this case owns alone. This
        mirrors Base.paths and Base.setUp, but targets an arbitrary
        directory instead of self.d, so a parallel worker never shares a
        file with another case."""
        store_dir = os.path.join(directory, "store")
        shutil.copytree(self.store, store_dir)
        m = os.path.join(directory, "map.json")
        c = os.path.join(directory, "claims.json")
        t = os.path.join(directory, "transcript.json")
        for path, obj in ((m, cmap), (c, claims), (t, transcript)):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(obj, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
        return m, c, t

    def _run_mutation_case_for_walk(self, cmap, claims, transcript, path):
        """Run one G4 or H5 mutation case end to end. Use its own
        fresh temp directory. Return a result dict the main thread can
        act on. This method asserts nothing itself. It is safe to call
        from a worker thread this way. The caller turns the returned
        fields into the same assertions _run_and_check made, on the
        main thread, as part of the walk's own collect-then-fail-once
        report."""
        case_dir = tempfile.mkdtemp()
        m, c, t = self._write_case_files(case_dir, cmap, claims, transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c)
        after_map = self._read_bytes(m)
        after_claims = self._read_bytes(c)
        return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                   wrote=after_map != before_map or after_claims != before_claims)

    def _assert_mutation_result(self, result):
        """Apply _run_and_check's own assertions to one precomputed
        mutation result, on the main thread. This raises AssertionError
        at the same point _run_and_check raised it. The caller can wrap
        it in the same subTest-plus-except pattern the serial G4 and H5
        walks used, and collect every failure the same way."""
        path = result["path"]
        self.assertNotIn("Traceback", result["stderr"], f"{path}: {result['stdout']} {result['stderr']}")
        self.assertNotIn("Exception in thread", result["stderr"],
                         f"{path}: {result['stdout']} {result['stderr']}")
        if result["returncode"] in (1, 2):
            self.assertFalse(result["wrote"], f"{path}: wrote a file on exit {result['returncode']}")
        else:
            self.fail(f"{path}: exited {result['returncode']}, but the contract requires exit "
                     f"1 or 2 for every mutation: {result['stdout']} {result['stderr']}")

    def _run_deletion_case_for_walk(self, cmap, claims, transcript, path):
        """Run one J3 deletion case end to end. Use its own fresh temp
        directory. Return a result dict the main thread can act on.
        This method asserts nothing itself. It is safe to call from a
        worker thread this way. It also runs check_claims.py and a
        second apply on an exit-0 result. This mirrors the serial J3
        walk's own invariant check. The caller only has to read the
        returned fields."""
        case_dir = tempfile.mkdtemp()
        m, c, t = self._write_case_files(case_dir, cmap, claims, transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        try:
            r = run("--transcript", t, "--map", m, "--claims", c)
        except Exception as e:
            return dict(path=path, raised=repr(e))
        after_map = self._read_bytes(m)
        after_claims = self._read_bytes(c)
        result = dict(path=path, raised=None, returncode=r.returncode,
                     stdout=r.stdout, stderr=r.stderr,
                     wrote=after_map != before_map or after_claims != before_claims)
        if result["returncode"] == 0:
            check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
            cr = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
            result["check_claims_returncode"] = cr.returncode
            result["check_claims_stdout"] = cr.stdout
            result["check_claims_stderr"] = cr.stderr
            if cr.returncode == 0:
                r2 = run("--transcript", t, "--map", m, "--claims", c)
                result["second_run_returncode"] = r2.returncode
                result["second_run_stdout"] = r2.stdout
        return result

    def _assert_deletion_result(self, result, failing_paths):
        """Apply the serial J3 walk's own per-deletion rule to one
        result, on the main thread. Record a failure the same way the
        serial walk did: append (path, message) to failing_paths,
        instead of raising immediately."""
        path = result["path"]
        if result.get("raised") is not None:
            failing_paths.append((path, f"raised: {result['raised']}"))
            return
        if "Traceback" in result["stderr"] or "Exception in thread" in result["stderr"]:
            failing_paths.append((path, f"unclean stderr, exit {result['returncode']}: "
                                  f"{result['stdout']} {result['stderr']}"))
            return
        if result["returncode"] in (1, 2):
            if result["wrote"]:
                failing_paths.append((path, f"exit {result['returncode']} wrote a file"))
            return
        if result["returncode"] == 0:
            if result["check_claims_returncode"] != 0:
                failing_paths.append(
                    (path, f"exit 0, check_claims.py exit {result['check_claims_returncode']}: "
                          f"{result['check_claims_stdout']} {result['check_claims_stderr']}"))
                return
            if (result["second_run_returncode"] != 0
                    or result["second_run_stdout"].strip() != "NO CHANGE"):
                failing_paths.append(
                    (path, f"exit 0, second run gave {result['second_run_returncode']} "
                          f"{result['second_run_stdout']!r} instead of NO CHANGE"))
            return
        failing_paths.append((path, f"unexpected exit {result['returncode']}: "
                              f"{result['stdout']} {result['stderr']}"))

    def _navigate(self, root, steps):
        """Follow a list of dict-key and list-index steps from root and
        return the node reached. Each step is either a string (a dict
        key) or an integer (a list element's index)."""
        node = root
        for step in steps:
            node = node[step]
        return node

    def _collect_mutation_points(self, node, steps, skip_step_lists):
        """Recursively collect every (steps, field, wrong_value) point a
        nested JSON value offers for mutation. This value holds only
        dict, list, and scalar nodes. For a dict, every field is a
        point. For a list, every element is a point, not only the
        first. A list of scalars (for example a markers list of
        strings) also visits each of its own elements. Such an element
        is addressed by its own index, not a field name. skip_step_lists
        holds the one named exception: the seed concept_id. Its null
        value is a valid form and stays out of the walk entirely."""
        points = []
        if isinstance(node, dict):
            for field, value in node.items():
                child_steps = steps + [field]
                if child_steps not in skip_step_lists:
                    for wrong_value in self._wrong_type_samples(value).values():
                        points.append((steps, field, wrong_value))
                points.extend(self._collect_mutation_points(value, child_steps, skip_step_lists))
        elif isinstance(node, list):
            for index, element in enumerate(node):
                child_steps = steps + [index]
                if isinstance(element, (dict, list)):
                    points.extend(self._collect_mutation_points(element, child_steps, skip_step_lists))
                else:
                    # A scalar list element (for example one markers
                    # string) is itself a mutation point. It is
                    # addressed by its own list and index, not a field
                    # name.
                    if child_steps not in skip_step_lists:
                        for wrong_value in self._wrong_type_samples(element).values():
                            points.append((steps, index, wrong_value))
        return points


class AddendumRoundSixTests(G4HelpersMixin, Base):
    """Round 6 addendum items G1 to G6. See round6-addendum.txt for the
    contract text. G4 replaces the round 5 F3 mutation walk. It is a
    complete, generic recursive walk over transcript, map, and claims,
    using the round 6 fixture and walk from G4HelpersMixin. G5 checks
    stale-file cleanup. G6 checks kill-between-writes recovery."""

    def test_addendum_g3_and_g4_complete_recursive_mutation_walk(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        base_run = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        base_check = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(base_check.returncode, 0, base_check.stdout + base_check.stderr)
        base_run2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run2.returncode, 0, base_run2.stdout + base_run2.stderr)
        self.assertEqual(base_run2.stdout.strip(), "NO CHANGE", base_run2.stdout)

        # The one named exception, per H5: a seed concept_id has two
        # valid forms, null and string, in both places it lives. Its
        # value is never mutated from one of those forms to the other,
        # in a transcript seed entry or in a map seed verdict.
        skip_step_lists = [["seed_dropped", 0, "concept_id"],
                          ["reconciliation", "seed_dropped_verdicts", 0, "concept_id"]]

        cases = []
        for root_label, root in (
                ("transcript", transcript), ("map", cmap), ("claims", claims)):
            points = self._collect_mutation_points(root, [], skip_step_lists)
            for steps, field, wrong_value in points:
                path = f"{root_label}.{'.'.join(str(s) for s in steps)}.{field}={self._json_type_of(wrong_value)}"
                use_transcript = copy.deepcopy(transcript)
                use_cmap = copy.deepcopy(cmap)
                use_claims = copy.deepcopy(claims)
                target_root = {"transcript": use_transcript, "map": use_cmap,
                              "claims": use_claims}[root_label]
                self._navigate(target_root, steps)[field] = wrong_value
                cases.append((path, use_cmap, use_claims, use_transcript))

        # Every case runs in its own temp directory, so the parallel
        # dispatch below never lets two cases share a file.
        results = run_parallel_cases(
            [lambda cmap=cmap, claims=claims, transcript=transcript, path=path:
             self._run_mutation_case_for_walk(cmap, claims, transcript, path)
             for path, cmap, claims, transcript in cases])

        sub_test_count = 0
        failing_paths = []
        for result in results:
            sub_test_count += 1
            with self.subTest(path=result["path"]):
                try:
                    self._assert_mutation_result(result)
                except AssertionError as e:
                    failing_paths.append((result["path"], str(e)))

        print(f"G4 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("G4 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))

    def test_addendum_g5_stale_temp_files_removed_on_success(self):
        map_dir = os.path.join(self.d, "map_dir")
        claims_dir = os.path.join(self.d, "claims_dir")
        os.makedirs(map_dir, exist_ok=True)
        os.makedirs(claims_dir, exist_ok=True)

        cmap = self.base_map()
        claims = self.base_claims()
        transcript = self.one_kept_row_transcript()
        # store_dir resolves relative to the map file's own directory, so
        # name it absolutely: map.json now lives in a directory of its
        # own, separate from the store.
        cmap["store_dir"] = self.store
        transcript["store_dir"] = self.store

        m = os.path.join(map_dir, "map.json")
        c = os.path.join(claims_dir, "claims.json")
        t = os.path.join(self.d, "transcript.json")
        with open(m, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cmap, f, indent=1, ensure_ascii=False)
            f.write("\n")
        with open(c, "w", encoding="utf-8", newline="\n") as f:
            json.dump(claims, f, indent=1, ensure_ascii=False)
            f.write("\n")
        with open(t, "w", encoding="utf-8", newline="\n") as f:
            json.dump(transcript, f, indent=1, ensure_ascii=False)
            f.write("\n")

        stale_map = os.path.join(map_dir, ".apply_recognition-stale.tmp")
        stale_claims = os.path.join(claims_dir, ".apply_recognition-stale.tmp")
        with open(stale_map, "w", encoding="utf-8") as f:
            f.write("leftover")
        with open(stale_claims, "w", encoding="utf-8") as f:
            f.write("leftover")

        before_map_dir = set(os.listdir(map_dir)) - {".apply_recognition-stale.tmp", "map.json"}
        before_claims_dir = set(os.listdir(claims_dir)) - {".apply_recognition-stale.tmp", "claims.json"}

        r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", "k1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        self.assertFalse(os.path.exists(stale_map), "stale file in map dir must be gone")
        self.assertFalse(os.path.exists(stale_claims), "stale file in claims dir must be gone")

        after_map_dir = set(os.listdir(map_dir)) - {"map.json"}
        after_claims_dir = set(os.listdir(claims_dir)) - {"claims.json"}
        self.assertEqual(after_map_dir, before_map_dir,
                         "no other file in the map directory can change")
        self.assertEqual(after_claims_dir, before_claims_dir,
                         "no other file in the claims directory can change")

    def test_addendum_g6_kill_between_writes_recovery(self):
        cmap = self.base_map()
        claims = self.base_claims()
        transcript = self.one_kept_row_transcript()

        # Uninterrupted run: get the reference bytes both files end at.
        m1, c1, t1 = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), copy.deepcopy(transcript))
        r1 = run("--transcript", t1, "--map", m1, "--claims", c1, "--concepts", "k1")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        uninterrupted_map_bytes = self._read_bytes(m1)
        uninterrupted_claims_bytes = self._read_bytes(c1)

        # Fresh copy: run once so claims already holds the new result.
        # The script writes claims before map. Then roll the map file
        # back to its old content. This simulates a kill between the
        # two writes. One more run then recovers both files to the
        # uninterrupted bytes.
        m2, c2, t2 = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), copy.deepcopy(transcript))
        old_map_bytes = self._read_bytes(m2)
        r2 = run("--transcript", t2, "--map", m2, "--claims", c2, "--concepts", "k1")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        with open(m2, "wb") as f:
            f.write(old_map_bytes)

        r3 = run("--transcript", t2, "--map", m2, "--claims", c2, "--concepts", "k1")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertEqual(self._read_bytes(m2), uninterrupted_map_bytes)
        self.assertEqual(self._read_bytes(c2), uninterrupted_claims_bytes)


class AddendumRoundSevenTests(G4HelpersMixin, Base):
    """Round 7 addendum items H1 to H6. See round7-addendum.txt for the
    contract text. H1 and H2 add direct schema tests. H3 lists the
    pass-through fields the completeness test excludes. H4 checks the
    G4 fixture holds every non-pass-through field the real files hold.
    H5 amends the G4 walk to replace a whole list element too, and
    widens the seed concept_id skip to both places it lives. H6 forces
    an OSError at the atomic write."""

    # H3. Every field the script never reads. It copies each one
    # unchanged, so it can hold any JSON type. The completeness test in
    # H4 does not require the fixture to hold it. A path here names a
    # record kind, not a JSON container instance. It applies at every
    # instance of that kind the walk or the completeness test visits.
    PASS_THROUGH_FIELDS = {
        ("map",): {"delivery_paths", "note", "rule_counts"},
        ("map", "reconciliation"): {"coverage_table", "date", "doctrine",
                                    "r002_2026-09-01", "retired_map",
                                    "retired_map_unique_row", "store_move",
                                    "store_move_r0003"},
        ("map", "reconciliation", "dropped_rows"): {"date"},
        ("map", "concepts"): {"coverage_method"},
        ("map", "concepts", "governed_files"): {"note", "reread", "file_r0001", "file_r0002"},
        ("transcript", "binary"): {"path", "sha256", "tweakcc_version"},
        ("transcript", "concepts", "rows"): {"live_seen"},
        # A phase-owned decision (Phase 12 on) carries phase and kind.
        # apply_recognition.py never reads either field and keeps it as is.
        ("claims", "decisions"): {"phase", "kind"},
    }

    def _is_seed_concept_id_step(self, steps):
        """Tell whether steps ends at a seed entry's or a seed verdict's
        concept_id field. Both a transcript seed_dropped entry and a map
        seed_dropped_verdicts entry carry this one field under this one
        name, at any list index. Its null form and its string form are
        both valid, so the walk must never flip it from one to the
        other."""
        if len(steps) < 3 or steps[-1] != "concept_id" or not isinstance(steps[-2], int):
            return False
        return steps[-3] in ("seed_dropped", "seed_dropped_verdicts")

    def _collect_mutation_points(self, node, steps, skip_step_lists):
        """The H5 walk. It extends the round 6 walk with one change. A
        list element that is itself a dict or a list is also a mutation
        point in its own right. It gets replaced whole with each wrong
        JSON type, on top of the round 6 walk recursing into its own
        fields and elements. skip_step_lists is unused here. The skip
        rule is _is_seed_concept_id_step instead. The exception must
        match by path shape, not by one exact, hardcoded path."""
        points = []
        if isinstance(node, dict):
            for field, value in node.items():
                child_steps = steps + [field]
                if not self._is_seed_concept_id_step(child_steps):
                    for wrong_value in self._wrong_type_samples(value).values():
                        points.append((steps, field, wrong_value))
                points.extend(self._collect_mutation_points(value, child_steps, skip_step_lists))
        elif isinstance(node, list):
            for index, element in enumerate(node):
                child_steps = steps + [index]
                if isinstance(element, (dict, list)):
                    # The whole element is itself a mutation point.
                    if not self._is_seed_concept_id_step(child_steps):
                        for wrong_value in self._wrong_type_samples(element).values():
                            points.append((steps, index, wrong_value))
                    points.extend(self._collect_mutation_points(element, child_steps, skip_step_lists))
                else:
                    # A scalar list element (for example one markers
                    # string) is itself a mutation point. It is
                    # addressed by its own list and index, not a field
                    # name.
                    if not self._is_seed_concept_id_step(child_steps):
                        for wrong_value in self._wrong_type_samples(element).values():
                            points.append((steps, index, wrong_value))
        return points

    def g4_base_fixture(self):
        """H4's fixture. Same shape as the round 6 fixture, plus every
        non-pass-through field the real files hold that the round 6
        fixture lacked. It adds map concept markers. It adds a
        governed_files row's delivery_path, state, and verified. It adds
        a fuller seed verdict: governed, concept_id, catalog_recommendation,
        and reason. It adds claim text. It adds a seed decision (seed_id,
        verdict, text, depends_on) left by an earlier run, alongside the
        row decision."""
        return super().g4_base_fixture()

    def test_addendum_h1_dropped_rows_and_seed_verdict_elements_must_be_objects(self):
        cmap, claims, transcript = self.g4_base_fixture()
        for list_path in (("dropped_rows",), ("seed_dropped_verdicts",)):
            for type_name, wrong_element in self._wrong_type_samples({"k": 1}).items():
                with self.subTest(list_field=list_path[0], type=type_name):
                    use_cmap = copy.deepcopy(cmap)
                    use_cmap["reconciliation"][list_path[0]].append(wrong_element)
                    m, c, t = self.paths(use_cmap, copy.deepcopy(claims), copy.deepcopy(transcript))
                    before_map = self._read_bytes(m)
                    before_claims = self._read_bytes(c)
                    r = run("--transcript", t, "--map", m, "--claims", c)
                    self.assertEqual(r.returncode, 2,
                                     f"{list_path[0]}={type_name}: {r.stdout} {r.stderr}")
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertNotIn("Exception in thread", r.stderr)
                    self.assertEqual(self._read_bytes(m), before_map)
                    self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_h2_newly_typed_fields_reject_the_wrong_json_type(self):
        cmap, claims, transcript = self.g4_base_fixture()
        cases = [
            ("claims.decisions[1].seed_id", claims["decisions"][1], "seed_id", (2,)),
            ("claims.decisions[1].verdict", claims["decisions"][1], "verdict", (2,)),
            ("claims.claims[0].text", claims["claims"][0], "text", (2,)),
            ("map.concepts[0].governed_files[0].delivery_path",
             cmap["concepts"][0]["governed_files"][0], "delivery_path", (1, 2)),
            ("map.concepts[0].governed_files[0].state",
             cmap["concepts"][0]["governed_files"][0], "state", (1, 2)),
            ("map.concepts[0].governed_files[0].verified",
             cmap["concepts"][0]["governed_files"][0], "verified", (1, 2)),
            ("map.reconciliation.seed_dropped_verdicts[0].governed",
             cmap["reconciliation"]["seed_dropped_verdicts"][0], "governed", (1, 2)),
            ("map.reconciliation.seed_dropped_verdicts[0].catalog_recommendation",
             cmap["reconciliation"]["seed_dropped_verdicts"][0], "catalog_recommendation", (1, 2)),
            ("map.reconciliation.seed_dropped_verdicts[0].reason",
             cmap["reconciliation"]["seed_dropped_verdicts"][0], "reason", (1, 2)),
        ]
        for label, record, field, expected_exits in cases:
            for type_name, wrong_value in self._wrong_type_samples(record[field]).items():
                with self.subTest(field=label, type=type_name):
                    use_cmap = copy.deepcopy(cmap)
                    use_claims = copy.deepcopy(claims)
                    use_transcript = copy.deepcopy(transcript)
                    target = {"claims": use_claims, "map": use_cmap}[label.split(".")[0]]
                    # Re-locate the same record inside the fresh copy by
                    # its position, mirroring the case table above.
                    if label.startswith("claims.decisions"):
                        node = target["decisions"][1]
                    elif label.startswith("claims.claims"):
                        node = target["claims"][0]
                    elif "governed_files" in label:
                        node = target["concepts"][0]["governed_files"][0]
                    else:
                        node = target["reconciliation"]["seed_dropped_verdicts"][0]
                    node[field] = wrong_value
                    m, c, t = self.paths(use_cmap, use_claims, use_transcript)
                    before_map = self._read_bytes(m)
                    before_claims = self._read_bytes(c)
                    r = run("--transcript", t, "--map", m, "--claims", c)
                    self.assertIn(r.returncode, expected_exits,
                                 f"{label}={type_name}: {r.stdout} {r.stderr}")
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertNotIn("Exception in thread", r.stderr)
                    self.assertEqual(self._read_bytes(m), before_map)
                    self.assertEqual(self._read_bytes(c), before_claims)

    # The record-kind labels both _collect_field_names call sites use.
    # Listing them once here, as plain names rather than a dict literal
    # of string keys, keeps the linter from reading this list as prose.
    FIELD_KIND_LABELS = [
        "transcript.binary", "transcript.session", "transcript.concepts",
        "transcript.concepts.rows", "transcript.concepts.dropped",
        "transcript.seed_dropped", "map.concepts", "map.concepts.governed_files",
        "map.reconciliation", "map.reconciliation.dropped_rows",
        "map.reconciliation.seed_dropped_verdicts", "claims.claims", "claims.decisions",
    ]

    def _collect_field_names(self, tr, cm, cl):
        """Collect the field names present, per record kind, across one
        transcript object, one map object, and one claims object. tr,
        cm, and cl can be the three real prompt-store files or the H4
        fixture: this method reads the same shape either way, so
        _real_file_field_names and _fixture_field_names share it."""
        fields = {label: set() for label in self.FIELD_KIND_LABELS}
        fields["transcript.binary"] = set(tr.get("binary", {}).keys())
        fields["transcript.session"] = set(tr.get("session", {}).keys())
        fields["map.reconciliation"] = set(cm.get("reconciliation", {}).keys())
        for block in tr.get("concepts", []):
            fields["transcript.concepts"] |= set(block.keys())
            for row in block.get("rows", []):
                fields["transcript.concepts.rows"] |= set(row.keys())
            for entry in block.get("dropped", []):
                fields["transcript.concepts.dropped"] |= set(entry.keys())
        for entry in tr.get("seed_dropped", []):
            fields["transcript.seed_dropped"] |= set(entry.keys())
        for concept in cm.get("concepts", []):
            fields["map.concepts"] |= set(concept.keys())
            for row in concept.get("governed_files", []):
                fields["map.concepts.governed_files"] |= set(row.keys())
        for entry in cm.get("reconciliation", {}).get("dropped_rows", []):
            fields["map.reconciliation.dropped_rows"] |= set(entry.keys())
        for entry in cm.get("reconciliation", {}).get("seed_dropped_verdicts", []):
            fields["map.reconciliation.seed_dropped_verdicts"] |= set(entry.keys())
        for claim in cl.get("claims", []):
            fields["claims.claims"] |= set(claim.keys())
        for decision in cl.get("decisions", []):
            fields["claims.decisions"] |= set(decision.keys())
        return fields

    def _real_file_field_names(self):
        """Read the three real prompt-store files and collect the set of
        field names present on each record kind H4 names. A field
        counts once per kind, regardless of how many records or files
        hold it."""
        if WORKSPACE is None:
            self.skipTest("no project workspace above this test file")
        root = WORKSPACE
        recognition_path = _newest_recognition(os.path.join(root, "prompt-store", "recognition"))
        map_path = os.path.join(root, "prompt-store", "concept-map-proposed.json")
        claims_path = os.path.join(root, "prompt-store", "claims.json")
        with open(recognition_path, encoding="utf-8") as f:
            tr = json.load(f)
        with open(map_path, encoding="utf-8") as f:
            cm = json.load(f)
        with open(claims_path, encoding="utf-8") as f:
            cl = json.load(f)
        return self._collect_field_names(tr, cm, cl)

    def _fixture_field_names(self, cmap, claims, transcript):
        """Collect the field names the H4 fixture holds, per the same
        record kinds _real_file_field_names names."""
        return self._collect_field_names(transcript, cmap, claims)

    def _pass_through_for_kind(self, kind_path):
        return self.PASS_THROUGH_FIELDS.get(kind_path, set())

    def test_addendum_h4_fixture_holds_every_non_pass_through_field(self):
        cmap, claims, transcript = self.g4_base_fixture()

        # The base fixture itself must exit 0. It must pass
        # check_claims.py. It must print NO CHANGE on a second run. Show
        # this on disk before the completeness check below reads the
        # real files.
        m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), copy.deepcopy(transcript))
        base_run = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        base_check = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(base_check.returncode, 0, base_check.stdout + base_check.stderr)
        base_run2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run2.returncode, 0, base_run2.stdout + base_run2.stderr)
        self.assertEqual(base_run2.stdout.strip(), "NO CHANGE", base_run2.stdout)

        real_fields = self._real_file_field_names()
        fixture_fields = self._fixture_field_names(cmap, claims, transcript)

        missing = {}
        for label, real_field_set in real_fields.items():
            # A label like "map.reconciliation.dropped_rows" is the dot
            # form of the same kind_path tuple PASS_THROUGH_FIELDS keys
            # on, so it splits back into that tuple directly.
            kind_path = tuple(label.split("."))
            excluded = self._pass_through_for_kind(kind_path)
            required = real_field_set - excluded
            gap = required - fixture_fields[label]
            if gap:
                missing[label] = sorted(gap)

        self.assertEqual(missing, {}, f"fixture is missing fields: {missing}")

    def test_addendum_h5_g4_walk_replaces_whole_list_elements_too(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        base_run = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        base_check = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(base_check.returncode, 0, base_check.stdout + base_check.stderr)
        base_run2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run2.returncode, 0, base_run2.stdout + base_run2.stderr)
        self.assertEqual(base_run2.stdout.strip(), "NO CHANGE", base_run2.stdout)

        cases = []
        for root_label, root in (
                ("transcript", transcript), ("map", cmap), ("claims", claims)):
            points = self._collect_mutation_points(root, [], [])
            for steps, field, wrong_value in points:
                path = f"{root_label}.{'.'.join(str(s) for s in steps)}.{field}={self._json_type_of(wrong_value)}"
                use_transcript = copy.deepcopy(transcript)
                use_cmap = copy.deepcopy(cmap)
                use_claims = copy.deepcopy(claims)
                target_root = {"transcript": use_transcript, "map": use_cmap,
                              "claims": use_claims}[root_label]
                self._navigate(target_root, steps)[field] = wrong_value
                cases.append((path, use_cmap, use_claims, use_transcript))

        # Every case runs in its own temp directory, so the parallel
        # dispatch below never lets two cases share a file.
        results = run_parallel_cases(
            [lambda cmap=cmap, claims=claims, transcript=transcript, path=path:
             self._run_mutation_case_for_walk(cmap, claims, transcript, path)
             for path, cmap, claims, transcript in cases])

        sub_test_count = 0
        failing_paths = []
        for result in results:
            sub_test_count += 1
            with self.subTest(path=result["path"]):
                try:
                    self._assert_mutation_result(result)
                except AssertionError as e:
                    failing_paths.append((result["path"], str(e)))

        print(f"H5 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("H5 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))

    def test_addendum_h6_oserror_at_atomic_write_gives_exit_1_and_no_write(self):
        cmap = self.base_map()
        claims = self.base_claims()
        transcript = self.one_kept_row_transcript()
        m, c, t = self.paths(cmap, claims, transcript)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)

        wrapper = (
            "import runpy, sys, os\n"
            "real_replace = os.replace\n"
            "def fail_replace(*a, **k):\n"
            "    raise FileNotFoundError('forced by test: no such file or directory')\n"
            "os.replace = fail_replace\n"
            f"sys.argv = [{SCRIPT!r}, '--transcript', {t!r}, '--map', {m!r},\n"
            f"           '--claims', {c!r}, '--concepts', 'k1']\n"
            f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
        )
        r = subprocess.run([sys.executable, "-c", wrapper], capture_output=True,
                           text=True, timeout=60)

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Exception in thread", r.stderr)
        error_lines = [line for line in r.stderr.splitlines() if line.strip()]
        self.assertEqual(len(error_lines), 1, r.stderr)
        self.assertIn("run", error_lines[0].lower())
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)


class AddendumRoundEightTests(G4HelpersMixin, Base):
    """Round 8 addendum items I2 to I6. See round8-addendum.txt for the
    contract text. Each test starts from the round 7 (H4) base fixture,
    where it fits, then applies one mutation the addendum names."""

    check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")

    def _check_claims_passes(self, claims_path):
        cr = subprocess.run([sys.executable, self.check_script, claims_path],
                            capture_output=True, text=True)
        self.assertEqual(cr.returncode, 0, cr.stdout + cr.stderr)

    def test_addendum_i2_seed_grounding_checks_reject_bad_values(self):
        cmap, claims, transcript = self.g4_base_fixture()
        cases = [
            ("r0002_file missing", dict(r0002_file="missing.md")),
            ("r0002_line past end", dict(r0002_line=100)),
            ("needle absent at line", dict(needle="nonexistent phrase")),
        ]
        for label, override in cases:
            with self.subTest(case=label):
                use_transcript = copy.deepcopy(transcript)
                use_transcript["seed_dropped"][0].update(override)
                m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)
                r = run("--transcript", t, "--map", m, "--claims", c)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)
                self.assertIn("seed-1", r.stdout)
                self.assertEqual(self._read_bytes(m), before_map)
                self.assertEqual(self._read_bytes(c), before_claims)

        # I4's positive control: the unmutated fixture still exits 0 and
        # writes a claims table check_claims.py passes.
        m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), copy.deepcopy(transcript))
        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self._check_claims_passes(c)

    def test_addendum_i3_padded_file_names_fail_instead_of_writing_padded(self):
        cmap, claims, transcript = self.g4_base_fixture()

        with self.subTest(case="row file padded"):
            use_transcript = copy.deepcopy(transcript)
            use_transcript["concepts"][0]["rows"][1]["file"] = " b.md "
            m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotIn("Exception in thread", r.stderr)
            self.assertEqual(self._read_bytes(m), before_map)
            self.assertEqual(self._read_bytes(c), before_claims)

        with self.subTest(case="seed r0002_file padded"):
            use_transcript = copy.deepcopy(transcript)
            use_transcript["seed_dropped"][0]["r0002_file"] = " old.md "
            m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotIn("Exception in thread", r.stderr)
            self.assertEqual(self._read_bytes(m), before_map)
            self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_i5_verified_text_names_the_transcript_version(self):
        cmap, claims, transcript = self.g4_base_fixture()

        with self.subTest(case="named version reaches the written row"):
            use_transcript = copy.deepcopy(transcript)
            use_transcript["binary"]["claude_version"] = "9.9.9 (Claude Code)"
            m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
            r = run("--transcript", t, "--map", m, "--claims", c)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self._check_claims_passes(c)
            with open(m, encoding="utf-8") as fh:
                written_map = json.load(fh)
            new_row = [row for row in written_map["concepts"][0]["governed_files"]
                      if row["file"] == "b.md"][0]
            self.assertTrue(new_row["verified"].endswith("on 9.9.9"), new_row["verified"])

        with self.subTest(case="empty claude_version fails"):
            use_transcript = copy.deepcopy(transcript)
            use_transcript["binary"]["claude_version"] = ""
            m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c)
            self.assertIn(r.returncode, (1, 2), r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotIn("Exception in thread", r.stderr)
            self.assertEqual(self._read_bytes(m), before_map)
            self.assertEqual(self._read_bytes(c), before_claims)

        with self.subTest(case="missing claude_version fails"):
            use_transcript = copy.deepcopy(transcript)
            del use_transcript["binary"]["claude_version"]
            m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), use_transcript)
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c)
            self.assertIn(r.returncode, (1, 2), r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotIn("Exception in thread", r.stderr)
            self.assertEqual(self._read_bytes(m), before_map)
            self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_i6_concepts_flag_skips_seed_store_dir_resolution(self):
        cmap, claims, transcript = self.g4_base_fixture()

        valid_transcript = copy.deepcopy(transcript)
        m1, c1, t1 = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), valid_transcript)
        with_valid_store = run("--transcript", t1, "--map", m1, "--claims", c1, "--concepts", "k1")

        bad_transcript = copy.deepcopy(transcript)
        bad_transcript["seed_store_dir"] = "no-such-dir"
        m2, c2, t2 = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), bad_transcript)
        with_bad_store = run("--transcript", t2, "--map", m2, "--claims", c2, "--concepts", "k1")

        self.assertEqual(with_bad_store.returncode, with_valid_store.returncode,
                         f"valid: {with_valid_store.stdout} {with_valid_store.stderr}; "
                         f"bad: {with_bad_store.stdout} {with_bad_store.stderr}")

        no_concepts_transcript = copy.deepcopy(transcript)
        no_concepts_transcript["seed_store_dir"] = "no-such-dir"
        m3, c3, t3 = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims), no_concepts_transcript)
        without_concepts = run("--transcript", t3, "--map", m3, "--claims", c3)
        self.assertEqual(without_concepts.returncode, 2,
                         without_concepts.stdout + without_concepts.stderr)


class AddendumRoundNineTests(G4HelpersMixin, Base):
    """Round 9 addendum item J3. See round9-addendum.txt for the
    contract text. J3 is a deletion property over the G4 base fixture
    (the H4 version). Delete one field at a time, at every depth of the
    transcript, the map, and the claims table. This includes each field
    of each list element. No deletion raises. Each deletion gives exit 1
    or 2 with a clean error and no write. The one other allowed result
    is exit 0, where check_claims.py passes on the written table and a
    second run prints NO CHANGE."""

    def _collect_deletion_points(self, node, steps):
        """Recursively collect every (steps, field) point where deleting
        one field is a candidate mutation. For a dict, every field is a
        point. For a list, the walk enters every element that is itself
        a dict or list. A list index is never itself a deletable field.
        Only a dict field can be deleted by name."""
        points = []
        if isinstance(node, dict):
            for field, value in node.items():
                points.append((steps, field))
                points.extend(self._collect_deletion_points(value, steps + [field]))
        elif isinstance(node, list):
            for index, element in enumerate(node):
                if isinstance(element, (dict, list)):
                    points.extend(self._collect_deletion_points(element, steps + [index]))
        return points

    def test_addendum_j3_deletion_property_over_g4_base_fixture(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        base_run = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        base_check = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(base_check.returncode, 0, base_check.stdout + base_check.stderr)
        base_run2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run2.returncode, 0, base_run2.stdout + base_run2.stderr)
        self.assertEqual(base_run2.stdout.strip(), "NO CHANGE", base_run2.stdout)

        cases = []
        for root_label, root in (
                ("transcript", transcript), ("map", cmap), ("claims", claims)):
            points = self._collect_deletion_points(root, [])
            for steps, field in points:
                path = f"{root_label}.{'.'.join(str(s) for s in steps)}.{field}"
                use_transcript = copy.deepcopy(transcript)
                use_cmap = copy.deepcopy(cmap)
                use_claims = copy.deepcopy(claims)
                target_root = {"transcript": use_transcript, "map": use_cmap,
                              "claims": use_claims}[root_label]
                del self._navigate(target_root, steps)[field]
                cases.append((path, use_cmap, use_claims, use_transcript))

        # Every case runs in its own temp directory, so the parallel
        # dispatch below never lets two cases share a file.
        results = run_parallel_cases(
            [lambda cmap=cmap, claims=claims, transcript=transcript, path=path:
             self._run_deletion_case_for_walk(cmap, claims, transcript, path)
             for path, cmap, claims, transcript in cases])

        sub_test_count = 0
        failing_paths = []
        for result in results:
            sub_test_count += 1
            with self.subTest(path=result["path"]):
                self._assert_deletion_result(result, failing_paths)

        print(f"J3 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("J3 deletion walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


class AddendumRoundTenTests(G4HelpersMixin, Base):
    """Round 10 addendum items K1 to K3. See round10-addendum.txt for
    the contract text. K1 checks the check_claims.py gate still runs on
    a run that prints NO CHANGE on its own. K2 forces a failure inside
    the gate itself. K3 checks the stale-temp cleanup also removes a
    leftover gate temp file."""

    def test_addendum_k1_no_change_run_still_gates_on_the_current_claims_file(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        r1 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)

        with open(c, encoding="utf-8") as fh:
            written = json.load(fh)
        written["claims"].append(
            {"id": "c-bad", "category": "inference", "from": [{}], "text": "x"})
        with open(c, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(written, indent=1, ensure_ascii=False) + "\n")

        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertIn("GATE FAILED", r2.stdout)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

    def test_addendum_k2_gate_internal_failure_gives_exit_1_and_no_write(self):
        cmap, claims, transcript = self.g4_base_fixture()

        mkstemp_prelude = """\
import runpy, sys, os, tempfile
real_mkstemp = tempfile.mkstemp
def fail_mkstemp(*a, **k):
    if k.get('prefix') == '.apply_recognition_gate-':
        raise FileNotFoundError('forced by test: gate temp create failed')
    return real_mkstemp(*a, **k)
tempfile.mkstemp = fail_mkstemp
"""
        subprocess_prelude = """\
import runpy, sys, os, subprocess
real_run = subprocess.run
def fail_run(*a, **k):
    raise FileNotFoundError('forced by test: check_claims.py did not launch')
subprocess.run = fail_run
"""
        cases = [
            ("gate temp creation fails", mkstemp_prelude),
            ("check_claims.py launch fails", subprocess_prelude),
        ]
        for label, patch_prelude in cases:
            with self.subTest(case=label):
                m, c, t = self.paths(copy.deepcopy(cmap), copy.deepcopy(claims),
                                     copy.deepcopy(transcript))
                map_dir = os.path.dirname(os.path.abspath(m))
                claims_dir = os.path.dirname(os.path.abspath(c))
                before_map = self._read_bytes(m)
                before_claims = self._read_bytes(c)

                wrapper = (
                    patch_prelude
                    + f"sys.argv = [{SCRIPT!r}, '--transcript', {t!r}, '--map', {m!r},\n"
                    + f"           '--claims', {c!r}]\n"
                    + f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
                )
                r = subprocess.run([sys.executable, "-c", wrapper], capture_output=True,
                                   text=True, timeout=60)

                self.assertEqual(r.returncode, 1, f"{label}: {r.stdout} {r.stderr}")
                self.assertNotIn("Traceback", r.stderr, f"{label}: {r.stderr}")
                self.assertNotIn("Exception in thread", r.stderr, f"{label}: {r.stderr}")
                error_lines = [line for line in r.stderr.splitlines() if line.strip()]
                self.assertEqual(len(error_lines), 1, f"{label}: {r.stderr}")
                self.assertEqual(self._read_bytes(m), before_map, label)
                self.assertEqual(self._read_bytes(c), before_claims, label)

                leftover = [name for name in os.listdir(claims_dir)
                           if name.startswith(".apply_recognition_gate-")]
                self.assertEqual(leftover, [], f"{label}: {leftover}")

    def test_addendum_k3_stale_cleanup_also_removes_gate_temp_files(self):
        map_dir = os.path.join(self.d, "map_dir")
        claims_dir = os.path.join(self.d, "claims_dir")
        os.makedirs(map_dir, exist_ok=True)
        os.makedirs(claims_dir, exist_ok=True)

        # G5's own simple fixture, not the H4 one. The H4 fixture bakes
        # in claims-relative anchors like "store/a.md:2". That anchor
        # form needs the claims file to sit next to store/ itself. This
        # test only needs a run that reaches the cleanup step (exit 0).
        # The simple fixture G5 already uses is enough for that.
        cmap = self.base_map()
        claims = self.base_claims()
        transcript = self.one_kept_row_transcript()
        cmap["store_dir"] = self.store
        transcript["store_dir"] = self.store

        m = os.path.join(map_dir, "map.json")
        c = os.path.join(claims_dir, "claims.json")
        t = os.path.join(self.d, "transcript.json")
        with open(m, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(cmap, indent=1, ensure_ascii=False) + "\n")
        with open(c, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(claims, indent=1, ensure_ascii=False) + "\n")
        with open(t, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(transcript, indent=1, ensure_ascii=False) + "\n")

        stale_names = [".apply_recognition_gate-stale", ".apply_recognition-stale"]
        for directory in (map_dir, claims_dir):
            for name in stale_names:
                with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
                    fh.write("leftover")
            with open(os.path.join(directory, "unrelated.txt"), "w", encoding="utf-8") as fh:
                fh.write("keep me")

        r = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        for directory in (map_dir, claims_dir):
            for name in stale_names:
                self.assertFalse(os.path.exists(os.path.join(directory, name)),
                                 f"{directory}/{name} must be gone")
            self.assertTrue(os.path.exists(os.path.join(directory, "unrelated.txt")),
                            f"{directory}/unrelated.txt must stay")


class AddendumRoundElevenTests(G4HelpersMixin, Base):
    """Round 11 addendum items L2 and L3. See round11-addendum.txt for
    the contract text. L2 is a value property over the G4 base fixture
    (the H4 version): every string value gets three edge-of-string
    values, and every integer value gets three edge-of-range values.
    Each run gives the invariant outcome. L3 forces a failure while the
    gate writes its own temp file."""

    # H3's pass-through fields, repeated here so this class does not
    # depend on AddendumRoundSevenTests. A path here names a record
    # kind, not a JSON container instance, the same way H4 read it.
    PASS_THROUGH_FIELDS = {
        ("map",): {"delivery_paths", "note", "rule_counts"},
        ("map", "reconciliation"): {"coverage_table", "date", "doctrine",
                                    "r002_2026-09-01", "retired_map",
                                    "retired_map_unique_row", "store_move",
                                    "store_move_r0003"},
        ("map", "reconciliation", "dropped_rows"): {"date"},
        ("map", "concepts"): {"coverage_method"},
        ("map", "concepts", "governed_files"): {"note", "reread", "file_r0001", "file_r0002"},
        ("transcript", "binary"): {"path", "sha256", "tweakcc_version"},
        ("transcript", "concepts", "rows"): {"live_seen"},
        # A phase-owned decision (Phase 12 on) carries phase and kind.
        # apply_recognition.py never reads either field and keeps it as is.
        ("claims", "decisions"): {"phase", "kind"},
    }

    def _is_seed_concept_id_step(self, steps):
        """Tell whether steps ends at a seed entry's or a seed verdict's
        concept_id field. This is the same rule H5 uses, repeated here
        for the same reason PASS_THROUGH_FIELDS is repeated: this class
        does not depend on AddendumRoundSevenTests."""
        if len(steps) < 3 or steps[-1] != "concept_id" or not isinstance(steps[-2], int):
            return False
        return steps[-3] in ("seed_dropped", "seed_dropped_verdicts")

    def _pass_through_for_kind(self, kind_path):
        return self.PASS_THROUGH_FIELDS.get(kind_path, set())

    def _string_edge_values(self, value):
        return ["", "   ", f" {value} "]

    def _int_edge_values(self):
        return [0, -1, 1000000000]

    def _collect_value_points(self, node, steps, kind_path):
        """Recursively collect every (steps, field, edge_value) point
        the L2 walk visits. kind_path is the record-kind tuple the
        current node belongs to, for example ("map", "concepts"). A
        crossing into a new record kind boundary grows kind_path by one
        field name, the same way H3 and H4 defined one. This way
        PASS_THROUGH_FIELDS looks up on the same keys H4 used. A string
        field gets its three edge values. An integer field gets its
        three edge values too, with bool excluded, since bool is a
        distinct JSON type. The seed concept_id exception stops here,
        the same as H5's mutation walk."""
        points = []
        if isinstance(node, dict):
            for field, value in node.items():
                child_steps = steps + [field]
                if self._is_seed_concept_id_step(child_steps):
                    continue
                excluded = self._pass_through_for_kind(kind_path)
                if field not in excluded:
                    if isinstance(value, str):
                        for edge_value in self._string_edge_values(value):
                            points.append((steps, field, edge_value))
                    elif isinstance(value, int) and not isinstance(value, bool):
                        for edge_value in self._int_edge_values():
                            points.append((steps, field, edge_value))
                child_kind_path = kind_path + (field,) if kind_path in self.KIND_PATH_CHILDREN else kind_path
                points.extend(self._collect_value_points(value, child_steps, child_kind_path))
        elif isinstance(node, list):
            for index, element in enumerate(node):
                child_steps = steps + [index]
                if self._is_seed_concept_id_step(child_steps):
                    continue
                if isinstance(element, str):
                    for edge_value in self._string_edge_values(element):
                        points.append((steps, index, edge_value))
                elif isinstance(element, int) and not isinstance(element, bool):
                    for edge_value in self._int_edge_values():
                        points.append((steps, index, edge_value))
                points.extend(self._collect_value_points(element, child_steps, kind_path))
        return points

    # The record-kind boundaries H4's completeness test named. A field
    # here is the one place the walk's kind_path grows by that field
    # name. PASS_THROUGH_FIELDS is read at the same granularity H3 and
    # H4 defined it at: a record kind, not a JSON container instance.
    # Every other field stays inside its parent's kind_path.
    KIND_PATH_CHILDREN = {
        (): {"transcript", "map", "claims"},
        ("map",): {"reconciliation", "concepts"},
        ("map", "reconciliation"): {"dropped_rows", "seed_dropped_verdicts"},
        ("map", "concepts"): {"governed_files"},
        ("transcript",): {"binary", "concepts"},
    }

    def _collect_value_points_from_roots(self, transcript, cmap, claims):
        points = []
        for root_label, root in (("transcript", transcript), ("map", cmap), ("claims", claims)):
            for steps, field, edge_value in self._collect_value_points(root, [], (root_label,)):
                points.append((root_label, steps, field, edge_value))
        return points

    def test_addendum_l2_value_property_over_g4_base_fixture(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        base_run = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)
        check_script = os.path.join(os.path.dirname(HERE), "anchored-claims", "check_claims.py")
        base_check = subprocess.run([sys.executable, check_script, c], capture_output=True, text=True)
        self.assertEqual(base_check.returncode, 0, base_check.stdout + base_check.stderr)
        base_run2 = run("--transcript", t, "--map", m, "--claims", c)
        self.assertEqual(base_run2.returncode, 0, base_run2.stdout + base_run2.stderr)
        self.assertEqual(base_run2.stdout.strip(), "NO CHANGE", base_run2.stdout)

        raw_points = self._collect_value_points_from_roots(transcript, cmap, claims)

        cases = []
        for root_label, steps, field, edge_value in raw_points:
            path = f"{root_label}.{'.'.join(str(s) for s in steps)}.{field}={edge_value!r}"
            use_transcript = copy.deepcopy(transcript)
            use_cmap = copy.deepcopy(cmap)
            use_claims = copy.deepcopy(claims)
            target_root = {"transcript": use_transcript, "map": use_cmap,
                          "claims": use_claims}[root_label]
            self._navigate(target_root, steps)[field] = edge_value
            cases.append((path, use_cmap, use_claims, use_transcript))

        # Every case runs in its own temp directory, so the parallel
        # dispatch below never lets two cases share a file.
        results = run_parallel_cases(
            [lambda cmap=cmap, claims=claims, transcript=transcript, path=path:
             self._run_deletion_case_for_walk(cmap, claims, transcript, path)
             for path, cmap, claims, transcript in cases])

        sub_test_count = 0
        failing_paths = []
        for result in results:
            sub_test_count += 1
            with self.subTest(path=result["path"]):
                self._assert_deletion_result(result, failing_paths)

        print(f"L2 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("L2 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))

    def test_addendum_l3_gate_temp_write_failure_gives_exit_1_and_no_write(self):
        cmap, claims, transcript = self.g4_base_fixture()
        m, c, t = self.paths(cmap, claims, transcript)
        map_dir = os.path.dirname(os.path.abspath(m))
        claims_dir = os.path.dirname(os.path.abspath(c))
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)

        # Patch tempfile.mkstemp to remember the one file descriptor
        # the gate itself opens (its own prefix, .apply_recognition_gate-).
        # Then patch os.fdopen to fail only for that one descriptor.
        # The atomic write's own temp file uses a different prefix,
        # .apply_recognition-, so it stays untouched. Only the gate's
        # own write fails this way, not the later map or claims write.
        fdopen_prelude = """\
import runpy, sys, os, tempfile
gate_fds = set()
real_mkstemp = tempfile.mkstemp
def watch_mkstemp(*a, **k):
    fd, path = real_mkstemp(*a, **k)
    if k.get('prefix') == '.apply_recognition_gate-':
        gate_fds.add(fd)
    return fd, path
tempfile.mkstemp = watch_mkstemp
real_fdopen = os.fdopen
def fail_fdopen(fd, *a, **k):
    if fd in gate_fds:
        raise OSError('forced by test: gate temp write failed')
    return real_fdopen(fd, *a, **k)
os.fdopen = fail_fdopen
"""
        wrapper = (
            fdopen_prelude
            + f"sys.argv = [{SCRIPT!r}, '--transcript', {t!r}, '--map', {m!r},\n"
            + f"           '--claims', {c!r}]\n"
            + f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n"
        )
        r = subprocess.run([sys.executable, "-c", wrapper], capture_output=True,
                           text=True, timeout=60)

        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("Exception in thread", r.stderr)
        error_lines = [line for line in r.stderr.splitlines() if line.strip()]
        self.assertEqual(len(error_lines), 1, r.stderr)
        self.assertEqual(self._read_bytes(m), before_map)
        self.assertEqual(self._read_bytes(c), before_claims)

        leftover = [name for name in os.listdir(claims_dir)
                   if name.startswith(".apply_recognition_gate-")]
        self.assertEqual(leftover, [], leftover)


class AddendumRoundTwelveTests(G4HelpersMixin, Base):
    """Round 12 addendum item M1. See round12-addendum.txt for the
    contract text. The verified text of a new or changed map row names
    two tokens from the transcript: the first whitespace-separated
    token of the trimmed binary.claude_version, and the trimmed
    session.date. Every run that exits 0 writes a verified text that
    matches "main-session recognition <token> on <token>", with each
    token non-empty and holding no whitespace. An empty or
    whitespace-only claude_version or session.date gives exit 1 or 2,
    a clean error, and no write."""

    VERIFIED_PATTERN = re.compile(r"^main-session recognition (\S+) on (\S+)$")

    def _run_verified_case(self, cmap, claims, transcript, path):
        """Run one M1 case end to end, in its own fresh temp
        directory. Return a result dict the main thread can act on.
        On an exit-0 result, this also reads the written map back and
        collects every governed_files row's verified text. This way
        the main thread can check each one against VERIFIED_PATTERN,
        with no second file read."""
        case_dir = tempfile.mkdtemp()
        m, c, t = self._write_case_files(case_dir, cmap, claims, transcript)
        before_map_bytes = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        # A row this run itself wrote or changed carries a fresh
        # "verified" value. An unchanged prior row keeps its own value
        # untouched, so it is out of scope for this check: read the
        # before-state row by row and compare, rather than reading
        # every row the written map holds.
        before_verified_by_key = {}
        for concept in cmap.get("concepts", []):
            for row in concept.get("governed_files", []):
                if "verified" in row:
                    before_verified_by_key[(concept.get("concept_id"), row.get("file"))] = row["verified"]
        r = run("--transcript", t, "--map", m, "--claims", c)
        after_map = self._read_bytes(m)
        after_claims = self._read_bytes(c)
        result = dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                     wrote=after_map != before_map_bytes or after_claims != before_claims)
        if result["returncode"] == 0:
            with open(m, encoding="utf-8") as fh:
                written_map = json.load(fh)
            verified_texts = []
            for concept in written_map.get("concepts", []):
                for row in concept.get("governed_files", []):
                    if "verified" not in row:
                        continue
                    key = (concept.get("concept_id"), row.get("file"))
                    if before_verified_by_key.get(key) != row["verified"]:
                        verified_texts.append(row["verified"])
            result["verified_texts"] = verified_texts
        return result

    def _assert_verified_case(self, result):
        """Apply M1's own rule to one precomputed result, on the main
        thread. Raise AssertionError at the point the rule is broken,
        so the caller can wrap this in the same subTest-plus-except
        pattern the other walks use."""
        path = result["path"]
        self.assertNotIn("Traceback", result["stderr"], f"{path}: {result['stdout']} {result['stderr']}")
        self.assertNotIn("Exception in thread", result["stderr"],
                         f"{path}: {result['stdout']} {result['stderr']}")
        if result["returncode"] in (1, 2):
            self.assertFalse(result["wrote"], f"{path}: wrote a file on exit {result['returncode']}")
        elif result["returncode"] == 0:
            for verified in result["verified_texts"]:
                self.assertRegex(verified, self.VERIFIED_PATTERN, f"{path}: {verified!r}")
        else:
            self.fail(f"{path}: unexpected exit {result['returncode']}: "
                     f"{result['stdout']} {result['stderr']}")

    def test_addendum_m1_verified_text_tokens_and_empty_value_rejection(self):
        cmap, claims, transcript = self.g4_base_fixture()

        # Every padding variant is expected to exit 0 and still produce
        # two clean tokens in the verified text.
        padded_values = dict([
            ("leading space", " 2.1.280 (Claude Code)"),
            ("trailing space", "2.1.280 (Claude Code) "),
            ("tab", "2.1.280\t(Claude Code)"),
            ("several spaces between tokens", "2.1.280    (Claude Code)"),
        ])
        padded_dates = dict([
            ("leading space", " 2026-09-23"),
            ("trailing space", "2026-09-23 "),
            ("tab", "2026-09-23\t"),
            ("several spaces between tokens", "2026-09-23    "),
            ("whitespace inside", "2026 09 23"),
            ("leading tab and inner space", "\t2026-09-23 x"),
        ])
        # Every empty or whitespace-only variant is expected to exit 1
        # or 2 and write nothing.
        empty_values = dict([("empty", ""), ("whitespace-only", "   ")])

        cases = []
        for label, value in padded_values.items():
            use_transcript = copy.deepcopy(transcript)
            use_transcript["binary"]["claude_version"] = value
            cases.append((f"claude_version {label}", copy.deepcopy(cmap),
                         copy.deepcopy(claims), use_transcript))
        for label, value in padded_dates.items():
            use_transcript = copy.deepcopy(transcript)
            use_transcript["session"]["date"] = value
            cases.append((f"session.date {label}", copy.deepcopy(cmap),
                         copy.deepcopy(claims), use_transcript))
        for label, value in empty_values.items():
            use_transcript = copy.deepcopy(transcript)
            use_transcript["binary"]["claude_version"] = value
            cases.append((f"claude_version {label}", copy.deepcopy(cmap),
                         copy.deepcopy(claims), use_transcript))
        for label, value in empty_values.items():
            use_transcript = copy.deepcopy(transcript)
            use_transcript["session"]["date"] = value
            cases.append((f"session.date {label}", copy.deepcopy(cmap),
                         copy.deepcopy(claims), use_transcript))

        # Every case runs in its own temp directory, so the parallel
        # dispatch below never lets two cases share a file.
        results = run_parallel_cases(
            [lambda cmap=cmap, claims=claims, transcript=transcript, path=path:
             self._run_verified_case(cmap, claims, transcript, path)
             for path, cmap, claims, transcript in cases])

        sub_test_count = 0
        failing_paths = []
        for (path, _cmap, _claims, _transcript), result in zip(cases, results):
            sub_test_count += 1
            with self.subTest(case=path):
                try:
                    self._assert_verified_case(result)
                except AssertionError as e:
                    failing_paths.append((path, str(e)))

        print(f"M1 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("M1 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


class AddendumRoundFourteenTests(G4HelpersMixin, Base):
    """Round 14 addendum items N1 and N2. See round13-addendum.txt for
    the contract text (round 13 found these, round 14 fixes them). N1
    checks a --concepts value that holds no concept id. N2 checks a
    JSON escape of a lone surrogate, in a key, a nested value, and a
    list item of each input file."""

    # A lone surrogate as raw JSON text: a backslash, then u, then
    # d800. chr(92) is a literal backslash, joined here with the ASCII
    # digits that follow it. A Python "\ud800" string literal holds the
    # real surrogate code point instead of this ASCII escape text. That
    # code point fails this file's own UTF-8 encoding, before the test
    # process even starts.
    SURROGATE_ESCAPE = chr(92) + "ud800"

    def test_addendum_n1_concepts_value_with_no_id_gives_exit_2(self):
        cmap, claims, transcript = self.g4_base_fixture()

        def run_case(value):
            case_dir = tempfile.mkdtemp()
            m, c, t = self._write_case_files(case_dir, copy.deepcopy(cmap),
                                             copy.deepcopy(claims), copy.deepcopy(transcript))
            before_map = self._read_bytes(m)
            before_claims = self._read_bytes(c)
            r = run("--transcript", t, "--map", m, "--claims", c, "--concepts", value)
            return dict(value=value, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                       wrote=self._read_bytes(m) != before_map or self._read_bytes(c) != before_claims)

        values = ["", " ", ",", " , "]
        results = run_parallel_cases([lambda value=value: run_case(value) for value in values])

        sub_test_count = 0
        for result in results:
            sub_test_count += 1
            with self.subTest(value=repr(result["value"])):
                self.assertEqual(result["returncode"], 2,
                                 f"{result['value']!r}: {result['stdout']} {result['stderr']}")
                self.assertNotIn("Traceback", result["stderr"])
                self.assertNotIn("Exception in thread", result["stderr"])
                self.assertFalse(result["wrote"], f"{result['value']!r}: wrote a file")

        print(f"N1 subTest count: {sub_test_count}", file=sys.stderr)

    # A placeholder string json.dumps writes out verbatim, with no
    # backslash of its own to collide with the surrogate escape's own
    # backslash. A Python string that already holds a literal
    # backslash-u-d800 text sequence gets its own backslash escaped a
    # second time by json.dumps. That doubles the backslash and turns
    # the escape into inert literal text, instead of the text JSON
    # parses back into a lone surrogate.
    SURROGATE_MARKER = "SURROGATE-MARKER-PLACEHOLDER"

    def _run_surrogate_case(self, cmap, claims, transcript, target, steps, insertion_kind, path):
        """Write one N2 case's three JSON files. target names which
        file (transcript, map, or claims) carries the lone surrogate.
        steps follows a chain of dict keys and list indexes to the dict
        holding the field the escape replaces. insertion_kind names one
        of three forms. "key": the field name itself becomes the
        marker. "value": the field's string value becomes the marker.
        "list_item": the field's list gets one extra element holding
        the marker. Every other file writes normally through json.dump.
        Every other field of the target file does too. json.dump writes
        the target file first. Only then does this method replace the
        one marker occurrence with the raw surrogate escape text,
        directly in the file's own text. json.dump's own escaping never
        touches that text this way. This method asserts nothing itself.
        It returns a result dict the main thread can act on."""
        roots = {"transcript": transcript, "map": cmap, "claims": claims}
        node = self._navigate(roots[target], steps[:-1])
        field = steps[-1]
        if insertion_kind == "key":
            node[self.SURROGATE_MARKER] = node.pop(field)
        elif insertion_kind == "value":
            node[field] = self.SURROGATE_MARKER
        else:
            node[field] = node[field] + [self.SURROGATE_MARKER]

        case_dir = tempfile.mkdtemp()
        m, c, t = self._write_case_files(case_dir, cmap, claims, transcript)
        target_path = {"transcript": t, "map": m, "claims": c}[target]
        with open(target_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count(self.SURROGATE_MARKER), 1,
                         f"{path}: expected exactly one marker occurrence")
        text = text.replace(self.SURROGATE_MARKER, self.SURROGATE_ESCAPE, 1)
        with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c)
        return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                   wrote=self._read_bytes(m) != before_map or self._read_bytes(c) != before_claims)

    def test_addendum_n2_lone_surrogate_escape_gives_exit_2(self):
        cmap, claims, transcript = self.g4_base_fixture()

        # One (target, steps, insertion_kind) point per input file.
        # steps follows a chain of dict keys and list indexes to the
        # dict that holds the named field. "key" replaces the field
        # name. "value" replaces its string value. "list_item" adds one
        # escape-holding element to its list.
        points = [
            ("transcript", ["concepts", 0], "notes_append", "key"),
            ("transcript", ["concepts", 0], "notes_append", "value"),
            ("transcript", ["concepts", 1], "markers", "list_item"),
            ("map", ["concepts", 0], "notes", "key"),
            ("map", ["concepts", 0], "notes", "value"),
            ("map", ["concepts", 0], "markers", "list_item"),
            ("claims", ["claims", 0], "text", "key"),
            ("claims", ["claims", 0], "text", "value"),
            ("claims", ["decisions", 0], "depends_on", "list_item"),
        ]

        case_fns = []
        labels = []
        for target, container_steps, field, insertion_kind in points:
            steps = container_steps + [field]
            path = f"{target}.{'.'.join(str(s) for s in container_steps)}.{field}={insertion_kind}"
            labels.append(path)
            case_fns.append(
                lambda target=target, steps=steps, insertion_kind=insertion_kind, path=path:
                self._run_surrogate_case(copy.deepcopy(cmap), copy.deepcopy(claims),
                                         copy.deepcopy(transcript), target, steps,
                                         insertion_kind, path))

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        failing_paths = []
        for label, result in zip(labels, results):
            sub_test_count += 1
            with self.subTest(path=label):
                try:
                    self.assertEqual(result["returncode"], 2,
                                     f"{label}: {result['stdout']} {result['stderr']}")
                    self.assertNotIn("Traceback", result["stderr"], f"{label}: {result['stderr']}")
                    self.assertNotIn("Exception in thread", result["stderr"], f"{label}: {result['stderr']}")
                    self.assertFalse(result["wrote"], f"{label}: wrote a file")
                except AssertionError as e:
                    failing_paths.append((label, str(e)))

        print(f"N2 apply subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("N2 apply walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


class AddendumRoundFifteenTests(G4HelpersMixin, Base):
    """Round 15 addendum item O1. See round14-addendum.txt for the
    contract text. Each JSON input file (the transcript, the map, and
    the claims table) is read as strict UTF-8 and parsed as strict
    JSON. This walk starts from the G4 base fixture, so the run
    otherwise succeeds. It covers three defect forms. The first is an
    invalid UTF-8 byte inside a string value. The second is the
    constants NaN, Infinity, and -Infinity, at more than one depth.
    The third is a duplicated key, at the top level and inside a
    nested object."""

    # A placeholder string json.dumps writes out verbatim as a plain
    # JSON string value. Only after that text exists does this walk
    # corrupt it in place: swap the placeholder for raw invalid bytes,
    # a bare NaN-family token, or one more copy of a key already
    # present nearby. json.dumps itself never sees the corrupted form,
    # so its own escaping never touches it.
    MARKER = "O1-MARKER-PLACEHOLDER"

    def _write_marked_case(self, target, field_path, marker_value):
        """Build one G4 base fixture. Set marker_value at field_path
        inside the named target ("transcript", "map", or "claims").
        Write all three files. Return (m, c, t, target_path, text) for
        the caller to corrupt further. field_path is a list of dict
        keys and list indexes ending at the field to set. The test builds
        self._o1_base once on the main thread. A worker only copies it,
        because g4_base_fixture writes the shared store directory."""
        cmap, claims, transcript = copy.deepcopy(self._o1_base)
        roots = {"transcript": transcript, "map": cmap, "claims": claims}
        node = self._navigate(roots[target], field_path[:-1])
        node[field_path[-1]] = marker_value
        case_dir = tempfile.mkdtemp()
        m, c, t = self._write_case_files(case_dir, cmap, claims, transcript)
        target_path = {"transcript": t, "map": m, "claims": c}[target]
        with open(target_path, encoding="utf-8") as fh:
            text = fh.read()
        return m, c, t, target_path, text

    def _run_case(self, m, c, t, target_path, text, path):
        """Write the already-corrupted text to target_path, run the
        script, and return a result dict. This method asserts nothing
        itself. It is safe to call from a worker thread this way."""
        with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c)
        return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                   wrote=self._read_bytes(m) != before_map or self._read_bytes(c) != before_claims)

    def _invalid_utf8_case(self, target, field_path, path):
        m, c, t, target_path, text = self._write_marked_case(target, field_path, self.MARKER)
        self.assertEqual(text.count(f'"{self.MARKER}"'), 1, f"{path}: expected one marker occurrence")
        raw = text.encode("utf-8").replace(self.MARKER.encode("utf-8"), b"\xff")
        with open(target_path, "wb") as fh:
            fh.write(raw)
        before_map = self._read_bytes(m)
        before_claims = self._read_bytes(c)
        r = run("--transcript", t, "--map", m, "--claims", c)
        return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr,
                   wrote=self._read_bytes(m) != before_map or self._read_bytes(c) != before_claims)

    def _nan_family_case(self, target, field_path, constant, path):
        m, c, t, target_path, text = self._write_marked_case(target, field_path, self.MARKER)
        marker_literal = f'"{self.MARKER}"'
        self.assertEqual(text.count(marker_literal), 1, f"{path}: expected one marker occurrence")
        text = text.replace(marker_literal, constant, 1)
        return self._run_case(m, c, t, target_path, text, path)

    def _duplicate_key_case(self, target, field_path, path):
        """field_path names an existing field. This case adds a second
        copy of that same key, immediately after the first, inside the
        same object. json.loads keeps only the later value, silently,
        on HEAD."""
        m, c, t, target_path, text = self._write_marked_case(target, field_path, self.MARKER)
        marker_literal = f'"{field_path[-1]}": "{self.MARKER}"'
        self.assertEqual(text.count(marker_literal), 1, f"{path}: expected one marker occurrence")
        text = text.replace(marker_literal, marker_literal + f', "{field_path[-1]}": "dup"', 1)
        return self._run_case(m, c, t, target_path, text, path)

    def test_addendum_o1_strict_json_rejects_bad_utf8_nan_and_duplicate_keys(self):
        # One point per (target, field_path) pair. transcript.binary.path
        # and map.note are root-level pass-through fields (depth 1).
        # map.concepts[0].governed_files[0].note is a pass-through field
        # nested four levels deep. claims.claims[0].category is a typed
        # but unconstrained string field, read and passed through by
        # check_claims.py without becoming part of a schema-checked
        # numeric or object field.
        utf8_points = [
            ("transcript", ["binary", "path"]),
            ("map", ["note"]),
            ("map", ["concepts", 0, "governed_files", 0, "note"]),
            ("claims", ["claims", 0, "category"]),
        ]
        nan_points = [
            ("transcript", ["binary", "path"], "NaN"),
            ("map", ["note"], "Infinity"),
            ("map", ["concepts", 0, "governed_files", 0, "note"], "-Infinity"),
            ("claims", ["claims", 0, "annotation"], "NaN"),
        ]
        # dup:map.store_dir and dup:claims.claims[0].id are left out.
        # Duplicating either key still gives exit 2 on HEAD, so they
        # cannot show this defect. Duplicating map.store_dir resolves
        # to a directory that does not exist, a store_dir usage error,
        # not a JSON-strictness rejection. Duplicating
        # claims.claims[0].id trips the id-uniqueness check the script
        # already runs elsewhere. transcript.session.vantage and the
        # two pass-through fields below carry no such competing check,
        # so a duplicate there gives exit 2 for one reason only: O1.
        dup_key_points = [
            ("transcript", ["session", "vantage"]),
            ("map", ["concepts", 0, "governed_files", 0, "marker"]),
            ("claims", ["claims", 0, "annotation"]),
        ]

        self._o1_base = self.g4_base_fixture()
        case_fns = []
        labels = []
        for target, field_path in utf8_points:
            path = f"utf8:{target}.{'.'.join(str(s) for s in field_path)}"
            labels.append(path)
            case_fns.append(
                lambda target=target, field_path=field_path, path=path:
                self._invalid_utf8_case(target, field_path, path))
        for target, field_path, constant in nan_points:
            path = f"nan:{target}.{'.'.join(str(s) for s in field_path)}={constant}"
            labels.append(path)
            case_fns.append(
                lambda target=target, field_path=field_path, constant=constant, path=path:
                self._nan_family_case(target, field_path, constant, path))
        for target, field_path in dup_key_points:
            path = f"dup:{target}.{'.'.join(str(s) for s in field_path)}"
            labels.append(path)
            case_fns.append(
                lambda target=target, field_path=field_path, path=path:
                self._duplicate_key_case(target, field_path, path))

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        failing_paths = []
        for label, result in zip(labels, results):
            sub_test_count += 1
            with self.subTest(path=label):
                try:
                    self.assertEqual(result["returncode"], 2,
                                     f"{label}: {result['stdout']} {result['stderr']}")
                    self.assertNotIn("Traceback", result["stderr"], f"{label}: {result['stderr']}")
                    self.assertNotIn("Exception in thread", result["stderr"], f"{label}: {result['stderr']}")
                    self.assertFalse(result["wrote"], f"{label}: wrote a file")
                except AssertionError as e:
                    failing_paths.append((label, str(e)))

        print(f"O1 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("O1 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


if __name__ == "__main__":
    unittest.main()
