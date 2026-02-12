---
title: Audit Pack YAML Examples Cookbook
version: 1.0
generated: 2026-02-10T17:41:52Z
---

# Audit Pack YAML Examples Cookbook (v1.0)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


This cookbook provides copy-paste YAML patterns for common pack authoring tasks.

---

## 1) Minimal deterministic audit question

```yaml
version: 1.0.0
pack_type: rust_audit_custom
engine: raqt
response_schema: |
  VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE
  CITATIONS=repo/path.rs:line(-line), ...
defaults:
  chat_top_k: 12
  max_tokens: 2000
  temperature: 0.0
questions:
  - id: Q_1
    title: Basic deterministic check
    category: safety
    answer_mode: deterministic
    question: |
      Determine whether unsafe blocks exist in production code.
    preflight:
      - name: unsafe_hits
        cmd: ["search", "unsafe", "--limit", "100", "--format", "json"]
validation:
  required_verdicts: ["TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"]
  fail_on_missing_citations: true
```

---

## 2) Deterministic answer + advice pass

Use deterministic answer for contract stability, then optional LLM advice:

```yaml
questions:
  - id: Q_OWNERSHIP_1
    title: Ownership/API review
    category: architecture
    answer_mode: deterministic
    advice_mode: llm
    question: |
      Evaluate ownership-related API surface risk.
    preflight:
      - name: pub_fn
        cmd: ["search", "pub fn ", "--limit", "80", "--format", "json"]
```

---

## 3) Strict citation and path gates

Use this when you want fail-closed citation discipline:

```yaml
validation:
  required_verdicts: ["TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"]
  fail_on_missing_citations: true
  enforce_citations_from_evidence: true
  enforce_no_new_paths: true
  enforce_paths_must_be_cited: true
```

---

## 4) RAQT pack with RSQT cross-engine preflight

Pattern: use RAQT as primary engine, but override some preflights to RSQT:

```yaml
engine: raqt
questions:
  - id: Q_BOUNDARY_1
    title: Boundary conversion coverage
    category: error_handling
    preflight:
      - name: raqt_defs
        cmd: ["defs", "--kind", "enum", "--name", "Error", "--format", "json"]
      - name: rsqt_impl_search
        engine_override: rsqt
        cmd: ["search", "impl From<", "--limit", "40", "--format", "json"]
```

---

## 5) Preflight filtering: exclude tests/comments

```yaml
preflight:
  - name: unwrap_hits
    cmd: ["search", ".unwrap(", "--limit", "200", "--format", "json"]
    transform:
      exclude_test_files: true
      exclude_comments: true
      max_items: 30
      max_chars: 4000
      render: lines
```

---

## 6) Regex-constrained evidence

```yaml
preflight:
  - name: boundary_usage
    cmd: ["search", ".map_err(", "--limit", "120", "--format", "json"]
    transform:
      require_regex:
        - "(?i)(CommandError|CliError|Error)"
      max_items: 25
```

---

## 7) Cross-preflight top-path narrowing (`group_by_path_top_n`)

Use aggregate stats preflight to pick top files, then narrow detail preflight.

```yaml
preflight:
  - name: impl_distribution
    cmd: ["impls", "--format", "json"]
  - name: impl_detail
    cmd: ["search", "impl ", "--limit", "500", "--format", "json"]
    transform:
      group_by_path_top_n:
        from: impl_distribution
        top_n: 5
        per_path: 5
        sort_key: impl_count
      render: lines
```

---

## 8) Render strategy patterns

### Compact list (default)

```yaml
render: list
```

### Source-like snippets

```yaml
render: block
fence_lang: rust
block_max_chars: 8000
```

### Citation-friendly line output

```yaml
render: lines
```

### Full structure (debug-heavy)

```yaml
render: json
```

---

## 9) Plugin wiring per pack domain

## 9.1 RSQT general

```yaml
runner:
  plugin: rsqt_guru
  plugin_config:
    rules_path: cfg_rust_audit_rsqt_general_finding_rules.yaml
    question_validators_path: cfg_rust_audit_rsqt_general_question_validators.yaml
```

## 9.2 RSQT extension 3q

```yaml
runner:
  plugin: rsqt_guru
  plugin_config:
    rules_path: cfg_rust_audit_rsqt_extension_3q_finding_rules.yaml
    question_validators_path: cfg_rust_audit_rsqt_extension_3q_question_validators.yaml
```

## 9.3 RAQT domain

```yaml
runner:
  plugin: rsqt_guru
  plugin_config:
    rules_path: cfg_rust_audit_raqt_finding_rules.yaml
    question_validators_path: cfg_rust_audit_raqt_question_validators.yaml
```

---

## 10) Prompt override pattern in pack YAML

```yaml
runner:
  prompts:
    grounding: prompts/RUST_GURU_GROUNDING.md
    analyze: prompts/RUST_GURU_ANALYZE_ONLY.md
```

CLI `--system-prompt-*` flags still override these values.

---

## 11) Validator rule examples

## 11.1 Require non-test citations when trigger appears

```yaml
defaults:
  test_path_patterns:
    - '(^|/)(tests)(/|$)'
    - '(^|/)test_[^/]+\.rs$'
validators:
  Q_1:
    - type: require_non_test_fileline_citations_if_regex
      trigger_regex: '(?i)unsafe|panic|unwrap'
      message_no_citations: 'Missing required file:line citations'
      message_all_test: 'All citations point to test-only files'
```

## 11.2 Require regex count

```yaml
validators:
  Q_2:
    - type: require_min_inline_regex_count
      regex: '(?m)^VERDICT='
      min_count: 1
      message: 'Missing VERDICT line'
```

## 11.3 Conditional regex count

```yaml
validators:
  Q_3:
    - type: require_min_inline_regex_count_if_regex
      if_regex: '(?i)INSUFFICIENT EVIDENCE'
      regex: '(?m)^CITATIONS='
      min_count: 1
      message: 'INSUFFICIENT EVIDENCE must still include citations'
```

## 11.4 Ban pattern

```yaml
validators:
  Q_4:
    - type: ban_regex
      regex: '(?i)^#+\\s'
      message: 'Markdown headings are not allowed'
```

---

## 12) Recommended file naming pattern for new packs

- pack: `pack_<domain>_<engine>_<purpose>.yaml`
- validators: `cfg_<domain>_<purpose>_question_validators.yaml`
- findings: `cfg_<domain>_<purpose>_finding_rules.yaml`

Example:

- `pack_security_raqt_diff_scan.yaml`
- `cfg_security_diff_scan_question_validators.yaml`
- `cfg_security_diff_scan_finding_rules.yaml`

