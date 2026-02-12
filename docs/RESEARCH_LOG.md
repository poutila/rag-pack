# FCDRAG Research Log

## Purpose
Capture the empirical tuning outcomes and the design rationale that shaped the current FCDRAG runner + pack workflow.

## Audience
Engineers tuning quality and operators choosing safe defaults.

## When to read this
Read before changing defaults, prompts, or chunking strategy.

## Scope and sources
This summary is grounded in:
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md`
- runtime controls in `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py`

## Key outcomes adopted

| Decision area | Chosen baseline | Why |
|---|---|---|
| Chunk strategy (RSQT index build) | `hybrid` | Better Cargo/workspace/features coverage with small chunk overhead |
| Prompt profile (retrieval chat) | `grounded` for audits | Stronger citation discipline and explicit fail-closed behavior |
| Retrieval breadth | `top-k` around 10-15 for boundary questions | Reduced mechanical `NOT FOUND`, improved citation richness |
| Answer flow for strict audits | deterministic preflight + quote-bypass analyze-only when evidence exists | Avoids quote-compliance failures in pure grounding mode |
| Validation posture | fail-closed schema/citation/path checks | Makes quality measurable and auditable |

## Recent optimization timeline (2026-02-11)
This timeline captures what changed, why, and what it achieved. Source of record: `XREF_WORKFLOW_II_new/tools/rag_packs/OPTIMIZATION.md`.

| ID | Change | Why | Evidence |
|---|---|---|---|
| OPT-0005 | RAQT mission path gating + normalization hardening | stale `audit_runs/...` path leakage and weak-QID retries | `OPTIMIZATION.md:166` |
| OPT-0006 | RAQT mission hotfix to restore evidence volume | over-filtering collapsed many steps to zero rows | `OPTIMIZATION.md:206` |
| OPT-0007 | Runner fix: explicit empty exclude override | `exclude_path_regex: []` previously still applied default excludes | `run_pack.py:2027` and `OPTIMIZATION.md:231` |
| OPT-0008 | Logging enrichment for filter diagnostics | prior logs lacked filter-source and dropped-path diagnostics | `run_pack.py:1868`, `run_pack.py:3085`, `OPTIMIZATION.md:256` |

Concrete code excerpt for OPT-0007:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
```python
# If a step explicitly sets exclude_path_regex (even to an empty list),
# honor that value and do not fall back to runner defaults.
if "exclude_path_regex" in transform:
    exclude_path_regex = transform.get("exclude_path_regex")
else:
    exclude_path_regex = transform.get("_default_exclude_path_regex")
```

Concrete code excerpt for OPT-0008:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3085`
```python
_log_event(logging.INFO, "preflight.step.filtered", ...)
if _raw_count > 0 and _new_count == 0:
    _log_event(logging.WARNING, "preflight.step.filtered_to_zero", ...)
```

## 3-phase protocol and mapping
Research describes a 3-phase protocol:
1. Locate (deterministic, no LLM)
2. Explain (LLM)
3. Validate (automatic fail-closed)

This maps directly to the framework pipeline:
- preflights + evidence injection
- chat or deterministic mode
- validator gates + adaptive rerun + fatal contract exit

## Failure modes observed

| Failure mode | Typical cause | Mitigation used |
|---|---|---|
| `NOT FOUND` despite expected context | retrieval window too small, wrong tool path, compliance drift | deterministic preflights, higher `top-k`, quote-bypass mode |
| Answer cites paths not in evidence | model hallucination or formatting drift | provenance gate + path gates + strict schema |
| Cargo/features questions fail under entities-only indexing | missing file-anchor context | hybrid chunking |
| High variability between runs | sampling variability | deterministic decoding (`temperature=0`, fixed seed), replicate mode |
| Overly strict grounded responses with low retrieval breadth | grounded profile + low `top-k` | adaptive top-k rerun and question-specific top-k |

Recent observed failure mode from a real run (`RAQT_MISSION_13_strand_opt`):
- heavy row collapse in filtered preflights despite successful command execution.
- example: `rows_before=221 | rows_after=0` for `R_PORTS_1_raqt_trait_impls`.
- source: `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:67`.

Concrete run excerpt:
`XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:66`
```text
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_traits | rows_before=18 | rows_after=0
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_trait_impls | rows_before=221 | rows_after=0
event=preflight.step.filtered ... qid=R_PORTS_1 | step=dyn_usage | rows_before=28 | rows_after=2
```

## Tool-audit findings that affect tuning outcomes
Repository tool audits add operational constraints beyond pure prompt/index tuning:

| Tool | Finding theme | Why it matters for tuning | Practical mitigation |
|---|---|---|---|
| RSQT | docs coverage checks can false-negative in `--missing-only` path | can overstate docs quality during evidence design | validate missing-doc counts against full docs payload |
| RSQT | `chat --format json` may include non-JSON preamble during rebuild path | machine pipelines can fail despite valid model answer | pre-build indexes before runs and keep strict parsing gates |
| RAQT | `refs` call-chain semantics can be incomplete/misleading | weakens boundary/callgraph evidence quality | pair RAQT with RSQT deterministic preflights for boundary questions |
| RAQT | kind/token mismatches and rag-index output stem behavior | can produce empty evidence or brittle artifacts | normalize kind values and use stable output stems |

These are tracked as tool-level issues/feature requests, not runner regressions.

## What was tried and deprioritized

| Tried | Status | Why |
|---|---|---|
| Relying on LLM quote behavior alone | deprioritized | unstable for deterministic extraction tasks |
| Treating model quantization as primary fix for `NOT FOUND` | deprioritized | research found retrieval/windowing dominates this failure mode |
| Pure generation without validation gates | rejected | cannot guarantee auditable contract compliance |

## What we achieved vs what remains
From `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt`:
- runner contract pass: `8/8` (`RUN_MANIFEST.json:35-37`)
- guru-level pass: `6/8` (`RUN_MANIFEST.json:49-52`)
- failing mission questions: `R_PORTS_1`, `R_MISSION_SAFETY_1` (`GURU_METRICS.json:5`)

Concrete metrics excerpt:
`XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/GURU_METRICS.json:1`
```json
{
  "guru_score": 6,
  "total_questions": 8,
  "issues": 2,
  "failing_questions": ["R_PORTS_1", "R_MISSION_SAFETY_1"]
}
```

## Runner features that encode research learnings
- quote-bypass mode (`auto|on|off`)
- evidence-empty gate to avoid unsupported generation
- adaptive top-k rerun after validator failure
- deterministic `answer_mode` and optional `advice_mode`
- replicate mode + stability summaries

## Recommended operating defaults (practical)
1. Build RSQT indexes with hybrid chunking for audit packs involving Cargo/workspace/features.
2. Start with grounded profile and question `top_k` suitable for question complexity.
3. Use deterministic preflights first; treat model as analyzer over evidence, not extractor.
4. Keep fail-closed validators on for production audit runs.
5. Use replicate mode before claiming stable "guru-level" behavior.

## Certification-style confidence checks
The tuning manual suggests a staged confidence protocol before claiming "guru-level" behavior:
1. run Core 8 as smoke
2. run Full 15 for stronger coverage
3. run at least 3 fixed-seed replicates and require stability

This aligns with the runner's replicate/stability outputs and helps separate one-off success from stable quality.

## Unknowns and constraints
- Exact quantitative gains may vary by model/backend/hardware and corpus.
- mdparse tuning depth in this folder is lighter than RSQT/RAQT tooling specifics; additional mdparse-focused experiments may still be needed for parity (`UNKNOWN/NOT FOUND`).

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Concepts: [CONCEPTS.md](CONCEPTS.md)
- Data/chunking: [DATA_MODEL_PARQUET_AND_CHUNKING.md](DATA_MODEL_PARQUET_AND_CHUNKING.md)
- Runner operations: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)

## Source anchors
- `XREF_WORKFLOW_II_new/tools/rag_packs/OPTIMIZATION.md:166`
- `XREF_WORKFLOW_II_new/tools/rag_packs/OPTIMIZATION.md:206`
- `XREF_WORKFLOW_II_new/tools/rag_packs/OPTIMIZATION.md:231`
- `XREF_WORKFLOW_II_new/tools/rag_packs/OPTIMIZATION.md:256`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1868`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3085`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_MANIFEST.json:35`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/GURU_METRICS.json:1`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:66`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:39`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:45`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:184`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:288`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:313`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:319`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:359`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:395`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING.md:629`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:29`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:47`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:62`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:79`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:166`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md:186`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2460`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2664`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:191`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAQT_TOOL_REPORT.md:208`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:3`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:224`
- `XREF_WORKFLOW_II_new/tools/rag_packs/audit_runs/rsqt_tool_audit/RSQT_TOOL_REPORT.md:236`
