---
title: Audit Pack YAML Strict Schema Reference
version: 1.0
generated: 2026-02-10T17:41:52Z
---

# Audit Pack YAML Strict Schema Reference (v1.0)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


This document is the strict schema reference for YAML used by:

- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py`
- `XREF_WORKFLOW_II_new/tools/rag_packs/plugins/rsqt_guru.py`

It focuses on structure, allowed keys, and fail conditions.

---

## 1) Canonical YAML files

### Runner-level

- `engine_specs.yaml`
- `runner_policy.yaml`

### Pack files

- `pack_rust_audit_rsqt_general_v1_6_explicit.yaml`
- `pack_rust_audit_rsqt_extension_3q.yaml`
- `pack_rust_audit_raqt.yaml`
- `docs_audit_pack.explicit.yaml`

### Plugin config files

- `cfg_rust_audit_rsqt_general_question_validators.yaml`
- `cfg_rust_audit_rsqt_general_finding_rules.yaml`
- `cfg_rust_audit_rsqt_extension_3q_question_validators.yaml`
- `cfg_rust_audit_rsqt_extension_3q_finding_rules.yaml`
- `cfg_rust_audit_raqt_question_validators.yaml`
- `cfg_rust_audit_raqt_finding_rules.yaml`

---

## 2) Pack YAML strict schema

## 2.1 Top-level keys

Required:

- `version` (string)
- `pack_type` (string)
- `engine` (string; must exist in `engine_specs.yaml`)
- `response_schema` (string)
- `defaults` (mapping)
- `questions` (non-empty list)

Optional:

- `validation` (mapping)
- `runner` (mapping)

Hard-fail conditions:

- any required key missing
- `questions` missing, not a list, or empty

## 2.2 `defaults` mapping

Allowed keys:

- `chat_top_k` (int)
- `max_tokens` (int)
- `temperature` (float)

Defaults fall back to `runner_policy.yaml -> pack_defaults`.

## 2.3 `validation` mapping

Allowed keys:

- `required_verdicts` (list of strings)
- `citation_format` (string)
- `fail_on_missing_citations` (bool)
- `enforce_citations_from_evidence` (bool)
- `enforce_no_new_paths` (bool)
- `enforce_paths_must_be_cited` (bool)
- `minimum_questions` (int)

Defaults fall back to `runner_policy.yaml -> pack_validation`.

Schema parser tolerance:

- accepts `VERDICT=` or `VERDICT:`
- accepts `CITATIONS=` or `CITATIONS:`
- strips bold markers before parsing (`**...**`)

## 2.4 Question object schema

Required keys:

- `id` (string, non-empty)
- `title` (string, non-empty)
- `category` (string, non-empty)
- `question` (string, non-empty)

Optional keys:

- `top_k` (int)
- `preflight` (list)
- `chat` (mapping)
- `expected_verdict` (string)
- `answer_mode` (`llm` or `deterministic`)
- `advice_mode` (`none` or `llm`)
- `advice_prompt` (string)

Hard-fail conditions:

- missing required question key
- unsupported `answer_mode`
- unsupported `advice_mode`

Mode defaults come from `runner_policy.yaml -> question_modes`.

## 2.5 `preflight` step schema

Required:

- `name` (string)
- `cmd` (list of strings)

Optional:

- `engine_override` (string, engine key from `engine_specs.yaml`)
- `stop_if_nonempty` (bool)
- `render` (`list|block|lines|json`)
- `fence_lang` (string)
- `block_max_chars` (int)
- `transform` (mapping)

Step entries that are not mapping or have invalid `name`/`cmd` are skipped.

## 2.6 `transform` schema

Supported keys:

- `max_items` (int)
- `max_chars` (int)
- `exclude_test_files` (bool)
- `test_path_patterns` (list of regex strings)
- `exclude_comments` (bool)
- `require_contains` (string)
- `require_regex` (string or list of regex strings)
- `group_by_path_top_n` (mapping)
- `filter_fn` (string; currently `compact_docs`)
- `render` (string; overrides step-level render)

`group_by_path_top_n` fields:

- `from` (string, source preflight step name)
- `top_n` (int, optional)
- `per_path` (int, optional)
- `sort_key` (string, optional)

---

## 3) `runner` block schema in pack

Allowed keys:

- `plugin` (string)
- `plugins` (list)
- `plugin_config` (mapping)
- `prompts` (mapping)

## 3.1 Plugin selection

- `plugin` or `plugins` explicitly chooses plugins.
- Disable aliases: `none`, `null`, `no`, `false`, `off`, empty string.
- Unknown explicit plugin -> hard fail.

If no explicit plugin:

- heuristic may auto-enable `rsqt_guru` when `engine=rsqt` and `pack_type` starts with `rust_audit`.

## 3.2 `plugin_config` keys

Supported keys:

- `rules_path`
- `question_validators_path`

Paths are resolved relative to pack directory when not absolute.

## 3.3 `prompts` keys

Supported keys:

- `grounding`
- `analyze`

CLI flags still override these prompt paths.

---

## 4) `engine_specs.yaml` strict schema

Top-level required:

- `engines` (mapping, non-empty)

Each engine item supports:

- `prefix_uv` (list)
- `prefix_direct` (list)
- `target_dir_flag` (string or null)
- `chat_subcommand` (string)
- `parquet_flag` (string)
- `index_flag` (string)
- `backend_flag` (string)
- `top_k_flag` (string)
- `model_flag` (string)
- `system_prompt_flag` (string)
- `max_tokens_flag` (string)
- `temperature_flag` (string)
- `format_flag` (string)
- `format_value` (string)
- `prompt_profile_flag` (string or null)
- `top_p_flag` (string or null)
- `num_ctx_flag` (string or null)
- `preflight_needs_index_cmds` (list)

Hard-fail conditions:

- missing `engines` mapping
- selected pack engine not found in `engines`
- resolved engine prefix empty

---

## 5) `runner_policy.yaml` strict role

`runner_policy.yaml` is merged over built-in defaults and controls:

- default file paths and names
- quote-bypass defaults
- validator regexes and caps
- evidence formatting defaults
- plugin known/disable aliases
- legacy filename/path aliases

Override policy path via `RUNNER_POLICY_PATH`.

---

## 6) Question validators YAML strict schema

Top-level expected:

- `defaults` (mapping, optional)
- `validators` (mapping, optional)

Per-question validator list item:

- must include `type`

Supported `type` values:

- `require_non_test_fileline_citations_if_regex`
- `require_min_inline_regex_count`
- `ban_regex`
- `require_min_inline_regex_count_if_regex`

Unknown types are ignored by current plugin logic.

---

## 7) Finding rules YAML strict schema

Expected:

- `finding_rules` mapping (rule id -> rule object)

Rule fields commonly used:

- `category`
- `severity`
- `recommendation_code`
- `recommendation`

These power deterministic findings and recommendation output.

---

## 8) Contract and exit semantics

Important runtime semantics tied to YAML:

- Missing required pack keys -> immediate hard fail.
- Invalid question mode enum -> immediate hard fail.
- `fail_on_missing_citations=true` + schema issues -> runner exits `2`.
- Explicit unknown plugin -> immediate hard fail.
- Missing required paths after alias resolution -> immediate hard fail.

