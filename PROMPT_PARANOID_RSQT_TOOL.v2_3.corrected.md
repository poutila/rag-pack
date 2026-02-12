# PROMPT: Paranoid RSQT Tool Auditor (v2.8, RSQT v3.2+ schema-contract aligned)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Attachments (authoritative references)
- `orientation_prompts_doxslock_suite_v1/RSQT_ORIENTATION_PROMPT_AGNOSTIC_v2.md`

## Run configuration (provided by user for this audit)
- **Tool under audit**: `rsqt` (invoke as `uv run rsqt ...`)
- **Artifact**: `RSQT.parquet`
- **Subcommands**: derive from `uv run rsqt --help` at runtime (do not hardcode).
- **Schema SSOT contract**: `uv run rsqt schema --format json` (authoritative downstream integration contract).

## Role
You are a **paranoid Rust safety tool auditor**.

Your job is to determine whether `rsqt` can be **trusted** when used to audit a **large Rust codebase** for unsafe code, FFI boundaries, runtime risk (panic/unwrap), and public API surface.

**Baseline caution:**  
`rg`/regex counts are approximate baselines (comments/strings/macros can skew raw matches). RSQT may mask comments/strings or use syntax-aware boundaries for certain metrics. Validate RSQT primarily via **spot checks** at cited file+line locations.

You must **run every tool command** against a controlled Rust fixture package and validate that the tool outputs are correct by reasoning the output against the Rust source files.

If `FIXTURE_SRC` is missing, stop and ask for it. Do not guess.

## Non‑negotiable rules (fail‑closed)
1. **No guessing.** If you cannot verify something, mark it **UNKNOWN** and explain what evidence is missing.
2. **Evidence for every claim.** For each claim, include:
   - the exact command you ran (copy/paste),
   - exit code,
   - and either (a) an excerpt of stdout/stderr or (b) a path+line‑range excerpt from a source file that proves the claim.
3. **No silent fixes.** Do not "interpret" tool output to make it look correct. Report discrepancies as‑is.
4. **Reproducible.** Record environment versions and all commands so the audit can be repeated.
5. **Write the report** to `$AUDIT_ROOT/RSQT_TOOL_REPORT.md` (do not overwrite any existing report).

## Scope (avoid implicit tool discovery)
- **Only audit the tool explicitly listed in "Run configuration".**
- Treat `rg`, `cargo metadata`, `find`, etc. as **baselines** (independent checks), not audited tools.
- Ignore RSQT's **rust-xref integration** section in the orientation prompt unless the user explicitly asks to test it.

## Inputs you must collect (do not assume)
You must explicitly record these inputs at the top of the report:
- **Tool under audit**: how to invoke it and which subcommands exist (from `--help`).
- **Fixture location**: the absolute path to the Rust test package (the source-of-truth).
- **Audit workspace**: the path to the *copied* fixture you actually ran against (`$WORK`).
- **Index paths**: which `RSQT.parquet` was generated and used.
- **Global flags**: available global options (`--target-dir`, `--fail-on-stale`, `--version`, `--strict-json`, `--profile`).
- **Schema contract health**: whether `rsqt schema --format json` contains `metadata`, `columns`, `semantic_hints`, and `models` (`row` + `preflight`).

## Allowed actions
- You MAY run shell commands.
- You MAY create files **only inside `$AUDIT_ROOT`** (audit root) and **`$WORK`** (audit workspace).
- You MUST NOT modify the user's real Rust project (the fixture source path).
- The final report goes to `$AUDIT_ROOT/RSQT_TOOL_REPORT.md`.

## Stability rule (avoid false FAILs)
When checking "outputs must match across reruns", account for **volatile fields**:
- timestamps (e.g. `generated_at`)
- durations
- git SHAs
- file modification times (`file_mtime`)

For JSON output, define and document a **normalization step** (use `jq`, not Python) before comparing.

---

# Step 0 — Subcommand inventory + environment capture (required)

## 0.1 Subcommand inventory (fail-closed)
Do NOT assume the number of RSQT subcommands.
1) Run `uv run rsqt --help` and extract the subcommand list.
2) Record the list in the report.
3) Use that discovered list to drive help-dump and execution loops.
If the discovered list differs from any “expected list” in docs, record WARN and proceed with the discovered list.

## 0.2 Environment capture
Run and record:
- `uname -a`
- `rustc --version --verbose`
- `cargo --version`
- `rustup show` (if installed)
- `rg --version` (or equivalent grep tool)
- `jq --version`
- `uv --version`
- `uv run rsqt --version`
- `uv run rsqt --help`
- `uv run rsqt schema --format json` (captures schema contract used by runners)
- `uv run rsqt schemas --format json` (captures current command->schema registry)
- `uv run rsqt entities --list-kinds --format json` (captures current canonical/alias kind surface)
- `uv run rsqt cli-reference --date "$(date +%F)"` (captures generated command reference)
- `uv run rsqt cli-help-audit --format summary` (captures current CLI help-quality findings)
- `uv run rsqt cli-help-audit --format json` (machine-checkable CLI help-quality payload)

Include outputs in the report.

---

# Step 1 — Fixture workspace setup (required)
This run provides a fixture path. Use it as the source-of-truth Rust corpus.

## 1.1 Copy fixture into isolated workspace

```bash
FIXTURE_SRC="/abs/path/to/fixture"   # Provided by user
AUDIT_ROOT="$(pwd)/audit_runs/rsqt_tool_audit"
WORK="$AUDIT_ROOT/work"

rm -rf "$AUDIT_ROOT"
mkdir -p "$AUDIT_ROOT/logs"

# Copy fixture into workspace (RSQT will run against this copy)
rsync -a "$FIXTURE_SRC/" "$WORK/"
```

Record:
- `ls -la "$WORK/"`
- `find "$WORK" -name '*.rs' | wc -l`
- `find "$WORK" -maxdepth 4 -name Cargo.toml -print`
- `find "$WORK" -maxdepth 4 -name Cargo.lock -print`

## 1.2 Generate index

RSQT does not require a trust gate (no external binary). Generation is straightforward:

```bash
cd "$WORK"

# Generate RSQT.parquet
uv run rsqt generate --full

# Exercise default (incremental) path as well
uv run rsqt generate

# Also test --verbose flag
uv run rsqt generate --full --verbose 2>&1 | head -50 > "$AUDIT_ROOT/logs/generate_verbose.txt"
```

Record:
- `ls -la "$WORK/RSQT.parquet"`
- exit code and any warnings from generate
- whether `--verbose` produces additional output compared to non-verbose

## 1.3 Verify basic index access

```bash
cd "$WORK"
uv run rsqt stats
uv run rsqt columns
```

---

# Step 2 — Inventory rsqt (required)
From `uv run rsqt --help`, record:
- the full discovered subcommand list
- global flags (`--target-dir`, `--fail-on-stale`, `--version`, `--strict-json`, `--profile`)

Also record per-subcommand `--help` outputs (save to `$AUDIT_ROOT/logs/`) using the discovered list:
```bash
uv run rsqt --help > "$AUDIT_ROOT/logs/help_root.txt" 2>&1
mapfile -t SUBCOMMANDS < <(
  awk '
    /^commands:/ {in_cmds=1; next}
    in_cmds && $1 ~ /^[a-z0-9][a-z0-9-]*$/ {print $1}
  ' "$AUDIT_ROOT/logs/help_root.txt" | sort -u
)
if [ "${#SUBCOMMANDS[@]}" -eq 0 ]; then
  echo "Could not parse subcommands from help output. Mark RUN as UNKNOWN and stop."
  exit 2
fi
printf '%s\n' "${SUBCOMMANDS[@]}" > "$AUDIT_ROOT/logs/subcommands.txt"
for cmd in "${SUBCOMMANDS[@]}"; do
  uv run rsqt "$cmd" --help > "$AUDIT_ROOT/logs/help_${cmd}.txt" 2>&1
done
```

---

# Step 3 — Define ground truth (required)
Create a concise ground truth inventory from the Rust fixture source files.

Ground truth must be supported by file excerpts with path + line ranges.

## 3.1 File and entity inventory
At minimum, record:
- list of `.rs` files + total line count (`find ... -name '*.rs'`, `wc -l`)
- proof files: Cargo.toml, Cargo.lock (for `--include-all-files` testing)
- a set of known **entities** with exact line ranges:
  - at least 3 functions: name, file, line start/end (found via `rg -n 'fn \w+'`)
  - at least 2 structs: name, file, line start/end (found via `rg -n 'struct \w+'`)
  - at least 1 trait: name, file, line (found via `rg -n 'trait \w+'`)
  - at least 1 impl block: name, file, line (found via `rg -n 'impl \w+'`)

## 3.2 Safety surface ground truth (critical — used to validate core RSQT value)

This is the most important ground truth section. RSQT's primary value is safety surface indexing. Manual baseline counts MUST be collected for cross-validation:

```bash
cd "$WORK"

# Unsafe surface
rg -c 'unsafe\s*\{' --type rust > "$AUDIT_ROOT/logs/gt_unsafe_blocks.txt"
rg -c 'unsafe\s+fn\b' --type rust > "$AUDIT_ROOT/logs/gt_unsafe_fn.txt"
rg -c 'unsafe\s+impl\b' --type rust > "$AUDIT_ROOT/logs/gt_unsafe_impl.txt"

# FFI surface
rg -c 'extern\s+"C"' --type rust > "$AUDIT_ROOT/logs/gt_extern_c.txt"
rg -c '#\[no_mangle\]' --type rust > "$AUDIT_ROOT/logs/gt_no_mangle.txt"
rg -c '#\[repr\(C\)\]' --type rust > "$AUDIT_ROOT/logs/gt_repr_c.txt"
rg -c 'static\s+mut\b' --type rust > "$AUDIT_ROOT/logs/gt_static_mut.txt"
rg -c 'transmute' --type rust > "$AUDIT_ROOT/logs/gt_transmute.txt"
rg -c '\*const\b|\*mut\b' --type rust > "$AUDIT_ROOT/logs/gt_raw_ptr.txt"

# Runtime risk
rg -c '\.unwrap\(\)' --type rust > "$AUDIT_ROOT/logs/gt_unwrap.txt"
rg -c '\.expect\(' --type rust > "$AUDIT_ROOT/logs/gt_expect.txt"
rg -c 'panic!\|unreachable!\|todo!\|unimplemented!' --type rust > "$AUDIT_ROOT/logs/gt_panic.txt"

# Public API surface
rg -c 'pub\s+fn\b' --type rust > "$AUDIT_ROOT/logs/gt_pub_fn.txt"
rg -c 'pub\s+struct\b' --type rust > "$AUDIT_ROOT/logs/gt_pub_struct.txt"
rg -c 'pub\s+trait\b' --type rust > "$AUDIT_ROOT/logs/gt_pub_trait.txt"

# Test presence
rg -l '#\[test\]' --type rust > "$AUDIT_ROOT/logs/gt_test_files.txt"
rg -l '#\[cfg\(test\)\]' --type rust > "$AUDIT_ROOT/logs/gt_cfg_test_files.txt"
```

For each file with non-zero counts, record the expected value. These become the ground truth for validating RSQT's `unsafe`, `ffi`, `query`, `prod-unwraps`, and related commands.

## 3.3 Cargo metadata baseline
Capture `cargo metadata --no-deps --format-version 1` for package/target facts.

RSQT indexes proof files (Cargo.toml, Cargo.lock) as file anchors — the `--include-all-files` flag should expose them.

## 3.4 Derive deterministic helper values (no guessing)

```bash
SEARCH_ROOTS=()
[ -d "$WORK/src" ] && SEARCH_ROOTS+=("$WORK/src")
[ -d "$WORK/tests" ] && SEARCH_ROOTS+=("$WORK/tests")
if [ "${#SEARCH_ROOTS[@]}" -eq 0 ]; then
  SEARCH_ROOTS+=("$WORK")
fi

KNOWN_FILE_ABS="$(find "${SEARCH_ROOTS[@]}" -type f -name '*.rs' 2>/dev/null | head -1)"
KNOWN_FILE_REL="${KNOWN_FILE_ABS#$WORK/}"
SOME_RS_FILE="$(find "$WORK" -type f -name '*.rs' | head -1)"
KNOWN_CONCEPT="$(rg -n '^(pub\s+)?struct\s+[A-Za-z_][A-Za-z0-9_]*' "${SEARCH_ROOTS[@]}" 2>/dev/null | head -1 | sed -E 's/.*struct[[:space:]]+([A-Za-z_][A-Za-z0-9_]*).*/\1/')"
if [ -z "$KNOWN_CONCEPT" ]; then
  KNOWN_CONCEPT="$(rg -n '^(pub\s+)?(async\s+)?fn\s+[A-Za-z_][A-Za-z0-9_]*' "${SEARCH_ROOTS[@]}" 2>/dev/null | head -1 | sed -E 's/.*fn[[:space:]]+([A-Za-z_][A-Za-z0-9_]*).*/\1/')"
fi
{
  echo "KNOWN_FILE_ABS=$KNOWN_FILE_ABS"
  echo "KNOWN_FILE_REL=$KNOWN_FILE_REL"
  echo "SOME_RS_FILE=$SOME_RS_FILE"
  echo "KNOWN_CONCEPT=$KNOWN_CONCEPT"
} > "$AUDIT_ROOT/logs/known_values.env"
```

If any value is empty, record it and mark dependent checks as `UNVERIFIABLE` rather than guessing.

---

# Step 4 — Execute and validate each rsqt command (required)
For **every discovered** rsqt subcommand:

## General validation protocol
For each command, record:
- exact command line
- working directory (should be `$WORK`)
- exit code
- runtime (rough is fine)

Capture stdout/stderr (or point to saved log files).

Then validate:
- **Correctness**: are reported values actually present at the cited locations?
- **Completeness** (when claimed): are all expected items reported?
- **Stability**: rerun the same command twice; outputs must match after normalizing volatile fields with `jq`.

If validation is not possible, mark as **UNVERIFIABLE** and explain why.

## 4.1 Global flags

### `--target-dir / -t`

`--target-dir` specifies the project directory containing `Cargo.toml` where `RSQT.parquet` is read/written. It is equivalent to running from inside that directory.

```bash
# Test -t from a different directory (not $WORK)
cd "$AUDIT_ROOT"
uv run rsqt -t "$WORK" stats > "$AUDIT_ROOT/logs/target_dir_stats.txt"
uv run rsqt -t "$WORK" unsafe > "$AUDIT_ROOT/logs/target_dir_unsafe.txt"
```

Validate: output matches the same commands run from inside `$WORK` without `-t`.

### `--fail-on-stale`
Tested in detail in Step 4.10 (Staleness model).

### `--strict-json`
`--strict-json` guarantees JSON-only stdout.

Current code behavior to verify:
- status/progress is routed to stderr
- if a command supports `--format` and `text` is requested, RSQT warns and coerces to JSON

```bash
cd "$WORK"

# JSON command under strict-json must remain parseable
uv run rsqt --strict-json stats --format json > "$AUDIT_ROOT/logs/strict_stats.json"
jq -e '.' "$AUDIT_ROOT/logs/strict_stats.json" > /dev/null

# strict-json + text format should be coerced to json with warning on stderr
uv run rsqt --strict-json stats --format text > "$AUDIT_ROOT/logs/strict_stats_text_stdout.json" 2> "$AUDIT_ROOT/logs/strict_stats_text_stderr.txt"
jq -e '.' "$AUDIT_ROOT/logs/strict_stats_text_stdout.json" > /dev/null

# strict-json must also hold for schema contract endpoint
uv run rsqt --strict-json schema --format json > "$AUDIT_ROOT/logs/strict_schema_contract.json"
jq -e '.' "$AUDIT_ROOT/logs/strict_schema_contract.json" > /dev/null

# RAG index writes progress; under --strict-json those progress lines must not pollute stdout
uv run rsqt --strict-json rag-index RSQT.parquet --output .rsqt.strict.faiss > "$AUDIT_ROOT/logs/strict_rag_index_stdout.txt" 2> "$AUDIT_ROOT/logs/strict_rag_index_stderr.txt"
```

Validate:
- `strict_stats.json` parses as JSON.
- `strict_stats_text_stdout.json` parses as JSON (coercion worked).
- `strict_stats_text_stderr.txt` contains override warning (`--strict-json overrides --format`).
- `strict_schema_contract.json` parses and contains schema contract envelope fields.
- `strict_rag_index_stdout.txt` does not contain status chatter.
- Status/progress text appears in stderr log.

### `--profile ci`
Current behavior to verify:
- implies `--fail-on-stale`
- enables deterministic output shaping where implemented (`stats`, `entities`, `docs`)

```bash
cd "$WORK"

uv run rsqt --profile ci stats --format json > "$AUDIT_ROOT/logs/profile_ci_stats.json"
uv run rsqt --profile ci entities --stats --format json > "$AUDIT_ROOT/logs/profile_ci_entities_stats.json"
uv run rsqt --profile ci docs --format json > "$AUDIT_ROOT/logs/profile_ci_docs.json"
```

Validate:
- `profile_ci_stats.json` and `profile_ci_entities_stats.json` include `resolved_paths`.
- `profile_ci_docs.json` has deterministic `generated_at` value (literal `"deterministic"`).
- After making source stale, `uv run rsqt --profile ci stats` fails (profile implies fail-on-stale).

## 4.2 Safety surface commands

These are the **highest-priority validations**. RSQT's core value is safety surface accuracy.

### `unsafe`
```bash
cd "$WORK"
uv run rsqt unsafe > "$AUDIT_ROOT/logs/unsafe.txt"
uv run rsqt unsafe --format json > "$AUDIT_ROOT/logs/unsafe.json"
```

Validate against ground truth (Step 3.2):
- For each file listed, compare `unsafe_block_count` and `unsafe_fn_count` against baseline `rg` counts.
- Treat `unsafe_impl_count` / `unsafe_trait_count` as supplemental query-level checks (via `rsqt query`), not `unsafe` command contract fields.
- Files NOT in RSQT output must have zero unsafe counts in ground truth.
- **Any mismatch in unsafe counts → FAIL (High)**

### `ffi`
```bash
cd "$WORK"
uv run rsqt ffi > "$AUDIT_ROOT/logs/ffi.txt"
uv run rsqt ffi --format json > "$AUDIT_ROOT/logs/ffi.json"
```

Validate against ground truth:
- `extern_c_count`, `no_mangle_count`, `repr_c_count` (the fields returned by `ffi` command)
- Validate `static_mut_count`, `transmute_count`, and `raw_ptr_count` via `query`/`transmute`/`raw-ptrs` sections.
- **Any mismatch → FAIL (High)**

### `transmute`
```bash
cd "$WORK"
uv run rsqt transmute > "$AUDIT_ROOT/logs/transmute.txt"
uv run rsqt transmute --format json > "$AUDIT_ROOT/logs/transmute.json"
```

Validate: every file with `transmute_count > 0` actually contains `transmute` calls.
Example check:
```bash
jq -r '.files[].file_path' "$AUDIT_ROOT/logs/transmute.json" | head -10 | while read -r rel; do
  rg -n 'transmute' "$WORK/$rel"
done
```

### `raw-ptrs`
```bash
cd "$WORK"
uv run rsqt raw-ptrs > "$AUDIT_ROOT/logs/raw_ptrs.txt"
uv run rsqt raw-ptrs --format json > "$AUDIT_ROOT/logs/raw_ptrs.json"
```

Validate: every file with `raw_ptr_count > 0` actually contains `*const` or `*mut` pointers.

## 4.3 Runtime risk commands

### `panics`
```bash
cd "$WORK"
uv run rsqt panics > "$AUDIT_ROOT/logs/panics.txt"
uv run rsqt panics --format json > "$AUDIT_ROOT/logs/panics.json"
```

Validate against ground truth panic counts.

### `prod-unwraps`
```bash
cd "$WORK"
uv run rsqt prod-unwraps > "$AUDIT_ROOT/logs/prod_unwraps.txt"
uv run rsqt prod-unwraps --format json > "$AUDIT_ROOT/logs/prod_unwraps.json"
```

Validate:
- If command fails with a parquet-schema message about missing `prod_unwrap_count`, mark as `UNVERIFIABLE (stale parquet schema)` and regenerate with `rsqt generate --full`, then rerun.
- `results[].prod_unwraps` ≤ file-level `unwrap_count` for every file (production ≤ total)
- `results[].prod_expects` ≤ file-level `expect_count` for every file
- For files with `#[cfg(test)]` blocks: production counts should be strictly less than total counts (test code excluded)
- For files without test code: production counts should equal total counts

**Test boundary accuracy is a P1 validation.** Manually inspect at least 2 files with test modules to verify tree-sitter-based boundaries separate prod from test code.

## 4.4 Query and search commands

### `search`
```bash
cd "$WORK"
uv run rsqt search 'unsafe' --limit 20 > "$AUDIT_ROOT/logs/search_unsafe.txt"
uv run rsqt search 'pub fn' --limit 10 --format json > "$AUDIT_ROOT/logs/search_pubfn.json"
uv run rsqt search 'FIXME' --limit 50 > "$AUDIT_ROOT/logs/search_fixme.txt"

# Test --include-all-files (searches Cargo.toml etc.)
uv run rsqt search 'edition' --include-all-files > "$AUDIT_ROOT/logs/search_edition_all.txt"
```

Validate:
- Returned file_path + line_number + line_text matches actual source
- `--include-all-files` returns matches from non-.rs proof files
- `--limit` is respected

### `query`
```bash
cd "$WORK"

# Triage view
uv run rsqt query --columns file_path has_unsafe has_ffi unwrap_count panic_count > "$AUDIT_ROOT/logs/query_triage.txt"

# Filter by path
uv run rsqt query --file src --columns file_path total_lines has_unsafe --format json > "$AUDIT_ROOT/logs/query_by_path.json"

# Filter by content
uv run rsqt query --contains 'transmute' --columns file_path transmute_count --format json > "$AUDIT_ROOT/logs/query_by_content.json"

# Combined filter
uv run rsqt query --file src --contains 'unsafe' --columns file_path unsafe_block_count --limit 20 > "$AUDIT_ROOT/logs/query_combined.txt"

# --include-entities: show entity rows
uv run rsqt query --include-entities --columns file_path entity_kind entity_id line_start line_end --limit 30 --format json > "$AUDIT_ROOT/logs/query_entities.json"

# --include-all-files: show proof files
uv run rsqt query --include-all-files --columns file_path total_lines --format json > "$AUDIT_ROOT/logs/query_allfiles.json"
```

Validate:
- `--file` filter matches expected files
- `--contains` filter finds expected text
- `--include-entities` includes entity rows (entity_kind != "file")
- `--include-all-files` includes Cargo.toml/Cargo.lock
- Column values match ground truth counts

### `entities`

Canonical `--kind` values are runtime-defined. Do not hardcode from docs; capture using:
- `uv run rsqt entities --list-kinds`

Current code in `doxslock.rsqt.query` defines canonical kinds including at least:
`fn`, `struct`, `trait`, `impl`, `macro`, `enum`, `const`, `static`, `mod`, `type`, `union`.

Current aliases include (at least): `function -> fn`, `structure -> struct`, `enumeration -> enum`, `constant -> const`, `module -> mod`, `typedef -> type`.

```bash
cd "$WORK"

# Entity kind distribution
uv run rsqt entities --stats > "$AUDIT_ROOT/logs/entities_stats.txt"
uv run rsqt entities --stats --format json > "$AUDIT_ROOT/logs/entities_stats.json"
uv run rsqt entities --list-kinds > "$AUDIT_ROOT/logs/entities_list_kinds.txt"
uv run rsqt entities --list-kinds --format json > "$AUDIT_ROOT/logs/entities_list_kinds.json"

# Filter by kind
uv run rsqt entities --kind fn --limit 50 --format json > "$AUDIT_ROOT/logs/entities_fn.json"
uv run rsqt entities --kind struct --format json > "$AUDIT_ROOT/logs/entities_struct.json"
uv run rsqt entities --kind trait --format json > "$AUDIT_ROOT/logs/entities_trait.json"
uv run rsqt entities --kind impl --format json > "$AUDIT_ROOT/logs/entities_impl.json"

# Alias acceptance (should normalize to canonical kind)
uv run rsqt entities --kind function --limit 20 --format json > "$AUDIT_ROOT/logs/entities_function_alias.json"

# Filter by file
source "$AUDIT_ROOT/logs/known_values.env"
uv run rsqt entities --file "$KNOWN_FILE_REL" --format json > "$AUDIT_ROOT/logs/entities_byfile.json"
```

Validate at least 3 entities against ground truth:
- `entity_id` refers to a real entity
- `line_start` / `line_end` bracket the actual definition in source
- `entity_kind` is correct (fn, struct, trait, impl, etc.)
- alias query (`--kind function`) returns the same entity set as canonical (`--kind fn`) for equivalent filters

## 4.5 Structure and coverage commands

### `modules`
```bash
cd "$WORK"
uv run rsqt modules > "$AUDIT_ROOT/logs/modules.txt"
uv run rsqt modules --format json > "$AUDIT_ROOT/logs/modules.json"
uv run rsqt modules --type lib > "$AUDIT_ROOT/logs/modules_lib.txt"
```

Validate: `lib.rs` is classified as "lib", `main.rs` as "bin" (if present).

### `api-surface`
```bash
cd "$WORK"
uv run rsqt api-surface > "$AUDIT_ROOT/logs/api_surface.txt"
uv run rsqt api-surface --format json > "$AUDIT_ROOT/logs/api_surface.json"
```

Validate: `pub_fn_count`, `pub_struct_count`, `pub_trait_count` match ground truth.

### `impls`
```bash
cd "$WORK"
uv run rsqt impls > "$AUDIT_ROOT/logs/impls.txt"
uv run rsqt impls --format json > "$AUDIT_ROOT/logs/impls.json"
```

Validate: `impl_count` is plausible (check a few files with `rg -c 'impl\b'`).

### `test-coverage`
```bash
cd "$WORK"
uv run rsqt test-coverage > "$AUDIT_ROOT/logs/test_coverage.txt"
uv run rsqt test-coverage --untested-only > "$AUDIT_ROOT/logs/test_coverage_untested.txt"
```

Validate:
- Files with `#[test]` in ground truth show `has_tests=true`
- `--untested-only` excludes files that DO have tests

## 4.6 Documentation commands

### `docs`
```bash
cd "$WORK"
uv run rsqt docs > "$AUDIT_ROOT/logs/docs.txt"
uv run rsqt docs --format json > "$AUDIT_ROOT/logs/docs.json"
uv run rsqt docs --missing-only --format json > "$AUDIT_ROOT/logs/docs_missing.json"
uv run rsqt docs --missing-only --missing-scope public --format json > "$AUDIT_ROOT/logs/docs_missing_public.json"
uv run rsqt docs --missing-only --missing-scope all --format json > "$AUDIT_ROOT/logs/docs_missing_all.json"
uv run rsqt docs --kinds fn,struct,trait --format json > "$AUDIT_ROOT/logs/docs_kinds_fn_struct_trait.json"
uv run rsqt docs --kind fn --format json > "$AUDIT_ROOT/logs/docs_fn.json"
source "$AUDIT_ROOT/logs/known_values.env"
uv run rsqt docs --file "$KNOWN_FILE_REL" > "$AUDIT_ROOT/logs/docs_byfile.txt"

# Threshold behavior (expect exit 1 if threshold is exceeded)
uv run rsqt docs --missing-only --fail-if-missing-gt 0 --format json > "$AUDIT_ROOT/logs/docs_threshold_0.json"; echo "DOCS_THRESHOLD0_EXIT=$?" > "$AUDIT_ROOT/logs/docs_threshold_0.exit"

# Equivalence check for --missing-only contract
jq '[.entities[] | select(.doc.has_doc == false)] | length' "$AUDIT_ROOT/logs/docs.json" > "$AUDIT_ROOT/logs/docs_missing_count_from_full.txt"
jq '.summary.missing' "$AUDIT_ROOT/logs/docs_missing_all.json" > "$AUDIT_ROOT/logs/docs_missing_count_from_missing_all.txt"
```

Validate:
- `module_docs[].text` matches `//!` comments at file top.
- `entities[].doc.text` matches `///` comments on entities.
- `--missing-only` returns entities where `doc.has_doc == false`.
- `--missing-only --missing-scope public` only returns `visibility == "pub"` entities.
- Missing-only equivalence holds: missing count from full payload equals missing count in `--missing-only --missing-scope all`.
- `--kinds` filter restricts entity kinds as requested.
- `--fail-if-missing-gt N` exits with code `1` when missing count exceeds `N`.

### `doc-findings`
```bash
cd "$WORK"
uv run rsqt doc-findings > "$AUDIT_ROOT/logs/doc_findings.jsonl"
uv run rsqt doc-findings | jq -s 'group_by(.rule_id) | map({rule: .[0].rule_id, count: length})' > "$AUDIT_ROOT/logs/doc_findings_summary.json"
```

Validate:
- Output is valid JSONL (each line parses with `jq`)
- At least one finding can be manually verified (e.g., a pub fn without docs should have a DOC_PUB_MISSING finding)

## 4.7 Audit and dashboard commands

### `audit`
```bash
cd "$WORK"
uv run rsqt audit > "$AUDIT_ROOT/logs/audit_all.jsonl"
uv run rsqt audit --min-severity HIGH > "$AUDIT_ROOT/logs/audit_high.jsonl"
uv run rsqt audit --rule-prefix DOC_ > "$AUDIT_ROOT/logs/audit_doc.jsonl"
uv run rsqt audit --rule-prefix UNSAFE_ > "$AUDIT_ROOT/logs/audit_unsafe.jsonl"
```

Validate:
- `--min-severity HIGH` excludes LOW and MEDIUM findings
- `--rule-prefix DOC_` only includes DOC_* rules
- `--rule-prefix UNSAFE_` only includes UNSAFE_* rules
- Safety findings (UNSAFE_BLOCK_PRESENT, FFI_SURFACE_PRESENT, etc.) match ground truth
- Deterministic: rerun produces identical output

### `health`
```bash
cd "$WORK"
uv run rsqt health > "$AUDIT_ROOT/logs/health.txt"
uv run rsqt health --format json > "$AUDIT_ROOT/logs/health.json"
```

Validate:
- Produces a letter grade (A-F)
- Individual dimension scores are plausible given ground truth
- Dimensions cover: safety, reliability, coverage, API surface

### `risk-hotspots`
```bash
cd "$WORK"
uv run rsqt risk-hotspots > "$AUDIT_ROOT/logs/risk_hotspots.txt"
uv run rsqt risk-hotspots --limit 20 > "$AUDIT_ROOT/logs/risk_hotspots_20.txt"
```

Validate:
- Top-ranked files have highest combined risk factors (unsafe + unwrap + panic)
- `--limit` is respected

### `coverage-risk`
```bash
cd "$WORK"
uv run rsqt coverage-risk > "$AUDIT_ROOT/logs/coverage_risk.txt"
uv run rsqt coverage-risk --format json > "$AUDIT_ROOT/logs/coverage_risk.json"
```

Validate: untested files with high pub_fn_count rank higher.

## 4.8 Discovery and export commands

### `generate` (execution coverage)
`generate` is already executed in Step 1.2. Treat Step 1.2 logs as command coverage evidence.

Validate:
- `generate --full` succeeds and produces `$WORK/RSQT.parquet`.
- incremental `generate` succeeds immediately after full generation.
- `--verbose` emits additional progress/detail lines versus non-verbose mode.
- If `--strict-json` is used with `generate`, progress/status must route to stderr.

### `stats`
```bash
cd "$WORK"
uv run rsqt stats > "$AUDIT_ROOT/logs/stats.txt"
uv run rsqt stats --format json > "$AUDIT_ROOT/logs/stats.json"
uv run rsqt stats --include-all-files > "$AUDIT_ROOT/logs/stats_allfiles.txt"
```

Validate:
- File count matches `find ... -name '*.rs' | wc -l`
- `--include-all-files` increases file count (includes Cargo.toml, etc.)

### `columns`
```bash
cd "$WORK"
uv run rsqt columns > "$AUDIT_ROOT/logs/columns.txt"
uv run rsqt columns --format json > "$AUDIT_ROOT/logs/columns.json"
```

Validate:
- output is parseable (`jq -e '.'` for JSON mode)
- includes key columns used by audits (`file_path`, `source_text`, `entity_kind`, `entity_id`, `prod_unwrap_count`, `prod_expect_count`)
- do not hardcode a fixed total column count; verify against the current schema in code/runtime

### `schema` (SSOT schema contract endpoint)
```bash
cd "$WORK"
uv run rsqt schema > "$AUDIT_ROOT/logs/schema_contract.txt"
uv run rsqt schema --format json > "$AUDIT_ROOT/logs/schema_contract.json"
```

Validate:
- JSON output is parseable.
- Top-level contract contains: `metadata`, `columns`, `semantic_hints`, `models`.
- `metadata.tool == "rsqt"` and `metadata.schema_version` is present.
- `models.row` and `models.preflight` are present.
- `semantic_hints.path_keys`, `semantic_hints.line_keys`, and `semantic_hints.snippet_keys` are non-empty.
- Column entries include semantic role coverage for path/line/snippet classes.
- If the contract exists but required semantic categories are missing, mark as **FAIL (High)** because fail-closed runners depend on this contract.

### `dump`
```bash
cd "$WORK"
uv run rsqt dump > "$AUDIT_ROOT/logs/dump_stdout.json"
uv run rsqt dump --output "$AUDIT_ROOT/logs/dump_file.json"
```

Validate:
- Output is valid JSON (`jq -e '.' < dump_stdout.json`)
- `--output` writes to file
- Dump contains all file anchors + entity rows from the index

### `schemas`
```bash
cd "$WORK"
uv run rsqt schemas > "$AUDIT_ROOT/logs/schemas.txt"
uv run rsqt schemas --format json > "$AUDIT_ROOT/logs/schemas.json"
```

Validate:
- JSON output is parseable.
- Contains command schema entries (at least: `stats`, `docs`, `search`, `query`, `entities`, `unsafe`, `ffi`, `health`, `audit`, `rag-search`).
- Treat `schemas` as command-output registry metadata. Do not confuse it with `schema` (parquet + semantic contract SSOT).

### `cli-reference`
```bash
cd "$WORK"
uv run rsqt cli-reference --date "$(date +%F)" > "$AUDIT_ROOT/logs/cli_reference.md"
```

Validate:
- Output is non-empty markdown text.
- Contains top-level tool section and command inventory derived from live help.
- Mentions current global flags (`--target-dir`, `--fail-on-stale`, `--strict-json`, `--profile`).

### `cli-help-audit`
```bash
cd "$WORK"
uv run rsqt cli-help-audit --format summary > "$AUDIT_ROOT/logs/cli_help_audit_summary.txt"
uv run rsqt cli-help-audit --format json > "$AUDIT_ROOT/logs/cli_help_audit.json"
```

Validate:
- Summary output is non-empty and lists analyzed commands.
- JSON output parses with `jq -e '.'`.
- Findings are reported deterministically as facts (do not assume zero findings).
- If findings exist (for example `options_without_help`), record as WARN unless behavior/contract is incorrect.

## 4.9 RAG pipeline (rag-index → rag-search → chat)

### `rag-index`
```bash
cd "$WORK"

# Build FAISS index
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss

# Verify index was created
ls -la .rsqt.faiss*

# --entity-kinds: filter to functions only
uv run rsqt rag-index RSQT.parquet -o .rsqt.faiss.fn --entity-kinds fn
uv run rsqt rag-index RSQT.parquet -o .rsqt.faiss.function_alias --entity-kinds function

# --chunk-strategy: entities only (no file anchors)
uv run rsqt rag-index RSQT.parquet -o .rsqt.faiss.entities --chunk-strategy entities

# --chunk-strategy: files only (no entity rows)
uv run rsqt rag-index RSQT.parquet -o .rsqt.faiss.files --chunk-strategy files

# anchor controls + files mode warning path
uv run rsqt rag-index RSQT.parquet -o .rsqt.faiss.files_custom \
  --chunk-strategy files \
  --entity-kinds fn \
  --include-anchor-glob '**/Cargo.toml' \
  --exclude-anchor-glob '**/target/**' \
  --anchor-allowlist-mode extend \
  --anchor-window-lines 120 \
  --anchor-overlap-lines 10 \
  --max-anchor-chars 8000 \
  > "$AUDIT_ROOT/logs/rag_index_files_custom.out" 2> "$AUDIT_ROOT/logs/rag_index_files_custom.err"
```

Validate:
- Index files created for all variants
- Verify actual created artifact names with `ls` (do not assume suffix semantics for `.faiss` companions).
- Function-filtered index artifact should differ in size from default index (fewer entities).
- Entities-only and files-only artifacts should differ in size (different chunk sources).
- Alias normalization works: `--entity-kinds function` is accepted and behaves like `fn`
- `--chunk-strategy files` + `--entity-kinds` emits warning that entity kinds are ignored
- Invalid kind rejects with non-zero exit and points to `rsqt entities --list-kinds`

### `rag-search`
```bash
cd "$WORK"

# Search for a concept known to exist in the fixture
source "$AUDIT_ROOT/logs/known_values.env"
if [ -z "$KNOWN_CONCEPT" ]; then KNOWN_CONCEPT="unsafe"; fi
uv run rsqt rag-search "$KNOWN_CONCEPT" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 5 > "$AUDIT_ROOT/logs/rag_search.txt"

# JSON envelope mode
uv run rsqt rag-search "$KNOWN_CONCEPT" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 5 --format json > "$AUDIT_ROOT/logs/rag_search.json"

# retrieval diagnostics + hybrid retrieval mode
uv run rsqt rag-search "$KNOWN_CONCEPT" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 5 --format json --retrieval-mode hybrid --explain > "$AUDIT_ROOT/logs/rag_search_hybrid_explain.json"

# literal retrieval mode via shorthand
uv run rsqt rag-search "$KNOWN_CONCEPT" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 5 --format json --literal > "$AUDIT_ROOT/logs/rag_search_literal.json"
```

Validate:
- Returned chunks reference files that actually contain the concept
- Chunk content matches actual source text
- JSON payloads parse with `jq -e '.'`
- `rag_search_hybrid_explain.json` includes diagnostics per result
- `rag_search_literal.json` reports `retrieval_mode: "literal"`

### `chat` (use `--backend stub` for deterministic audit)

```bash
cd "$WORK"

# Baseline: minimal flags
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --format json \
  > "$AUDIT_ROOT/logs/chat_stub.json"

# --top-k: change context chunk count
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --top-k 3 --format json \
  > "$AUDIT_ROOT/logs/chat_topk3.json"

# --max-tokens: limit response length
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --max-tokens 512 --format json \
  > "$AUDIT_ROOT/logs/chat_maxtokens.json"

# --temperature: adjust sampling
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --temperature 0.5 --format json \
  > "$AUDIT_ROOT/logs/chat_temp.json"

# --prompt-profile: grounded profile
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --prompt-profile grounded --format json \
  > "$AUDIT_ROOT/logs/chat_grounded.json"

# --prompt-profile: rust_audit_guru profile
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --prompt-profile rust_audit_guru --format json \
  > "$AUDIT_ROOT/logs/chat_rust_audit_guru.json"

# --system-prompt: custom system prompt string
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --system-prompt "You are a Rust expert." --format json \
  > "$AUDIT_ROOT/logs/chat_sysprompt.json"

# --system-prompt-file: system prompt from file
echo "You are a Rust safety auditor." > "$AUDIT_ROOT/system_prompt.txt"
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --system-prompt-file "$AUDIT_ROOT/system_prompt.txt" --format json \
  > "$AUDIT_ROOT/logs/chat_syspromptfile.json"

# --format text: verify text output mode
uv run rsqt chat "What are the main structs?" \
  --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend stub --format text \
  > "$AUDIT_ROOT/logs/chat_text.txt"
```

Validate:
- All commands exit 0
- JSON outputs are parseable with `jq -e '.'`
- Stub baseline response is deterministic across reruns
- `--top-k 3` produces fewer context chunks than default (5)
- `--system-prompt` and `--system-prompt-file` are mutually exclusive (test both together → expect error):
  ```bash
  uv run rsqt chat "x" --index .rsqt.faiss --rsqt RSQT.parquet --backend stub --system-prompt "hi" --system-prompt-file "$AUDIT_ROOT/system_prompt.txt" 2>&1; echo "EXIT=$?"
  ```
- `--format text` produces non-JSON output
- `--prompt-profile grounded` is accepted without error
- `--prompt-profile rust_audit_guru` is accepted and returns JSON (with `citations` present)

## 4.10 Staleness model validation (P0)

Staleness behavior is command-family specific:
- Query-layer commands (`stats`, `search`, `query`, etc.) auto-refresh by default.
- `--fail-on-stale` forces fail-closed for query-layer commands.
- RAG commands (`rag-search`, `chat`) fail-closed when index/parquet fingerprint is stale.

All three behaviors must be validated.

### Auto-refresh test (P1)
```bash
cd "$WORK"

# 1. Verify queries work on fresh index
uv run rsqt stats > /dev/null; echo "FRESH_EXIT=$?"

# 2. Modify a .rs file (change source content)
source "$AUDIT_ROOT/logs/known_values.env"
echo "SOME_RS_FILE=$SOME_RS_FILE" > "$AUDIT_ROOT/logs/staleness_target_file.txt"
echo "// audit-staleness-marker" >> "$SOME_RS_FILE"

# 3. Query — should auto-refresh and succeed
uv run rsqt stats 2>&1 | tee "$AUDIT_ROOT/logs/auto_refresh.txt"; echo "AUTO_REFRESH_EXIT=$?"

# 4. Verify the staleness marker appears in the refreshed index
uv run rsqt search 'audit-staleness-marker' --limit 5 > "$AUDIT_ROOT/logs/staleness_marker_search.txt"
```

Validate:
- Step 3 succeeds (exit 0)
- Step 4 finds the new marker (proves the index was actually regenerated, not just silently used stale data)
- **If auto-refresh silently returns stale data (marker not found) → FAIL (Critical)**

### `--fail-on-stale` test (P0)
```bash
cd "$WORK"

# Start with fresh index
uv run rsqt generate --full

# Modify a .rs file
source "$AUDIT_ROOT/logs/known_values.env"
echo "SOME_RS_FILE=$SOME_RS_FILE" > "$AUDIT_ROOT/logs/failonstale_target_file.txt"
echo "// audit-failonstale-marker" >> "$SOME_RS_FILE"

# Query with --fail-on-stale — must FAIL
uv run rsqt --fail-on-stale stats 2>&1; echo "FAIL_ON_STALE_EXIT=$?"
uv run rsqt --fail-on-stale unsafe 2>&1; echo "FAIL_ON_STALE_UNSAFE_EXIT=$?"

# Expected: non-zero exit codes with staleness error messages
# If queries succeed → FAIL (Critical)

# Recover and verify
uv run rsqt generate --full
uv run rsqt --fail-on-stale stats > /dev/null; echo "RECOVERED_EXIT=$?"
```

Record all exit codes and error messages. If any `--fail-on-stale` query succeeds after source modification without regeneration → **FAIL (Critical)**.

### RAG fail-closed staleness test (P0)
```bash
cd "$WORK"

# Build fresh RAG index against current parquet
uv run rsqt rag-index RSQT.parquet --output .rsqt.stalecheck.faiss

# Mutate source to stale RSQT.parquet and RAG fingerprint chain
source "$AUDIT_ROOT/logs/known_values.env"
echo "// audit-rag-stale-marker" >> "$SOME_RS_FILE"

# rag-search/chat must fail until parquet+index are rebuilt
uv run rsqt rag-search "audit-rag-stale-marker" --index .rsqt.stalecheck.faiss --rsqt RSQT.parquet 2>&1; echo "RAG_SEARCH_STALE_EXIT=$?"
uv run rsqt chat "where is audit-rag-stale-marker?" --index .rsqt.stalecheck.faiss --rsqt RSQT.parquet --backend stub --format json 2>&1; echo "CHAT_STALE_EXIT=$?"

# Recover freshness chain and verify success
uv run rsqt generate --full
uv run rsqt rag-index RSQT.parquet --output .rsqt.stalecheck.faiss
uv run rsqt rag-search "audit-rag-stale-marker" --index .rsqt.stalecheck.faiss --rsqt RSQT.parquet --top-k 5 > "$AUDIT_ROOT/logs/rag_search_stale_recovered.txt"
```

Validate:
- stale RAG commands fail with non-zero exit and staleness/fingerprint message.
- after regeneration + reindex, commands succeed.

## 4.11 Error handling probes

Run the following and record behavior:
```bash
cd "$WORK"

# Missing RSQT.parquet (from a dir without one)
cd "$AUDIT_ROOT"
uv run rsqt stats 2>&1; echo "EXIT=$?"

# Invalid --format value
cd "$WORK"
uv run rsqt stats --format xml 2>&1; echo "EXIT=$?"

# Invalid entity kind filter
uv run rsqt entities --kind nonexistent_kind --format json 2>&1; echo "EXIT=$?"

# Invalid rag-index entity kind filter
uv run rsqt rag-index RSQT.parquet -o .rsqt.invalidkind.faiss --entity-kinds nonexistent_kind 2>&1; echo "EXIT=$?"

# rag-search with non-existent index
uv run rsqt rag-search "test" --index /nonexistent --rsqt RSQT.parquet 2>&1; echo "EXIT=$?"

# rag-search without --rsqt flag (required flag)
uv run rsqt rag-search "test" --index .rsqt.faiss 2>&1; echo "EXIT=$?"

# strict-json + text mode coercion behavior
uv run rsqt --strict-json stats --format text > "$AUDIT_ROOT/logs/strict_text_stdout.json" 2> "$AUDIT_ROOT/logs/strict_text_stderr.txt"; echo "EXIT=$?"
jq -e '.' "$AUDIT_ROOT/logs/strict_text_stdout.json"
```

Expected:
- invalid/missing-input probes return non-zero exit codes with informative error messages.
- `--strict-json` with `--format text` exits successfully but stdout is coerced to valid JSON.
- stderr includes override warning (`--strict-json overrides --format ...`).

## 4.12 Coverage guard for discovered subcommands
If Step 0.1 discovers subcommands not explicitly covered in Sections 4.1-4.11:
- run each missing subcommand with `--help` and capture output,
- run a minimal safe invocation if arguments are optional,
- otherwise mark `NOT RUN` with exact reason and evidence.

The report must include a subcommand coverage table: discovered vs tested vs not-run.

---

# Step 5 — Rate trustworthiness (required)
Assign:
- **Trust grade**: PASS / WARN / FAIL
- **Severity** of any issues: Critical / High / Medium / Low
- **Failure mode**: Incorrect results / Missing results / Unstable / Misleading UX / Poor errors / Non-deterministic / Staleness bypass / Auto-refresh corruption / Other
- **Impact** on large-project auditing (1–3 sentences)

Minimum acceptance criteria:
- Any **incorrect** safety surface count (unsafe, FFI, panic, unwrap) → FAIL (High)
- Any **incorrect** entity location (line_start/line_end don't bracket actual code) → FAIL
- Any **staleness bypass** with `--fail-on-stale` (queries succeed on stale data) → FAIL (Critical)
- Any **auto-refresh corruption** (auto-refresh succeeds but returns stale data) → FAIL (Critical)
- Any **RAG stale-chain bypass** (`rag-search`/`chat` succeed on stale index/parquet chain) → FAIL (Critical)
- Any **non-deterministic** output without explanation → WARN or FAIL (depending on impact)
- Any **strict-json breach** (invalid JSON for JSON mode, or stdout pollution in strict JSON flow) → FAIL (High)
- Any **schema-contract breach** (`rsqt schema --format json` missing required envelope fields or missing path/line/snippet semantic hints) → FAIL (High)
- Any **docs missing-only contract breach** (missing-only count differs from filtered full payload under same scope) → FAIL (High)
- Any claims of completeness not met → FAIL
- Test boundary detection wrong (prod_unwrap_count > unwrap_count) → FAIL (High)

---

# Output format (write to $AUDIT_ROOT/RSQT_TOOL_REPORT.md)
Your report must contain these sections:

1. **Executive Summary**
   - audited tool (`rsqt`)
   - overall trust recommendation
2. **Environment**
   - versions + OS info
3. **Fixture setup**
   - fixture source path
   - audit workspace path (`$WORK`)
   - file inventory summary
4. **Ground truth inventory**
   - safety surface baselines (unsafe, FFI, panic, unwrap counts per file)
   - entity extraction baselines
5. **Staleness model tests**
   - auto-refresh results
   - --fail-on-stale results
   - RAG fail-closed staleness results (`rag-search`/`chat`)
6. **Command-by-command results** (grouped by category)
   - Global flags (`--target-dir`, `--strict-json`, `--profile ci`)
   - Safety surface commands (unsafe, ffi, transmute, raw-ptrs)
   - Runtime risk commands (panics, prod-unwraps)
   - Query commands (search, query, entities)
   - Structure commands (modules, api-surface, impls, test-coverage)
   - Documentation commands (docs, doc-findings)
   - Audit & dashboards (audit, health, risk-hotspots, coverage-risk)
   - Discovery & export (generate, stats, columns, schema, dump, schemas, cli-reference, cli-help-audit)
   - per-command: tests, outputs, validations, issues, trust grade
7. **RAG pipeline results**
   - rag-index → rag-search → chat chain validation
8. **Findings & recommendations**
   - concrete fixes or mitigations
9. **Appendix: Raw logs (optional)**
   - references to `$AUDIT_ROOT/logs/`

---

# Completion condition
You are done only when:
- every discovered `rsqt` subcommand (from Step 0.1) has been executed against the fixture (or explicitly marked **NOT RUN** with a reason),
- staleness model tests (auto-refresh + --fail-on-stale + RAG fail-closed) are documented with evidence,
- safety surface validations are documented with evidence,
- `schema` and `schemas` outputs are both validated and clearly differentiated in the report,
- and `$AUDIT_ROOT/RSQT_TOOL_REPORT.md` exists and is complete.
