# Evidence Usage Validation Plan

Purpose: Prove that deterministic evidence produced by RSQT/RAQT is not only collected, but actually injected, cited, and used by final answers.

Audience: Maintainers of `run_pack.py`, pack authors, and audit operators.

When to read this: Before changing preflight schemas, evidence transforms, prompt wiring, or validation gates.

## Scope and SSOT

This plan is grounded in:

- `run_pack.py`
- `runner_policy.yaml`
- `out/*` run artifacts and logs
- `audit_runs/*` tool audit outputs

Current code checkpoints:

- Preflight filtering telemetry: `run_pack.py:3329`, `run_pack.py:3347`
- Evidence injection loop: `run_pack.py:3362`
- Evidence summary telemetry: `run_pack.py:3506`
- Citation/path validation gates: `run_pack.py:3524`, `run_pack.py:3530`, `run_pack.py:1417`, `run_pack.py:1515`
- Empty-evidence fail-fast gate: `run_pack.py:3550`, `run_pack.py:3566`
- Field mapping policy (row/path/line/snippet aliases): `runner_policy.yaml:172`, `runner_policy.yaml:175`, `runner_policy.yaml:176`, `runner_policy.yaml:177`

Observed evidence from recent runs:

- `question.evidence.summary` appears for mission packs with non-zero evidence blocks in multiple runs, e.g. `out/RAQT_MISSION_15_strand_opt/RUN_LOG.txt`.
- `preflight.step.filtered_to_zero` appears repeatedly for `R_MISSION_SAFETY_1` / `panic_inventory`, e.g. `out/RAQT_MISSION_15_strand_opt/RUN_LOG.txt`.
- There are historical answers with `INSUFFICIENT EVIDENCE` despite successful preflights and injected prompt sections, e.g. `out/general_v1_6_16/REPORT.md`, `out/general_v1_6_16/R_API_2_augmented_prompt.md`.

## Validation Questions

1. Did preflight outputs parse into rows correctly (no silent schema drift)?
2. Did parsed rows survive transforms/filters?
3. Did surviving rows get injected into the final prompt?
4. Did the answer cite only injected evidence and avoid non-evidence paths?
5. When critical evidence is removed, does the answer degrade/fail (causal proof of usage)?

## Threat Model (What Can Go Wrong)

- Field-name mismatch between RSQT/RAQT output and `iter_rows_keys` / `path_keys`.
- Transform filters dropping valid evidence to zero (`filtered_to_zero`), especially due path constraints.
- Evidence injected but model still outputs generic `INSUFFICIENT EVIDENCE`.
- Citation tokens in answers not backed by injected evidence.
- Path canonicalization mismatch (artifact paths vs repo paths) causing false gate failures.
- Missing run logs (many historical outputs have `REPORT.md` but no `RUN_LOG.txt`), limiting forensic confidence.

## Workstream A: Schema and Mapping Validation (Deterministic)

Goal: prove row extraction is robust to RSQT/RAQT schema variants.

Tasks:

1. Build a fixture corpus from real artifacts in:
   - `audit_runs`
   - `out/*/*_*.json`
2. For each fixture, replay parsing logic used by `_iter_rows` and path/line/snippet extraction aliases from `runner_policy.yaml`.
3. Emit a matrix: `artifact -> extracted_rows -> rows_with_path -> rows_with_line`.
4. Flag any artifact with expected hits but zero extracted rows.

Acceptance criteria:

- 0 fixtures with known-hit content returning zero extracted rows.
- Any new tool output key must be intentionally added to policy aliases.

## Workstream B: Injection Integrity (Artifact -> Prompt)

Goal: prove every intended preflight artifact is represented in augmented prompt.

Tasks:

1. For each QID, compare successful preflight steps (`rc=0`) with `[Preflight <step>]` sections in `*_augmented_prompt.md`.
2. Validate `CITE=` token exists per injected section and maps to actual artifact.
3. Add a machine-readable per-question trace file (proposed): `QID_evidence_trace.json` with:
   - preflight step names
   - artifact paths
   - rows before/after filters
   - evidence block count
   - allowed citation tokens

Acceptance criteria:

- 100% of successful preflight steps either injected or explicitly marked skipped-with-reason.
- No injected `CITE=` token pointing to missing artifact.

## Workstream C: Contract Coupling (Prompt -> Answer)

Goal: prove answer structure and citations are constrained by evidence.

Tasks:

1. Keep `enforce_citations_from_evidence`, `enforce_no_new_paths`, and `enforce_paths_must_be_cited` enabled for target packs.
2. Parse each `*_chat.json` into final answer and extract citation tokens.
3. Compare cited tokens against allowed set derived from prompt evidence blocks (same logic as `validate_citations_from_evidence`).
4. Fail run if citations/path mentions exceed allowed evidence scope.

Acceptance criteria:

- 0 unknown citation tokens for strict packs.
- 0 uncited path mentions in answer body when path-gate is enabled.

## Workstream D: Causal Usage Tests (Counterfactual)

Goal: show the model depends on evidence content, not just template priors.

Tasks:

1. A/B run per probe QID:
   - A: normal evidence
   - B: remove one critical preflight step
   - C: replace one critical evidence snippet with sentinel text
2. Compare:
   - answer verdict/body changes
   - citation set changes
   - validator issues
3. Require expected degradation/failure in B/C for evidence-sensitive QIDs.

Acceptance criteria:

- Probe QIDs fail or materially change when critical evidence is removed/mutated.
- If answer stays unchanged in B/C, mark as suspected evidence-nonuse.

## Workstream E: Historical Replay and Regression Gate

Goal: enforce this continuously, not as one-off audit.

Tasks:

1. Add a nightly/CI job to run a compact validation pack (5-10 QIDs).
2. Store machine summary in run output (proposed): `EVIDENCE_USAGE_SUMMARY.json`.
3. Track metrics:
   - percent questions with non-empty evidence
   - percent strict-citation passes
   - count of `filtered_to_zero` events
   - count of `INSUFFICIENT EVIDENCE` where evidence_blocks > 0

Acceptance criteria:

- Regression budget: no increase in citation/path violations.
- No new `filtered_to_zero` hotspots without explicit allowlist.

## Fast Triage Commands

Evidence summary and zero-filter warnings:

```bash
uv run mdparse search "question.evidence.summary" out --limit 200
uv run mdparse search "preflight.step.filtered_to_zero" out --limit 200
```

Prompt contains specific preflight section:

```bash
rg -n "\\[Preflight pub_fn_hits\\]|CITE=" out/general_v1_6_16/R_API_2_augmented_prompt.md
```

Report says insufficient while preflights succeeded:

```bash
rg -n "R_API_2|INSUFFICIENT EVIDENCE|Preflight .*: rc=0" out/general_v1_6_16/REPORT.md
```

## Proposed Implementation Order (Pragmatic)

1. Add per-question `QID_evidence_trace.json` artifact.
2. Add one compact `pack_evidence_usage_probe_v1_0.yaml` with counterfactual QIDs.
3. Add strict gating defaults for that probe pack only.
4. Add CI/nightly replay and summary trend check.

## Exit Criteria (Plan Complete)

- We can prove, for each probe QID, a full chain:
  - preflight output exists
  - rows extracted
  - evidence injected
  - citations constrained to injected evidence
  - answer behavior changes when evidence is perturbed
- Any violation yields machine-readable failure and blocks promotion of pack changes.
