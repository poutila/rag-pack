# RAQT Feature Request

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Title
RAQT Auditability and Usability Enhancements (Post-Correctness Hardening)

## Date
2026-02-10

## Context
This request is based on empirical findings from:
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_BUG_REPORTS.md`

Priority note: correctness defects (especially `refs` reliability) should be fixed first. The items below are feature gaps that remain important for production-grade Rust auditing.

## Problem Statement
RAQT already provides valuable capabilities (`generate`, `defs`, `refs`, `stats`, `schema`, `rag-index`, `rag-search`, `chat`), but it lacks several features required for high-trust, large-repo Rust audits:
- insufficient line-level provenance in outputs,
- weak discoverability/validation of symbol-kind filters,
- inconsistent deterministic behavior for audit workflows,
- ambiguous target resolution visibility,
- unsafe/unclear output naming behavior in index generation,
- limited support for copied fixture/workspace audit scenarios.

## Requested Features

### FR-1: End-to-End Source Provenance in Outputs
Add line/column source anchors to all user-facing semantic outputs:
- `defs`: include `line_start`, `line_end`, `col_start`, `col_end` in JSON/text modes.
- `refs`: include caller/callee source spans where available.
- `rag-search` and `chat`: return chunk source spans with real line numbers (not `0-0`).

Why:
- Audit evidence needs path+line anchoring for verification and triage.

Acceptance criteria:
- For representative fixture symbols, output lines map to real source definitions/usages.
- `rag-search` and `chat` responses expose non-zero line ranges for Rust chunks.

### FR-2: Symbol-Kind Introspection and Alias Normalization
Add explicit symbol-kind discoverability and robust filter behavior:
- new command or flag: `raqt defs --list-kinds` (or equivalent) to show canonical kinds.
- accept common aliases (`fn -> function`) or fail with explicit validation errors.

Why:
- Prevent silent under-indexing from ambiguous filter tokens.

Acceptance criteria:
- Users can enumerate valid kinds from CLI.
- Alias handling is deterministic and documented, or unknown aliases hard-fail.

### FR-3: Deterministic Audit Mode
Add a strict deterministic mode for reproducible audits:
- stable ordering in JSON/text outputs,
- predictable messaging (optional suppression/segregation of operational chatter),
- explicit normalization guidance for volatile fields.

Why:
- Reliable diffing between reruns is mandatory for audit trust.

Acceptance criteria:
- Re-running same query on unchanged data yields byte-stable output (or explicitly documented volatile fields only).
- Deterministic mode can be used in CI.

### FR-4: Explicit Target Resolution Visibility
Improve target discovery ergonomics:
- print resolved target directory and parquet path at command start,
- clear distinction between implicit default and explicit `-t/--target-dir`.

Why:
- Prevent misleading stale/not-found outcomes due to hidden path resolution.

Acceptance criteria:
- Every command logs resolved target path before query execution.
- Error messages include resolved path and concrete remediation.

### FR-5: Safer `rag-index --output` Naming Contract
Strengthen output-path semantics:
- preserve user-provided output stem exactly, including dotted suffixes,
- document artifact naming (`.faiss`, `.ids.json`, `.meta`) unambiguously.

Why:
- Avoid accidental overwrite/aliasing across variant indexes.

Acceptance criteria:
- `-o /tmp/a.b.c` produces artifacts anchored to `/tmp/a.b.c` stem.
- Regression tests cover dotted, hidden, and nested output names.

### FR-6: Workspace-Aware Fixture Audit Mode
Add first-class support for copied fixture crates in monorepo/workspace contexts:
- optional flag for isolated workspace behavior,
- clearer handling around Cargo workspace expectations during metadata validation.

Why:
- Auditing copied fixtures is common; current behavior can be brittle in workspace setups.

Acceptance criteria:
- Metadata-related workflows for copied fixtures succeed or fail with actionable guidance.
- Documentation includes tested fixture-audit patterns.

## Prioritization

### P0
- FR-1 End-to-End Source Provenance
- FR-5 Safer `rag-index --output` Naming

### P1
- FR-2 Symbol-Kind Introspection/Aliases
- FR-4 Explicit Target Resolution Visibility

### P2
- FR-3 Deterministic Audit Mode
- FR-6 Workspace-Aware Fixture Audit Mode

## Non-Goals
- Expanding to non-Rust language indexing.
- Replacing RAQT trust-gate model (`--trusted` flow remains in place).

## Dependencies and Risks
- Depends on stable and correct `refs` semantics; otherwise provenance/call-graph features provide limited value.
- Requires backward-compatible CLI evolution (new flags should avoid breaking existing scripts).

## Suggested Delivery Plan
1. Fix correctness blockers (`refs`, output stem behavior) and add regression tests.
2. Add provenance fields and symbol-kind introspection.
3. Add target-resolution diagnostics and deterministic mode.
4. Add workspace-aware fixture guidance/mode and update docs.

## Related Artifacts
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_BUG_REPORTS.md`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/raqt_tool_audit/logs/`
