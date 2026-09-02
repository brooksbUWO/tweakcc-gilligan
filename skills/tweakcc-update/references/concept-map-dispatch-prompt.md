# Concept-mapping dispatch prompt (template)

The proven dispatch prompt for mapping ONE doctrine concept to the prompt files it governs,
recognition-first. Substitute every `<PLACEHOLDER>` before dispatch. Agent type:
general-purpose, background. Read-only mapping; the agent writes only its two output files
plus close-out memories.

Provenance: evolved from the concept-40 blind-test prompt (validated 2026-08-28: the
concept-40, 41, 38, 39, over-constraint, and silent-failure maps were all produced by this
shape). Lessons folded in from the prompt-improve corpus of those runs.

Placeholders:
- `<MAPPING_RECIPE>`: the newest `recipe-concept-prompt-mapping-*.md` (resolve on disk; never pin a stale version).
- `<SOURCE_READS>`: the concept's SOURCE recipes/docs, full absolute paths. Every concept sourced from a recipe MUST have that recipe read in full; a paraphrase in the prompt substituting for the source re-creates the cold-read failure class.
- `<CONCEPT_ID>`, `<CONCEPT_STATEMENT>`: quote the doctrine verbatim, do not summarize.
- `<TAG_EXPECTATION>`: surface-nameable or diffuse, with the recipe's method note for that tag.
- `<SIBLING_TABLE>`: which adjacent concepts are already mapped and which surface each owns, so shared prompts are not double-claimed.
- `<MAGNITUDE>`: expected order-of-magnitude of the governed set, so a large candidate pool is pruned, not swept.
- `<OUT_DIR>`, `<MAP_JSON>`, `<REPORT_MD>`: per-concept output dir and the two deliverable paths.
- `<STORE_DIR>`: the binary-faithful stock store (state the file count so a wrong/empty dir is detected early).
- `<DOCTRINE>`: the goals-concepts doctrine file (newest version).
- `<PROJECT_ROOT>`: the Serena project root.

---- PROMPT BEGINS ----

START NOW: your FIRST action is to read the mapping recipe at <MAPPING_RECIPE> in full. Then, BEFORE mapping, fully read your concept's SOURCES (they define what your concept's fix looks like; every keep/drop and fix-kind judgment must be grounded in their actual lessons, not this prompt's summary): <SOURCE_READS>. Begin with those reads; no preamble. The sources, not this prompt, are the standard.

MANDATORY: You MUST respond to every message the user sends you while you are running. When the user sends a message, answer it directly and keep going rather than ending the turn on that message alone. You finish when the task is done or the user (or orchestrator) tells you to stop; a user message asking you to stop is a normal, legitimate reason to stop, not something to work around.

CLOSE-OUT (unconditional, on ANY exit including stop/abort/partial): before you end a turn that concludes or halts the work, write a comprehensive Serena memory of where you got to and report its FULL ABSOLUTE PATH. If write_memory is unavailable, write the .md directly into <PROJECT_ROOT>\.serena\memories and report that absolute path.

PROMPT-IMPROVEMENT MEMORY (your VERY LAST task, AFTER the session memory above): write a SEPARATE second Serena memory just about THIS dispatch prompt. Looking back over the session, name anything missing from your initial prompt that would have been good to know up front (a fact, a precondition, a method, an unreachable or mis-framed success criterion), plus anything about prompts you think is important that may have been missed. Report its full absolute path too. This memory must conform to the prompt-improve template (`recipe-prompt-improve-template-*.md` in D:/Data/Programs/AI/Claude/recipes/, the most recent version): include a `## Lessons` section with at least one grammar-conforming lesson line before the two verbatim prompt sections. Hook `06-enforce-prompt-improve-format.js` denies non-conforming writes.

TOOL ERROR HANDLING: If a tool call errors, returns "not found", or looks like the WRONG project or path, do NOT conclude failure, do NOT silently work around it after one attempt, and do NOT assume a cause. RETRY with a VARIED approach each time (re-activate the project, adjust the path, try a different tool or a direct read, wait in case it is transient), with NO hard limit. Only after exhausting several DISTINCT approaches, report it "unresolved, cause undetermined" and continue with what you CAN do. Never state a root cause you have not proven. If a call is blocked by a safety or permission gate rather than erroring, that block is a real signal, not noise; report it rather than routing around it.

SERENA: Serena is available on this project. Activate it ONCE on <PROJECT_ROOT> (mcp__plugin_serena_serena__activate_project), then USE Serena's symbolic tools for navigation and its create_text_file for memories. The memory store is <PROJECT_ROOT>\.serena\memories. Do NOT create a second project or activate a subdirectory.

STANDING RESOURCES (use these exact absolute paths; do not re-search for them):
- Serena project root: <PROJECT_ROOT>
- The MAPPING RECIPE you must follow: <MAPPING_RECIPE>
- Your concept's SOURCES: <SOURCE_READS>
- The prompt store (extracted binary prompts, one .md per prompt): <STORE_DIR> (state the expected file count)
- The concept doctrine (defines the concepts): <DOCTRINE>
- Your OUTPUT directory (write ONLY here): <OUT_DIR>

WRITING YOUR OUTPUT FILES:
- Write your two deliverables to these exact absolute paths, using the Write tool:
  1. The map: <MAP_JSON>
  2. A short report: <REPORT_MD>
- These paths are under .claude\workspace\, which Serena CANNOT write to, so the Write tool is the ONLY option for them; do not relocate them. Do NOT build them with a Bash heredoc (a Windows apostrophe truncates it). After writing, CONFIRM the done-gate before you report done: the map JSON PARSES (load it back), both files are non-empty on disk, and the governed_files count you report matches the parsed file. A Write-only output dir makes a silent write miss invisible otherwise.

TREAT THE MAPPING RECIPE AS THE METHOD, NOT GROUND TRUTH ABOUT YOUR CONCEPT. Its worked examples are DIFFERENT concepts than yours; do not copy their answers. Follow the recipe's steps for YOUR concept. Do not copy any other concept's finished map as your answer.

SIBLING CONCEPTS (already mapped; do NOT re-claim their surfaces): <SIBLING_TABLE>. A prompt whose teaching belongs to a sibling concept is that sibling's row, not yours; note the overlap in your report instead of claiming the file.

VALID OUTCOMES: an all-body-invariant governed set is a VALID result, not a failure. A concept can be an already-satisfied standard in the current stock; report covered-by-conformance with the evidence. Do not manufacture body-rewrite rows to look productive. Expected governed-set magnitude: <MAGNITUDE>; if your set lands far outside it, re-check your keep-test before writing, and say so in the report either way.

TASK - map ONE concept, following the mapping recipe end to end:

Your concept is <CONCEPT_ID> from the doctrine (<DOCTRINE>): "<CONCEPT_STATEMENT>" The sources carry the full lesson set; apply THEM as the governance standard. <TAG_EXPECTATION>

Do the recipe's [R001] MAP workflow, all steps in order (state the concept and its markers; recognize governed prompts from your OWN loaded context FIRST; confirm and extend with a store search; read each candidate and record its fix kind; record the map; apply the method by tag; gate on per-file coverage; report; verify the recognition layer). IMPORTANT context: this project's un-nerf patch already rewrote many prompts; the store you read is the STOCK (pre-patch) baseline, which is the correct mapping target. Produce:

1. <MAP_JSON> - the concept-to-files map in the recipe's [A001.2] schema: concept statement, tag, coverage_method, and a governed_files list where each row has {file, marker, fix_kind, provenance, fix_present}.

2. <REPORT_MD> - a short report: for EACH governed file, HOW you found it (recognition vs search), the counts of each, which surface-matched prompts you DROPPED as not-governed and why, and the recipe's Step 9 self-check (recognition layer non-empty; did recognition catch a prompt a keyword search would miss).

HARD CONSTRAINTS (non-destructive):
- Write ONLY the two output files above (plus your close-out memories). Do NOT edit, move, or create any other file. Do NOT touch: apply-unnerfs.py, the prompt store, or anything under .claude\workspace\prompt-store\ except to READ the store directory named above.
- Do NOT run any apply, splice, or patch. This is a read-and-map task only.

RETURN, numbered and concise:
1. The concept as you stated it and its markers.
2. The tag you assigned and why.
3. The count of governed files, split by fix_kind and by provenance.
4. The count of surface-matched-but-dropped prompts and one example of why one was dropped.
5. The Step 9 self-check result.
6. The two output file absolute paths (done-gate confirmed: JSON parses, both non-empty).
7. UNCONDITIONAL CLOSE-OUT: the comprehensive Serena memory's full absolute path.
8. PROMPT-IMPROVEMENT MEMORY: the second memory's full absolute path.

---- PROMPT ENDS ----
