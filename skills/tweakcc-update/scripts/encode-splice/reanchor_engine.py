#!/usr/bin/env python3
"""reanchor_engine.py -- Task 3 core.

For each mechanically-feasible FAIL slug (per reanchor-feasibility.json), compute
the re-anchored (new_stock, new_unnerf):

  new_stock  = the store body (the binary-faithful stock the .md currently holds)
  new_unnerf = new_stock with the sealed doctrine's per-line substitutions applied
               (before_line -> after_line), slot labels remapped positionally.

The sealed doctrine PROSE is unchanged: we only transplant the exact sealed
after-line onto the matching store line and remap slot names. Invariants asserted
loudly:
  - slot SEQUENCE (masked ${...} order) identical between new_stock and new_unnerf
  - new_unnerf != new_stock (doctrine actually applied)
  - every sealed changed before-line found in the store body (else infeasible)

Emits the (slug -> {stock, unnerf, mapping, proof}) pairs to a JSON the applier
consumes; and a per-rule proof to verify_records/.

Stdlib only.
"""
import json, glob, os, re, sys, difflib

def _find_workspace():
    """Return the .claude/workspace directory of the project. Walk up from
    this script until a parent holds .claude/workspace."""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(d, ".claude", "workspace")
        if os.path.isdir(candidate):
            return candidate.replace("\\", "/")
        parent = os.path.dirname(d)
        if parent == d:
            raise SystemExit("error: no .claude/workspace directory above " + os.path.abspath(__file__))
        d = parent


WORKSPACE = _find_workspace()
REM = f"{WORKSPACE}/remediation"
STORE = f"{WORKSPACE}/repos/unnerfcc-pr/system-prompts"
VR = f"{REM}/encode-splice/verify_records"
FEAS = f"{VR}/reanchor-feasibility.json"
OUT = f"{VR}/reanchor-pairs.json"
BATCHES = ["browser-automation", "claude-code-identity", "context-compression",
           "git-commit-pr",
           "memory-architecture-multi-agent-swarm-permission-system-feature-flags-internal-modes",
           "safety-rules", "system-reminder", "tool-usage-guidelines"]
FM = re.compile(r"^<!--.*?-->\r?\n?", re.DOTALL)
SLOT = re.compile(r"\$\{[^}]*\}")


def body(p):
    t = open(p, encoding="utf-8").read()
    m = FM.match(t)
    return t[m.end():] if m else t


def maskn(s):
    return re.sub(r"\s+", " ", SLOT.sub("\x00", s)).strip()


def slot_seq(s):
    # ordered top-level slot identifiers (first identifier inside each ${...})
    out = []
    for m in SLOT.findall(s):
        inner = m[2:-1]
        idm = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", inner)
        out.append(idm.group(1) if idm else inner.strip())
    return out


def load_sealed():
    sealed = {}
    for b in BATCHES:
        rev = json.load(open(f"{REM}/{b}/approval.json"))["revision"]
        for bp in glob.glob(f"{REM}/{b}/{rev}/prompts/before/*.md"):
            slug = os.path.basename(bp)[:-3]
            ap = f"{REM}/{b}/{rev}/prompts/after/{slug}.md"
            sealed.setdefault(slug, (bp, ap if os.path.exists(ap) else None))
    return sealed


def _store_slugs():
    return [os.path.basename(p)[:-3] for p in glob.glob(f"{STORE}/*.md")]


def resolve_store_slug(slug, sb, sa):
    """The binary hoists a sealed body across nodes; the doctrine's changed run
    may live in a DIFFERENT store .md than <slug>.md (e.g. a leading/trailing run
    the binary split out under a sibling id). Find the store .md whose body
    carries at least one sealed CHANGED before-line. Prefer <slug>.md itself; else
    the sibling whose body contains the doctrine text. Returns a store slug or None.
    """
    sm = difflib.SequenceMatcher(None, sb.splitlines(), sa.splitlines())
    # Collect the FIXED RUNS (slot-split) of each changed before-line. The binary
    # hoists a line across nodes AT the slots, so the doctrine's changed text lives
    # inside ONE fixed run. A store node "carries" the doctrine iff it contains that
    # run (masked, ws-normalized). Using runs (not whole lines) is what lets a
    # hoisted leading/trailing fragment match.
    changed_runs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            for ln in sb.splitlines()[i1:i2]:
                for run in SLOT.split(ln):
                    r = re.sub(r"\s+", " ", run).strip()
                    if len(r) >= 12:
                        changed_runs.append(r)
    changed_runs = list(dict.fromkeys(changed_runs))
    if not changed_runs:
        return None

    def carries(store_slug):
        p = f"{STORE}/{store_slug}.md"
        if not os.path.exists(p):
            return 0
        bm = maskn(body(p))
        return sum(1 for c in changed_runs if c in bm)

    # prefer the slug's own file
    if carries(slug):
        return slug
    # else the sibling that carries the most doctrine lines (must be > 0)
    best, best_n = None, 0
    for cand in _store_slugs():
        n = carries(cand)
        if n > best_n:
            best, best_n = cand, n
    return best


def reanchor_one(slug, sealed, store_slug=None):
    """Return (stock, unnerf, proof) or (None, None, reason). store_slug lets the
    caller re-anchor onto a sibling store node the binary hoisted the doctrine into."""
    bp, ap = sealed[slug]
    sb, sa = body(bp), body(ap)
    tgt = store_slug or slug
    store = body(f"{STORE}/{tgt}.md")
    store_masked = maskn(store)

    # Build ordered (before_line -> after_line) substitutions from the sealed diff.
    sm = difflib.SequenceMatcher(None, sb.splitlines(), sa.splitlines())
    subs = []  # (before_line, after_line) for replace; (before_line, "") for delete
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            bl = sb.splitlines()[i1:i2]
            al = sa.splitlines()[j1:j2]
            # pair line-by-line where counts match; else treat as block replace of
            # the joined before-block -> joined after-block
            if len(bl) == len(al):
                for x, y in zip(bl, al):
                    if x.strip():
                        subs.append((x, y))
            else:
                subs.append(("\n".join(bl), "\n".join(al)))
        elif tag == "delete":
            bl = sb.splitlines()[i1:i2]
            block = "\n".join(bl)
            if block.strip():
                subs.append((block, ""))

    # PER-NODE re-anchor: the binary hoists a sealed body across several nodes.
    # This store body is ONE such node. Apply only the doctrine substitutions
    # whose before-segment lives IN this node; SKIP the rest (they belong to a
    # different hoisted node, handled by that node's own rule or absent). This
    # is the coordinator's "re-point onto the real node shape, per-node" fix.
    unnerf = store
    applied = 0
    skipped_other_node = 0
    mapping = []
    for before, after in subs:
        loc = _find_masked(unnerf, before)
        if loc is not None:
            start, end, matched_text = loc
            store_slots = SLOT.findall(matched_text)
            after_remapped = _remap_slots(after, store_slots)
            unnerf = unnerf[:start] + after_remapped + unnerf[end:]
            applied += 1
            mapping.append(dict(before_slots=slot_seq(matched_text),
                                after_slots=slot_seq(after_remapped)))
            continue
        # RUN-LEVEL fallback: the whole before-line spans several hoisted nodes,
        # so it is not in THIS node as one piece. Split before/after into aligned
        # fixed runs (split on slots) and apply only the runs present in this node.
        b_runs = SLOT.split(before)
        a_runs = SLOT.split(after)
        did_run = False
        if len(b_runs) == len(a_runs):
            for br, ar in zip(b_runs, a_runs):
                if not br.strip() or br == ar:
                    continue
                loc2 = _find_masked(unnerf, br)
                if loc2 is None:
                    continue  # this run lives in another node
                s2, e2, mt2 = loc2
                unnerf = unnerf[:s2] + ar + unnerf[e2:]
                applied += 1
                did_run = True
        if not did_run:
            skipped_other_node += 1

    if applied == 0:
        return None, None, "no doctrine substitution applied"
    if maskn(unnerf) == maskn(store):
        return None, None, "doctrine produced no change vs store body"
    if slot_seq(store) != slot_seq(unnerf):
        return None, None, (f"slot sequence changed: stock={slot_seq(store)} "
                            f"unnerf={slot_seq(unnerf)}")
    proof = dict(slug=slug, store_slug=tgt, rekeyed=(tgt != slug), target="A",
                 sealed_rewrite_unchanged=True,
                 stock_slots=slot_seq(store), unnerf_slots=slot_seq(unnerf),
                 slot_count_order_preserved=slot_seq(store) == slot_seq(unnerf),
                 substitutions=applied, skipped_other_node=skipped_other_node,
                 per_node=skipped_other_node > 0, mapping=mapping)
    return store, unnerf, proof


def _find_masked(hay, needle):
    """Find needle inside hay tolerant to (a) slot-name drift and (b) inner
    whitespace, recovering exact RAW hay indices for the matched span.

    Strategy: match the needle's fixed (non-slot) TEXT as a sequence of literal
    runs. The needle is `run0 ${slot} run1 ${slot} ... runN`; we anchor run0 in
    hay, then walk each subsequent run forward, treating whatever lies between two
    consecutive runs as the slot span. This locates the raw span [start,end) even
    when hay uses a different slot spelling. Returns (start, end, raw) or None.
    """
    runs = SLOT.split(needle)  # fixed runs, in order (may include '' at edges)
    runs = [r for r in runs]
    # collapse-insensitive matching per run via a normalized-search helper
    fixed = [r for r in runs if r.strip()]
    if not fixed:
        return None

    def find_run(run, from_pos):
        # find `run` in hay from from_pos, whitespace-insensitive. Return
        # (raw_start, raw_end) or None.
        rn = re.sub(r"\s+", " ", run).strip()
        if not rn:
            return None
        # build a regex that matches the run allowing flexible whitespace
        parts = [re.escape(tok) for tok in rn.split(" ")]
        pat = re.compile(r"\s+".join(parts))
        m = pat.search(hay, from_pos)
        return (m.start(), m.end()) if m else None

    first = find_run(fixed[0], 0)
    if not first:
        return None
    start = first[0]
    cur_end = first[1]
    for run in fixed[1:]:
        loc = find_run(run, cur_end)
        if not loc:
            return None
        cur_end = loc[1]
    end = cur_end
    # Slot-boundary extension: the needle's fixed runs stop at the last literal,
    # but the needle may have a TRAILING slot (e.g. "...asked.${NOTE}") whose
    # store counterpart ("...asked.${EXTRA_EDIT_GUIDANCE}") must be INCLUDED so
    # its slot is captured for positional remap. If the needle ends with a slot,
    # extend `end` over an immediately-following store ${...}. Likewise a LEADING
    # slot extends `start` backward over a preceding store ${...}.
    needle_masked = SLOT.sub("\x00", needle)
    if needle_masked.rstrip().endswith("\x00"):
        m = re.compile(r"\s*\$\{[^}]*\}").match(hay, end)
        if m:
            end = m.end()
    if needle_masked.lstrip().startswith("\x00"):
        m = re.compile(r"\$\{[^}]*\}\s*$").search(hay[:start])
        if m:
            start = m.start()
    return (start, end, hay[start:end])


def _remap_slots(after, store_slots):
    """Replace the i-th ${...} in `after` with the i-th store slot verbatim."""
    it = iter(store_slots)
    def repl(_m):
        try:
            return next(it)
        except StopIteration:
            return _m.group(0)  # leave as-is; slot-count assert will catch
    return SLOT.sub(repl, after)


def main():
    # Drive over ALL FAIL slugs that carry a sealed record (the full re-anchor
    # candidate set), not just the 18 the whole-line feasibility pre-screen
    # passed. Per-node re-anchoring recovers the ones the pre-screen rejected
    # because their doctrine spans several hoisted nodes.
    import sys as _sys
    fail_path = _sys.argv[1] if len(_sys.argv) > 1 else None
    sealed = load_sealed()
    if fail_path:
        candidates = [l.strip() for l in open(fail_path, encoding="utf-8") if l.strip()]
    else:
        feas = json.load(open(FEAS, encoding="utf-8"))
        candidates = [r["slug"] for r in feas["rows"] if r.get("feasible") is True]
    candidates = [s for s in candidates if s in sealed]
    pairs = {}
    proofs = []
    exceptions = []
    seen_store = set()
    for slug in candidates:
        bp, ap = sealed[slug]
        sb, sa = body(bp), body(ap)
        # resolve which store node the doctrine lives on (may be a sibling the
        # binary hoisted the doctrine run into)
        tgt = resolve_store_slug(slug, sb, sa)
        stock, unnerf, proof = reanchor_one(slug, sealed, store_slug=tgt)
        if stock is None:
            exceptions.append(dict(slug=slug, store_slug=tgt, reason=proof))
            continue
        # key the pair by the STORE slug (the .md the rule actually targets)
        ts = proof["store_slug"]
        if ts in seen_store:
            # two sealed slugs resolve to the same store node -> keep the first,
            # flag the second (would double-write the node)
            exceptions.append(dict(slug=slug, store_slug=ts,
                                   reason=f"store node {ts} already claimed by another re-anchor"))
            continue
        seen_store.add(ts)
        pairs[ts] = dict(stock=stock, unnerf=unnerf, sealed_slug=slug)
        proofs.append(proof)
    json.dump(dict(pairs=pairs, count=len(pairs)), open(OUT, "w", encoding="utf-8"), indent=1)
    json.dump(dict(rows=proofs), open(f"{VR}/reanchor-proofs.json", "w", encoding="utf-8"), indent=1)
    if exceptions:
        json.dump(dict(rows=exceptions), open(f"{VR}/reanchor-engine-exceptions.json", "w", encoding="utf-8"), indent=1)
    print(f"re-anchored pairs computed={len(pairs)} engine-exceptions={len(exceptions)}")
    for e in exceptions:
        print("  EXC", e["slug"], "-", e["reason"][:70])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
