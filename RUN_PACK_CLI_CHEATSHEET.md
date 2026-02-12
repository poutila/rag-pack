# RUN_PACK CLI Cheat Sheet

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Core commands

### Help
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py --help
```

### RSQT pack run
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_run
```

### RAQT pack run
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_run
```

### Force standard mode (no quote-bypass)
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_std \
  --quote-bypass-mode off
```

### Force quote-bypass mode
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_qb \
  --quote-bypass-mode on
```

### Replicates (stability)
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_replicates \
  --replicate \
  --replicate-seeds 42,123,456
```

### Faster reruns with cached preflights
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_cached \
  --cache-preflights \
  --short-circuit-preflights
```

### Adaptive top-k retry
```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_adaptive \
  --adaptive-top-k \
  --chat-top-k-initial 8
```

### Use custom runner policy
```bash
RUNNER_POLICY_PATH=XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml \
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_policy
```

---

## Common failure fixes

### `python: command not found`
Use `uv run python ...` (not plain `python`).

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py --help
```

### `Missing required path: ...`
Pass explicit paths for pack/parquet/index/engine-specs.

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --engine-specs XREF_WORKFLOW_II_new/tools/rag_packs/engine_specs.yaml \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_fix_paths
```

### `Engine '...' not found in engine specs`
Set pack `engine:` to one that exists in `engine_specs.yaml` (`rsqt`, `raqt`, `mdparse`) or pass correct specs file.

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --engine-specs XREF_WORKFLOW_II_new/tools/rag_packs/engine_specs.yaml \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_fix_engine
```

### Contract failure exit code `2` (VERDICT/CITATIONS issues)
Inspect validator issues in report and question chat artifact:

```bash
cat XREF_WORKFLOW_II_new/xref_state/RAQT_run/REPORT.md
cat XREF_WORKFLOW_II_new/xref_state/RAQT_run/R_BOUNDARY_1_chat.json
```

Then rerun with stronger evidence behavior:

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_fix_contract \
  --quote-bypass-mode on \
  --adaptive-top-k
```

### Mission advice gate failure (also exit code `2`)
Symptoms in `RUN_LOG.txt`:
- `event=question.advice.validator.issues`
- `event=run.done ... fatal_advice_gate_issues=<n>`

Quick checks:
```bash
rg -n "question.advice.validator|fatal_advice_gate_issues" XREF_WORKFLOW_II_new/xref_state/<RUN_DIR>/RUN_LOG.txt
rg -n "Advice quality|Validator issues" XREF_WORKFLOW_II_new/xref_state/<RUN_DIR>/REPORT.md
```

Common fixes:
- Ensure mission pack questions use `advice_mode: llm`.
- Ensure advice contains at least two concrete issues with:
  `ISSUE_n`, `WHY_IT_MATTERS_n`, `PATCH_SKETCH_n`, `TEST_PLAN_n`, `CITATIONS_n`.
- Ensure `CITATIONS_n` tokens are evidence-backed `path:line(-line)`.

### Empty evidence aborts run (strict mode)
Symptoms in `RUN_LOG.txt`:
- `event=question.evidence.empty.fail_fast`
- `event=run.abort.empty_evidence`

Fix preflight evidence first:

```bash
rg -n "question.evidence.empty.fail_fast|run.abort.empty_evidence" XREF_WORKFLOW_II_new/xref_state/<RUN_DIR>/RUN_LOG.txt
```

If you intentionally need non-strict experimentation, relax policy:

```yaml
runner:
  evidence_presence_gate:
    fail_on_empty_evidence: false
```

### Old filenames after rename
Use current file names in `tools/rag_packs`:

- `pack_rust_audit_raqt.yaml`
- `pack_rust_audit_rsqt_extension_3q.yaml`
- `pack_rust_audit_rsqt_general_v1_6_explicit.yaml`
- `cfg_rust_audit_*_question_validators.yaml`
- `cfg_rust_audit_*_finding_rules.yaml`
