# AGENTS.md (rag_packs)

## Mission profile
Deliver **NASA-compatible, mission-grade Rust audit outputs** for orbiter-class software.

This module exists to produce corrective, evidence-backed engineering guidance.  
Praise-only output is considered a failure mode.

## Hard requirements for mission packs
1. `advice_mode=llm` for every question.
2. If evidence exists, advice must include at least two concrete corrective issues.
3. Every issue must include:
   - `ISSUE_n`
   - `WHY_IT_MATTERS_n`
   - `PATCH_SKETCH_n`
   - `TEST_PLAN_n`
   - `CITATIONS_n`
4. `CITATIONS_n` must be valid `path:line(-line)` and present in injected evidence.
5. Generic/praise-only advice must fail the run.

## Evidence presence gate
1. Questions with zero extracted deterministic evidence must fail immediately.
2. No model-generated "improvement advice" is accepted without evidence.
3. Treat empty evidence as a retrieval/preflight defect, not as a generation task.

## Primary control points
- Runtime enforcement:
  - `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py`
- Policy knobs:
  - `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml` (`runner.advice_quality_gate`)
- Operator references:
  - `XREF_WORKFLOW_II_new/tools/rag_packs/docs/RUNNER_GUIDE.md`
  - `XREF_WORKFLOW_II_new/tools/rag_packs/RUN_PACK_USER_MANUAL_v1_0.md`
