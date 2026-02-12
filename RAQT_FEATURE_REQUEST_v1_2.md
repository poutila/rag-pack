# RAQT Feature Request v1.2

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Title
RAQT Rust-Semantic Reliability and Auditability Enhancements

## Date
2026-02-10

## Scope
Feature-level improvements for `raqt` (not only bug fixes) to support high-trust Rust audits and Guru-grade evidence extraction.

## Why this request exists
RAQT is already useful, but current CLI/data ergonomics force brittle workarounds in audit packs and reduce confidence for large-repo automation.

Observed evidence from current tool behavior:
- `refs` output is not modeling caller->callee edges usefully for call-graph tasks.
- `defs --kind` and `rag-index --symbol-kinds` behave inconsistently for Rust aliases (`fn` vs `function`).
- `rag-index --output` does not preserve dotted stems as users expect.
- RAQT kinds are LSP-generic (`interface`, `object`, `function`) instead of Rust-native (`trait`, `impl`, `fn`) and this leaks complexity into packs.

## Concrete evidence (from current environment)
1. Reference graph shape is not actionable:
- `uv run raqt -t <WORK> refs --format json` returned `total_refs=300` and `self_ref_rows=300` (all rows had `from_def_id == to_def_id`).

2. Kind mismatch across commands:
- `uv run raqt -t <WORK> defs --kind function --format json | jq 'length'` -> `755`
- `uv run raqt -t <WORK> defs --kind fn --format json | jq 'length'` -> `0`
- `uv run raqt -t <WORK> rag-index ... --symbol-kinds fn` indexes correctly (alias normalization present there).

3. Output naming contract surprise:
- `uv run raqt ... rag-index ... -o /tmp/raqt.dot.out.function`
- created files: `/tmp/raqt.dot.out.faiss`, `/tmp/raqt.dot.out.ids.json`, `/tmp/raqt.dot.out.meta`
- expected stem preservation for `.function` suffix was not honored.

4. Source-level code references for these behaviors:
- refs row construction: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/collector_refs.py:94`
- refs from/to assignment: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/collector_refs.py:102`
- defs exact-kind filter (no alias normalization): `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/query.py:68`
- rag-index alias normalization path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/rag.py:116`
- vector store suffix handling: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/backends.py:154`

## Requested features

### FR-1 (P0): Rust Call Graph v2 API
Add first-class call graph semantics instead of only generic reference rows.

Requested capability:
- New command family, e.g. `raqt callgraph` or `raqt refs --mode callers|callees`.
- Distinguish:
  - call-site owner definition (caller def id)
  - target definition (callee def id)
  - callsite source location (file/line/col)
- Keep existing `refs` for backward compatibility, but provide explicit semantic API for audit use.

Acceptance criteria:
- For known callsites in fixture projects, caller/callee mappings are correct and non-self by default.
- `--from-def-id` and `--to-def-id` return directionally meaningful edges.
- JSON contract includes `caller_def_id`, `callee_def_id`, `callsite_file_path`, `callsite_line_start`, `callsite_line_end`.

### FR-2 (P0): Unified Rust Kind Taxonomy + Discoverability
Normalize symbol kinds at CLI boundary and expose discoverable valid values.

Requested capability:
- New `raqt kinds` or `raqt defs --list-kinds`.
- Accept both Rust aliases and canonical internal values (e.g. `fn/function`, `trait/interface`, `impl/object`).
- Optional strict mode to fail unknown kinds.

Acceptance criteria:
- `defs --kind fn` and `defs --kind function` return the same result set.
- `defs`, `refs`-related filters, and `rag-index --symbol-kinds` share one normalization path.
- Help text lists canonical + alias mapping.

### FR-3 (P0): Stable Output Stem Contract for RAG Artifacts
Preserve user-provided `--output` stem exactly when materializing `.faiss`, `.ids.json`, `.meta`.

Requested capability:
- For output `X`, artifacts should be `X.faiss`, `X.ids.json`, `X.meta` regardless of dots in `X`.
- Avoid `Path.with_suffix` truncation behavior for multi-dot stems.

Acceptance criteria:
- `-o /tmp/a.b.c` produces `/tmp/a.b.c.faiss`, `/tmp/a.b.c.ids.json`, `/tmp/a.b.c.meta`.
- Regression tests for hidden names and dotted names.

### FR-4 (P1): Structured Output Modes for stats/schema
Add `--format json` to `stats` and `schema` for machine-safe CI consumption.

Requested capability:
- `raqt stats --format json`
- `raqt schema --format json`

Acceptance criteria:
- JSON output is valid and stable (no Python repr formatting).
- Text mode remains backward-compatible.

### FR-5 (P1): Query Surface Parity for Audit Packs
Expose richer deterministic query controls to reduce brittle grep fallbacks.

Requested capability:
- `defs`/`refs` support `--limit`, `--file`, `--columns`, deterministic sort options.
- Optional `query` command for column-projection/filter patterns similar to RSQT.

Acceptance criteria:
- Audit packs can fetch bounded evidence without post-filtering shell glue.
- Same filter semantics across commands.

### FR-6 (P1): Provenance-Rich Retrieval Results
Include semantic IDs and source spans in `rag-search` and `chat` sources.

Requested capability:
- Add `entity_id` / def-id and explicit path+line+col in returned sources.
- Ensure non-null line spans for all def-backed chunks.

Acceptance criteria:
- `chat --format json` sources include machine-usable IDs and real source spans.
- `rag-search` output can be consumed directly by deterministic citation builders.

### FR-7 (P2): RAQT Doctor/Preflight Command
Add a command to validate trust gate, workspace assumptions, and index freshness chain before audits.

Requested capability:
- `raqt doctor` checks:
  - rust-analyzer path/hash readiness
  - target dir resolution
  - parquet presence/freshness
  - faiss/meta coherence when index files exist

Acceptance criteria:
- One command gives PASS/WARN/FAIL with actionable remediation text.

## Delivery priority
- P0: FR-1, FR-2, FR-3
- P1: FR-4, FR-5, FR-6
- P2: FR-7

## Non-goals
- Replacing trust-gate policy (`--trusted` remains mandatory for generation).
- Expanding to non-Rust languages.

## Suggested rollout plan
1. Implement FR-3 quickly (low-risk path handling fix).
2. Implement FR-2 normalization + kinds introspection.
3. Implement FR-1 call graph v2 contract and tests on known fixture callsites.
4. Add FR-4/FR-5 CLI JSON/query ergonomics.
5. Add FR-6 retrieval provenance payload enhancements.
6. Add FR-7 doctor command and update manual.

## Related files
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_FEATURE_REQUEST.md`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/collector_refs.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/query.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/backends.py`
