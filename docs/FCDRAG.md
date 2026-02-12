# FCDRAG: Deep Explainer

## Purpose
Define `FCDRAG (Fail-Closed Deterministic Corrective RAG)` precisely, explain why this architecture exists in this repo, and map it to known RAG topologies.

## Audience
Engineers who already know LLMs/RAG and want exact operational semantics, tradeoffs, and implementation constraints.

## When to read this
Read this first if someone asks "What is FCDRAG?" then continue with [ARCHITECTURE.md](ARCHITECTURE.md), [CONCEPTS.md](CONCEPTS.md), and [RUNNER_GUIDE.md](RUNNER_GUIDE.md).

## One-line definition
`FCDRAG` is a RAG topology where deterministic evidence extraction and strict post-generation contracts are first-class, and failures are explicit (fail-closed), not silently accepted.

## Acronym breakdown
| Letter | Meaning in this repo | Concrete mechanism |
|---|---|---|
| `F` | Fail-Closed | Contract failures can terminate run (`SystemExit(2)`) |
| `C` | Corrective | Schema/path/citation issues trigger correction loops |
| `D` | Deterministic | Preflight evidence is extracted by tool CLIs before LLM answer |
| `RAG` | Retrieval-Augmented Generation | Engine retrieval + augmented prompts + generation |

Concrete code excerpt:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3800`
```python
if fatal_contract_issues and pack.validation.fail_on_missing_citations:
    raise SystemExit(2)
```

## How FCDRAG maps to known topologies
From `RAG_TOPOLOGIES.md`, this repo matches a composite:
1. Corrective RAG (`RAG_TOPOLOGIES.md:9`): generate -> detect issues -> correct.
2. Rule-Based RAG (`RAG_TOPOLOGIES.md:189`): strict response/citation/path rules.
3. Iterative RAG (`RAG_TOPOLOGIES.md:213`): retry loops with validator feedback.
4. Adaptive RAG (`RAG_TOPOLOGIES.md:57`): retrieval depth can increase when validation fails.

It is not plain "single-shot retrieve+generate". It is an orchestrated multi-stage contract pipeline.

## How FCDRAG actually works here
1. Resolve policy + pack + engine specs.
2. Run deterministic preflights (`rsqt`/`raqt`/`mdparse`) and persist artifacts.
3. Transform/filter evidence and inject `CITE=` anchors into prompts.
4. Choose answer path:
   - deterministic mode (skip model), or
   - LLM mode (grounding/analyze-only).
5. Validate response schema + citation provenance + path gates.
6. If needed, run corrective loops (adaptive top-k rerun and/or schema retry).
7. Emit auditable artifacts (`REPORT.md`, `RUN_MANIFEST.json`, per-question files, plugin metrics).

Concrete evidence of this sequence:
`XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
```text
event=run.start ...
event=run.prompts.selected ...
event=preflight.step.start ...
event=question.chat.prepare ...
event=question.done ...
```

Concrete code anchors:
- preflight orchestration: `run_pack.py:2941`
- prompt preparation: `run_pack.py:3372`
- schema retry: `run_pack.py:3476`
- fail-closed exit: `run_pack.py:3800`

## Why this architecture was built
The repo needed all of these simultaneously:
1. deterministic, inspectable evidence artifacts per question
2. strict machine-checkable answer contracts (`VERDICT`/`CITATIONS`)
3. path-level provenance controls (no new paths, no uncited paths)
4. explicit failure semantics for CI/audit use
5. optional specialist LLM pass without giving up deterministic controls

`run_pack.py` and the pack/validator/rule files implement this combined requirement set.

## "Was there no known solution?"
Inside this repo, no single drop-in component was found that combined all five requirements above with this file-level artifact granularity.

What is known from repo evidence:
1. existing engine CLIs provide retrieval/extraction primitives.
2. this runner composes them with fail-closed contracts and plugin-based audit outputs.

What is `UNKNOWN/NOT FOUND` from repo evidence:
- whether an external off-the-shelf framework outside this repo fully matches these exact constraints.

## Challenges encountered
### 1) Evidence collapse due transform filtering
Observed in real run:
`XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:67`
```text
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_trait_impls | rows_before=221 | rows_after=0
```

### 2) Default-vs-explicit exclude precedence bugs
Fix implemented:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
```python
if "exclude_path_regex" in transform:
    exclude_path_regex = transform.get("exclude_path_regex")
else:
    exclude_path_regex = transform.get("_default_exclude_path_regex")
```

### 3) Need for richer run telemetry
Enrichment implemented:
`XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3085`
```python
_log_event(logging.INFO, "preflight.step.filtered", ...)
if _raw_count > 0 and _new_count == 0:
    _log_event(logging.WARNING, "preflight.step.filtered_to_zero", ...)
```

### 4) Contract pass vs guru-quality pass divergence
Observed in same run:
- runner contract score `8/8`
- guru score `6/8`

Source:
- `RUN_MANIFEST.json:35`
- `GURU_METRICS.json:1`

## What we learned
1. "RAG quality" is not only model quality; evidence plumbing and contract gates dominate failure behavior.
2. Deterministic preflights reduce ambiguity and improve auditability.
3. Corrective loops help, but only if evidence quality is preserved.
4. Fail-closed semantics are essential for mission-grade use cases; silent pass is unacceptable.
5. Rich logs are not optional for optimization; they are the feedback loop.

## How to explain FCDRAG quickly
If someone asks "I know RAG, what is FCDRAG?":
1. It is RAG with deterministic evidence first.
2. The model is constrained by explicit output contracts.
3. The system self-corrects schema/provenance issues.
4. If contract requirements are not met, the run fails explicitly.

## Related docs
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Concepts: [CONCEPTS.md](CONCEPTS.md)
- Runner operations: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Research/tuning outcomes: [RESEARCH_LOG.md](RESEARCH_LOG.md)
- Terms: [GLOSSARY.md](GLOSSARY.md)

## Source anchors
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:9`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:57`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:189`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:213`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1291`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1327`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1425`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2027`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2413`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2463`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2941`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3372`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3476`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:3800`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_MANIFEST.json:35`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/GURU_METRICS.json:1`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
- `XREF_WORKFLOW_II_new/xref_state/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:67`
