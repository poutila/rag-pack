# RAQT Bug Reports (From Audit Run 2026-02-10)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Bug 1: `refs` misses known call edges and can return self-referential edges

- Severity: High
- Component: `raqt refs`
- Affected version: `raqt 1.0.0`
- Environment:
  - OS: Linux 6.17.0-14-generic
  - rustc: 1.92.0
  - rust-analyzer: 1.92.0

### Summary
`refs` does not return expected caller/callee relationships for known source-level calls. In some cases, it returns only self-referential rows (`from_def_id == to_def_id`) which are not useful call-graph edges.

### Reproduction
```bash
AUDIT_ROOT="/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit"
WORK="$AUDIT_ROOT/work"

export RUST_ANALYZER_PATH="$(command -v rust-analyzer)"
export RUST_ANALYZER_SHA256="$(sha256sum "$RUST_ANALYZER_PATH" | awk '{print $1}')"

uv run raqt -t "$WORK" generate --full --trusted

# Ground truth: compute_avatar calls avatar_icon_id + load_avatar_mapping
nl -ba "$WORK/src/avatar.rs" | sed -n '152,170p'

# Query refs for compute_avatar
audit_id=$(uv run raqt -t "$WORK" defs --name compute_avatar --format json | jq -r '.[0].entity_id')
uv run raqt -t "$WORK" refs --from-def-id "$audit_id" --format json
uv run raqt -t "$WORK" refs --to-def-id "$audit_id" --format json

# Query refs for known id that returns rows
uv run raqt -t "$WORK" refs --from-def-id c1bf773b820dbb133219da598af3262516393fa8a436ab86e541e139a57a3a96 --format json
uv run raqt -t "$WORK" refs --to-def-id c1bf773b820dbb133219da598af3262516393fa8a436ab86e541e139a57a3a96 --format json
```

### Expected
- For `compute_avatar`, `refs` should include call edges to `avatar_icon_id` and `load_avatar_mapping`.
- `refs --from-def-id` and `refs --to-def-id` should describe real inter-definition edges, not only self-edges.

### Actual
- `refs` for `compute_avatar` returns `[]` both directions.
- For other IDs, results can be self-referential only.

### Evidence
- `audit_runs/raqt_tool_audit/logs/fix_gt_avatar_fn_calls.log`
- `audit_runs/raqt_tool_audit/logs/fix_refs_from_compute.log`
- `audit_runs/raqt_tool_audit/logs/fix_refs_to_compute.log`
- `audit_runs/raqt_tool_audit/logs/fix_refs_from_selfid.log`
- `audit_runs/raqt_tool_audit/logs/fix_refs_to_selfid.log`

### Impact
Call-graph-driven Rust audit checks become untrustworthy.

### Proposed fix
- Validate RA reference extraction mapping for caller/callee.
- Add fixture tests that assert known call edges.
- Detect and separately classify declaration/self spans vs call edges.

### Acceptance criteria
- Known fixture calls are returned in `refs` both ways.
- `refs` includes non-self inter-definition edges for known calls.
- Regression tests for at least 3 known call relationships pass.

---

## Bug 2: `rag-index --output` with dotted names collapses suffix and can overwrite/alias outputs

- Severity: High (UX/Data safety)
- Component: `raqt rag-index`
- Affected version: `raqt 1.0.0`

### Summary
When `--output` contains dotted suffixes, output files are written to the base stem, dropping the suffix segment. This breaks expected naming and can cause accidental overwrite/aliasing.

### Reproduction
```bash
WORK="/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit/work"
cd "$WORK"

uv run raqt rag-index RAQT.parquet -o /tmp/raqt_dot_out.function --symbol-kinds function
ls -la /tmp/raqt_dot_out*
```

### Expected
Outputs preserve full logical output basename (or documented deterministic mapping), e.g. files tied to `raqt_dot_out.function`.

### Actual
Files are written as `/tmp/raqt_dot_out.faiss`, `/tmp/raqt_dot_out.ids.json`, `/tmp/raqt_dot_out.meta` (suffix `.function` dropped).

### Evidence
- `audit_runs/raqt_tool_audit/logs/fix_rag_index_dot_function.log`
- `audit_runs/raqt_tool_audit/logs/fix_rag_index_dot_function_files.log`

### Impact
Different runs/variants can collide on the same output prefix and overwrite artifacts.

### Proposed fix
- Treat provided `--output` as full prefix token; do not strip dotted suffixes.
- Document exact filename derivation.

### Acceptance criteria
- `-o /tmp/a.b.c` produces files anchored to `a.b.c` and does not collapse to `a`.
- Regression tests cover dotted and hidden-prefix names.

---

## Bug 3: `rag-index --symbol-kinds fn` returns zero chunks while docs/help imply `fn` is valid

- Severity: Medium
- Component: `raqt rag-index`
- Affected version: `raqt 1.0.0`

### Summary
`--symbol-kinds fn` indexes 0 chunks; `--symbol-kinds function` works and returns expected chunks. This is inconsistent with help/examples that suggest `fn` as a valid kind token.

### Reproduction
```bash
WORK="/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit/work"
cd "$WORK"

uv run raqt rag-index RAQT.parquet -o /tmp/raqt_dot_out.fn --symbol-kinds fn
uv run raqt rag-index RAQT.parquet -o /tmp/raqt_dot_out.function --symbol-kinds function
```

### Expected
Either:
- `fn` is accepted as alias for `function`, or
- command fails with clear validation error.

### Actual
- `fn` accepted but indexes `0` chunks.
- `function` indexes `755` chunks.

### Evidence
- `audit_runs/raqt_tool_audit/logs/fix_rag_index_dot_fn.log`
- `audit_runs/raqt_tool_audit/logs/fix_rag_index_dot_function.log`
- `audit_runs/raqt_tool_audit/logs/s2_help_rag-index.log`

### Impact
Silent under-indexing can invalidate downstream search/chat quality.

### Proposed fix
- Normalize aliases (`fn -> function`, `trait -> interface` if applicable), or hard-fail on unknown kinds.
- Align help/docs with canonical symbol kinds.

### Acceptance criteria
- `fn` and `function` produce equivalent result counts, or `fn` errors clearly.
- Integration tests assert non-zero expected function chunk count.

---

## Bug 4: Missing line-level anchors in semantic outputs (`defs`/`rag-search`/`chat`)

- Severity: Medium
- Component: `defs`, `rag-search`, `chat`
- Affected version: `raqt 1.0.0`

### Summary
Semantic outputs do not provide reliable line anchors. `chat` and `rag-search` show `line_start=0`, `line_end=0`. `defs --format json` omits line fields and source excerpts entirely.

### Reproduction
```bash
AUDIT_ROOT="/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit"
WORK="$AUDIT_ROOT/work"

uv run raqt -t "$WORK" defs --kind function --format json | sed -n '1,40p'
uv run raqt chat "What are the main structs?" --index "$WORK/.raqt.faiss" --raqt "$WORK/RAQT.parquet" --backend stub --format json
uv run raqt rag-search struct --index "$WORK/.raqt.faiss" --raqt "$WORK/RAQT.parquet" --top-k 5
```

### Expected
- `defs` includes line/column location fields and optionally source snippet.
- `chat`/`rag-search` return valid non-zero line ranges for cited chunks.

### Actual
- `defs` JSON has byte offsets only.
- `chat`/`rag-search` show line ranges as `0-0`.

### Evidence
- `audit_runs/raqt_tool_audit/logs/fix_defs_fn_t.log`
- `audit_runs/raqt_tool_audit/logs/s4_chat_stub.log`
- `audit_runs/raqt_tool_audit/logs/s4_rag_search.log`
- `audit_runs/raqt_tool_audit/logs/fix_chat_syspromptfile_abs.log`

### Impact
Weak source traceability for audit evidence and code review workflows.

### Proposed fix
- Propagate RA line/column metadata through parquet -> retrieval -> presentation.
- Add fallback byte->line mapping if needed before rendering outputs.

### Acceptance criteria
- `defs` returns line_start/line_end for rows where data exists.
- `rag-search`/`chat` sources include non-zero line ranges for Rust chunks.

---

## Bug 5: Default target resolution is easy to misuse and yields misleading stale/not-found errors

- Severity: Medium (UX/Operational correctness)
- Component: CLI target resolution (`--target-dir`, implicit defaults)
- Affected version: `raqt 1.0.0`

### Summary
Behavior differs significantly between implicit target resolution and explicit absolute `-t`. In real usage this leads to stale/not-found errors even when a fresh `RAQT.parquet` exists in the working workspace.

### Reproduction
```bash
AUDIT_ROOT="/mnt/data/Dropbox/python/omat/rust/audit_runs/raqt_tool_audit"
WORK="$AUDIT_ROOT/work"

# Explicit target works
cd "$AUDIT_ROOT"
uv run raqt -t "$WORK" stats

# Implicit/default from workspace can fail stale (depending on repo defaults)
cd "$WORK"
uv run raqt stats
```

### Expected
If current directory contains `RAQT.parquet`, implicit commands should target it by default, or emit a clear warning showing the resolved target path before execution.

### Actual
Implicit resolution can point elsewhere and produce stale/not-found responses, while explicit absolute `-t` works for the same workspace.

### Evidence
- `audit_runs/raqt_tool_audit/logs/s4_target_stats.log`
- `audit_runs/raqt_tool_audit/logs/s4_target_defs_count.log`
- `audit_runs/raqt_tool_audit/logs/s1_stats_base.log`
- `audit_runs/raqt_tool_audit/logs/fix_stats_t.log`

### Impact
Operators can run correct-looking commands and get misleading failures, reducing confidence and increasing triage time.

### Proposed fix
- Print resolved target directory/path at command start.
- Prefer CWD-local `RAQT.parquet` when present, unless overridden.
- Tighten validation/error text to include actual resolved path.

### Acceptance criteria
- Running without `-t` in a directory containing `RAQT.parquet` targets that file.
- Error output always includes resolved target path.
- Relative `-t` behavior is unambiguous in logs/help.
