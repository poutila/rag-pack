# RAQT Tool Audit Report

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## 1. Executive Summary
- Audited tool: `raqt` (invoked as `uv run raqt ...`)
- Overall trust recommendation: **FAIL** for large-project Rust auditing in current state.
- Why FAIL:
  - `refs` is not producing usable call-graph relationships for known calls (returns empty for known caller/callee pairs; also returns self-referential rows), which is an **incorrect-results** failure.
  - `rag-index --output` handling is unsafe for dotted output names (suffix collapse/truncation), which can silently overwrite/alias indexes.
  - `rag-index --symbol-kinds fn` returns 0 chunks while real function kind is `function`; docs/examples and behavior are misaligned.
- Positive controls that passed:
  - Trust gate (`--trusted` + pinned `rust-analyzer`) works.
  - Staleness fail-closed works when commands target the intended workspace with `-t`.

## 2. Environment
Evidence source: `audit_runs/raqt_tool_audit/logs/`

- OS: `Linux raju 6.17.0-14-generic ... x86_64 GNU/Linux` (`s0_uname.log`)
- `rustc`: `1.92.0` (`s0_rustc.log`)
- `cargo`: `1.92.0` (`s0_cargo.log`)
- `rust-analyzer`: `1.92.0` (`s0_ra_version.log`)
- `rg`: `14.1.1` (`s0_rg.log`)
- `jq`: `1.7` (`s0_jq.log`)
- `uv`: `0.9.27` (`s0_uv.log`)
- `raqt`: `1.0.0` (`s0_raqt_version.log`)
- Subcommands from `--help`: `generate defs refs stats schema rag-index rag-search chat` (`s0_raqt_help.log`)

Trust-gate env vars:
- `RUST_ANALYZER_PATH=/home/lasse/.cargo/bin/rust-analyzer`
- `RUST_ANALYZER_SHA256=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c`
- SHA verified against binary (`s0_ra_sha_actual.log`): match.

## 3. Fixture Setup
- Fixture source (authoritative):
  - `/mnt/data/Dropbox/python/omat/rust/crates/engine`
- Audit root:
  - `/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit`
- Workspace copy used for execution:
  - `/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit/work`
- Inventory:
  - Rust files: `75` (`s1_rs_count.log`)
  - `Cargo.toml` present in workspace (`s1_cargo_toml.log`)
  - `RAQT.parquet` generated in workspace (`s1_ls_parquet.log`)
- Index paths used:
  - `.../work/RAQT.parquet`
  - `.../work/.raqt.faiss`

Note on command targeting:
- Initial pass mixed implicit defaults and relative `-t` path usage; this can point RAQT at the wrong location.
- Corrected authoritative pass used explicit absolute `-t "$WORK"` (`fix_commands.tsv`).

## 4. Ground Truth Inventory
Evidence from source excerpts in:
- `audit_runs/raqt_tool_audit/logs/fix_gt_avatar_fn_calls.log`
- `audit_runs/raqt_tool_audit/logs/fix_gt_avatar_copy_call.log`
- `audit_runs/raqt_tool_audit/logs/fix_gt_init_calls.log`
- `audit_runs/raqt_tool_audit/logs/fix_gt_trait_ports.log`
- `audit_runs/raqt_tool_audit/logs/fix_gt_enum_layoutmode.log`

Known entities (minimum set):
- Functions:
  - `load_avatar_mapping` at `src/avatar.rs:84`
  - `avatar_icon_id` at `src/avatar.rs:101`
  - `compute_avatar` at `src/avatar.rs:152`
- Structs:
  - `AvatarInfo` at `src/avatar.rs:65`
  - `InitReport` at `src/init.rs:17`
- Trait:
  - `ProcessCtl` at `src/ports.rs:62`
- Enum:
  - `LayoutMode` at `src/paths.rs:14`

Known call relationships from source:
- `compute_avatar` calls `avatar_icon_id` (`src/avatar.rs:153`)
- `compute_avatar` calls `load_avatar_mapping` (`src/avatar.rs:166`)
- `copy_avatar` calls `safe_copy_file` (`src/avatar.rs:245`)
- `init_app_layout` calls `create_dir_if_needed` (`src/init.rs:47`)
- `init_app_layout` calls `write_file_if_needed` (`src/init.rs:60`)

Cargo metadata baseline:
- Running `cargo metadata` on isolated copied crate failed due workspace-membership expectations (`s3_cargo_metadata.log`), so metadata comparisons are **UNVERIFIABLE** in this fixture-copy layout.

Safety baseline:
- `UNWRAP_EXPECT=975`, `UNSAFE=0`, `FFI=0` (`s3_baseline_safety.log`)

## 5. Trust Gate & Staleness Tests

### Trust gate
- Positive test:
  - Command: `uv run raqt generate --full --trusted`
  - Exit: `0` (`s1_generate_trusted.log`, `fix_generate_t`)
- Negative test:
  - Command: `uv run raqt generate --full`
  - Exit: `1` with explicit refusal (`s1_generate_no_trusted.log`)
  - Error excerpt: `RAQT generate requires --trusted flag.`

### Staleness fail-closed (authoritative run with `-t`)
- Fresh query works:
  - `uv run raqt -t "$WORK" defs --kind function --format json | jq 'length'`
  - Exit: `0` (`fix_stale_fresh_defs`, output length `755`)
- Modify source:
  - Appended marker to `src/avatar.rs` (`fix_stale_modify`, `fix_stale_file.log`)
- Post-modification queries fail closed:
  - `defs` exit `1` (`fix_stale_defs.log`)
  - `rag-search` exit `1` with stale context showing modified file (`fix_stale_rag.log`)
- Regeneration recovery:
  - `generate --full --trusted` exit `0` (`fix_stale_regen`)
  - `defs` works again, length `755` (`fix_stale_recovered_defs.log`)

Verdict: Trust gate and stale-source fail-closed behavior are **PASS** when commands are correctly targeted.

## 6. Command-by-Command Results

### `generate`
- Status: **PASS**
- Evidence:
  - `s1_generate_trusted` rc=0
  - `s1_generate_no_trusted` rc=1 with required trust warning

### `defs`
- Corrected target run: **PASS with limitations**
  - `fix_defs_fn_t` rc=0
  - deterministic across reruns after normalization (`fix_norm_defs_t` rc=0)
- Limitation / UX gap:
  - Output lacks `line_start/line_end` and `source_text` fields; only bytes are returned in JSON.
  - In-practice line-level location validation is **UNVERIFIABLE** from `defs` output alone.

### `refs`
- Status: **FAIL (High)**
- Evidence:
  - Known caller/callee case (`compute_avatar`) returns empty both directions:
    - `fix_refs_from_compute.log` = `[]`
    - `fix_refs_to_compute.log` = `[]`
  - A known id can return only self-referential edges (`from_def_id == to_def_id`) rather than meaningful caller/callee mapping:
    - `fix_refs_from_selfid.log`
    - `fix_refs_to_selfid.log`
- Failure mode: **Incorrect results / Missing results**
- Impact: Call-graph-based audit checks are not trustworthy.

### `stats`
- Status: **PASS** (with explicit `-t`)
- Evidence:
  - `fix_stats_t` rc=0; reports `num_rows=1715`, `num_columns=28`, `def_count=1414`.
  - stable across rerun (`fix_norm_stats_t` rc=0)

### `schema`
- Status: **PASS**
- Evidence:
  - `s4_schema` and rerun both rc=0; 28 columns listed.

### `rag-index`
- Baseline build: **PASS**
  - `s4_rag_index_base` rc=0
- `--include-refs`/`--no-include-refs`: **PASS**
  - both rc=0 and `.meta` sizes differ (`fix_rag_index_nodot_sizes.log`)
- Findings:
  - **WARN/High UX bug**: dotted `--output` names collapse to base stem files (suffix not preserved as expected).
    - Example: `-o /tmp/raqt_dot_out.function` writes `/tmp/raqt_dot_out.*` (`fix_rag_index_dot_function_files.log`)
  - **WARN**: `--symbol-kinds fn` gives 0 chunks while `--symbol-kinds function` works.
    - `fix_rag_index_dot_fn.log`: `Indexed 0 chunks`
    - `fix_rag_index_dot_function.log`: `Indexed 755 chunks`

### `rag-search`
- Status: **PASS with caveat**
- Evidence:
  - baseline search works (`s4_rag_search` rc=0)
  - stale-source correctly fails in corrected stale test (`fix_stale_rag` rc=1)
- Caveat:
  - rerun can emit auto-rebuild chatter when RAQT/FAISS mismatch exists (`s4_rag_search_rerun.log`), reducing strict output determinism (`s5_norm_rag_search` rc=1).

### `chat`
- Status: **PASS (stub backend), with metadata limitation**
- Evidence:
  - baseline and flag tests mostly rc=0: model/top-k/max-tokens/temperature/prompt-profile/system-prompt/text format.
  - mutual exclusion correctly errors (`s4_chat_mutual_exclusion.log`).
  - `--system-prompt-file` works with absolute path (`fix_chat_syspromptfile_abs.rc` = 0).
- Limitation:
  - returned source line ranges are `0-0` in responses (`s4_chat_stub.log`, `fix_chat_syspromptfile_abs.log`).

## 7. RAG Pipeline Results (rag-index -> rag-search -> chat)
- Chain execution succeeded end-to-end on fixture copy:
  - `rag-index` built index
  - `rag-search` returned relevant `src/init.rs` chunks
  - `chat --backend stub` returned deterministic JSON/text responses and sources
- But trust caveats remain:
  - line anchors in retrieved sources are `0-0`
  - output-file naming for dotted `-o` is hazardous
  - `refs` reliability is insufficient for deep semantic auditing

## 8. Findings & Recommendations

### F1. `refs` does not provide reliable call graph (FAIL, High)
- Failure mode: Incorrect results / Missing results
- Evidence:
  - `fix_refs_from_compute.log`, `fix_refs_to_compute.log`, `fix_refs_from_selfid.log`
- Recommendation:
  - Validate ref extraction against known call sites (`compute_avatar -> avatar_icon_id`, etc.) in CI.
  - Reject/flag self-only ref edges unless explicitly expected.
  - Add command-level contract tests for `refs --to-def-id` and `refs --from-def-id` using fixture functions with known calls.

### F2. `rag-index --output` dotted path behavior is unsafe (WARN, High UX)
- Failure mode: Misleading UX / Potential overwrite risk
- Evidence:
  - `fix_rag_index_dot_function_files.log`
- Recommendation:
  - Preserve full output basename exactly as provided by user.
  - Add regression tests for outputs like `.raqt.faiss.refs_on` and `/tmp/x.y.z`.

### F3. Symbol-kind vocabulary mismatch (`fn` vs `function`) (WARN, Medium)
- Failure mode: Missing results / Misleading docs
- Evidence:
  - `fix_rag_index_dot_fn.log` vs `fix_rag_index_dot_function.log`
- Recommendation:
  - Accept aliases (`fn -> function`) or update all docs/help examples to canonical kind names.

### F4. Missing line-level anchors in semantic outputs (WARN, Medium)
- Failure mode: Reduced auditability
- Evidence:
  - Chat/rag-search source spans report `line_start=0`, `line_end=0`.
- Recommendation:
  - Populate and surface line/column fields from RA data through defs/refs/rag/search/chat outputs.

### F5. Default target-dir resolution is easy to misuse (WARN, Medium UX)
- Failure mode: Misleading UX
- Evidence:
  - Initial pass had stale/not-found behavior when not using explicit correct `-t`.
- Recommendation:
  - Strongly warn when implicit target differs from current working directory.
  - Consider `pwd`-local default precedence or clearer startup banner showing resolved target.

## 9. Appendix: Raw Logs
Primary artifacts:
- Initial run ledger: `audit_runs/raqt_tool_audit/commands.tsv`
- Corrected run ledger: `audit_runs/raqt_tool_audit/fix_commands.tsv`
- Logs directory: `audit_runs/raqt_tool_audit/logs/`

High-value logs:
- Environment: `s0_*.log`
- Trust gate/staleness: `s1_generate_no_trusted.log`, `fix_stale_*.log`
- Corrected defs/refs/stats: `fix_defs_*.log`, `fix_refs_*.log`, `fix_stats_t.log`
- RAG/chat: `s4_rag_*.log`, `s4_chat_*.log`, `fix_rag_index_*.log`, `fix_chat_syspromptfile_abs.log`

---

## Completion Checklist
- All 8 subcommands executed: **Yes** (`generate`, `defs`, `refs`, `stats`, `schema`, `rag-index`, `rag-search`, `chat`)
- Trust gate and staleness documented with evidence: **Yes**
- Validations documented with evidence: **Yes**
- `RAQT_TOOL_REPORT.md` written in current directory: **Yes**
