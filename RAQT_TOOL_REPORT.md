# RAQT Tool Audit Report (v2.6)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## 1. Executive Summary
- Audited tool: `raqt` (invoked as `uv run raqt ...`)
- Audit run root: `/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224`
- Tool version under test: `raqt 1.1.0` (`logs/s0_raqt_version.log`)
- Trust grade: **WARN** (no critical fail-closed break found; some audit-surface and UX gaps remain)

Key outcomes:
- Trust gate works: `generate --full --trusted` passes, `generate --full` fails with explicit trust warning (`logs/s1_generate_trusted.exit`, `logs/s1_generate_no_trusted.log`).
- All 12 subcommands were executed successfully or with expected error semantics.
- Strict stale behavior is fail-closed when tested strict-first (supplemental probe): query exits `2`, RAG/chat exits non-zero with explicit stale error (`logs/strict_probe_*.exit`, `logs/strict_probe_*.err`).
- The scripted Step 3/Step 4 symbol derivation assumed `work/src` + `work/tests`; this fixture is a workspace under `work/crates`, so some baseline ground-truth steps were initially `UNVERIFIABLE` and were supplemented.

## 2. Environment
Evidence root: `/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224/logs`

- OS: `Linux raju 6.17.0-14-generic ...` (`logs/s0_uname.log`)
- `rustc`: `1.92.0` (`logs/s0_rustc.log`)
- `cargo`: `1.92.0` (`logs/s0_cargo.log`)
- `rust-analyzer`: `1.92.0` (`logs/s0_ra_version.log`)
- `rg`: `14.1.1` (`logs/s0_rg.log`)
- `jq`: `1.7` (`logs/s0_jq.log`)
- `uv`: `0.9.27` (`logs/s0_uv.log`)
- `raqt`: `1.1.0` (`logs/s0_raqt_version.log`)

Trust values actually used for generation:
- `RUST_ANALYZER_PATH=/home/lasse/.cargo/bin/rust-analyzer`
- `RUST_ANALYZER_SHA256=20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c`
- Evidence: `logs/s1_effective_ra_env.log`

## 3. Fixture Setup
- `FIXTURE_SRC=/home/lasse/Dropbox/python/omat/doxslock/tests/rsqt/rust` (`logs/run_env.txt`)
- `AUDIT_ROOT=/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224` (`AUDIT_PATHS.env`)
- `WORK=/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224/work` (`AUDIT_PATHS.env`)
- `LOGS=/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224/logs` (`AUDIT_PATHS.env`)

Artifacts:
- Rust files in copy: `107` (`logs/s1_rs_count.log`)
- `Cargo.toml` files found at workspace root + crates (`logs/s1_cargo_toml.log`)
- Parquet generated: `work/RAQT.parquet` (`logs/s1_ls_parquet.log`)
- Locks observed: `.parquet.lock` for RAQT/RSQT/MD_PARSE (`logs/s1_lock_files.log`)

## 4. Ground Truth Inventory
Initial scripted ground truth was invalid for this fixture layout:
- `work/src` and `work/tests` do not exist; `rg` errors recorded (`logs/s3_gt_fn.log`, `logs/s3_gt_struct.log`, `logs/s3_gt_trait.log`, `logs/s3_gt_enum.log`).
- `KNOWN_SYMBOL`/`KNOWN_CONCEPT` therefore empty (`logs/known_symbols.env`).

Supplemental source-grounded inventory (workspace-aware under `work/crates`):
- Functions sample (10 captured): `logs/supp_gt_fn.log`
- Structs sample (10 captured): `logs/supp_gt_struct.log`
- Traits sample (6 captured): `logs/supp_gt_trait.log`
- Enums sample (10 captured): `logs/supp_gt_enum.log`

Representative call evidence from source scans:
- `compute_avatar` calls `avatar_icon_id` and `load_avatar_mapping` in `crates/engine/src/avatar.rs` (`logs/s3_gt_calls_index.log`)
- `init_app_layout` calls `create_dir_if_needed` and `write_file_if_needed` in `crates/engine/src/init.rs` (`logs/s3_gt_calls_index.log`)

Cargo/safety baseline:
- `cargo metadata` succeeded in copied workspace (`logs/s3_cargo_metadata.log`)
- `UNWRAP_EXPECT=0`, `UNSAFE=0`, `FFI=0` in scripted baseline (due initial `src/tests` path assumption) (`logs/s3_baseline_safety.log`)

## 5. Trust Gate and Staleness Tests
Trust gate:
- Positive: `uv run raqt -t "$WORK" generate --full --trusted` exit `0` (`logs/s1_generate_trusted.exit`)
- Negative: `uv run raqt -t "$WORK" generate --full` exit `1` with explicit trust refusal (`logs/s1_generate_no_trusted.exit`, `logs/s1_generate_no_trusted.log`)

Scripted staleness sequence (prompt order):
- `stale_defs_auto`, `stale_callgraph_auto`, then strict variants all returned success (`logs/stale_*.exit`).
- This sequence is not conclusive for strict-mode fail-closed because auto-refresh calls ran first.

Strict-first supplemental stale probe (conclusive):
- `--fail-on-stale defs` exit `2` with `RAQT.parquet is stale - Command rejected` (`logs/strict_probe_defs_fail_on_stale.exit`, `logs/strict_probe_defs_fail_on_stale.err`)
- `--profile ci defs` exit `2` with same stale rejection (`logs/strict_probe_defs_profile_ci.exit`, `logs/strict_probe_defs_profile_ci.err`)
- `--fail-on-stale rag-search` exit `1` with stale hash-proof failure (`logs/strict_probe_rag_fail_on_stale.exit`, `logs/strict_probe_rag_fail_on_stale.err`)
- `--profile ci chat` exit `1` with stale hash-proof failure (`logs/strict_probe_chat_profile_ci.exit`, `logs/strict_probe_chat_profile_ci.err`)

Verdict:
- No strict-mode stale bypass observed in strict-first testing.

## 6. Command-by-Command Results
`generate`
- PASS: trusted generate exits `0`; untrusted generate exits non-zero with explicit safety guidance.
- Evidence: `logs/s1_generate_trusted.exit`, `logs/s1_generate_no_trusted.log`.

`defs`
- PASS: `--kind function` and alias `--kind fn` both exit `0`; both lengths `905`.
- Evidence: `logs/defs_function.exit`, `logs/defs_fn_alias.exit`, `logs/defs_function.json`, `logs/defs_fn_alias.json`.

`refs`
- PASS (supplemental execution required due empty `KNOWN_SYMBOL` in scripted path).
- Evidence: `logs/supp2_refs_to.exit=0` (47 rows), `logs/supp4_refs_from.exit=0` (16 rows).

`callgraph`
- PASS (supplemental execution required due empty `KNOWN_SYMBOL` in scripted path).
- Evidence: `logs/supp3_callgraph_default.exit=0` (16 rows), directional fields present (`caller_def_id`, `callee_def_id`).

`kinds`
- PASS: alias map includes `fn`, `trait`, `const`, `variant`.
- Evidence: `logs/kinds.json`.

`stats`
- PASS: valid JSON and plausible counts (`num_rows=1841`, `def_count=1835`, `file_count=100`).
- Evidence: `logs/stats.exit`, `logs/stats.json`.

`schema`
- PASS: valid JSON schema with 28 columns.
- Evidence: `logs/schema.exit`, `logs/schema.json`.

`doctor`
- PASS:
  - No-index mode exits `0` with PASS checks.
  - Missing explicit index path exits non-zero with `index` FAIL.
  - Present index exits `0` with PASS.
- Evidence: `logs/doctor_no_index.json`, `logs/doctor_with_missing_index.json`, `logs/doctor_with_index_present.json`.

`rag-index`
- PASS:
  - Base index and all variants (`function`, `fn`, `refs_on`, `refs_off`) exit `0`.
  - Expected companion files present for each output stem.
- Evidence: `logs/rag_index_*.exit`, `logs/rag_index_ls_all.log`.

`rag-search`
- PASS:
  - Text and JSON formats both exit `0`; JSON parse check passes.
  - Default stale behavior can auto-rebuild FAISS when parquet/index mismatch exists.
- Evidence: `logs/rag_search.exit`, `logs/rag_search_json.exit`, `logs/rag_search_json.jq.exit`, `logs/stale_rag_default.txt`.

`chat`
- PASS (stub backend path):
  - JSON variants parse successfully.
  - Text mode works.
  - Mutual exclusion (`--system-prompt` + `--system-prompt-file`) errors as expected.
- Evidence: `logs/chat_json_parse_status.txt`, `logs/chat_text.exit`, `logs/chat_mutual_exclusion.exit`, `logs/chat_mutual_exclusion.txt`.

`cli-reference`
- PASS:
  - Command exits `0`.
  - Generated markdown is non-empty.
- Evidence: `logs/cli_reference.exit`, `logs/cli_reference.nonempty.exit`, `logs/cli_reference.md`.

Global option checks:
- `--strict-json` coerces output to JSON and keeps status on stderr.
- `--profile ci` accepted in query path.
- Evidence: `logs/strict_stats.out.json`, `logs/strict_stats.err.txt`, `logs/profile_ci_defs_fresh.exit`.

## 7. RAG Pipeline Results (`rag-index` -> `rag-search` -> `chat`)
- Indexing chain completed successfully from `RAQT.parquet` to FAISS artifacts (`logs/rag_index_base.log`, `logs/rag_index_ls_all.log`).
- `rag-search` returned relevant ranked chunks with source titles and line spans (`logs/rag_search.txt`, `logs/rag_search.json`).
- `chat --backend stub` produced deterministic JSON contract (`answer`, `sources`, `model`, `backend`) and text output mode (`logs/chat_stub.json`, `logs/chat_text.txt`).
- Rerun normalization and diffs were stable for defs/stats/schema/doctor/rag/chat (`logs/diff_*.exit=0`, `logs/diff_*.txt` empty).

## 8. Findings and Recommendations
1. `WARN` - Prompt workflow is not fixture-layout-agnostic.
- Evidence: `logs/s3_gt_*.log` path errors and empty `logs/known_symbols.env`.
- Impact: `refs`/`callgraph` may be skipped despite tool health.
- Recommendation: update prompt scripts to detect workspace roots and search `work/crates/**/{src,tests}` when `work/src` is absent.

2. `WARN` - Scripted stale test ordering can mask strict-mode validation.
- Evidence: scripted strict checks follow auto-refresh calls (`logs/stale_*.exit` all `0`), while strict-first probe correctly fails (`logs/strict_probe_*.exit`).
- Impact: false confidence risk if only scripted stale outputs are read.
- Recommendation: run strict checks immediately after mutation, before any auto-refresh path.

3. `LOW` - `callgraph` row line anchors may be null even when byte ranges are present.
- Evidence: `logs/supp3_callgraph_default.json` rows include `line_start: null`, `line_end: null`.
- Impact: lower ergonomics for line-precise UX, but data is still joinable via byte offsets and file path.
- Recommendation: if intended, document this explicitly; otherwise populate line anchors in callgraph projection.

## 9. Appendix: Raw Logs
- Audit root: `/mnt/data/Dropbox/python/omat/rust/XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit_v26_20260211_015224`
- Primary pointers:
  - `AUDIT_PATHS.env`
  - `logs/run_env.txt`
  - `logs/s0_*.log`
  - `logs/s1_*.log`
  - `logs/s2_help_status.txt`
  - `logs/s3_*` + `logs/supp_gt_*`
  - `logs/strict_probe_*`
  - `logs/supp*_{refs,callgraph}*`
  - `logs/rag_*`, `logs/chat_*`, `logs/cli_reference.*`
  - `logs/diff_*.txt`, `logs/diff_*.exit`

Completion status:
- 12/12 audited subcommands executed and evidenced.
- Trust gate tested (positive/negative).
- Staleness semantics tested (scripted + strict-first supplemental).
- Fresh `RAQT_TOOL_REPORT.md` generated for this run.
