# RUN_PACK User Manual (v1.0)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


## Scope
This manual documents how to use:

- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py`

It is based on runner behavior in `run_pack.py` version `3.2.0`.

## What the runner does
`run_pack.py` executes an audit pack end-to-end:

1. Loads pack YAML (`questions`, `response_schema`, validation rules).
2. Runs deterministic preflight commands per question.
3. Injects preflight evidence into prompt text.
4. Calls engine chat (or skips chat in deterministic mode).
5. Validates response contract (`VERDICT`, `CITATIONS`, optional provenance/path gates).
6. Writes artifacts (`REPORT.md`, per-question JSON/MD files, `RUN_MANIFEST.json`).
7. Runs optional plugins (for Rust audit packs, `rsqt_guru` may auto-apply).

## Mission-grade advice contract (fail-closed)
For mission packs (`pack_type` matching `mission`), advice is mandatory and quality-gated:

1. Every question must use `advice_mode: llm`.
2. If deterministic evidence exists, advice must include at least 2 concrete issues.
3. Each issue must include:
   - `ISSUE_n`
   - `WHY_IT_MATTERS_n`
   - `PATCH_SKETCH_n`
   - `TEST_PLAN_n`
   - `CITATIONS_n`
4. `CITATIONS_n` must be valid `path:line(-line)` and must come from injected evidence.
5. Praise-only/generic advice is treated as validator failure.

When this gate fails, the run exits with code `2`.

---

## Prerequisites

- `uv` installed.
- Engine CLIs available (`rsqt`, `raqt`, `mdparse`) through `uv run` or directly.
- Parquet and index files already built.
- Pack and engine spec YAML files present.

Recommended invocation style:

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py ...
```

---

## Quick start

### RSQT pack

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_run_01
```

### RAQT pack

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_raqt.yaml \
  --parquet RAQT.parquet \
  --index .raqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RAQT_run_01
```

### Replicate mode (stability)

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py \
  --pack XREF_WORKFLOW_II_new/tools/rag_packs/pack_rust_audit_rsqt_general_v1_6_explicit.yaml \
  --parquet RSQT.parquet \
  --index .rsqt.faiss \
  --out-dir XREF_WORKFLOW_II_new/xref_state/RSQT_replicates \
  --replicate \
  --replicate-seeds 42,123,456
```

---

## CLI reference

### Required

- `--out-dir`

### Core inputs

- `--pack`: pack YAML path.
- `--parquet`: engine parquet (`RSQT.parquet`, `RAQT.parquet`, `MD_PARSE.parquet`, etc.).
- `--index`: FAISS index path.
- `--engine-specs`: engine definitions YAML (default: `engine_specs.yaml`).
- `--rsqt`: legacy alias for `--parquet`.

### Engine/LLM controls

- `--backend`
- `--model`
- `--prompt-profile`
- `--max-tokens`
- `--temperature`
- `--top-p`
- `--num-ctx`
- `--no-uv`: use direct CLI instead of `uv run`.

### Prompt file controls

- `--system-prompt-file`: legacy single prompt for both modes.
- `--system-prompt-grounding-file`: prompt for standard mode.
- `--system-prompt-analyze-file`: prompt for quote-bypass mode.

### Preflight/runtime controls

- `--cache-preflights`
- `--short-circuit-preflights`
- `--adaptive-top-k`
- `--chat-top-k-initial`
- `--preflight-max-chars`

### Quote-bypass controls

- `--quote-bypass` (legacy alias for mode `on`)
- `--quote-bypass-mode auto|on|off`
- `--no-quote-bypass` (alias for mode `off`)
- `--evidence-empty-gate`
- `--no-evidence-empty-gate`

### Replicate controls

- `--replicate`
- `--replicate-seeds`

---

## Path resolution rules

For `--pack`, `--parquet`, `--index`, `--engine-specs`, the runner searches:

1. current working directory
2. script directory (`tools/rag_packs`)
3. repo root

Then it applies compatibility aliases from `runner_policy.yaml` `runner.path_aliases` (legacy names -> current filenames).

Output directory behavior:

- absolute path: used as-is.
- relative multi-segment path (for example `XREF_WORKFLOW_II_new/xref_state/RAQT_9`): resolved from current working directory.
- single-segment name (for example `RAQT_9`): created under default base (`XREF_WORKFLOW_II_new/xref_state`).

---

## Configuration model

## Runner policy (`runner_policy.yaml`)

- Default file: `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml`
- Override with env var:

```bash
RUNNER_POLICY_PATH=/path/to/custom_runner_policy.yaml
```

Policy is deep-merged onto built-in defaults.

Typical policy controls:

- default file names (`pack_file`, `engine_specs_file`, prompts, default parquet/index)
- quote-bypass defaults
- validators regexes and caps
- report/manifest filenames
- path aliases for renamed files

## Engine specs (`engine_specs.yaml`)

Defines CLI shape per engine:

- command prefix (`prefix_uv`, `prefix_direct`)
- target dir flag
- chat subcommand and flag names (`--index`, parquet flag, backend/model flags, etc.)
- which preflight commands need auto-appended index/parquet args

---

## Pack YAML schema

Required top-level keys:

- `version`
- `pack_type`
- `engine`
- `response_schema`
- `defaults`
- `questions`

Optional top-level keys:

- `validation`
- `runner`

### `defaults`

- `chat_top_k`
- `max_tokens`
- `temperature`

### `validation`

Supported fields:

- `required_verdicts`
- `citation_format`
- `fail_on_missing_citations`
- `enforce_citations_from_evidence`
- `enforce_no_new_paths`
- `enforce_paths_must_be_cited`
- `minimum_questions`

### Question entry

Required:

- `id`
- `title`
- `category`
- `question`

Optional:

- `top_k`
- `preflight` (list)
- `chat` (mapping, for example per-question top_k)
- `expected_verdict`
- `answer_mode`: `llm` or `deterministic`
- `advice_mode`: `none` or `llm`
- `advice_prompt`

### `preflight` step schema

Required:

- `name`
- `cmd` (list of CLI tokens)

Optional:

- `engine_override`
- `stop_if_nonempty`
- `render`: `list | block | lines | json`
- `fence_lang` (for `render: block`)
- `block_max_chars`
- `transform` (filters/limits/render override)

### `transform` keys

- `max_items`
- `max_chars`
- `exclude_test_files`
- `test_path_patterns`
- `exclude_comments`
- `require_contains`
- `require_regex`
- `group_by_path_top_n` (cross-preflight path narrowing)
- `filter_fn` (currently `compact_docs`)
- `render` (override)

---

## Prompting and modes

## Standard mode

- Uses grounding prompt.
- Injects evidence + mandatory procedure + response schema.

## Quote-bypass mode

- Uses analyze prompt.
- Injects deterministic evidence with quote-bypass instructions.
- Mode activation:
  - `auto`: on only when evidence exists.
  - `on`: always.
  - `off`: never.

## Evidence-empty gate

If quote-bypass is active and no evidence is extracted:

- default strict behavior: fail the question immediately and abort the run (fail-closed).
- strict behavior is controlled by `runner.evidence_presence_gate.fail_on_empty_evidence` (default `true`).
- `--no-evidence-empty-gate` only applies to non-strict policy mode.

## Deterministic answer mode

If question has `answer_mode: deterministic`:

- chat model call is skipped.
- answer is synthesized deterministically from evidence/artifacts.

## Advice mode

If question has `advice_mode: llm` and evidence exists:

- runner performs a second chat pass for actionable improvements.
- writes separate advice artifacts.

---

## Plugin behavior

Plugin selection order:

1. explicit `pack.runner.plugin` or `pack.runner.plugins`
2. fallback heuristic: if `engine=rsqt` and `pack_type` starts with `rust_audit`, `rsqt_guru` may auto-load

Disable plugins explicitly with:

- `runner.plugin: none`

`rsqt_guru` plugin config keys (under `runner.plugin_config`):

- `rules_path`
- `question_validators_path`

---

## Output artifacts

## Run-level files

- `REPORT.md`
- `RUN_MANIFEST.json`

If `--replicate`:

- `STABILITY_SUMMARY.md`
- one subdir per seed (for example `seed_42/`)
- optional `GURU_STABILITY_SUMMARY.md` when guru metrics exist

## Per-question files

- `<QID>_<preflight-name>.json`
- `<QID>_augmented_prompt.md`
- `<QID>_bypass_prompt.md` (only when quote-bypass prompt used)
- `<QID>_chat.json`
- `<QID>_advice_prompt.md` (when advice mode runs)
- `<QID>_advice_chat.json` (when advice mode runs)

## Plugin files (`rsqt_guru`)

- `GURU_AUDIT_REPORT.md`
- `GURU_METRICS.json`
- `FINDINGS.jsonl`
- `EVIDENCE_INDEX.json`

Plugin outputs are also registered in `RUN_MANIFEST.json`.

---

## Exit behavior

- `0`: run completed.
- `2`: fatal contract failure (response schema/citations/path gates/advice gates/evidence-presence gate).
- non-zero (general): missing/invalid inputs, bad config, or runtime errors.

Note: individual preflight command failures are recorded in artifacts/report; they do not always abort the full run.

---

## Troubleshooting

## `Missing required path: ...`

- Use current file names (for example `pack_rust_audit_raqt.yaml`, `cfg_rust_audit_*`).
- If using legacy names, ensure alias exists in `runner_policy.yaml` `runner.path_aliases`.
- Pass explicit absolute or repo-relative paths.

## `python: command not found`

Use:

```bash
uv run python XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py ...
```

## `Engine '...' not found in engine specs`

- Check `--engine-specs` points to correct YAML.
- Ensure pack `engine` key exists under `engines:` mapping.

## Empty evidence aborts run immediately (strict mode)

- Inspect `<QID>_<preflight>.json`.
- If you intentionally need non-strict behavior, relax `runner.evidence_presence_gate.fail_on_empty_evidence`.
- Tune preflight `transform` and `render` settings.

## Contract failures (VERDICT/CITATIONS)

- Check `response_schema` in pack.
- Verify citations are real `path:line(-line)` tokens from injected evidence.
- Review `**Validator issues:**` section in `REPORT.md`.

---

## Recommended working pattern

1. Run one question set once (non-replicate) and inspect artifacts.
2. Fix preflight evidence quality first.
3. Tune quote-bypass/standard prompt split.
4. Enable replicate mode for stability checks.
5. Use plugin outputs (`FINDINGS.jsonl`, `GURU_METRICS.json`) for CI gates.
