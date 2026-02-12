# RSQT Feature Request v1.0

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Title
RSQT Reliability, Determinism, and Rust-Audit UX Enhancements

## Date
2026-02-10

## Scope
Feature-level improvements for `rsqt` to support CI-safe automation and Guru-grade Rust audit workflows.

## Why this request exists
RSQT is already strong for Rust indexing and query, but current CLI/output ergonomics force fragile wrappers for audit packs.

Observed evidence:
- `docs --missing-only` behavior is not usable for compliance pipelines.
- `chat --format json` is not guaranteed strict machine output during stale rebuild paths.
- `entities --kind` is not discoverable/validated enough for typo-safe automation.
- docs metadata versioning is ambiguous for provenance tracking.

Primary evidence source:
- `audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md`
- `audit_runs/rsqt_tool_audit/bug_reports/`

## Current limitations (evidence-backed)
1. Missing-doc detection contract is weak:
- Full docs output shows undocumented public entities, but `docs --missing-only` can return empty.
- Code path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/cli.py:2224`.

2. JSON mode is fragile under operational events:
- `chat --format json` can be polluted by rebuild/status output in stale-index scenarios.
- Chat command path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/cli.py:1925`.
- Shared RAG auto-refresh/rebuild path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/base.py:212`.

3. Kind filter ergonomics are typo-prone:
- `entities --kind <invalid>` can silently return `[]` and rc=0.
- Query path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/query.py:326`.

4. Provenance metadata has unclear semantics:
- Docs payload hardcodes `tool.version = "3.0.0"` while CLI is `2.0.0`.
- Serializer path: `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/cli.py:2288`.

## Requested features

### FR-1 (P0): Documentation Compliance Mode
Add a first-class docs compliance surface that is deterministic and policy-driven.

Requested capability:
- New docs policy flags:
  - `--missing-scope public|all` (default: `public`)
  - `--kinds fn,trait,struct,...`
- `--missing-only` must be equivalent to filtering full payload by `doc.has_doc == false` under selected scope.
- Optional summary output: totals by kind, visibility, and file.

Acceptance criteria:
- For any repo, `docs --missing-only` count equals filtered full-docs count under same scope.
- CI can enforce thresholds, e.g. `--fail-if-missing-gt N`.
- Regression tests cover at least one undocumented public entity and one private entity.

### FR-2 (P0): Strict Machine Output Contract
Provide a command-level mode that guarantees parseable JSON output for automation.

Requested capability:
- New global/command flag: `--strict-json` (or `--machine` profile).
- In strict mode:
  - stdout is JSON only.
  - all progress/status/rebuild messaging is stderr only.
  - no emoji/decorative prefixes in stdout text envelopes.

Acceptance criteria:
- `chat --format json --strict-json` remains valid JSON in fresh and stale-rebuild paths.
- Same contract applies to `docs`, `audit`, `doc-findings`, `entities`, `stats`, `health`.
- Tests verify `jq -e .` parseability across representative commands.

### FR-3 (P0): Entity Kind Registry + Validation
Expose valid kinds programmatically and enforce input validation.

Requested capability:
- New command/flag: `entities --list-kinds` (JSON/text).
- `entities --kind` validates against registry and returns non-zero on invalid values.
- Optional aliases map for ergonomics (for example `function -> fn`).

Acceptance criteria:
- Invalid kind always fails fast with actionable error and accepted values.
- List-kinds output is stable and documented.
- Registry used by CLI help and runtime validation from one source of truth.

### FR-4 (P1): Versioned Output Contract and Schema Introspection
Separate tool runtime version from output schema version in every machine payload.

Requested capability:
- Standard metadata envelope in JSON commands:
  - `tool.name`
  - `tool.version` (runtime CLI version)
  - `schema.name`
  - `schema.version`
- Add `rsqt schemas` (or `--print-schema`) to list command payload schemas.

Acceptance criteria:
- No hardcoded mismatched tool version values.
- Downstream pipelines can gate on schema version without guessing.
- Manual documents compatibility policy.

### FR-5 (P1): CI/Deterministic Execution Profile
Add a profile for reproducible audit runs with explicit freshness behavior.

Requested capability:
- New profile flag: `--profile ci` (or `--deterministic`), which:
  - enforces stable ordering in list outputs,
  - disables implicit behavior that can change payload shape unexpectedly,
  - requires explicit decision for stale rebuild (`fail`, `rebuild`, `prompt-never`).
- Include `resolved_paths` in JSON metadata for traceability.

Acceptance criteria:
- Repeated runs over unchanged inputs produce stable output ordering.
- Stale-index behavior is explicit and machine-documented.
- CI profile behavior is covered by integration tests.

### FR-6 (P2): Rust Audit Persona Profile for RAG QA
Add an RSQT-native retrieval/prompt profile tuned for Rust audit answers.

Requested capability:
- Built-in chat profile, e.g. `--prompt-profile rust_audit_guru`, with:
  - stricter citation requirements,
  - deterministic source selection policy,
  - concise, evidence-first answer scaffolding.
- Optional profile-level retrieval defaults (`top_k`, chunk strategy hints).

Acceptance criteria:
- Same question and unchanged index produce stable citation structure.
- Answers always include file/line-grounded evidence sections.
- Profile is documented and measurable via regression fixtures.

## Priority
- P0: FR-1, FR-2, FR-3
- P1: FR-4, FR-5
- P2: FR-6

## Non-goals
- Replacing RSQT core indexing model.
- Expanding RSQT beyond Rust source analysis in this request.
- Fixing external audit harness parser defects (tracked separately; not RSQT core).

## Dependencies and sequencing
1. Resolve bug tickets BR-001..BR-004 first (correctness and contract baseline).
2. Implement FR-3 (kind registry) because FR-1 and FR-5 depend on stable filtering semantics.
3. Implement FR-2 strict output mode and apply across high-value commands.
4. Implement FR-4 metadata/schema contract.
5. Implement FR-5 CI profile.
6. Implement FR-6 Rust audit persona profile.

## Related artifacts
- `audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md`
- `audit_runs/rsqt_tool_audit/bug_reports/BUG_REPORTS_INDEX.md`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/cli.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rsqt/query.py`
- `/mnt/data/Dropbox/python/omat/doxslock/src/doxslock/rag/base.py`
