# RAQT Feature Request v1.2 - Implementation Checklist

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Source
- Request: `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_FEATURE_REQUEST_v1_2.md`
- Baseline code: `/mnt/data/Dropbox/python/omat/doxslock`

## Effort scale
- S: 0.5-1.0 day
- M: 1-2 days
- L: 2-4 days

## Summary table (feature -> files -> effort)

| Feature | Priority | Primary source files | Primary tests | Effort |
|---|---|---|---|---|
| FR-1 Call Graph v2 API | P0 | `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/query.py`, `src/doxslock/raqt/collector_refs.py`, `src/doxslock/raqt/spec.py` | `tests/raqt/test_collector_refs.py`, `tests/raqt/test_refs_from_def_id.py`, `tests/raqt/test_raqt_cli_integration.py` + new callgraph tests | L (2.5-4 days) |
| FR-2 Unified kind taxonomy + discoverability | P0 | `src/doxslock/raqt/columns.py`, `src/doxslock/raqt/query.py`, `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/rag.py` | `tests/raqt/test_raqt_rag.py`, `tests/raqt/test_raqt_cli_integration.py` + new kind normalization tests | M (1-2 days) |
| FR-3 Stable dotted output stem contract | P0 | `src/doxslock/rag/_path_utils.py`, `src/doxslock/rag/backends.py`, `src/doxslock/rag/retriever.py` (if needed), `src/doxslock/rag/base.py` (meta path validation) | `tests/rag/test_path_utils.py`, `tests/rag/test_retriever_index_chunks.py`, `tests/raqt/test_raqt_rag.py` | S (0.5-1 day) |
| FR-4 `--format json` for stats/schema | P1 | `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/spec.py` (schema source stays SSOT) | `tests/raqt/test_raqt_cli_integration.py` + new formatting tests | S (0.5-1 day) |
| FR-5 Query surface parity (`limit/file/columns/sort`) | P1 | `src/doxslock/raqt/query.py`, `src/doxslock/raqt/cli.py`, `src/doxslock/qt_base/query.py` (if sort/limit support extension needed) | `tests/raqt/test_raqt_cli_integration.py`, `tests/raqt/test_refs_from_def_id.py` + new query controls tests | M-L (1.5-3 days) |
| FR-6 Provenance-rich retrieval payload | P1 | `src/doxslock/raqt/rag_chunks.py`, `src/doxslock/rag/models.py`, `src/doxslock/rag/retriever.py`, `src/doxslock/rag/chatbot.py`, `src/doxslock/raqt/cli.py` | `tests/rag/test_retriever_index_chunks.py`, `tests/rag/test_prompt_profiles.py`, `tests/raqt/test_raqt_rag.py` + new source payload tests | M (1-2 days) |
| FR-7 `raqt doctor` preflight | P2 | `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/trust_gate.py`, `src/doxslock/qt_base/cleaning.py`, `src/doxslock/rag/staleness.py` | `tests/raqt/test_trust_gate.py`, `tests/raqt/test_raqt_cli_integration.py` + new doctor tests | M (1-2 days) |

Estimated total: 8-15 engineer-days (depends on FR-1 callgraph contract and FR-5 query ergonomics scope).

---

## FR-1 (P0): Rust Call Graph v2 API

### Implementation checklist
- [ ] Add `raqt callgraph` CLI command (or `refs --mode`) with explicit directional semantics.
- [ ] Define stable JSON contract fields:
  - `caller_def_id`, `callee_def_id`
  - `callsite_file_path`, `callsite_line_start`, `callsite_line_end`
  - optional: `callsite_col_start`, `callsite_col_end`
- [ ] Ensure default mode excludes self-edges unless `--include-self` is set.
- [ ] Add filters: `--from-def-id`, `--to-def-id`, `--file`, `--limit`, `--format`.
- [ ] Keep `refs` backward-compatible; avoid breaking existing pack integrations.
- [ ] Add API-level query method(s) in `RAQuery` for directional edges.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/query.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/collector_refs.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/spec.py`

### Tests
- [ ] Extend `tests/raqt/test_collector_refs.py` for caller/callee edge assertions.
- [ ] Extend `tests/raqt/test_refs_from_def_id.py` for directionality and non-self defaults.
- [ ] Add CLI integration tests for JSON contract and filters.

---

## FR-2 (P0): Unified Rust kind taxonomy + discoverability

### Implementation checklist
- [ ] Centralize canonical kinds and aliases in one registry (`columns.py` or new `kinds.py`).
- [ ] Normalize kinds at CLI boundary for all command paths (`defs`, `refs`-related filters, `rag-index --symbol-kinds`).
- [ ] Add discoverability command:
  - `raqt kinds` or `raqt defs --list-kinds`
- [ ] Add optional strict mode (`--strict-kind`) to fail unknown kind tokens.
- [ ] Update help text with canonical+alias mapping examples.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/columns.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/query.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/rag.py`

### Tests
- [ ] Add tests proving `defs --kind fn` and `defs --kind function` return same set.
- [ ] Add tests for unknown kind behavior in permissive vs strict mode.
- [ ] Keep `rag-index --symbol-kinds` alias tests green in `test_raqt_rag.py`.

---

## FR-3 (P0): Stable output stem contract for RAG artifacts

### Implementation checklist
- [ ] Verify all artifact writes use append semantics (never `Path.with_suffix` truncation).
- [ ] Confirm for `-o /tmp/a.b.c` the outputs are:
  - `/tmp/a.b.c.faiss`
  - `/tmp/a.b.c.ids.json`
  - `/tmp/a.b.c.meta`
- [ ] Add regression tests for hidden files and dotted stems.
- [ ] Ensure existing artifacts with explicit suffix still dedupe cleanly.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/_path_utils.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/backends.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/base.py`

### Tests
- [ ] Extend `tests/rag/test_path_utils.py` with dotted/hidden stem matrix.
- [ ] Add RAQT-level integration assertion in `tests/raqt/test_raqt_rag.py`.

Note: this appears mostly implemented already; remaining work is hardening and explicit regression coverage.

---

## FR-4 (P1): Structured output modes for stats/schema

### Implementation checklist
- [ ] Add `--format` argument (`text|json`) to `stats` and `schema`.
- [ ] `stats --format json` must emit valid JSON object (not Python repr).
- [ ] `schema --format json` must emit stable machine contract (name->dtype map or list of column descriptors).
- [ ] Keep text mode output backward-compatible.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/spec.py`

### Tests
- [ ] Add CLI tests validating parseable JSON output for both commands.
- [ ] Add snapshots for text mode to prevent accidental CLI drift.

---

## FR-5 (P1): Query surface parity for audit packs

### Implementation checklist
- [ ] Add `--limit` support for `defs` and `refs`.
- [ ] Add `--file` filter (`file_path` exact or prefix, decide and document).
- [ ] Add `--columns` projection with allowlist validation against schema columns.
- [ ] Add deterministic sorting option (`--sort`, `--order asc|desc`).
- [ ] Keep defaults deterministic even without sort flags.
- [ ] Optional: add `raqt query` command for richer projection/filter patterns if needed by packs.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/query.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/qt_base/query.py` (only if generic query primitives need extension)

### Tests
- [ ] Add unit tests for limit/order/projection combinations.
- [ ] Add integration tests for bounded deterministic outputs (same order across runs).
- [ ] Add negative tests for invalid columns/sort keys.

---

## FR-6 (P1): Provenance-rich retrieval results

### Implementation checklist
- [ ] Extend retrieval source payload to include machine-usable IDs and spans:
  - `entity_id` (or equivalent def id/chunk id)
  - `file_path`
  - `line_start`, `line_end`
  - optional: `col_start`, `col_end`, `byte_start`, `byte_end`
- [ ] Ensure `chat --format json` and `rag-search` surface the same provenance contract.
- [ ] Ensure line spans are non-null for def-backed chunks.
- [ ] Preserve backward compatibility for existing source fields (`title`, `score`, etc.).

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/rag_chunks.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/models.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/retriever.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/chatbot.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`

### Tests
- [ ] Add tests validating JSON source payload keys and non-null line spans.
- [ ] Add regression tests for deterministic source ordering.

---

## FR-7 (P2): RAQT doctor/preflight command

### Implementation checklist
- [ ] Add `raqt doctor` command.
- [ ] Implement check groups with PASS/WARN/FAIL:
  - rust-analyzer path and SHA readiness
  - target dir and RAQT.parquet discoverability
  - freshness chain status (source -> parquet)
  - index coherence (`.faiss`, `.ids.json`, `.meta`) if index path provided
- [ ] Print remediation text per failed check.
- [ ] Add `--format json` for CI-friendly consumption.

### Source files
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/raqt/trust_gate.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/qt_base/cleaning.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/staleness.py`

### Tests
- [ ] Add doctor command integration tests for pass/fail scenarios.
- [ ] Add fixtures for stale parquet, missing index companion files, and invalid RA env.

---

## Cross-cutting checklist
- [ ] Documentation updates:
  - `/mnt/data/Dropbox/python/omat/doxslock/docs/RAQT_USER_MANUAL.md`
  - `/mnt/data/Dropbox/python/omat/rust/CLI_REFERENCE.md` (if RAQT command reference is maintained there)
- [ ] Keep backward compatibility for current pack automation where possible.
- [ ] Add changelog entry and migration notes for new flags/contracts.
- [ ] Add one end-to-end smoke script for CI covering: generate -> defs/refs -> rag-index -> rag-search -> chat JSON.

## Recommended implementation order
1. FR-3 (fast hardening, low risk).
2. FR-2 (kind normalization SSOT, unblocks CLI consistency).
3. FR-1 (largest semantic feature).
4. FR-4 + FR-5 (CLI/data ergonomics for audit packs).
5. FR-6 (citation/provenance contract).
6. FR-7 (preflight reliability gate).
