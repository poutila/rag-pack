# Runner Guide

## Purpose
Provide a practical operator manual for running FCDRAG packs via `run_pack.py` with predictable, auditable outputs.

## Audience
Operators and CI maintainers executing RSQT/RAQT/MDParse packs.

## When to read this
Read before running a new pack, and during incident/debug sessions.

Canonical architecture term: `FCDRAG (Fail-Closed Deterministic Corrective RAG)`.

## Prerequisites
- `uv` installed.
- Engine CLI available via `uv run` (or direct binary when `--no-uv`).
- Existing parquet + FAISS index files.
- Pack YAML and engine specs file available.

## Baseline command
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_run_01
```

## Core run controls

| Area | Flags |
|---|---|
| Inputs | `--pack`, `--parquet`, `--index`, `--engine-specs`, `--out-dir` |
| Engine/LLM | `--backend`, `--model`, `--prompt-profile`, `--max-tokens`, `--temperature`, `--top-p`, `--num-ctx`, `--no-uv` |
| Prompt files | `--system-prompt-file`, `--system-prompt-grounding-file`, `--system-prompt-analyze-file` |
| Preflights | `--cache-preflights`, `--short-circuit-preflights`, `--preflight-max-chars` |
| Retrieval adaptation | `--adaptive-top-k`, `--chat-top-k-initial` |
| Quote-bypass | `--quote-bypass-mode auto|on|off`, `--quote-bypass`, `--no-quote-bypass`, `--evidence-empty-gate`, `--no-evidence-empty-gate` |
| Stability | `--replicate`, `--replicate-seeds` |

## Mission advice hard gate (NASA-grade mode)
Mission packs (`pack_type` matching `mission`) are fail-closed for advice quality:
- `advice_mode=llm` is mandatory for every mission QID.
- If evidence exists, advice must contain at least 2 concrete issues.
- Each issue must include `ISSUE_n`, `WHY_IT_MATTERS_n`, `PATCH_SKETCH_n`, `TEST_PLAN_n`, `CITATIONS_n`.
- `CITATIONS_n` must be valid `path:line(-line)` tokens backed by injected evidence.
- Praise-only/generic advice is rejected.

Config source:
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml` -> `runner.advice_quality_gate`

Run-log signals:
- `event=question.advice.validator.ok`
- `event=question.advice.validator.issues`
- `event=run.done ... fatal_advice_gate_issues=<n>`

## Path resolution and aliases
For required paths (`--pack`, `--parquet`, `--index`, `--engine-specs`), resolution order is:
1. current working directory
2. script directory (`XREF_WORKFLOW_II_new/tools/rag_packs`)
3. repository root
4. compatibility aliases in `runner_policy.yaml` -> `runner.path_aliases`

Out-dir resolution is different:
- absolute path: used as-is
- relative multi-segment path (or path starting with `.`): resolved from current working directory
- single-segment name: created under default base `XREF_WORKFLOW_II_new/xref_state`

## Quote-bypass controls

### Modes
- `auto`: use analyze-only when evidence exists.
- `on`: force analyze-only flow.
- `off`: force standard grounding flow.

### Evidence-empty gate
If quote-bypass path is active but evidence is empty:
- default strict behavior aborts the run immediately (fail-closed).
- this is controlled by `runner.evidence_presence_gate.fail_on_empty_evidence` (default `true`).
- `--no-evidence-empty-gate` only affects legacy non-strict behavior and does not bypass strict policy mode.

## Caching and short-circuiting
Use during iterative tuning:
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_cached \
  --cache-preflights \
  --short-circuit-preflights
```

## Adaptive top-k rerun
Use when contract failures happen at lower retrieval breadth:
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_adaptive \
  --adaptive-top-k \
  --chat-top-k-initial 8
```

## Replicate mode (stability)
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_replicates \
  --replicate \
  --replicate-seeds 42,123,456
```
Outputs include per-seed directories and aggregate stability markdown files.

## Artifacts to inspect first

| Artifact | Why inspect |
|---|---|
| `REPORT.md` | question-level answer + validator issue summary |
| `RUN_MANIFEST.json` | run metadata, input hashes, plugin outputs |
| `<QID>_<step>.json` | preflight raw evidence and command argv |
| `<QID>_augmented_prompt.md` | exact injected grounding prompt |
| `<QID>_bypass_prompt.md` | exact analyze-only prompt when used |
| `<QID>_chat.json` | chat payload and answer text |
| `<QID>_advice_chat.json` | second-pass advice output when enabled |
| plugin outputs (`FINDINGS.jsonl`, `GURU_AUDIT_REPORT.md`, `GURU_METRICS.json`) | deterministic post-run quality signal |

## Deep logging: what to inspect first
`RUN_LOG.txt` is now the primary debug artifact for question-level execution flow and filter behavior.

Key events:
- `run.start`: resolved pack/engine/backend/model/parquet/index.
- `run.prompts.selected`: effective grounding/analyze prompt files.
- `question.chat.prepare`: question prompt mode, prompt file, backend/model, top-k, strict-template/retry settings.
- `preflight.step.filtered`: row/path deltas after transforms.
- `preflight.step.filtered_to_zero`: warning when non-empty step output collapses to zero rows after transforms.
- `question.done`: timing and quality counters (`schema_retries`, `adaptive_reruns`, `citations_count`).

Concrete excerpt from a recent run:
`XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
```text
event=run.start ... engine=raqt | backend=ollama | model=strand-iq4xs:latest
event=run.prompts.selected ... grounding_prompt=...RUST_GURU_GROUNDING.md | analyze_prompt=...RUST_GURU_ANALYZE_ONLY.md
event=question.chat.prepare ... qid=R_BOUNDARY_1 ... strict_response_template=True | schema_retry_attempts=2
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_trait_impls | rows_before=221 | rows_after=0
```

Concrete code excerpt for new filter diagnostics:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3085`
```python
_log_event(logging.INFO, "preflight.step.filtered", ...)
if _raw_count > 0 and _new_count == 0:
    _log_event(logging.WARNING, "preflight.step.filtered_to_zero", ...)
```

Useful log queries:
```bash
rg -n "event=run.start|event=run.prompts.selected|event=question.chat.prepare|event=question.done" XREF_WORKFLOW_II_new/xref_state/<RUN_DIR>/RUN_LOG.txt
rg -n "event=preflight.step.filtered|event=preflight.step.filtered_to_zero" XREF_WORKFLOW_II_new/xref_state/<RUN_DIR>/RUN_LOG.txt
```

## Troubleshooting

### `Missing required path: ...`
Cause: unresolved pack/parquet/index/engine-spec path.
Fix:
1. pass absolute or repo-relative explicit paths
2. verify renamed files are covered by `runner_policy.yaml` aliases
3. if the error mentions `Legacy alias '...' was checked but no file was found`, update `runner.path_aliases` to current filenames

### `Engine '...' not found in engine specs`
Cause: pack `engine:` missing from `engine_specs.yaml`.
Fix:
1. align pack `engine` to defined keys (`rsqt`, `raqt`, `mdparse`)
2. or pass the correct specs file

### Exit code `2` with contract failures
Cause:
1. schema/citation/path validation failed and `fail_on_missing_citations=true`, or
2. mission advice quality gate failed (`fatal_advice_gate_issues>0`).
Fix:
1. inspect `REPORT.md` validator issues
2. inspect `<QID>_chat.json` and prompt artifacts
3. inspect `<QID>_advice_chat.json` for missing fields / generic advice / bad citations
4. strengthen evidence (`preflight`, transforms, top-k)
5. enable analyze-only path when deterministic evidence is strong

### `NOT FOUND` despite expected evidence
Common causes:
- low top-k
- wrong chunk strategy for question class
- missing deterministic preflight command
- model compliance issue in grounding mode

Mitigations:
1. increase per-question `top_k`
2. use hybrid chunking for Cargo/workspace/feature questions
3. add deterministic preflight extraction
4. use quote-bypass mode for evidence-first analysis

If strict evidence gate is enabled, missing evidence fails immediately before model generation.

### `preflight.step.filtered_to_zero` warnings
Cause: transform filters removed all rows from a preflight step.
Fix:
1. inspect `preflight.step.filtered_to_zero` fields in `RUN_LOG.txt` (filter source, include/exclude patterns, dropped path sample).
2. confirm whether `exclude_path_regex` was intended to inherit defaults or be explicit.
3. if you need to disable default excludes for a step, set:
   `transform.exclude_path_regex: []` (explicit empty list).
4. tighten `include_path_regex` to intended production paths to avoid broad zeroing.

Concrete code precedence:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
```python
if "exclude_path_regex" in transform:
    exclude_path_regex = transform.get("exclude_path_regex")
else:
    exclude_path_regex = transform.get("_default_exclude_path_regex")
```

## Known upstream tool issues (audit-backed)
These are tool-level findings from repository audit reports; they are not runner bugs, but they affect operator expectations.

| Tool | Known issue | Operational impact | Runner-side mitigation |
|---|---|---|---|
| RSQT | `docs --missing-only` can return empty despite missing public docs | false negatives in docs compliance checks | prefer full docs evidence + explicit missing filters in preflight design |
| RSQT | `chat --format json` may emit non-JSON preamble during auto-rebuild | breaks strict JSON consumers | pre-build/refresh indexes before CI, rely on deterministic preflights where possible |
| RSQT | `entities --kind <invalid>` can silently return empty with rc=0 | typos can look like clean results | validate kind values via controlled pack templates and tests |
| RAQT | `refs` can miss/mis-shape call graph edges | boundary/call-chain conclusions may be wrong | cross-check with deterministic RSQT preflights for boundary questions |
| RAQT | dotted `rag-index --output` handling can be unsafe | index file alias/overwrite risk | use conservative output stems and verify emitted artifact names |
| RAQT | kind vocabulary mismatch (`fn` vs `function`) | silent empty retrieval in kind-filtered flows | normalize allowed aliases in pack/pipeline conventions |
| RAQT | semantic outputs may miss line-level anchors | weaker citation precision | use additional preflight evidence with explicit file:line rows |

When strict audit confidence is required, treat these as risk controls and include them in run acceptance criteria.

## Operational best practices
1. Keep `temperature=0.0` for comparable audits.
2. Cache preflights during tuning; disable cache for final clean run when inputs changed.
3. Store `RUN_MANIFEST.json` for provenance in CI artifacts.
4. Use replicate mode before declaring stability.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Pack schema: [PACK_AUTHORING.md](PACK_AUTHORING.md)
- Prompt behavior: [PROMPTS.md](PROMPTS.md)
- Research defaults: [RESEARCH_LOG.md](RESEARCH_LOG.md)

## Source anchors
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2733`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2796`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2802`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2818`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2836`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1108`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1209`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2863`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3085`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3372`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2167`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2178`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2209`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2460`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2664`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RUN_PACK_USER_MANUAL_v1_0.md:128`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RUN_PACK_USER_MANUAL_v1_0.md:38`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RUN_PACK_USER_MANUAL_v1_0.md:364`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RUN_PACK_CLI_CHEATSHEET.md:93`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:224`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:236`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:257`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/bug_reports/BUG_REPORTS_INDEX.md:1`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:191`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:200`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:208`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:215`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:66`
