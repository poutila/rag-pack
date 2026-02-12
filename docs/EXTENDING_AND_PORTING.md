# Extending and Porting

## Purpose
Show how to extend the FCDRAG framework with new engines, packs, validators/rules, and plugins, and how to port the architecture beyond Rust.

## Audience
Systems engineers and maintainers evolving FCDRAG.

## When to read this
Read after [ARCHITECTURE.md](ARCHITECTURE.md) and [PACK_AUTHORING.md](PACK_AUTHORING.md) when implementing new capability.

## 1) Add a new engine in `engine_specs.yaml`

### Required changes
1. Add a new key under `engines:` in `engine_specs.yaml`.
2. Define command prefixes and required flag names used by `run_pack.py`.
3. Ensure `pack.engine` matches that key.

### Engine fields the runner expects
- `prefix_uv`, `prefix_direct`
- `target_dir_flag` (optional)
- `chat_subcommand`
- flag names for parquet/index/backend/model/top-k/system prompt/max tokens/temperature/format
- optional flags (`prompt_profile_flag`, `top_p_flag`, `num_ctx_flag`)
- `preflight_needs_index_cmds`

### Failure modes
- missing `engines` mapping -> hard fail
- selected engine missing -> hard fail
- empty command prefix -> hard fail

## 2) Add a new pack type + validators + finding rules

### Pack creation flow
1. create `pack_<domain>_<engine>_<purpose>.yaml`
2. define strict `response_schema`
3. add deterministic preflights per question
4. add `validation` gates as needed
5. set `runner.plugin` and `runner.plugin_config` if plugin outputs are desired

### Validator/finding config flow
1. create `cfg_<domain>_<purpose>_question_validators.yaml`
2. create `cfg_<domain>_<purpose>_finding_rules.yaml`
3. wire paths in pack `runner.plugin_config`

### Supported validator types in `rsqt_guru`
- `require_non_test_fileline_citations_if_regex`
- `require_min_inline_regex_count`
- `ban_regex`
- `require_min_inline_regex_count_if_regex`

## 3) Add or modify plugin outputs (post-run)

### Existing plugin contracts
- Interface is in `plugins/base.py`.
- Runner calls `plugin.post_run(ctx)` after report generation.
- Plugin returns `PluginOutputs(files, metrics, hashes)`; runner merges these into `RUN_MANIFEST.json`.

### Steps to add output
1. implement/modify plugin file (for example `plugins/rsqt_guru.py`)
2. write artifacts inside `ctx.out_dir`
3. return file names + metrics + hashes via `PluginOutputs`
4. ensure pack selects plugin (`runner.plugin`) or matches heuristic auto-apply rules

### Current built-in plugin outputs
`rsqt_guru` writes:
- `FINDINGS.jsonl`
- `EVIDENCE_INDEX.json`
- `GURU_AUDIT_REPORT.md`
- `GURU_METRICS.json`

## 4) Port architecture to non-Rust domains
Keep architecture, replace domain primitives.

### Keep unchanged
- `run_pack.py` orchestration
- pack schema (`response_schema`, `questions`, `preflight`, `validation`)
- evidence block model with cite tokens
- fail-closed validators and path gates
- manifest/report lifecycle

### Replace
- engine CLI implementation behind `engine_specs.yaml`
- prompt files for new domain language
- pack question sets and deterministic preflight commands
- plugin logic and rule/validator YAML tuned for new domain

### Suggested port sequence
1. add engine spec and verify preflight/chat command shape
2. create minimal deterministic pack
3. establish strict response schema and validation gates
4. add domain plugin for deterministic post-run findings
5. calibrate retrieval/chunking/top-k through experiments

## 5) Unknowns and caveats
- No generic plugin auto-discovery exists; plugin set is constrained by runner policy and explicit code paths.
- mdparse-specific plugin implementation is not present in this repo (`UNKNOWN/NOT FOUND` for mdparse domain-specific post-run outputs until added).

## 6) Audit-backed extension roadmap (RAQT/RSQT)
Use these files as the current backlog SSOT:
- `RAQT_FEATURE_REQUEST_v1_2.md`
- `RAQT_FEATURE_IMPLEMENTATION_CHECKLIST_v1_0.md`
- `RSQT_FEATURE_REQUEST_v1_0.md`

### RAQT prioritized implementation map
| Feature | Priority | Primary files | Estimated effort |
|---|---|---|---|
| FR-3 Stable output stem contract | P0 | `src/doxslock/rag/_path_utils.py`, `src/doxslock/rag/backends.py` | S (0.5-1 day) |
| FR-2 Unified kind taxonomy | P0 | `src/doxslock/raqt/columns.py`, `src/doxslock/raqt/query.py`, `src/doxslock/raqt/cli.py` | M (1-2 days) |
| FR-1 Call graph v2 API | P0 | `src/doxslock/raqt/query.py`, `src/doxslock/raqt/collector_refs.py`, `src/doxslock/raqt/spec.py` | L (2.5-4 days) |
| FR-4 Structured JSON stats/schema | P1 | `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/spec.py` | S (0.5-1 day) |
| FR-5 Query surface parity | P1 | `src/doxslock/raqt/query.py`, `src/doxslock/qt_base/query.py`, `src/doxslock/raqt/cli.py` | M-L (1.5-3 days) |
| FR-6 Provenance-rich retrieval payload | P1 | `src/doxslock/raqt/rag_chunks.py`, `src/doxslock/rag/models.py`, `src/doxslock/rag/chatbot.py` | M (1-2 days) |
| FR-7 `raqt doctor` preflight | P2 | `src/doxslock/raqt/cli.py`, `src/doxslock/raqt/trust_gate.py`, `src/doxslock/rag/staleness.py` | M (1-2 days) |

Recommended sequence from checklist: FR-3 -> FR-2 -> FR-1 -> FR-4/FR-5 -> FR-6 -> FR-7.

### RSQT prioritized implementation themes
RSQT feature request currently defines priorities and sequencing, but no file-level effort checklist like RAQT:
- P0: docs compliance mode, strict JSON/machine mode, kind registry + validation
- P1: output/schema versioning, CI deterministic profile
- P2: Rust audit persona profile for RAG QA

`UNKNOWN/NOT FOUND`: exact RSQT implementation effort by source file is not documented in `RSQT_FEATURE_REQUEST_v1_0.md`; create an RSQT implementation checklist analogous to RAQT if planning execution tracking.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Pack schema: [PACK_AUTHORING.md](PACK_AUTHORING.md)
- Prompt semantics: [PROMPTS.md](PROMPTS.md)
- Research protocol: [RESEARCH_LOG.md](RESEARCH_LOG.md)

## Source anchors
- `engine_specs.yaml:1`
- `run_pack.py:618`
- `run_pack.py:674`
- `run_pack.py:688`
- `run_pack.py:527`
- `run_pack.py:1481`
- `run_pack.py:2040`
- `plugins/base.py:8`
- `plugins/base.py:26`
- `plugins/rsqt_guru.py:2566`
- `plugins/rsqt_guru.py:2624`
- `plugins/rsqt_guru.py:2690`
- `plugins/rsqt_guru.py:2936`
- `runner_policy.yaml:207`
- `RAQT_FEATURE_REQUEST_v1_2.md:44`
- `RAQT_FEATURE_REQUEST_v1_2.md:60`
- `RAQT_FEATURE_REQUEST_v1_2.md:73`
- `RAQT_FEATURE_REQUEST_v1_2.md:84`
- `RAQT_FEATURE_REQUEST_v1_2.md:95`
- `RAQT_FEATURE_REQUEST_v1_2.md:106`
- `RAQT_FEATURE_REQUEST_v1_2.md:117`
- `RAQT_FEATURE_IMPLEMENTATION_CHECKLIST_v1_0.md:12`
- `RAQT_FEATURE_IMPLEMENTATION_CHECKLIST_v1_0.md:198`
- `RSQT_FEATURE_REQUEST_v1_0.md:45`
- `RSQT_FEATURE_REQUEST_v1_0.md:60`
- `RSQT_FEATURE_REQUEST_v1_0.md:75`
- `RSQT_FEATURE_REQUEST_v1_0.md:88`
- `RSQT_FEATURE_REQUEST_v1_0.md:104`
- `RSQT_FEATURE_REQUEST_v1_0.md:119`
- `RSQT_FEATURE_REQUEST_v1_0.md:144`
