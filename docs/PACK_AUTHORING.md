# Pack Authoring

## Purpose
Provide a strict, repo-grounded guide to authoring FCDRAG `pack_*.yaml` files for `run_pack.py`.

## Audience
Engineers creating or modifying audit/question packs.

## When to read this
Read before editing any pack YAML; keep [AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md](../AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md) open in parallel.

## Canonical schema references
- Strict schema: `AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md`
- Example patterns: `AUDIT_PACK_YAML_EXAMPLES_COOKBOOK_v1_0.md`
- Runtime parser/enforcement: `run_pack.py:527`

## Required top-level keys

| Key | Type | Required | Hard-fail if missing |
|---|---|---|---|
| `version` | string | yes | yes |
| `pack_type` | string | yes | yes |
| `engine` | string | yes | yes |
| `response_schema` | string | yes | yes |
| `defaults` | mapping | yes | yes |
| `questions` | non-empty list | yes | yes |
| `validation` | mapping | no | no |
| `runner` | mapping | no | no |

## `defaults` block

| Field | Type | Meaning |
|---|---|---|
| `chat_top_k` | int | default retrieval window when question `top_k` absent |
| `max_tokens` | int | default chat output cap |
| `temperature` | float | default sampling temperature |

Defaults fallback to `runner_policy.yaml -> pack_defaults`.

## `validation` block

| Field | Type | Effect |
|---|---|---|
| `required_verdicts` | list[str] | allowed values for `VERDICT` |
| `citation_format` | string | expected citation token form |
| `fail_on_missing_citations` | bool | contract issues become fatal |
| `enforce_citations_from_evidence` | bool | citation provenance gate |
| `enforce_no_new_paths` | bool | Path Gate A |
| `enforce_paths_must_be_cited` | bool | Path Gate B |
| `minimum_questions` | int | hard fail if pack too small |

## Question object schema
Required per question:
- `id`
- `title`
- `category`
- `question`

Optional per question:
- `top_k`
- `preflight`
- `chat`
- `expected_verdict`
- `answer_mode` (`llm` or `deterministic`)
- `advice_mode` (`none` or `llm`)
- `advice_prompt`

`answer_mode` or `advice_mode` outside allowed enums causes hard failure.

## Preflight steps
Required fields per step:
- `name`
- `cmd` (token list)

Optional fields:
- `engine_override`
- `stop_if_nonempty`
- `render`
- `fence_lang`
- `block_max_chars`
- `transform`

## Transform keys

| Key | Type | Description |
|---|---|---|
| `max_items` | int | row cap after filtering |
| `max_chars` | int | per-block character cap |
| `include_path_regex` | str or list[str] | keep only paths matching regex |
| `exclude_path_regex` | str or list[str] | drop paths matching regex |
| `exclude_test_files` | bool | drop test paths using regex patterns |
| `test_path_patterns` | list[str] | override default test-path regexes |
| `exclude_comments` | bool | drop comment-only hits |
| `require_contains` | str | keep rows containing substring |
| `require_regex` | str or list[str] | keep rows matching regex |
| `group_by_path_top_n` | mapping | narrow rows to top N files from another preflight |
| `filter_fn` | str | named filter (`compact_docs`) |
| `render` | str | transform-level render override |

## Transform precedence and defaults (critical)
Current runtime precedence for excludes:
1. if step defines `exclude_path_regex` (including empty list), use it
2. otherwise inherit runner default `_default_exclude_path_regex`

Concrete implementation excerpt:
`run_pack.py:2027`
```python
if "exclude_path_regex" in transform:
    exclude_path_regex = transform.get("exclude_path_regex")
else:
    exclude_path_regex = transform.get("_default_exclude_path_regex")
```

Implication for pack authors:
- `exclude_path_regex: []` means "do not apply default excludes for this step".
- omitting `exclude_path_regex` means "runner default excludes still apply".

Recent run evidence of why this matters:
`out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:66`
```text
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_traits | rows_before=18 | rows_after=0
event=preflight.step.filtered ... qid=R_PORTS_1 | step=raqt_trait_impls | rows_before=221 | rows_after=0
```

Recommended authoring pattern for mission packs:
```yaml
transform:
  include_path_regex:
    - "(^|/)crates/engine/"
    - "(^|/)crates/gui/"
  exclude_path_regex: []
  exclude_test_files: true
```

## `runner` block inside pack

| Key | Meaning |
|---|---|
| `plugin` / `plugins` | explicit plugin selection or disable (`none`, `off`, etc.) |
| `plugin_config.rules_path` | finding rules YAML path |
| `plugin_config.question_validators_path` | validator YAML path |
| `prompts.grounding` | prompt file path for grounding mode |
| `prompts.analyze` | prompt file path for analyze-only mode |

Unknown explicit plugin names hard-fail.

## End-to-end examples

### Example 1: Minimal deterministic question
```yaml
version: 1.0.0
pack_type: rust_audit_minimal
engine: rsqt
response_schema: |
  VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE
  CITATIONS=path:line(-line), ...
defaults:
  chat_top_k: 8
  max_tokens: 1024
  temperature: 0.0
validation:
  fail_on_missing_citations: true
questions:
  - id: Q_MIN_1
    title: Unsafe usage inventory
    category: safety
    answer_mode: deterministic
    question: |
      Start with VERDICT and CITATIONS.
      List unsafe usages from evidence.
    preflight:
      - name: unsafe_hits
        cmd: ["unsafe", "--format", "json"]
```

### Example 2: Deterministic answer + advice pass
```yaml
version: 1.0.0
pack_type: rust_audit_advice
engine: rsqt
response_schema: |
  VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE
  CITATIONS=path:line(-line), ...
defaults:
  chat_top_k: 12
  max_tokens: 1500
  temperature: 0.0
questions:
  - id: Q_ADV_1
    title: Prod unwrap risk and fixes
    category: safety
    answer_mode: deterministic
    advice_mode: llm
    question: |
      Start with VERDICT and CITATIONS.
      Classify highest-risk unwrap/expect usages.
    preflight:
      - name: prod_unwraps
        cmd: ["prod-unwraps", "--format", "json"]
      - name: unwrap_sites
        cmd: ["search", ".unwrap(", "--limit", "200", "--format", "json"]
        render: lines
        transform:
          exclude_test_files: true
          exclude_comments: true
          max_items: 25
```

### Example 3: Strict citation and path gating
```yaml
version: 1.0.0
pack_type: rust_audit_strict
engine: raqt
response_schema: |
  VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE
  CITATIONS=repo/path.rs:line(-line), ...
defaults:
  chat_top_k: 15
  max_tokens: 2000
  temperature: 0.0
validation:
  fail_on_missing_citations: true
  enforce_citations_from_evidence: true
  enforce_no_new_paths: true
  enforce_paths_must_be_cited: true
questions:
  - id: Q_STRICT_1
    title: Boundary conversion completeness
    category: architecture
    question: |
      Start with VERDICT and CITATIONS.
      Enumerate all From<...> boundary conversions from evidence only.
    preflight:
      - name: raqt_defs
        cmd: ["defs", "--kind", "enum", "--name", "Error", "--format", "json"]
      - name: rsqt_from_impls
        engine_override: rsqt
        cmd: ["search", "impl From<", "--limit", "80", "--format", "json"]
        transform:
          require_regex:
            - "(?i)Error"
          max_items: 40
runner:
  plugin: rsqt_guru
  plugin_config:
    rules_path: cfg_rust_audit_raqt_finding_rules.yaml
    question_validators_path: cfg_rust_audit_raqt_question_validators.yaml
```

## Authoring checklist
1. Confirm `engine` exists in `engine_specs.yaml`.
2. Keep `response_schema` explicit and machine-checkable.
3. Prefer deterministic preflights before chat.
4. Use transforms to reduce noise (tests/comments/regex filters).
5. Enable strict validation gates for compliance-style packs.
6. Wire `plugin_config` to domain-specific validator/finding YAML.
7. Run with `--cache-preflights` during iteration.

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Runtime operation: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Prompt behavior: [PROMPTS.md](PROMPTS.md)
- Extension strategy: [EXTENDING_AND_PORTING.md](EXTENDING_AND_PORTING.md)

## Source anchors
- `run_pack.py:527`
- `run_pack.py:560`
- `run_pack.py:572`
- `run_pack.py:1481`
- `run_pack.py:2027`
- `run_pack.py:2040`
- `run_pack.py:3085`
- `run_pack.py:2167`
- `out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:66`
- `AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md:43`
- `AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md:141`
- `AUDIT_PACK_YAML_EXAMPLES_COOKBOOK_v1_0.md:13`
- `AUDIT_PACK_YAML_EXAMPLES_COOKBOOK_v1_0.md:43`
- `AUDIT_PACK_YAML_EXAMPLES_COOKBOOK_v1_0.md:63`
- `pack_rust_audit_raqt.yaml:1`
- `pack_rust_audit_rsqt_general_v1_6_explicit.yaml:1`
- `pack_rust_audit_rsqt_extension_4q.yaml:1`
