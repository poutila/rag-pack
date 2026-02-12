from __future__ import annotations

"""
run_pack.py — Generic pack runner with plugin support

v3.2.0 additions:
- Plugin hook: post_run() for pack-type specific outputs (e.g., Rust Guru report + deterministic findings)
- RsqtGuruPlugin is loaded automatically for (engine=rsqt, pack_type startswith rust_audit)
- Per-question execution modes:
  - answer_mode=deterministic (skip answer LLM call)
  - advice_mode=llm (optional LLM feedback pass)

Keep in mind:
- Engine CLI shapes live in engine_specs.yaml (or pack.runner.engine_spec overrides)
- Domain-specific outputs belong in plugins/, not in this runner
"""

import argparse
import hashlib
import importlib
import json
import logging
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.validation import validate_response_schema as shared_validate_response_schema

# Ensure script directory is in sys.path for plugin imports
_SCRIPT_DIR = Path(__file__).parent.resolve()
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Plugin imports (optional; runner remains usable without plugins/)
try:
    from plugins.base import PluginContext, PluginOutputs, PackPlugin
    from plugins.rsqt_guru import RsqtGuruPlugin, synthesize_deterministic_answer
except Exception:  # pragma: no cover
    PluginContext = None  # type: ignore
    PluginOutputs = None  # type: ignore
    PackPlugin = object  # type: ignore
    RsqtGuruPlugin = None  # type: ignore
    synthesize_deterministic_answer = None  # type: ignore


DEFAULT_RUNNER_POLICY: Dict[str, Any] = {
    "runner": {
        "version": "3.2.0",
        "hash_chunk_size": 8192,
        "replicate": {
            "default_seeds": [42, 123, 456],
            "seed_prefix": "seed_",
        },
        "quote_bypass": {
            "mode_labels": {
                "auto": "AUTO",
                "on": "QUOTE_BYPASS",
                "off": "STANDARD",
            },
            "mode_choices": ["auto", "on", "off"],
            "default_mode": "auto",
            "evidence_empty_gate_default": True,
        },
        "evidence_presence_gate": {
            "fail_on_empty_evidence": True,
            "fail_fast": True,
        },
        "logging": {
            "level": "INFO",
            "field_max_chars": 320,
            "question_max_chars": 260,
            "prompt_max_chars": 420,
            "path_sample_items": 5,
            "to_file": True,
            "filename": "RUN_LOG.txt",
            "stderr_noise_patterns": [
                r"^raqt:\s+using\b",
            ],
            "evidence_delivery_audit": {
                "enabled": True,
                "filename_suffix": "_evidence_delivery_audit.json",
                "summary_filename": "EVIDENCE_DELIVERY_SUMMARY.json",
                "sample_items": 12,
                "parquet_scan_batch_size": 4096,
                "parquet_path_universe_cap": 250000,
            },
        },
        "advice_quality_gate": {
            "enabled": True,
            "mission_pack_type_regex": r"(?i)mission",
            "require_llm_advice_mode": True,
            "retry_on_validation_fail": True,
            "retry_attempts": 1,
            "retry_issue_bullets": 8,
            "min_concrete_issues_when_evidence": 2,
            "min_issue_words": 4,
            "required_issue_fields": ["ISSUE", "WHY_IT_MATTERS", "PATCH_SKETCH", "TEST_PLAN", "CITATIONS"],
            "praise_phrases": [
                "you are doing fine",
                "you are doing great",
                "looks good",
                "great job",
                "nice work",
                "very nice",
                "solid work",
                "well done",
            ],
            "generic_issue_phrases": [
                "improve error handling",
                "add more tests",
                "improve documentation",
                "refactor this code",
                "consider improvements",
                "optimize performance",
            ],
            "imperative_verbs": [
                "replace",
                "add",
                "remove",
                "enforce",
                "introduce",
                "refactor",
                "migrate",
                "implement",
                "guard",
                "ban",
                "split",
                "rename",
                "pin",
                "enable",
                "disable",
                "write",
                "test",
                "assert",
                "validate",
                "harden",
            ],
        },
        "manifest": {
            "schema_version": "1.1",
            "filename": "RUN_MANIFEST.json",
        },
        "reports": {
            "report_file": "REPORT.md",
            "stability_file": "STABILITY_SUMMARY.md",
            "guru_metrics_file": "GURU_METRICS.json",
            "guru_stability_file": "GURU_STABILITY_SUMMARY.md",
            "pack_stability_title": "Pack Stability Summary",
            "guru_stability_title": "Guru Stability Summary",
        },
        "defaults": {
            "pack_file": "pack_rust_audit_rsqt_general_v1_6_explicit.yaml",
            "engine_specs_file": "engine_specs.yaml",
            "system_prompt_file": "prompts/RUST_GURU_SYSTEM.md",
            "grounding_prompt_file": "prompts/RUST_GURU_GROUNDING.md",
            "analyze_prompt_file": "prompts/RUST_GURU_ANALYZE_ONLY.md",
            "out_dir_name": "xref_state",
            "out_dir_timestamp_format": "%y%m%d_%H:%M:%S",
            "out_dir_include_model": True,
            "out_dir_model_max_chars": 36,
            "out_dir_pack_max_chars": 52,
            "parquet_file": "RSQT.parquet",
            "index_file": ".rsqt.faiss",
            "backend": "ollama",
            "model": "strand-iq4xs:latest",
            "prompt_profile": "grounded",
            "top_p": 1.0,
            "chat_top_k_initial": 8,
            "preflight_max_chars": 1600,
        },
        "path_aliases": {
            "rust_audit_pack_general_v1_6.explicit.yaml": "pack_rust_audit_rsqt_general_v1_6_explicit.yaml",
            "rust_audit_extension_4q.yaml": "pack_rust_audit_rsqt_extension_4q.yaml",
            "rust_audit_raqt.yaml": "pack_rust_audit_raqt.yaml",
            "rsqt_question_validators.yaml": "cfg_rust_audit_rsqt_general_question_validators.yaml",
            "rsqt_finding_rules.yaml": "cfg_rust_audit_rsqt_general_finding_rules.yaml",
            "rsqt_question_validators_ext4q.yaml": "cfg_rust_audit_rsqt_extension_4q_question_validators.yaml",
            "rsqt_finding_rules_ext4q.yaml": "cfg_rust_audit_rsqt_extension_4q_finding_rules.yaml",
            "rsqt_question_validators_raqt.yaml": "cfg_rust_audit_raqt_question_validators.yaml",
            "rsqt_finding_rules_raqt.yaml": "cfg_rust_audit_raqt_finding_rules.yaml",
        },
        "env": {
            "default": {},
            "by_engine": {},
            "auto_detect_exec": {
                "raqt": {
                    "RUST_ANALYZER_PATH": "rust-analyzer",
                },
            },
            "auto_detect_sha256": {
                "raqt": {
                    "RUST_ANALYZER_SHA256": "RUST_ANALYZER_PATH",
                },
            },
        },
    },
    "pack_defaults": {
        "chat_top_k": 12,
        "max_tokens": 1024,
        "temperature": 0.0,
    },
    "pack_validation": {
        "required_verdicts": ["TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"],
        "citation_format": "path:line(-line)",
        "fail_on_missing_citations": True,
        "enforce_citations_from_evidence": True,
        "enforce_no_new_paths": True,
        "enforce_paths_must_be_cited": True,
        "minimum_questions": 0,
    },
    "question_modes": {
        "answer": ["llm", "deterministic"],
        "advice": ["none", "llm"],
    },
    "engine_defaults": {
        "chat_subcommand": "chat",
        "parquet_flag": "--rsqt",
        "index_flag": "--index",
        "backend_flag": "--backend",
        "top_k_flag": "--top-k",
        "model_flag": "--model",
        "system_prompt_flag": "--system-prompt-file",
        "max_tokens_flag": "--max-tokens",
        "temperature_flag": "--temperature",
        "format_flag": "--format",
        "format_value": "json",
        "prompt_profile_flag": "--prompt-profile",
        "top_p_flag": "--top-p",
        "num_ctx_flag": "--num-ctx",
        "preflight_needs_index_cmds": ["rag-search"],
    },
    "validators": {
        "question_validators_default_filename": "cfg_rust_audit_rsqt_general_question_validators.yaml",
        "citation_token_regex": r"(?:(?:file|path):)?[A-Za-z0-9_./\-]+:\d+(?:-\d+)?",
        "pathline_regex": r"^(?P<path>[^\s:]+(?:/[^\s:]+)*):(?P<a>\d+)(?:-(?P<b>\d+))?$",
        "file_path_regex": (
            r"(?<![A-Za-z0-9_.\-])"
            r"((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.(?:rs|toml|ya?ml|json|md|lock|sh|py))"
            r"(?![A-Za-z0-9_.\-])"
        ),
        "issue_caps": {
            "invalid_citations": 6,
            "unknown_citations": 8,
            "unknown_paths": 10,
            "uncited_paths": 10,
            "adaptive_rerun_bullets": 8,
            "sources": 20,
            "deterministic_citations": 5,
            "unknown_key_fields": 5,
            "git_short_sha": 7,
            "advice_top_k_cap": 8,
        },
    },
    "preflight": {
        "default_test_path_patterns": [
            r"(^|/)(tests)(/|$)",
            r"(^|/)(testdata|fixtures)(/|$)",
            r"(^|/)[^/]*_tests?\.rs$",
            r"(^|/)test_[^/]+\.rs$",
        ],
        "default_exclude_path_regex": [
            r"(^|/)audit_runs(/|$)",
            r"(^|/)xref_state(/|$)",
        ],
        "filtered_to_zero_fail": {
            "enabled": True,
            "raw_rows_threshold": 20,
            "fail_fast": True,
        },
        "corpus_scope_gate": {
            "enabled": True,
            "fail_fast": True,
            "require_path_universe": True,
            "forbidden_path_regex": [
                r"(^|/)audit_runs(/|$)",
                r"(^|/)xref_state(/|$)",
            ],
            "sample_items": 12,
        },
        "dynamic_key_discovery": {
            "enabled": True,
            "from_engine_registry": True,
            "from_parquet_schema": True,
            "from_preflight_payloads": True,
            "require_engine_schema_contract": True,
            "allow_engine_registry_fallback": False,
            "fail_on_missing_semantic_categories": True,
            "required_semantic_categories": ["path_keys", "line_keys", "snippet_keys"],
            "max_keys_per_category": 64,
            "path_hint_terms": ["path", "file", "uri", "title"],
            "line_hint_terms": ["line", "lineno", "line_number", "line_start", "line_end"],
            "snippet_hint_terms": ["snippet", "text", "source", "doc", "signature", "content", "body"],
            "row_container_hint_terms": ["rows", "results", "entities", "items", "files", "sources", "hits", "matches", "data"],
            "always_include_path_keys": [],
            "always_include_line_keys": [],
            "always_include_snippet_keys": [],
        },
        "iter_rows_keys": [],
        "has_hits_count_keys": [],
        "row_count_keys": [],
        "path_keys": [],
        "line_keys": [],
        "snippet_keys": [],
        "transform_filter_keys": [
            "include_path_regex",
            "exclude_path_regex",
            "exclude_test_files",
            "exclude_comments",
            "require_contains",
            "require_regex",
            "group_by_path_top_n",
            "filter_fn",
        ],
        "group_by_path_defaults": {
            "top_n": 5,
            "per_path": 5,
        },
    },
    "evidence_format": {
        "default_render_mode": "list",
        "max_chars": {
            "list": 1600,
            "block": 8000,
            "lines": 4000,
            "json": 10000,
            "evidence_block": 1600,
        },
        "shorten": {
            "default": 200,
            "signature": 120,
            "doc": 100,
            "line_text": 200,
        },
    },
    "prompts": {
        "retrieved_sources_header": "RETRIEVED SOURCES (authoritative; cite these sections):",
        "response_format_header": "RESPONSE FORMAT (MUST FOLLOW EXACTLY)",
        "response_format_cite_rule": "If evidence provides CITE=..., cite that token verbatim (without the CITE= prefix).",
        "question_header": "QUESTION:",
        "mandatory_procedure": (
            "MANDATORY PROCEDURE:\n"
            "1) Before any explanation, paste the required quoted code/text verbatim from the Sections above.\n"
            "2) If you cannot quote it verbatim, output NOT FOUND and stop.\n"
            "3) After quoting, provide the answer body."
        ),
        "quote_bypass": {
            "title": "QUOTE-BYPASS MODE",
            "preamble": (
                "The following evidence has been deterministically extracted from the corpus.\n"
                "You MUST NOT output 'NOT FOUND' - the evidence IS present below.\n"
                "Your task: Use the evidence and answer the question."
            ),
            "evidence_header": "EVIDENCE (authoritative)",
            "instructions": [
                "1. Reference the evidence above to answer the question.",
                "2. If the question asks for text/definitions, repeat the relevant parts from Evidence.",
                "3. If evidence is insufficient, say INSUFFICIENT EVIDENCE and list what's missing.",
            ],
        },
        "deterministic_answer": {
            "verdict": "INDETERMINATE",
            "note": "question.answer_mode=deterministic; model answer generation was skipped.",
            "fallback_suffix": "_preflight.json:1",
        },
        "advice_prompt": {
            "no_evidence_text": "(no evidence blocks available)",
            "text": (
                "RUST IMPROVEMENT ADVICE MODE\n\n"
                "You are a strict Rust reviewer for mission-grade systems.\n"
                "Your job is corrective guidance, not praise.\n"
                "Do not restate the audit answer.\n\n"
                "REQUIRED OUTPUT FORMAT (plain text):\n"
                "ISSUE_1=...\n"
                "WHY_IT_MATTERS_1=...\n"
                "PATCH_SKETCH_1=...\n"
                "TEST_PLAN_1=...\n"
                "CITATIONS_1=<copy citation tokens from evidence, e.g. crates/engine/src/store.rs:25-30>\n"
                "ISSUE_2=... (optional)\n"
                "WHY_IT_MATTERS_2=... (optional)\n"
                "PATCH_SKETCH_2=... (optional)\n"
                "TEST_PLAN_2=... (optional)\n"
                "CITATIONS_2=<copy citation tokens from evidence> (optional)\n"
                "ISSUE_3=... (optional)\n"
                "WHY_IT_MATTERS_3=... (optional)\n"
                "PATCH_SKETCH_3=... (optional)\n"
                "TEST_PLAN_3=... (optional)\n"
                "CITATIONS_3=<copy citation tokens from evidence> (optional)\n\n"
                "RULES:\n"
                "- Return at least ISSUE_1 and ISSUE_2 unless evidence is insufficient.\n"
                "- Max 3 issues.\n"
                "- Do not write compliments or generic praise.\n"
                "- Each ISSUE must be an imperative fix statement (for example: \"Replace panic path with Result propagation\").\n"
                "- Prefer Rust-idiomatic suggestions (error conversions, trait boundaries, async/thread safety, testing seams).\n"
                "- PATCH_SKETCH should name concrete Rust targets (module/function/type/test names) when evidence allows.\n"
                "- TEST_PLAN must include at least one failing test condition and one success condition.\n"
                "- CITATIONS_n must be copied verbatim from evidence tokens (look for lines starting with \"CITE=\"); do NOT invent tokens.\n"
                "- Every issue must include at least one such citation token.\n"
                "- If evidence is insufficient for an issue, do not include that issue.\n\n"
            ),
        },
        "evidence_empty_answer": (
            "**NOT FOUND**\n\n"
            "Deterministic evidence extraction returned no results. Model call skipped."
        ),
        "adaptive_rerun": {
            "preamble": (
                "IMPORTANT:\n"
                "- Follow the required response schema exactly (VERDICT/CITATIONS first).\n"
                "- If evidence is present, do not output NOT FOUND.\n"
                "- Ensure CITATIONS tokens are path:line(-line)."
            ),
            "issues_header": "Validation issues to fix in this rerun:",
        },
        "schema_retry": {
            "initial_preamble": (
                "OUTPUT CONTRACT OVERRIDE:\n"
                "- Return plain text only (no markdown headers/bullets).\n"
                "- First line must be VERDICT=...\n"
                "- Second line must be CITATIONS=...\n"
                "- CITATIONS must only use tokens from CITE= evidence lines."
            ),
            "preamble": (
                "SCHEMA RETRY MODE:\n"
                "- Fix all validation issues listed below.\n"
                "- Preserve factual claims; only repair format/citations as needed.\n"
                "- Return plain text only."
            ),
            "template_header": "STRICT RESPONSE TEMPLATE (MUST MATCH)",
            "issues_header": "Validation issues to fix in this retry:",
            "max_issue_bullets": 8,
        },
    },
    "plugin": {
        "known_plugins": ["rsqt_guru"],
        "disable_aliases": ["", "none", "null", "no", "false", "off"],
    },
}


def _deep_copy_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_copy_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy_obj(v) for v in obj]
    return obj


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = _deep_copy_obj(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = _deep_copy_obj(v)
    return out


def _load_runner_policy(defaults: Dict[str, Any], path: Path) -> Dict[str, Any]:
    policy = _deep_copy_obj(defaults)
    if not path.exists():
        return policy
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Warning: failed to parse runner policy at {path}: {e}", file=sys.stderr)
        return policy
    if not isinstance(raw, dict):
        print(f"Warning: runner policy at {path} is not a YAML mapping; using defaults", file=sys.stderr)
        return policy
    return _deep_merge_dict(policy, raw)


def _policy_get(path: str, default: Any = None) -> Any:
    cur: Any = RUNNER_POLICY
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


# Prefer external policy file shipped with the repo (SSOT) to avoid drift between
# embedded DEFAULT_RUNNER_POLICY and runner_policy.yaml.
_POLICY_FILE = Path(__file__).with_name("runner_policy.yaml")
if _POLICY_FILE.exists():
    try:
        _obj = yaml.safe_load(_POLICY_FILE.read_text(encoding="utf-8"))
        if isinstance(_obj, dict) and _obj:
            DEFAULT_RUNNER_POLICY = _obj
    except Exception:
        pass

_runner_policy_env = os.environ.get("RUNNER_POLICY_PATH")
RUNNER_POLICY_PATH = Path(_runner_policy_env).expanduser() if _runner_policy_env else (_SCRIPT_DIR / "runner_policy.yaml")
RUNNER_POLICY = _load_runner_policy(DEFAULT_RUNNER_POLICY, RUNNER_POLICY_PATH)

RUNNER_VERSION = str(_policy_get("runner.version", "3.2.0"))
HASH_CHUNK_SIZE = int(_policy_get("runner.hash_chunk_size", 8192))
DEFAULT_REPLICATE_SEEDS = [int(s) for s in (_policy_get("runner.replicate.default_seeds", [42, 123, 456]) or [42, 123, 456])]
REPLICATE_SEED_PREFIX = str(_policy_get("runner.replicate.seed_prefix", "seed_"))
MANIFEST_SCHEMA_VERSION = str(_policy_get("runner.manifest.schema_version", "1.1"))
MANIFEST_FILENAME = str(_policy_get("runner.manifest.filename", "RUN_MANIFEST.json"))
REPORT_FILE = str(_policy_get("runner.reports.report_file", "REPORT.md"))
STABILITY_FILE = str(_policy_get("runner.reports.stability_file", "STABILITY_SUMMARY.md"))
GURU_METRICS_FILE = str(_policy_get("runner.reports.guru_metrics_file", "GURU_METRICS.json"))
GURU_STABILITY_FILE = str(_policy_get("runner.reports.guru_stability_file", "GURU_STABILITY_SUMMARY.md"))
PACK_STABILITY_TITLE = str(_policy_get("runner.reports.pack_stability_title", "Pack Stability Summary"))
GURU_STABILITY_TITLE = str(_policy_get("runner.reports.guru_stability_title", "Guru Stability Summary"))
DEFAULT_PACK_FILE = str(_policy_get("runner.defaults.pack_file", "pack_rust_audit_rsqt_general_v1_6_explicit.yaml"))
DEFAULT_ENGINE_SPECS_FILE = str(_policy_get("runner.defaults.engine_specs_file", "engine_specs.yaml"))
DEFAULT_SYSTEM_PROMPT_FILE = str(_policy_get("runner.defaults.system_prompt_file", "prompts/RUST_GURU_SYSTEM.md"))
DEFAULT_GROUNDING_PROMPT_FILE = str(_policy_get("runner.defaults.grounding_prompt_file", "prompts/RUST_GURU_GROUNDING.md"))
DEFAULT_ANALYZE_PROMPT_FILE = str(_policy_get("runner.defaults.analyze_prompt_file", "prompts/RUST_GURU_ANALYZE_ONLY.md"))
DEFAULT_OUT_DIR_NAME = str(_policy_get("runner.defaults.out_dir_name", "xref_state"))
DEFAULT_OUT_DIR_TIMESTAMP_FORMAT = str(_policy_get("runner.defaults.out_dir_timestamp_format", "%y%m%d_%H:%M:%S"))
DEFAULT_OUT_DIR_INCLUDE_MODEL = bool(_policy_get("runner.defaults.out_dir_include_model", True))
DEFAULT_OUT_DIR_MODEL_MAX_CHARS = max(8, int(_policy_get("runner.defaults.out_dir_model_max_chars", 36)))
DEFAULT_OUT_DIR_PACK_MAX_CHARS = max(12, int(_policy_get("runner.defaults.out_dir_pack_max_chars", 52)))
DEFAULT_PARQUET_FILE = str(_policy_get("runner.defaults.parquet_file", "RSQT.parquet"))
DEFAULT_INDEX_FILE = str(_policy_get("runner.defaults.index_file", ".rsqt.faiss"))
DEFAULT_BACKEND = str(_policy_get("runner.defaults.backend", "ollama"))
DEFAULT_MODEL = str(_policy_get("runner.defaults.model", "strand-iq4xs:latest"))
DEFAULT_PROMPT_PROFILE = str(_policy_get("runner.defaults.prompt_profile", "grounded"))
DEFAULT_TOP_P = float(_policy_get("runner.defaults.top_p", 1.0))
DEFAULT_CHAT_TOP_K_INITIAL = int(_policy_get("runner.defaults.chat_top_k_initial", 8))
DEFAULT_PREFLIGHT_MAX_CHARS = int(_policy_get("runner.defaults.preflight_max_chars", 1600))
PATH_ALIASES: Dict[str, str] = dict(_policy_get("runner.path_aliases", {}))
RUNNER_ENV_DEFAULT: Dict[str, Any] = dict(_policy_get("runner.env.default", {}))
RUNNER_ENV_BY_ENGINE: Dict[str, Any] = dict(_policy_get("runner.env.by_engine", {}))
RUNNER_ENV_AUTO_DETECT_EXEC: Dict[str, Any] = dict(_policy_get("runner.env.auto_detect_exec", {}))
RUNNER_ENV_AUTO_DETECT_SHA256: Dict[str, Any] = dict(_policy_get("runner.env.auto_detect_sha256", {}))
QUOTE_BYPASS_MODE_LABELS: Dict[str, str] = dict(_policy_get("runner.quote_bypass.mode_labels", {"auto": "AUTO", "on": "QUOTE_BYPASS", "off": "STANDARD"}))
QUOTE_BYPASS_MODE_CHOICES = [str(x) for x in (_policy_get("runner.quote_bypass.mode_choices", ["auto", "on", "off"]) or ["auto", "on", "off"])]
QUOTE_BYPASS_DEFAULT_MODE = str(_policy_get("runner.quote_bypass.default_mode", "auto"))
EVIDENCE_EMPTY_GATE_DEFAULT = bool(_policy_get("runner.quote_bypass.evidence_empty_gate_default", True))
STRICT_FAIL_ON_EMPTY_EVIDENCE = bool(_policy_get("runner.evidence_presence_gate.fail_on_empty_evidence", True))
STRICT_EMPTY_EVIDENCE_FAIL_FAST = bool(_policy_get("runner.evidence_presence_gate.fail_fast", True))
ADVICE_GATE_ENABLED = bool(_policy_get("runner.advice_quality_gate.enabled", True))
ADVICE_MISSION_PACK_TYPE_REGEX = str(_policy_get("runner.advice_quality_gate.mission_pack_type_regex", r"(?i)mission"))
ADVICE_REQUIRE_LLM_MODE = bool(_policy_get("runner.advice_quality_gate.require_llm_advice_mode", True))
ADVICE_RETRY_ON_VALIDATION_FAIL = bool(_policy_get("runner.advice_quality_gate.retry_on_validation_fail", True))
try:
    ADVICE_RETRY_ATTEMPTS = int(_policy_get("runner.advice_quality_gate.retry_attempts", 1))
except Exception:
    ADVICE_RETRY_ATTEMPTS = 1
ADVICE_RETRY_ATTEMPTS = max(0, ADVICE_RETRY_ATTEMPTS)
try:
    ADVICE_RETRY_ISSUE_BULLETS = int(_policy_get("runner.advice_quality_gate.retry_issue_bullets", 8))
except Exception:
    ADVICE_RETRY_ISSUE_BULLETS = 8
ADVICE_RETRY_ISSUE_BULLETS = max(1, ADVICE_RETRY_ISSUE_BULLETS)
ADVICE_MIN_CONCRETE_ISSUES = int(_policy_get("runner.advice_quality_gate.min_concrete_issues_when_evidence", 2))
ADVICE_MIN_ISSUE_WORDS = int(_policy_get("runner.advice_quality_gate.min_issue_words", 4))
ADVICE_REQUIRED_FIELDS = tuple(
    str(x).strip().upper()
    for x in (_policy_get("runner.advice_quality_gate.required_issue_fields", ["ISSUE", "WHY_IT_MATTERS", "PATCH_SKETCH", "TEST_PLAN", "CITATIONS"]) or [])
    if str(x).strip()
)
ADVICE_PRAISE_PHRASES = tuple(
    str(x).strip().lower()
    for x in (_policy_get("runner.advice_quality_gate.praise_phrases", []) or [])
    if str(x).strip()
)
ADVICE_GENERIC_ISSUE_PHRASES = tuple(
    str(x).strip().lower()
    for x in (_policy_get("runner.advice_quality_gate.generic_issue_phrases", []) or [])
    if str(x).strip()
)
ADVICE_IMPERATIVE_VERBS = tuple(
    str(x).strip().lower()
    for x in (_policy_get("runner.advice_quality_gate.imperative_verbs", []) or [])
    if str(x).strip()
)

try:
    ADVICE_MISSION_PACK_TYPE_RE = re.compile(ADVICE_MISSION_PACK_TYPE_REGEX)
except re.error:
    ADVICE_MISSION_PACK_TYPE_RE = re.compile(r"mission", flags=re.IGNORECASE)

PACK_DEFAULTS_FALLBACK: Dict[str, Any] = dict(_policy_get("pack_defaults", {}))
PACK_VALIDATION_FALLBACK: Dict[str, Any] = dict(_policy_get("pack_validation", {}))
QUESTION_ANSWER_MODES = tuple(str(x) for x in (_policy_get("question_modes.answer", ["llm", "deterministic"]) or ["llm", "deterministic"]))
QUESTION_ADVICE_MODES = tuple(str(x) for x in (_policy_get("question_modes.advice", ["none", "llm"]) or ["none", "llm"]))
ENGINE_DEFAULTS: Dict[str, Any] = dict(_policy_get("engine_defaults", {}))

QUESTION_VALIDATORS_DEFAULT_FILENAME = str(_policy_get("validators.question_validators_default_filename", "cfg_rust_audit_rsqt_general_question_validators.yaml"))
_CITATION_TOKEN_PATTERN = str(_policy_get("validators.citation_token_regex", r"(?:(?:file|path):)?[A-Za-z0-9_./\-]+:\d+(?:-\d+)?"))
_PATHLINE_PATTERN = str(_policy_get("validators.pathline_regex", r"^(?P<path>[^\s:]+(?:/[^\s:]+)*):(?P<a>\d+)(?:-(?P<b>\d+))?$"))
_FILE_PATH_PATTERN = str(_policy_get("validators.file_path_regex", r"(?<![A-Za-z0-9_.\-])((?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.(?:rs|toml|ya?ml|json|md|lock|sh|py))(?![A-Za-z0-9_.\-])"))
ISSUE_CAPS: Dict[str, Any] = dict(_policy_get("validators.issue_caps", {}))

DEFAULT_TEST_PATH_PATTERNS = [str(x) for x in (_policy_get("preflight.default_test_path_patterns", []) or [])]
DEFAULT_EXCLUDE_PATH_REGEX = [str(x) for x in (_policy_get("preflight.default_exclude_path_regex", []) or [])]
PREFLIGHT_FILTERED_TO_ZERO_FAIL_ENABLED = bool(_policy_get("preflight.filtered_to_zero_fail.enabled", True))
try:
    PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD = int(
        _policy_get("preflight.filtered_to_zero_fail.raw_rows_threshold", 20)
    )
except Exception:
    PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD = 20
PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD = max(1, PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD)
PREFLIGHT_FILTERED_TO_ZERO_FAIL_FAST = bool(_policy_get("preflight.filtered_to_zero_fail.fail_fast", True))
PREFLIGHT_CORPUS_SCOPE_GATE_ENABLED = bool(_policy_get("preflight.corpus_scope_gate.enabled", True))
PREFLIGHT_CORPUS_SCOPE_GATE_FAIL_FAST = bool(_policy_get("preflight.corpus_scope_gate.fail_fast", True))
PREFLIGHT_CORPUS_SCOPE_REQUIRE_PATH_UNIVERSE = bool(
    _policy_get("preflight.corpus_scope_gate.require_path_universe", True)
)
try:
    PREFLIGHT_CORPUS_SCOPE_SAMPLE_ITEMS = int(_policy_get("preflight.corpus_scope_gate.sample_items", 12))
except Exception:
    PREFLIGHT_CORPUS_SCOPE_SAMPLE_ITEMS = 12
PREFLIGHT_CORPUS_SCOPE_SAMPLE_ITEMS = max(1, PREFLIGHT_CORPUS_SCOPE_SAMPLE_ITEMS)
PREFLIGHT_CORPUS_SCOPE_FORBIDDEN_REGEX = [
    str(x).strip()
    for x in (_policy_get("preflight.corpus_scope_gate.forbidden_path_regex", [r"(^|/)audit_runs(/|$)"]) or [])
    if str(x).strip()
]
DYNAMIC_KEY_DISCOVERY_ENABLED = bool(_policy_get("preflight.dynamic_key_discovery.enabled", True))
DYNAMIC_KEYS_FROM_ENGINE_REGISTRY = bool(_policy_get("preflight.dynamic_key_discovery.from_engine_registry", True))
DYNAMIC_KEYS_FROM_PARQUET_SCHEMA = bool(_policy_get("preflight.dynamic_key_discovery.from_parquet_schema", True))
DYNAMIC_KEYS_FROM_PREFLIGHT_PAYLOADS = bool(_policy_get("preflight.dynamic_key_discovery.from_preflight_payloads", True))
DYNAMIC_REQUIRE_ENGINE_SCHEMA_CONTRACT = bool(
    _policy_get("preflight.dynamic_key_discovery.require_engine_schema_contract", True)
)
DYNAMIC_ALLOW_ENGINE_REGISTRY_FALLBACK = bool(
    _policy_get("preflight.dynamic_key_discovery.allow_engine_registry_fallback", False)
)
DYNAMIC_FAIL_ON_MISSING_SEMANTIC_CATEGORIES = bool(
    _policy_get("preflight.dynamic_key_discovery.fail_on_missing_semantic_categories", True)
)
REQUIRED_SEMANTIC_KEY_CATEGORIES = tuple(
    str(x).strip()
    for x in (_policy_get("preflight.dynamic_key_discovery.required_semantic_categories", ["path_keys", "line_keys", "snippet_keys"]) or [])
    if str(x).strip()
)
DYNAMIC_MAX_KEYS_PER_CATEGORY = max(8, int(_policy_get("preflight.dynamic_key_discovery.max_keys_per_category", 64)))
DYNAMIC_PATH_HINT_TERMS = tuple(
    str(x).strip().lower()
    for x in (_policy_get("preflight.dynamic_key_discovery.path_hint_terms", ["path", "file", "uri", "title"]) or [])
    if str(x).strip()
)
DYNAMIC_LINE_HINT_TERMS = tuple(
    str(x).strip().lower()
    for x in (_policy_get("preflight.dynamic_key_discovery.line_hint_terms", ["line", "lineno", "line_number", "line_start", "line_end"]) or [])
    if str(x).strip()
)
DYNAMIC_SNIPPET_HINT_TERMS = tuple(
    str(x).strip().lower()
    for x in (_policy_get("preflight.dynamic_key_discovery.snippet_hint_terms", ["snippet", "text", "source", "doc", "signature", "content", "body"]) or [])
    if str(x).strip()
)
DYNAMIC_ROW_CONTAINER_HINT_TERMS = tuple(
    str(x).strip().lower()
    for x in (_policy_get("preflight.dynamic_key_discovery.row_container_hint_terms", ["rows", "results", "entities", "items", "files", "sources", "hits", "matches", "data"]) or [])
    if str(x).strip()
)
ALWAYS_INCLUDE_PATH_KEYS = tuple(
    str(x).strip()
    for x in (_policy_get("preflight.dynamic_key_discovery.always_include_path_keys", []) or [])
    if str(x).strip()
)
ALWAYS_INCLUDE_LINE_KEYS = tuple(
    str(x).strip()
    for x in (_policy_get("preflight.dynamic_key_discovery.always_include_line_keys", []) or [])
    if str(x).strip()
)
ALWAYS_INCLUDE_SNIPPET_KEYS = tuple(
    str(x).strip()
    for x in (_policy_get("preflight.dynamic_key_discovery.always_include_snippet_keys", []) or [])
    if str(x).strip()
)
ITER_ROWS_KEYS = tuple(str(x) for x in (_policy_get("preflight.iter_rows_keys", []) or []))
HAS_HITS_COUNT_KEYS = tuple(str(x) for x in (_policy_get("preflight.has_hits_count_keys", []) or []))
ROW_COUNT_KEYS = tuple(str(x) for x in (_policy_get("preflight.row_count_keys", []) or []))
PATH_KEYS = tuple(str(x) for x in (_policy_get("preflight.path_keys", []) or []))
LINE_KEYS = tuple(str(x) for x in (_policy_get("preflight.line_keys", []) or []))
SNIPPET_KEYS = tuple(str(x) for x in (_policy_get("preflight.snippet_keys", []) or []))
TRANSFORM_FILTER_KEYS = tuple(str(x) for x in (_policy_get("preflight.transform_filter_keys", ["include_path_regex", "exclude_path_regex", "exclude_test_files", "exclude_comments", "require_contains", "require_regex", "group_by_path_top_n", "filter_fn"]) or ["include_path_regex", "exclude_path_regex", "exclude_test_files", "exclude_comments", "require_contains", "require_regex", "group_by_path_top_n", "filter_fn"]))
GROUP_BY_PATH_DEFAULTS: Dict[str, Any] = dict(_policy_get("preflight.group_by_path_defaults", {}))

EVIDENCE_MAX_CHARS: Dict[str, Any] = dict(_policy_get("evidence_format.max_chars", {}))
EVIDENCE_SHORTEN: Dict[str, Any] = dict(_policy_get("evidence_format.shorten", {}))
DEFAULT_RENDER_MODE = str(_policy_get("evidence_format.default_render_mode", "list"))

PROMPTS: Dict[str, Any] = dict(_policy_get("prompts", {}))
PLUGIN_POLICY: Dict[str, Any] = dict(_policy_get("plugin", {}))
PROMPT_QUOTE_BYPASS: Dict[str, Any] = dict(PROMPTS.get("quote_bypass", {}))
PROMPT_DETERMINISTIC: Dict[str, Any] = dict(PROMPTS.get("deterministic_answer", {}))
PROMPT_ADVICE: Dict[str, Any] = dict(PROMPTS.get("advice_prompt", {}))
PROMPT_ADAPTIVE_RERUN: Dict[str, Any] = dict(PROMPTS.get("adaptive_rerun", {}))
PROMPT_SCHEMA_RETRY: Dict[str, Any] = dict(PROMPTS.get("schema_retry", {}))
DEFAULT_LOG_LEVEL = str(_policy_get("runner.logging.level", "INFO")).upper()
DEFAULT_LOG_FIELD_MAX_CHARS = int(_policy_get("runner.logging.field_max_chars", 320))
DEFAULT_LOG_QUESTION_MAX_CHARS = int(_policy_get("runner.logging.question_max_chars", 260))
DEFAULT_LOG_PROMPT_MAX_CHARS = int(_policy_get("runner.logging.prompt_max_chars", 420))
DEFAULT_LOG_PATH_SAMPLE_ITEMS = int(_policy_get("runner.logging.path_sample_items", 5))
DEFAULT_LOG_TO_FILE = bool(_policy_get("runner.logging.to_file", True))
DEFAULT_LOG_FILENAME = str(_policy_get("runner.logging.filename", "RUN_LOG.txt"))
LOG_STDERR_NOISE_PATTERNS = tuple(
    str(x) for x in (_policy_get("runner.logging.stderr_noise_patterns", [r"^raqt:\s+using\b"]) or [])
)
LOG_STDERR_NOISE_REGEXES = tuple(
    re.compile(pat, flags=re.IGNORECASE) for pat in LOG_STDERR_NOISE_PATTERNS if str(pat).strip()
)
EVIDENCE_AUDIT_ENABLED_DEFAULT = bool(
    _policy_get("runner.logging.evidence_delivery_audit.enabled", True)
)
EVIDENCE_AUDIT_FILENAME_SUFFIX = str(
    _policy_get("runner.logging.evidence_delivery_audit.filename_suffix", "_evidence_delivery_audit.json")
)
EVIDENCE_AUDIT_SUMMARY_FILENAME = str(
    _policy_get("runner.logging.evidence_delivery_audit.summary_filename", "EVIDENCE_DELIVERY_SUMMARY.json")
)
EVIDENCE_AUDIT_SAMPLE_ITEMS = max(
    1, int(_policy_get("runner.logging.evidence_delivery_audit.sample_items", 12))
)
EVIDENCE_AUDIT_PARQUET_SCAN_BATCH_SIZE = max(
    128, int(_policy_get("runner.logging.evidence_delivery_audit.parquet_scan_batch_size", 4096))
)
EVIDENCE_AUDIT_PARQUET_PATH_UNIVERSE_CAP = max(
    1000, int(_policy_get("runner.logging.evidence_delivery_audit.parquet_path_universe_cap", 250000))
)

LOGGER = logging.getLogger("run_pack")


def _normalize_log_level(raw_level: str | None) -> int:
    level_name = str(raw_level or DEFAULT_LOG_LEVEL).strip().upper()
    level = getattr(logging, level_name, None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _setup_logging(raw_level: str | None) -> None:
    level = _normalize_log_level(raw_level)
    fmt = "%(asctime)s %(levelname)s %(name)s:%(funcName)s:%(lineno)d %(message)s"
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=fmt)
    else:
        root.setLevel(level)
        for h in root.handlers:
            h.setLevel(level)
            if h.formatter is None:
                h.setFormatter(logging.Formatter(fmt))
    LOGGER.setLevel(level)


def _attach_log_file_handler(log_path: Path, raw_level: str | None) -> Path:
    level = _normalize_log_level(raw_level)
    fmt = "%(asctime)s %(levelname)s %(name)s:%(funcName)s:%(lineno)d %(message)s"
    target = log_path.expanduser()
    try:
        target = target.resolve()
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()

    for h in root.handlers:
        if not isinstance(h, logging.FileHandler):
            continue
        try:
            if Path(h.baseFilename).resolve() == target:
                h.setLevel(level)
                if h.formatter is None:
                    h.setFormatter(logging.Formatter(fmt))
                return target
        except Exception:
            continue

    fh = logging.FileHandler(target, mode="a", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)
    return target


def _compact_log_text(value: str, *, max_chars: int = DEFAULT_LOG_FIELD_MAX_CHARS) -> str:
    txt = re.sub(r"\s+", " ", str(value or "")).strip()
    limit = max(16, int(max_chars))
    if len(txt) <= limit:
        return txt
    return txt[: limit - 3] + "..."


def _shell_join(argv: List[str], *, max_chars: int = DEFAULT_LOG_FIELD_MAX_CHARS) -> str:
    try:
        joined = shlex.join([str(x) for x in argv])
    except Exception:
        joined = " ".join(str(x) for x in argv)
    return _compact_log_text(joined, max_chars=max_chars)


def _to_log_field(value: Any, *, max_chars: int = DEFAULT_LOG_FIELD_MAX_CHARS) -> str:
    if isinstance(value, str):
        s = value
    elif isinstance(value, (int, float, bool)) or value is None:
        s = str(value)
    elif isinstance(value, (list, tuple, set)):
        items = [str(x) for x in list(value)[:12]]
        if len(value) > 12:
            items.append("...")
        s = "[" + ", ".join(items) + "]"
    elif isinstance(value, dict):
        try:
            s = json.dumps(value, sort_keys=True, ensure_ascii=True)
        except Exception:
            s = str(value)
    else:
        s = str(value)
    return _compact_log_text(s, max_chars=max_chars)


def _log_event(level: int, event: str, **fields: Any) -> None:
    if not LOGGER.isEnabledFor(level):
        return
    parts = [f"event={event}"]
    for key, val in fields.items():
        if val is None:
            continue
        parts.append(f"{key}={_to_log_field(val)}")
    LOGGER.log(level, " | ".join(parts), stacklevel=2)


def _stderr_is_noise(stderr_text: str) -> bool:
    lines = [ln.strip() for ln in str(stderr_text or "").splitlines() if ln.strip()]
    if not lines or not LOG_STDERR_NOISE_REGEXES:
        return False
    for ln in lines:
        if any(rx.search(ln) for rx in LOG_STDERR_NOISE_REGEXES):
            continue
        return False
    return True


def _count_citations_in_answer(answer_text: str) -> int:
    m = re.search(r"(?mi)^\s*CITATIONS\s*[=:]\s*(.*)$", answer_text or "")
    if not m:
        return 0
    raw = (m.group(1) or "").strip()
    if not raw:
        return 0
    return len(re.findall(_CITATION_TOKEN_PATTERN, raw))


# =============================================================================
# Dynamic evidence key discovery (parquet + observed payloads)
# =============================================================================

def _dedupe_strs(values: List[str]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for raw in values:
        v = str(raw or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _empty_runtime_key_state() -> Dict[str, List[str]]:
    return {
        "iter_rows_keys": [],
        "has_hits_count_keys": [],
        "row_count_keys": [],
        "path_keys": [],
        "line_keys": [],
        "snippet_keys": [],
    }


_RUNTIME_DYNAMIC_KEYS: Dict[str, List[str]] = _empty_runtime_key_state()
_RUNTIME_PARQUET_COLUMNS: List[str] = []
_RUNTIME_PARQUET_SCHEMA_SOURCE: str = "(none)"
_RUNTIME_ENGINE_COLUMNS: List[str] = []
_RUNTIME_ENGINE_SCHEMA_SOURCE: str = "(none)"
_RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED: bool = False
_RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION: str = ""


def _reset_runtime_dynamic_keys() -> None:
    global _RUNTIME_DYNAMIC_KEYS
    global _RUNTIME_PARQUET_COLUMNS
    global _RUNTIME_PARQUET_SCHEMA_SOURCE
    global _RUNTIME_ENGINE_COLUMNS
    global _RUNTIME_ENGINE_SCHEMA_SOURCE
    global _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED
    global _RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION
    _RUNTIME_DYNAMIC_KEYS = _empty_runtime_key_state()
    _RUNTIME_PARQUET_COLUMNS = []
    _RUNTIME_PARQUET_SCHEMA_SOURCE = "(none)"
    _RUNTIME_ENGINE_COLUMNS = []
    _RUNTIME_ENGINE_SCHEMA_SOURCE = "(none)"
    _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED = False
    _RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION = ""
    for k in ALWAYS_INCLUDE_PATH_KEYS:
        _runtime_add_key("path_keys", k)
    for k in ALWAYS_INCLUDE_LINE_KEYS:
        _runtime_add_key("line_keys", k)
    for k in ALWAYS_INCLUDE_SNIPPET_KEYS:
        _runtime_add_key("snippet_keys", k)


def _runtime_add_key(category: str, key: Any) -> None:
    if category not in _RUNTIME_DYNAMIC_KEYS:
        return
    k = str(key or "").strip()
    if not k:
        return
    arr = _RUNTIME_DYNAMIC_KEYS[category]
    if k in arr:
        return
    if len(arr) >= DYNAMIC_MAX_KEYS_PER_CATEGORY:
        return
    arr.append(k)


def _ordered_union(base: Tuple[str, ...], extra: List[str]) -> Tuple[str, ...]:
    return tuple(_dedupe_strs(list(base) + [str(x) for x in (extra or [])]))


def _effective_iter_rows_keys() -> Tuple[str, ...]:
    return _ordered_union(ITER_ROWS_KEYS, _RUNTIME_DYNAMIC_KEYS.get("iter_rows_keys", []))


def _effective_has_hits_count_keys() -> Tuple[str, ...]:
    return _ordered_union(HAS_HITS_COUNT_KEYS, _RUNTIME_DYNAMIC_KEYS.get("has_hits_count_keys", []))


def _effective_row_count_keys() -> Tuple[str, ...]:
    return _ordered_union(ROW_COUNT_KEYS, _RUNTIME_DYNAMIC_KEYS.get("row_count_keys", []))


def _effective_path_keys() -> Tuple[str, ...]:
    return _ordered_union(PATH_KEYS, _RUNTIME_DYNAMIC_KEYS.get("path_keys", []))


def _effective_line_keys() -> Tuple[str, ...]:
    return _ordered_union(LINE_KEYS, _RUNTIME_DYNAMIC_KEYS.get("line_keys", []))


def _effective_snippet_keys() -> Tuple[str, ...]:
    return _ordered_union(SNIPPET_KEYS, _RUNTIME_DYNAMIC_KEYS.get("snippet_keys", []))


def _looks_like_repo_path_text(value: str) -> bool:
    txt = str(value or "").strip().replace("\\", "/")
    if not txt:
        return False
    if re.search(_FILE_PATH_PATTERN, txt):
        return True
    if txt.startswith("./") or txt.startswith("../"):
        return True
    if "/" in txt and "." in txt.split("/")[-1]:
        return True
    return False


def _looks_like_key_name(name: str, hints: Tuple[str, ...]) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    return any(h in n for h in hints)


def _is_metricish_key_name(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n.startswith("has_"):
        return True
    for suffix in ("_count", "_size", "_mtime", "_hash", "_version", "_json", "_bytes", "_total"):
        if n.endswith(suffix):
            return True
    for token in ("count", "size", "mtime", "hash", "version", "bytes"):
        if token in n:
            return True
    return False


def _is_path_key_candidate(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n in {"file", "path", "file_path", "doc_path", "target_file_path", "canonical_path", "title"}:
        return True
    if n.endswith("_path") or n.endswith("_file"):
        return True
    if _is_metricish_key_name(n):
        return False
    return _looks_like_key_name(n, DYNAMIC_PATH_HINT_TERMS)


def _is_line_key_candidate(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n in {"line", "line_number", "line_start", "line_end", "lineno"}:
        return True
    if "line_start" in n or "line_end" in n or "line_number" in n or "lineno" in n:
        return True
    if n.startswith("line_") or n.endswith("_line"):
        return True
    return False


def _is_snippet_key_candidate(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return False
    if n in {"snippet", "text", "source_text", "line_text", "doc_comment", "entity_doc_comment", "signature"}:
        return True
    if _is_metricish_key_name(n):
        return False
    return _looks_like_key_name(n, DYNAMIC_SNIPPET_HINT_TERMS)


def _discover_parquet_columns(parquet_path: Path) -> Tuple[List[str], str, str]:
    if not parquet_path.exists():
        return ([], "(missing parquet)", "parquet path does not exist")

    pyarrow_error = ""
    try:
        import pyarrow.parquet as pq  # type: ignore

        schema = pq.read_schema(str(parquet_path))
        cols = _dedupe_strs([str(n) for n in list(schema.names or [])])
        if cols:
            return (cols, "pyarrow.read_schema", "")
    except Exception as e:
        pyarrow_error = str(e)

    polars_error = ""
    try:
        import polars as pl  # type: ignore

        schema = pl.scan_parquet(str(parquet_path)).collect_schema()
        cols = _dedupe_strs([str(n) for n in list(schema.names())])
        if cols:
            return (cols, "polars.scan_parquet.collect_schema", "")
    except Exception as e:
        polars_error = str(e)

    err = pyarrow_error or polars_error or "unable to read parquet schema"
    if pyarrow_error and polars_error:
        err = f"pyarrow={pyarrow_error}; polars={polars_error}"
    return ([], "(unknown)", err)


def _engine_supports_schema_contract(engine_name: str) -> bool:
    eng = str(engine_name or "").strip().lower()
    return eng in {"rsqt", "raqt"}


def _build_engine_schema_cli_argv(engine_name: str, *, target_dir: Path | None = None) -> List[str]:
    eng = str(engine_name or "").strip().lower()
    argv = ["uv", "run", eng, "--strict-json"]
    if target_dir:
        argv.extend(["--target-dir", str(target_dir)])
    argv.extend(["schema", "--format", "json"])
    return argv


def _discover_engine_schema_contract_from_cli(
    engine_name: str, *, target_dir: Path | None = None
) -> Tuple[Dict[str, Any], str, str]:
    if not _engine_supports_schema_contract(engine_name):
        return ({}, "(n/a)", "engine has no schema contract endpoint")

    argv = _build_engine_schema_cli_argv(engine_name, target_dir=target_dir)
    source = "cli:" + " ".join(shlex.quote(x) for x in argv)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except Exception as e:
        return ({}, source, str(e))

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit={proc.returncode}"
        return ({}, source, err)

    raw = (proc.stdout or "").strip()
    if not raw:
        return ({}, source, "empty schema stdout")

    obj = parse_json_maybe(raw)
    if not isinstance(obj, dict):
        # Defensive parse for wrappers that may add benign leading/trailing text.
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j != -1 and j > i:
            obj = parse_json_maybe(raw[i : j + 1])
    if not isinstance(obj, dict):
        return ({}, source, "schema output is not valid JSON object")
    return (obj, source, "")


def _extract_semantic_keys_from_contract(contract: Dict[str, Any]) -> Dict[str, List[str]]:
    out = _empty_runtime_key_state()
    cols = contract.get("columns")
    if isinstance(cols, list):
        for entry in cols:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            roles = [
                str(x).strip().lower()
                for x in (entry.get("semantic_roles") or [])
                if str(x).strip()
            ]
            aliases = [str(x).strip() for x in (entry.get("aliases") or []) if str(x).strip()]
            for role in roles:
                if role == "path":
                    out["path_keys"].extend([name, *aliases])
                elif role == "line":
                    out["line_keys"].extend([name, *aliases])
                elif role == "snippet":
                    out["snippet_keys"].extend([name, *aliases])
                elif role in {"row_container", "rows_container", "row_container_key"}:
                    out["iter_rows_keys"].extend([name, *aliases])
                elif role in {"count", "row_count"}:
                    out["row_count_keys"].extend([name, *aliases])
                    out["has_hits_count_keys"].extend([name, *aliases])
                elif role in {"has_hits_count", "hit_count"}:
                    out["has_hits_count_keys"].extend([name, *aliases])

    hints = contract.get("semantic_hints")
    if isinstance(hints, dict):
        key_map = {
            "path_keys": "path_keys",
            "line_keys": "line_keys",
            "snippet_keys": "snippet_keys",
            "row_container_keys": "iter_rows_keys",
            "iter_rows_keys": "iter_rows_keys",
            "count_keys": "row_count_keys",
            "row_count_keys": "row_count_keys",
            "has_hits_count_keys": "has_hits_count_keys",
        }
        for hint_key, cat in key_map.items():
            val = hints.get(hint_key)
            if isinstance(val, list):
                out[cat].extend([str(x).strip() for x in val if str(x).strip()])

        # count_keys can safely seed both row_count and has_hits categories.
        count_keys = hints.get("count_keys")
        if isinstance(count_keys, list):
            normalized = [str(x).strip() for x in count_keys if str(x).strip()]
            out["row_count_keys"].extend(normalized)
            out["has_hits_count_keys"].extend(normalized)

    for k in list(out.keys()):
        out[k] = _dedupe_strs(out[k])
    return out


def _extract_columns_from_contract(contract: Dict[str, Any]) -> List[str]:
    cols_obj = contract.get("columns")
    if isinstance(cols_obj, list):
        vals: List[str] = []
        for item in cols_obj:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    vals.append(name)
            elif isinstance(item, str):
                txt = str(item).strip()
                if txt:
                    vals.append(txt)
        return _dedupe_strs(vals)
    if isinstance(cols_obj, dict):
        return _dedupe_strs([str(k).strip() for k in cols_obj.keys() if str(k).strip()])
    return []


def _seed_runtime_keys_from_contract(contract: Dict[str, Any]) -> None:
    semantic_keys = _extract_semantic_keys_from_contract(contract)
    for k in semantic_keys.get("iter_rows_keys", []):
        _runtime_add_key("iter_rows_keys", k)
    for k in semantic_keys.get("has_hits_count_keys", []):
        _runtime_add_key("has_hits_count_keys", k)
    for k in semantic_keys.get("row_count_keys", []):
        _runtime_add_key("row_count_keys", k)
    for k in semantic_keys.get("path_keys", []):
        _runtime_add_key("path_keys", k)
    for k in semantic_keys.get("line_keys", []):
        _runtime_add_key("line_keys", k)
    for k in semantic_keys.get("snippet_keys", []):
        _runtime_add_key("snippet_keys", k)


def _discover_engine_registry_columns(
    engine_name: str, *, target_dir: Path | None = None
) -> Tuple[List[str], str, str]:
    global _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED
    global _RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION

    eng = str(engine_name or "").strip().lower()
    if not _engine_supports_schema_contract(eng):
        return ([], "(n/a)", "engine has no known column registry module")

    contract, contract_source, contract_error = _discover_engine_schema_contract_from_cli(
        eng, target_dir=target_dir
    )
    if contract:
        contract_cols = _extract_columns_from_contract(contract)
        if contract_cols:
            _seed_runtime_keys_from_contract(contract)
            metadata = contract.get("metadata") if isinstance(contract.get("metadata"), dict) else {}
            schema_version = str((metadata or {}).get("schema_version") or "").strip()
            _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED = True
            _RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION = schema_version
            source = contract_source + (f"@{schema_version}" if schema_version else "")
            return (contract_cols, source, "")
        contract_error = contract_error or "schema contract has no columns"

    if DYNAMIC_REQUIRE_ENGINE_SCHEMA_CONTRACT and not DYNAMIC_ALLOW_ENGINE_REGISTRY_FALLBACK:
        err = contract_error or "engine schema contract is required but unavailable"
        return ([], contract_source, err)

    if eng == "rsqt":
        spec_mod_name = "doxslock.rsqt.spec"
        spec_attr = "RS_SCHEMA"
        col_mod_name = "doxslock.rsqt.columns"
    else:
        spec_mod_name = "doxslock.raqt.spec"
        spec_attr = "RA_SCHEMA"
        col_mod_name = "doxslock.raqt.columns"

    errors: List[str] = []
    sources: List[str] = []
    cols: List[str] = []

    # Prefer schema dict from spec.py (same SSOT used by generators).
    try:
        spec_mod = importlib.import_module(spec_mod_name)
        schema_obj = getattr(spec_mod, spec_attr, None)
        if isinstance(schema_obj, dict):
            spec_cols = [str(k).strip() for k in schema_obj.keys() if str(k).strip()]
            if spec_cols:
                cols.extend(spec_cols)
                sources.append(f"{spec_mod_name}.{spec_attr}")
            else:
                errors.append(f"{spec_mod_name}.{spec_attr} empty")
        else:
            errors.append(f"{spec_mod_name}.{spec_attr} missing/non-dict")
    except Exception as e:
        errors.append(f"{spec_mod_name}: {e}")

    # Merge column constants from columns.py when available.
    try:
        col_mod = importlib.import_module(col_mod_name)
        col_cls = getattr(col_mod, "Col", None)
        if col_cls is None:
            errors.append(f"{col_mod_name}.Col missing")
        else:
            col_vals = [str(val) for attr, val in vars(col_cls).items() if attr.isupper() and isinstance(val, str)]
            if col_vals:
                cols.extend(col_vals)
                sources.append(f"{col_mod_name}.Col")
            else:
                errors.append(f"{col_mod_name}.Col empty")
    except Exception as e:
        errors.append(f"{col_mod_name}: {e}")

    cols = _dedupe_strs(cols)
    if cols:
        merged_source = "+".join(sources) if sources else "(unknown)"
        if contract_error:
            errors.append(f"contract:{contract_error}")
        return (cols, merged_source, "; ".join(errors))
    return ([], "+".join(sources) if sources else "(none)", "; ".join(errors) or "no columns discovered")


def _seed_runtime_keys_from_parquet_columns(columns: List[str]) -> None:
    for col in columns:
        c = str(col).strip()
        if not c:
            continue
        lc = c.lower()
        if _is_path_key_candidate(lc):
            _runtime_add_key("path_keys", c)
        if _is_line_key_candidate(lc):
            _runtime_add_key("line_keys", c)
        if _is_snippet_key_candidate(lc):
            _runtime_add_key("snippet_keys", c)
        if "count" in lc or lc in ("total", "matches", "matched", "num_results", "result_count", "hit_count"):
            _runtime_add_key("row_count_keys", c)
            _runtime_add_key("has_hits_count_keys", c)


def _learn_runtime_keys_from_payload(stdout_data: Any) -> None:
    if not (DYNAMIC_KEY_DISCOVERY_ENABLED and DYNAMIC_KEYS_FROM_PREFLIGHT_PAYLOADS):
        return

    rows: List[Dict[str, Any]] = []
    if isinstance(stdout_data, list):
        rows = [x for x in stdout_data if isinstance(x, dict)]
    elif isinstance(stdout_data, dict):
        for k, v in stdout_data.items():
            if isinstance(v, list):
                rowish = [x for x in v if isinstance(x, dict)]
                if rowish:
                    lk = str(k).strip().lower()
                    if _looks_like_key_name(lk, DYNAMIC_ROW_CONTAINER_HINT_TERMS) or str(k) in ITER_ROWS_KEYS:
                        _runtime_add_key("iter_rows_keys", str(k))
                    rows = rowish
                    break
        for k, v in stdout_data.items():
            lk = str(k).strip().lower()
            if isinstance(v, (int, float)) and ("count" in lk or lk in ("total", "matches", "matched", "num_results", "result_count", "hit_count")):
                _runtime_add_key("has_hits_count_keys", str(k))
                _runtime_add_key("row_count_keys", str(k))
            elif isinstance(v, str):
                if _is_path_key_candidate(lk) and _looks_like_repo_path_text(v):
                    _runtime_add_key("path_keys", str(k))
                if _is_snippet_key_candidate(lk) and v.strip():
                    _runtime_add_key("snippet_keys", str(k))

    for row in rows:
        for k, v in row.items():
            key = str(k or "").strip()
            if not key:
                continue
            lkey = key.lower()
            if isinstance(v, str):
                if _is_path_key_candidate(lkey) or _looks_like_repo_path_text(v):
                    _runtime_add_key("path_keys", key)
                if _is_snippet_key_candidate(lkey) and v.strip():
                    _runtime_add_key("snippet_keys", key)
            elif isinstance(v, (int, float)):
                if _is_line_key_candidate(lkey):
                    _runtime_add_key("line_keys", key)
                if "count" in lkey or lkey in ("total", "matches", "matched", "num_results", "result_count", "hit_count"):
                    _runtime_add_key("has_hits_count_keys", key)
                    _runtime_add_key("row_count_keys", key)


def _evidence_key_snapshot(*, parquet_path: Path, engine_name: str) -> Dict[str, Any]:
    return {
        "dynamic_key_discovery_enabled": DYNAMIC_KEY_DISCOVERY_ENABLED,
        "engine": str(engine_name),
        "parquet_path": str(parquet_path),
        "engine_schema_source": _RUNTIME_ENGINE_SCHEMA_SOURCE,
        "engine_columns_count": len(_RUNTIME_ENGINE_COLUMNS),
        "engine_columns_sample": _RUNTIME_ENGINE_COLUMNS[:40],
        "engine_schema_contract_loaded": _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED,
        "engine_schema_contract_version": _RUNTIME_ENGINE_SCHEMA_CONTRACT_VERSION,
        "parquet_schema_source": _RUNTIME_PARQUET_SCHEMA_SOURCE,
        "parquet_columns_count": len(_RUNTIME_PARQUET_COLUMNS),
        "parquet_columns_sample": _RUNTIME_PARQUET_COLUMNS[:40],
        "required_semantic_categories": list(REQUIRED_SEMANTIC_KEY_CATEGORIES),
        "strict_require_engine_schema_contract": DYNAMIC_REQUIRE_ENGINE_SCHEMA_CONTRACT,
        "strict_fail_on_missing_semantic_categories": DYNAMIC_FAIL_ON_MISSING_SEMANTIC_CATEGORIES,
        "effective_keys": {
            "iter_rows_keys": list(_effective_iter_rows_keys()),
            "has_hits_count_keys": list(_effective_has_hits_count_keys()),
            "row_count_keys": list(_effective_row_count_keys()),
            "path_keys": list(_effective_path_keys()),
            "line_keys": list(_effective_line_keys()),
            "snippet_keys": list(_effective_snippet_keys()),
        },
        "runtime_added_keys": {k: list(v) for k, v in _RUNTIME_DYNAMIC_KEYS.items()},
    }


def _initialize_runtime_evidence_keys(*, parquet_path: Path, engine_name: str) -> Dict[str, Any]:
    global _RUNTIME_PARQUET_COLUMNS
    global _RUNTIME_PARQUET_SCHEMA_SOURCE
    global _RUNTIME_ENGINE_COLUMNS
    global _RUNTIME_ENGINE_SCHEMA_SOURCE
    _reset_runtime_dynamic_keys()
    engine_error = ""
    schema_error = ""
    if DYNAMIC_KEY_DISCOVERY_ENABLED and DYNAMIC_KEYS_FROM_ENGINE_REGISTRY:
        eng_cols, eng_source, eng_err = _discover_engine_registry_columns(
            engine_name, target_dir=parquet_path.parent
        )
        _RUNTIME_ENGINE_COLUMNS = eng_cols
        _RUNTIME_ENGINE_SCHEMA_SOURCE = eng_source
        engine_error = eng_err
        if eng_cols:
            _seed_runtime_keys_from_parquet_columns(eng_cols)
    else:
        _RUNTIME_ENGINE_SCHEMA_SOURCE = "(disabled)"

    if DYNAMIC_KEY_DISCOVERY_ENABLED and DYNAMIC_KEYS_FROM_PARQUET_SCHEMA:
        cols, source, err = _discover_parquet_columns(parquet_path)
        _RUNTIME_PARQUET_COLUMNS = cols
        _RUNTIME_PARQUET_SCHEMA_SOURCE = source
        schema_error = err
        if cols:
            _seed_runtime_keys_from_parquet_columns(cols)
    else:
        _RUNTIME_PARQUET_SCHEMA_SOURCE = "(disabled)"

    snap = _evidence_key_snapshot(parquet_path=parquet_path, engine_name=engine_name)
    if engine_error:
        snap["engine_discovery_error"] = engine_error
    if schema_error:
        snap["schema_discovery_error"] = schema_error

    effective_by_category: Dict[str, Tuple[str, ...]] = {
        "iter_rows_keys": _effective_iter_rows_keys(),
        "has_hits_count_keys": _effective_has_hits_count_keys(),
        "row_count_keys": _effective_row_count_keys(),
        "path_keys": _effective_path_keys(),
        "line_keys": _effective_line_keys(),
        "snippet_keys": _effective_snippet_keys(),
    }
    missing_required: List[str] = [
        cat
        for cat in REQUIRED_SEMANTIC_KEY_CATEGORIES
        if cat in effective_by_category and not effective_by_category[cat]
    ]
    if missing_required:
        snap["missing_required_semantic_categories"] = missing_required

    fatal_msgs: List[str] = []
    if (
        _engine_supports_schema_contract(engine_name)
        and DYNAMIC_REQUIRE_ENGINE_SCHEMA_CONTRACT
        and not _RUNTIME_ENGINE_SCHEMA_CONTRACT_LOADED
    ):
        fatal_msgs.append(
            "engine schema contract required but not loaded "
            f"(engine={engine_name} source={_RUNTIME_ENGINE_SCHEMA_SOURCE})"
        )
    if DYNAMIC_FAIL_ON_MISSING_SEMANTIC_CATEGORIES and missing_required:
        fatal_msgs.append(
            "missing required semantic categories: " + ", ".join(missing_required)
        )
    if fatal_msgs:
        snap["fatal_schema_contract_error"] = "; ".join(fatal_msgs)

    return snap


def _write_evidence_key_map(*, out_dir: Path, parquet_path: Path, engine_name: str) -> Path:
    path = out_dir / "EVIDENCE_KEY_MAP.json"
    write_json(path, _evidence_key_snapshot(parquet_path=parquet_path, engine_name=engine_name))
    return path


# =============================================================================
# Small IO helpers
# =============================================================================

@dataclass(frozen=True)
class CmdResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str


def run_cmd(
    argv: List[str], *, cwd: Path | None = None, env: Dict[str, str] | None = None
) -> CmdResult:
    proc_env: Dict[str, str] | None = None
    if env:
        proc_env = os.environ.copy()
        for k, v in env.items():
            if k and v is not None:
                proc_env[str(k)] = str(v)
    _log_event(
        logging.DEBUG,
        "run_cmd.start",
        cwd=str(cwd) if cwd else "(inherit)",
        argv=_shell_join(argv, max_chars=640),
        env_override_keys=sorted(env.keys()) if env else [],
    )
    p = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        text=True,
        capture_output=True,
        check=False,
    )
    _log_event(
        logging.DEBUG,
        "run_cmd.end",
        returncode=p.returncode,
        stdout_chars=len(p.stdout or ""),
        stderr_chars=len(p.stderr or ""),
    )
    return CmdResult(argv=argv, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)


def _coerce_env_map(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        if not key or v is None:
            continue
        val = os.path.expandvars(os.path.expanduser(str(v)))
        if val.strip():
            out[key] = val
    return out


def _coerce_str_map(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        val = str(v).strip() if v is not None else ""
        if key and val:
            out[key] = val
    return out


def _resolve_executable_path(raw_value: str) -> Path | None:
    if not raw_value:
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(raw_value).strip()))
    if not expanded:
        return None
    p = Path(expanded)
    if p.exists():
        return p
    found = shutil.which(expanded)
    return Path(found) if found else None


def _compute_runner_env_overrides(*, engine_name: str) -> Dict[str, str]:
    """Build per-engine subprocess env overrides from runner policy.

    Priority:
      1) shell environment (always wins)
      2) runner.env.by_engine[engine_name]
      3) runner.env.default
      4) runner.env.auto_detect_exec[engine_name] (only if unset)
      5) runner.env.auto_detect_sha256[engine_name] (only if unset)
    """
    out: Dict[str, str] = {}

    for k, v in _coerce_env_map(RUNNER_ENV_DEFAULT).items():
        if os.environ.get(k):
            continue
        out[k] = v

    per_engine = _coerce_env_map(RUNNER_ENV_BY_ENGINE.get(engine_name, {}))
    for k, v in per_engine.items():
        if os.environ.get(k):
            continue
        out[k] = v

    auto_detect = _coerce_env_map(RUNNER_ENV_AUTO_DETECT_EXEC.get(engine_name, {}))
    for env_key, exe_name in auto_detect.items():
        if os.environ.get(env_key) or out.get(env_key):
            continue
        resolved = shutil.which(exe_name)
        if resolved:
            out[env_key] = resolved

    auto_detect_sha = _coerce_str_map(RUNNER_ENV_AUTO_DETECT_SHA256.get(engine_name, {}))
    for sha_env_key, path_env_key in auto_detect_sha.items():
        if os.environ.get(sha_env_key) or out.get(sha_env_key):
            continue

        source_path_val = os.environ.get(path_env_key) or out.get(path_env_key)
        resolved_path = _resolve_executable_path(source_path_val) if source_path_val else None
        if not resolved_path:
            continue
        try:
            out[sha_env_key] = _sha256_file(resolved_path)
        except Exception:
            continue

    return out


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, s: str) -> None:
    path.write_text(s, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_maybe(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def build_artifact_signature(*, argv: List[str], inputs: List[Path]) -> str:
    parts: Dict[str, Any] = {"argv": argv, "inputs": []}
    for p in inputs:
        try:
            st = p.stat()
            parts["inputs"].append({"path": str(p), "mtime_ns": st.st_mtime_ns, "size": st.st_size})
        except FileNotFoundError:
            parts["inputs"].append({"path": str(p), "missing": True})
    return _sha256_text(json.dumps(parts, sort_keys=True, ensure_ascii=False))


def load_pack(pack_path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid pack file (expected mapping): {pack_path}")
    return data


# =============================================================================
# Pack schema (soft-validated; fail-closed on missing essentials)
# =============================================================================

@dataclass(frozen=True)
class PackDefaults:
    chat_top_k: int = int(PACK_DEFAULTS_FALLBACK.get("chat_top_k", 12))
    max_tokens: int = int(PACK_DEFAULTS_FALLBACK.get("max_tokens", 1024))
    temperature: float = float(PACK_DEFAULTS_FALLBACK.get("temperature", 0.0))


@dataclass(frozen=True)
class PackValidation:
    required_verdicts: Tuple[str, ...] = tuple(
        str(x) for x in PACK_VALIDATION_FALLBACK.get("required_verdicts", ["TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"])
    )
    citation_format: str = str(PACK_VALIDATION_FALLBACK.get("citation_format", "path:line(-line)"))
    fail_on_missing_citations: bool = bool(PACK_VALIDATION_FALLBACK.get("fail_on_missing_citations", True))
    enforce_citations_from_evidence: bool = bool(PACK_VALIDATION_FALLBACK.get("enforce_citations_from_evidence", False))
    enforce_no_new_paths: bool = bool(PACK_VALIDATION_FALLBACK.get("enforce_no_new_paths", False))
    enforce_paths_must_be_cited: bool = bool(PACK_VALIDATION_FALLBACK.get("enforce_paths_must_be_cited", False))
    minimum_questions: int = int(PACK_VALIDATION_FALLBACK.get("minimum_questions", 0))
    apply_question_validators: bool = bool(PACK_VALIDATION_FALLBACK.get("apply_question_validators", False))


@dataclass(frozen=True)
class Question:
    id: str
    title: str
    category: str
    question: str
    top_k: int | None = None
    preflight: List[Dict[str, Any]] | None = None
    chat: Dict[str, Any] | None = None
    expected_verdict: str | None = None
    answer_mode: str = (QUESTION_ANSWER_MODES[0] if QUESTION_ANSWER_MODES else "llm")
    advice_mode: str = (QUESTION_ADVICE_MODES[0] if QUESTION_ADVICE_MODES else "none")
    advice_prompt: str | None = None


@dataclass(frozen=True)
class Pack:
    version: str
    pack_type: str
    engine: str
    response_schema: str
    defaults: PackDefaults
    questions: List[Question]
    validation: PackValidation
    runner: Dict[str, Any]


def _parse_pack(obj: Dict[str, Any]) -> Pack:
    for k in ("version", "pack_type", "engine", "response_schema", "defaults", "questions"):
        if k not in obj:
            raise SystemExit(f"pack.yaml missing required key: {k}")

    defaults_obj = obj.get("defaults") or {}
    defaults = PackDefaults(
        chat_top_k=int(defaults_obj.get("chat_top_k", PACK_DEFAULTS_FALLBACK.get("chat_top_k", 12))),
        max_tokens=int(defaults_obj.get("max_tokens", PACK_DEFAULTS_FALLBACK.get("max_tokens", 1024))),
        temperature=float(defaults_obj.get("temperature", PACK_DEFAULTS_FALLBACK.get("temperature", 0.0))),
    )

    val_obj = obj.get("validation") or {}
    validation = PackValidation(
        required_verdicts=tuple(
            val_obj.get("required_verdicts")
            or PACK_VALIDATION_FALLBACK.get("required_verdicts")
            or ["TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"]
        ),
        citation_format=str(val_obj.get("citation_format") or PACK_VALIDATION_FALLBACK.get("citation_format") or "path:line(-line)"),
        fail_on_missing_citations=bool(
            val_obj.get("fail_on_missing_citations", PACK_VALIDATION_FALLBACK.get("fail_on_missing_citations", True))
        ),
        enforce_citations_from_evidence=bool(
            val_obj.get("enforce_citations_from_evidence", PACK_VALIDATION_FALLBACK.get("enforce_citations_from_evidence", False))
        ),
        enforce_no_new_paths=bool(val_obj.get("enforce_no_new_paths", PACK_VALIDATION_FALLBACK.get("enforce_no_new_paths", False))),
        enforce_paths_must_be_cited=bool(
            val_obj.get("enforce_paths_must_be_cited", PACK_VALIDATION_FALLBACK.get("enforce_paths_must_be_cited", False))
        ),
        minimum_questions=int(val_obj.get("minimum_questions") or PACK_VALIDATION_FALLBACK.get("minimum_questions", 0)),
        apply_question_validators=bool(
            val_obj.get("apply_question_validators", PACK_VALIDATION_FALLBACK.get("apply_question_validators", False))
        ),
    )

    qs_obj = obj.get("questions")
    if not isinstance(qs_obj, list) or not qs_obj:
        raise SystemExit("pack.yaml has no questions")

    questions: List[Question] = []
    for q in qs_obj:
        if not isinstance(q, dict):
            raise SystemExit(f"Invalid question entry: {q}")
        for k in ("id", "title", "category", "question"):
            if k not in q or not q.get(k):
                raise SystemExit(f"Invalid question entry (missing {k}): {q}")

        answer_mode_default = QUESTION_ANSWER_MODES[0] if QUESTION_ANSWER_MODES else "llm"
        answer_mode = str(q.get("answer_mode") or answer_mode_default).strip().lower()
        if answer_mode not in QUESTION_ANSWER_MODES:
            raise SystemExit(
                f"Invalid question.answer_mode for {q.get('id')}: {answer_mode!r} "
                f"(expected one of {list(QUESTION_ANSWER_MODES)})"
            )
        advice_mode_default = QUESTION_ADVICE_MODES[0] if QUESTION_ADVICE_MODES else "none"
        advice_mode = str(q.get("advice_mode") or advice_mode_default).strip().lower()
        if advice_mode not in QUESTION_ADVICE_MODES:
            raise SystemExit(
                f"Invalid question.advice_mode for {q.get('id')}: {advice_mode!r} "
                f"(expected one of {list(QUESTION_ADVICE_MODES)})"
            )

        questions.append(Question(
            id=str(q["id"]),
            title=str(q["title"]),
            category=str(q["category"]),
            question=str(q["question"]),
            top_k=int(q["top_k"]) if q.get("top_k") is not None else None,
            preflight=q.get("preflight") if isinstance(q.get("preflight"), list) else None,
            chat=q.get("chat") if isinstance(q.get("chat"), dict) else None,
            expected_verdict=str(q["expected_verdict"]) if q.get("expected_verdict") else None,
            answer_mode=answer_mode,
            advice_mode=advice_mode,
            advice_prompt=str(q["advice_prompt"]) if q.get("advice_prompt") else None,
        ))

    runner = obj.get("runner") if isinstance(obj.get("runner"), dict) else {}
    return Pack(
        version=str(obj["version"]),
        pack_type=str(obj["pack_type"]),
        engine=str(obj["engine"]),
        response_schema=str(obj["response_schema"]),
        defaults=defaults,
        questions=questions,
        validation=validation,
        runner=runner,
    )


# =============================================================================
# Engine specs (config-driven)
# =============================================================================

@dataclass(frozen=True)
class EngineSpec:
    name: str
    prefix_uv: List[str]
    prefix_direct: List[str]
    target_dir_flag: str | None = None

    chat_subcommand: str = str(ENGINE_DEFAULTS.get("chat_subcommand", "chat"))
    parquet_flag: str = str(ENGINE_DEFAULTS.get("parquet_flag", "--rsqt"))
    index_flag: str = str(ENGINE_DEFAULTS.get("index_flag", "--index"))
    backend_flag: str = str(ENGINE_DEFAULTS.get("backend_flag", "--backend"))
    top_k_flag: str = str(ENGINE_DEFAULTS.get("top_k_flag", "--top-k"))
    model_flag: str = str(ENGINE_DEFAULTS.get("model_flag", "--model"))
    system_prompt_flag: str = str(ENGINE_DEFAULTS.get("system_prompt_flag", "--system-prompt-file"))
    max_tokens_flag: str = str(ENGINE_DEFAULTS.get("max_tokens_flag", "--max-tokens"))
    temperature_flag: str = str(ENGINE_DEFAULTS.get("temperature_flag", "--temperature"))
    format_flag: str = str(ENGINE_DEFAULTS.get("format_flag", "--format"))
    format_value: str = str(ENGINE_DEFAULTS.get("format_value", "json"))

    prompt_profile_flag: str | None = ENGINE_DEFAULTS.get("prompt_profile_flag", "--prompt-profile")
    top_p_flag: str | None = ENGINE_DEFAULTS.get("top_p_flag", "--top-p")
    num_ctx_flag: str | None = ENGINE_DEFAULTS.get("num_ctx_flag", "--num-ctx")

    preflight_needs_index_cmds: Tuple[str, ...] = tuple(
        str(x) for x in (ENGINE_DEFAULTS.get("preflight_needs_index_cmds", ["rag-search"]) or ["rag-search"])
    )

    @classmethod
    def from_dict(cls, name: str, d: Dict[str, Any]) -> "EngineSpec":
        return cls(
            name=name,
            prefix_uv=list(d.get("prefix_uv") or []),
            prefix_direct=list(d.get("prefix_direct") or []),
            target_dir_flag=d.get("target_dir_flag"),
            chat_subcommand=str(d.get("chat_subcommand", ENGINE_DEFAULTS.get("chat_subcommand", "chat"))),
            parquet_flag=str(d.get("parquet_flag", ENGINE_DEFAULTS.get("parquet_flag", "--rsqt"))),
            index_flag=str(d.get("index_flag", ENGINE_DEFAULTS.get("index_flag", "--index"))),
            backend_flag=str(d.get("backend_flag", ENGINE_DEFAULTS.get("backend_flag", "--backend"))),
            top_k_flag=str(d.get("top_k_flag", ENGINE_DEFAULTS.get("top_k_flag", "--top-k"))),
            model_flag=str(d.get("model_flag", ENGINE_DEFAULTS.get("model_flag", "--model"))),
            system_prompt_flag=str(
                d.get("system_prompt_flag", ENGINE_DEFAULTS.get("system_prompt_flag", "--system-prompt-file"))
            ),
            max_tokens_flag=str(d.get("max_tokens_flag", ENGINE_DEFAULTS.get("max_tokens_flag", "--max-tokens"))),
            temperature_flag=str(d.get("temperature_flag", ENGINE_DEFAULTS.get("temperature_flag", "--temperature"))),
            format_flag=str(d.get("format_flag", ENGINE_DEFAULTS.get("format_flag", "--format"))),
            format_value=str(d.get("format_value", ENGINE_DEFAULTS.get("format_value", "json"))),
            prompt_profile_flag=d.get("prompt_profile_flag", ENGINE_DEFAULTS.get("prompt_profile_flag")),
            top_p_flag=d.get("top_p_flag", ENGINE_DEFAULTS.get("top_p_flag")),
            num_ctx_flag=d.get("num_ctx_flag", ENGINE_DEFAULTS.get("num_ctx_flag")),
            preflight_needs_index_cmds=tuple(
                d.get("preflight_needs_index_cmds") or ENGINE_DEFAULTS.get("preflight_needs_index_cmds") or ["rag-search"]
            ),
        )


def load_engine_specs(path: Path) -> Dict[str, EngineSpec]:
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or "engines" not in obj:
        raise SystemExit(f"Invalid engine specs file: {path} (expected mapping with key 'engines')")
    engines_obj = obj.get("engines")
    if not isinstance(engines_obj, dict) or not engines_obj:
        raise SystemExit(f"Invalid engine specs file: {path} (engines must be a mapping)")
    specs: Dict[str, EngineSpec] = {}
    for name, d in engines_obj.items():
        if isinstance(d, dict):
            specs[name] = EngineSpec.from_dict(name, d)
    return specs


def build_engine_prefix(spec: EngineSpec, *, use_uv: bool, target_dir: Path | None) -> List[str]:
    base = spec.prefix_uv if use_uv else spec.prefix_direct
    if not base:
        raise SystemExit(f"Engine '{spec.name}' has empty command prefix (check engine_specs.yaml)")
    argv = list(base)
    if spec.target_dir_flag and target_dir is not None:
        argv += [spec.target_dir_flag, str(target_dir)]
    return argv


def _materialize_preflight_cmd(cmd: List[str], *, index_path: Path, parquet_path: Path) -> List[str]:
    """Expand path placeholders in preflight cmd tokens.

    Supported placeholders:
      {index}      -> resolved index path
      {parquet}    -> resolved parquet path
      {target_dir} -> parquet parent directory
      {out_dir}    -> alias of target_dir (for backward compatibility)
    """
    target_dir = parquet_path.parent
    repl = {
        "{index}": str(index_path),
        "{parquet}": str(parquet_path),
        "{target_dir}": str(target_dir),
        "{out_dir}": str(target_dir),
    }
    out: List[str] = []
    for raw in cmd:
        tok = str(raw)
        for key, val in repl.items():
            tok = tok.replace(key, val)
        out.append(tok)
    return out


def build_engine_preflight_argv(
    spec: EngineSpec,
    prefix: List[str],
    *,
    cmd: List[str],
    index_path: Path,
    parquet_path: Path,
) -> List[str]:
    """Build the exact preflight argv that will be executed."""
    materialized = _materialize_preflight_cmd(cmd, index_path=index_path, parquet_path=parquet_path)
    argv = prefix + materialized
    if materialized and materialized[0] in spec.preflight_needs_index_cmds:
        argv += [spec.index_flag, str(index_path), spec.parquet_flag, str(parquet_path)]
    return argv


def run_engine_preflight(
    spec: EngineSpec,
    prefix: List[str],
    *,
    cmd: List[str],
    index_path: Path,
    parquet_path: Path,
    env_overrides: Dict[str, str] | None = None,
) -> CmdResult:
    argv = build_engine_preflight_argv(
        spec,
        prefix,
        cmd=cmd,
        index_path=index_path,
        parquet_path=parquet_path,
    )
    _log_event(
        logging.INFO,
        "preflight.exec",
        fn="run_engine_preflight",
        engine=spec.name,
        cmd=_shell_join(argv, max_chars=640),
        index=str(index_path),
        parquet=str(parquet_path),
    )
    t0 = time.perf_counter()
    res = run_cmd(argv, env=env_overrides)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _log_event(
        logging.INFO,
        "preflight.done",
        fn="run_engine_preflight",
        engine=spec.name,
        returncode=res.returncode,
        elapsed_ms=elapsed_ms,
        stdout_chars=len(res.stdout or ""),
        stderr_chars=len(res.stderr or ""),
    )
    stderr_text = (res.stderr or "").strip()
    if stderr_text:
        noise = _stderr_is_noise(stderr_text)
        _log_event(
            logging.DEBUG if noise else logging.WARNING,
            "preflight.stderr",
            fn="run_engine_preflight",
            engine=spec.name,
            returncode=res.returncode,
            noise=noise,
            stderr_preview=_compact_log_text(stderr_text, max_chars=DEFAULT_LOG_FIELD_MAX_CHARS),
        )
    return res


def run_engine_chat(
    spec: EngineSpec,
    prefix: List[str],
    *,
    question: str,
    index_path: Path,
    parquet_path: Path,
    backend: str,
    model: str | None,
    top_k: int,
    prompt_profile: str | None,
    system_prompt_file: Path | None,
    max_tokens: int,
    temperature: float,
    top_p: float = DEFAULT_TOP_P,
    num_ctx: int | None = None,
    env_overrides: Dict[str, str] | None = None,
) -> CmdResult:
    argv: List[str] = prefix + [spec.chat_subcommand, question]
    argv += [spec.index_flag, str(index_path)]
    argv += [spec.parquet_flag, str(parquet_path)]
    argv += [spec.backend_flag, backend]
    argv += [spec.top_k_flag, str(top_k)]

    if spec.prompt_profile_flag and prompt_profile:
        argv += [spec.prompt_profile_flag, prompt_profile]

    if system_prompt_file is not None:
        argv += [spec.system_prompt_flag, str(system_prompt_file)]

    argv += [spec.max_tokens_flag, str(max_tokens)]
    argv += [spec.temperature_flag, str(temperature)]
    argv += [spec.format_flag, spec.format_value]

    if model:
        argv += [spec.model_flag, model]
    if spec.top_p_flag and top_p != DEFAULT_TOP_P:
        argv += [spec.top_p_flag, str(top_p)]
    if spec.num_ctx_flag and num_ctx is not None:
        argv += [spec.num_ctx_flag, str(num_ctx)]
    _log_event(
        logging.INFO,
        "chat.exec",
        fn="run_engine_chat",
        engine=spec.name,
        backend=backend,
        model=model or "(default)",
        top_k=top_k,
        prompt_profile=prompt_profile or "(none)",
        system_prompt_file=str(system_prompt_file) if system_prompt_file else "(none)",
        question_preview=_compact_log_text(question, max_chars=DEFAULT_LOG_PROMPT_MAX_CHARS),
    )
    t0 = time.perf_counter()
    res = run_cmd(argv, env=env_overrides)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _log_event(
        logging.INFO,
        "chat.done",
        fn="run_engine_chat",
        engine=spec.name,
        returncode=res.returncode,
        elapsed_ms=elapsed_ms,
        stdout_chars=len(res.stdout or ""),
        stderr_chars=len(res.stderr or ""),
    )
    stderr_text = (res.stderr or "").strip()
    if stderr_text:
        noise = _stderr_is_noise(stderr_text)
        _log_event(
            logging.DEBUG if noise else logging.WARNING,
            "chat.stderr",
            fn="run_engine_chat",
            engine=spec.name,
            returncode=res.returncode,
            noise=noise,
            stderr_preview=_compact_log_text(stderr_text, max_chars=DEFAULT_LOG_FIELD_MAX_CHARS),
        )
    return res


# =============================================================================
# Validation (pack-driven response schema)
# =============================================================================

def validate_response_schema(answer: str, validation: PackValidation) -> List[str]:
    """Validate response against pack schema (shared implementation)."""
    return shared_validate_response_schema(answer, validation, ISSUE_CAPS)


# =============================================================================
# Citation provenance (fail-closed; optional)
# =============================================================================

_CITATION_TOKEN_RE = re.compile(_CITATION_TOKEN_PATTERN)


def _normalize_citation_token_for_provenance(tok: str) -> str:
    """Normalize a token for provenance comparisons."""
    t = (tok or "").strip()
    if not t:
        return ""
    t = re.sub(r"^\s*[-*]\s+", "", t)
    t = t.strip("`")
    # Allow optional URI-ish prefix emitted by some models:
    #   file:crates/foo.rs:12  -> crates/foo.rs:12
    t = re.sub(r"^\s*file:\s*", "", t, flags=re.IGNORECASE)
    # Some models echo schema docs literally:
    #   path:crates/foo.rs:12  -> crates/foo.rs:12
    t = re.sub(r"^\s*path:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*cite\s*=\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*section:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^\s*artifact:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
    m_anchor = re.match(
        r"^(?P<path>[^:]+)::file anchor\s+(?P<a>\d+):(?P<b>\d+)\s*$",
        t, flags=re.IGNORECASE,
    )
    if m_anchor:
        a = int(m_anchor.group("a"))
        b = int(m_anchor.group("b"))
        lo, hi = (a, b) if a <= b else (b, a)
        t = f"{m_anchor.group('path')}:{lo}-{hi}"
    if (":" not in t) and re.match(r"^R_[A-Z0-9_]+_[A-Za-z0-9_]+\.json$", t):
        t = t + ":1"
    return t.strip()


def _is_numeric_path_marker(path_text: str) -> bool:
    s = str(path_text or "").strip()
    if not s:
        return True
    return bool(re.match(r"^\d+(?:\.\d+)?$", s))


def _is_low_confidence_path_for_audit(path_text: str) -> bool:
    s = str(path_text or "").strip().replace("\\", "/")
    if not s:
        return True
    if _is_numeric_path_marker(s):
        return True
    if s in {".", ".."}:
        return True
    if any(ch.isspace() for ch in s):
        return True
    return False


def _should_skip_low_confidence_missing_path(
    path_text: str,
    *,
    repo_root: Path | None,
    parquet_path_universe: set[str],
) -> bool:
    """Suppress audit-noise path tokens that are unlikely corpus file paths."""
    norm = str(path_text or "").strip().replace("\\", "/")
    if _is_low_confidence_path_for_audit(norm):
        return True

    # Never treat runner inputs / indexes as corpus paths.
    if norm.lower().endswith((".parquet", ".faiss")):
        return True

    # Absolute paths are only meaningful if they are inside repo_root.
    if norm.startswith("/"):
        if repo_root is None:
            return True
        try:
            if not Path(norm).resolve().is_relative_to(repo_root.resolve()):
                return True
        except Exception:
            return True

    # Common toolchain paths that can leak into evidence (for example rust-analyzer).
    if "/.cargo/" in norm or "/.rustup/" in norm:
        return True

    # Single-segment literals inside snippets (for example "foo.json") can be
    # value strings, not corpus paths. Keep only if they are real repo-root files
    # or already part of parquet path universe.
    if "/" not in norm and norm not in parquet_path_universe:
        if repo_root is None:
            return True
        try:
            if not (repo_root / norm).exists():
                return True
        except Exception:
            return True

    # Manifest-local path literals like "src/lib.rs" often appear in TOML values
    # and are not canonical corpus paths unless they resolve under repo root.
    if norm.startswith("src/") and norm not in parquet_path_universe:
        if repo_root is None:
            return True
        try:
            if not (repo_root / norm).exists():
                return True
        except Exception:
            return True

    return False


def _extract_allowed_citation_tokens(evidence_blocks: List[str]) -> set:
    """Extract all citeable path:line(-line) tokens from injected evidence."""
    blob = "\n".join(evidence_blocks or [])
    raw = _CITATION_TOKEN_RE.findall(blob)
    pathline_re = re.compile(_PATHLINE_PATTERN)
    out: set = set()
    for tok in raw:
        nt = _normalize_citation_token_for_provenance(tok)
        if not nt:
            continue
        m = pathline_re.match(nt)
        if not m:
            continue
        p = str(m.groupdict().get("path") or "").strip()
        if _is_low_confidence_path_for_audit(p):
            continue
        out.add(nt)
    return out


def _extract_answer_citation_tokens(answer: str) -> List[str]:
    """Extract citation tokens from the CITATIONS line in the answer."""
    clean = (answer or "").replace("**", "")
    m = re.search(r"^\s*CITATIONS\s*[=:]\s*(.*)$", clean, flags=re.MULTILINE)
    citations_raw = ""
    if not m:
        cit_header = re.search(r"^\s*CITATIONS\s*[=:]?\s*$", clean, flags=re.MULTILINE)
        if cit_header:
            after = clean[cit_header.end():]
            bullet_tokens = _CITATION_TOKEN_RE.findall(after.split("\n\n")[0])
            citations_raw = ", ".join(bullet_tokens)
    else:
        citations_raw = (m.group(1) or "").strip()
    if not citations_raw:
        return []
    raw_tokens = [t.strip() for t in citations_raw.split(",") if t.strip()]
    out: List[str] = []
    for t in raw_tokens:
        nt = _normalize_citation_token_for_provenance(t)
        if nt:
            out.append(nt)
    return out


def _unknown_citation_tokens(tokens: List[str], *, allowed: set) -> List[str]:
    """Return citation tokens not known to evidence, with range-overlap tolerance."""
    if not tokens:
        return []
    if not allowed:
        return list(tokens)

    pathline_re = re.compile(_PATHLINE_PATTERN)
    allowed_by_path: Dict[str, List[Tuple[int, int]]] = {}
    for tok in allowed:
        m = pathline_re.match(tok)
        if not m:
            continue
        p = m.group("path")
        a = int(m.group("a"))
        b = int(m.group("b") or a)
        lo, hi = (a, b) if a <= b else (b, a)
        allowed_by_path.setdefault(p, []).append((lo, hi))

    def _is_known(tok: str) -> bool:
        if tok in allowed:
            return True
        m = pathline_re.match(tok)
        if not m:
            return False
        p = m.group("path")
        a = int(m.group("a"))
        b = int(m.group("b") or a)
        lo, hi = (a, b) if a <= b else (b, a)
        for alo, ahi in allowed_by_path.get(p, []):
            # Overlap test between [lo,hi] and [alo,ahi]
            if not (hi < alo or ahi < lo):
                return True
        # RAQT can emit byte-offset-like ranges while evidence may only expose
        # path:1 anchors. If the path itself is present in evidence, accept it.
        if p in allowed_by_path:
            return True
        return False

    return [t for t in tokens if not _is_known(t)]


def validate_citations_from_evidence(answer: str, *, allowed: set) -> List[str]:
    """Return issues if the answer cites anything not present in injected evidence."""
    if not allowed:
        return ["No citeable tokens extracted from evidence (cannot validate citation provenance)"]
    tokens = _extract_answer_citation_tokens(answer)
    if not tokens:
        return []  # schema validator handles missing CITATIONS line
    unknown = _unknown_citation_tokens(tokens, allowed=allowed)
    if unknown:
        return [f"Unknown citation tokens (not in evidence): {unknown[:int(ISSUE_CAPS.get('unknown_citations', 8))]}"]
    return []


# =============================================================================
# Path gates (Gate A + Gate B)
# =============================================================================

_FILE_PATH_RE = re.compile(_FILE_PATH_PATTERN)


def _strip_schema_header_lines(answer: str) -> str:
    """Remove VERDICT/CITATIONS lines before scanning body for path mentions."""
    lines = (answer or "").splitlines()
    kept: List[str] = []
    for ln in lines:
        if re.match(r"^\s*\*{0,2}VERDICT\*{0,2}\b", ln):
            continue
        if re.match(r"^\s*\*{0,2}CITATIONS\*{0,2}\b", ln):
            continue
        kept.append(ln)
    return "\n".join(kept)


def _extract_allowed_paths_from_evidence(evidence_blocks: List[str]) -> set:
    """Extract a conservative allowed-path set from injected evidence blocks."""
    blob = "\n".join(evidence_blocks or [])
    allowed: set = set()

    # 1) Any cite-like tokens in evidence
    for tok in _CITATION_TOKEN_RE.findall(blob):
        nt = _normalize_citation_token_for_provenance(tok)
        if ":" in nt:
            p = nt.split(":", 1)[0].strip()
            if not _is_low_confidence_path_for_audit(p):
                allowed.add(p)

    # 2) Any CITE=... tokens on evidence headers (including artifacts)
    for m in re.finditer(r"\bCITE\s*=\s*([^\s]+)", blob):
        nt = _normalize_citation_token_for_provenance(m.group(1) or "")
        if ":" in nt:
            p = nt.split(":", 1)[0].strip()
            if not _is_low_confidence_path_for_audit(p):
                allowed.add(p)
        elif nt and _FILE_PATH_RE.search(nt):
            if not _is_low_confidence_path_for_audit(nt):
                allowed.add(nt)

    # 3) Any bare file paths in evidence
    for p_match in _FILE_PATH_RE.findall(blob):
        if p_match and not _is_low_confidence_path_for_audit(p_match):
            allowed.add(p_match)

    return allowed


def validate_path_gates(
    answer: str, evidence_blocks: List[str], validation: PackValidation
) -> List[str]:
    """Gate A (enforce_no_new_paths) + Gate B (enforce_paths_must_be_cited).

    Gate A: Disallow any repo-ish file paths in answer (body OR CITATIONS)
            unless the path appears in injected evidence.
    Gate B: Any file path mentioned in the answer body must have a
            corresponding CITATIONS token for the same path prefix.
    """
    if not (validation.enforce_no_new_paths or validation.enforce_paths_must_be_cited):
        return []

    issues: List[str] = []
    allowed_paths = _extract_allowed_paths_from_evidence(evidence_blocks)

    cited_tokens = _extract_answer_citation_tokens(answer)
    cited_paths = {t.split(":", 1)[0] for t in cited_tokens if ":" in t}

    body = _strip_schema_header_lines(answer)
    mentioned_paths = set(_FILE_PATH_RE.findall(body))

    # Gate A: referenced paths must exist in evidence-derived allowed paths
    if validation.enforce_no_new_paths:
        referenced = mentioned_paths | cited_paths
        if referenced and not allowed_paths:
            issues.append(
                "Gate A (no new paths): evidence contained no extractable "
                "file paths; cannot validate"
            )
        else:
            unknown = sorted(p for p in referenced if p and p not in allowed_paths)
            if unknown:
                issues.append(
                    f"Gate A (no new paths): paths not present in evidence: "
                    f"{unknown[:int(ISSUE_CAPS.get('unknown_paths', 10))]}"
                )

    # Gate B: any body-mentioned path must be covered by CITATIONS
    if validation.enforce_paths_must_be_cited:
        uncited = sorted(p for p in mentioned_paths if p and p not in cited_paths)
        if uncited:
            issues.append(
                f"Gate B (paths must be cited): paths mentioned without "
                f"matching CITATIONS token: {uncited[:int(ISSUE_CAPS.get('uncited_paths', 10))]}"
            )

    return issues


def _extract_evidence_citation_tokens_by_path(evidence_blocks: List[str]) -> Dict[str, List[str]]:
    """Map file paths -> citation tokens present in injected evidence blocks.

    Used for deterministic Gate B repair (body path mention without matching CITATIONS token).
    """
    blob = "\n".join(evidence_blocks or [])
    by_path: Dict[str, List[str]] = {}

    def _add_token(tok: str) -> None:
        nt = _normalize_citation_token_for_provenance(tok or "")
        if ":" not in nt:
            return
        p = nt.split(":", 1)[0].strip()
        if not p or _is_low_confidence_path_for_audit(p):
            return
        by_path.setdefault(p, [])
        if nt not in by_path[p]:
            by_path[p].append(nt)

    # 1) Any cite-like tokens present in evidence text.
    for tok in _CITATION_TOKEN_RE.findall(blob):
        _add_token(tok)

    # 2) Any explicit CITE=... tokens on evidence headers/artifacts.
    for m in re.finditer(r"\bCITE\s*=\s*([^\s]+)", blob):
        _add_token(m.group(1) or "")

    return by_path


def _auto_complete_citations_for_path_gates(
    answer: str, evidence_blocks: List[str], validation: PackValidation
) -> Tuple[str, List[str]]:
    """Deterministically add missing CITATIONS tokens for body-mentioned paths.

    Only citations already present in injected evidence can be added.
    Returns (possibly_updated_answer, added_tokens).
    """
    if not validation.enforce_paths_must_be_cited:
        return (answer or "", [])

    ans = str(answer or "")
    if not ans or not evidence_blocks:
        return (ans, [])

    body = _strip_schema_header_lines(ans)
    mentioned_paths = sorted(set(_FILE_PATH_RE.findall(body)))
    if not mentioned_paths:
        return (ans, [])

    cited_tokens = _extract_answer_citation_tokens(ans)
    cited_paths = {t.split(":", 1)[0] for t in cited_tokens if ":" in t}
    missing_paths = [p for p in mentioned_paths if p and p not in cited_paths]
    if not missing_paths:
        return (ans, [])

    by_path = _extract_evidence_citation_tokens_by_path(evidence_blocks)
    added: List[str] = []
    for p in missing_paths:
        toks = by_path.get(p) or []
        if not toks:
            continue
        tok = toks[0]
        if tok not in cited_tokens and tok not in added:
            added.append(tok)

    if not added:
        return (ans, [])

    lines = ans.splitlines()
    out: List[str] = []
    updated = False
    for ln in lines:
        if re.match(r"^\s*\*{0,2}CITATIONS\*{0,2}\s*=", ln):
            prefix, rest = ln.split("=", 1)
            raw = rest.strip()
            delim = ", " if ", " in raw else ","
            existing = [t.strip() for t in raw.split(",") if t.strip()]
            merged = existing + [t for t in added if t not in existing]
            out.append(prefix + "=" + delim.join(merged))
            updated = True
        else:
            out.append(ln)

    if not updated:
        new_line = "CITATIONS=" + ", ".join([t for t in (cited_tokens + added) if t])
        inserted = False
        out2: List[str] = []
        for ln in out:
            out2.append(ln)
            if (not inserted) and re.match(r"^\s*\*{0,2}VERDICT\*{0,2}\s*=", ln):
                out2.append(new_line)
                inserted = True
        if not inserted:
            out2 = [new_line] + out
        out = out2

    return ("\n".join(out).strip(), added)


# =============================================================================
# Evidence delivery audit (prompt-input observability)
# =============================================================================

def _canonicalize_repo_path(path_text: str, *, repo_root: Path | None = None) -> str:
    s = str(path_text or "").strip().strip("\"'`")
    if not s:
        return ""
    s = s.replace("\\", "/")
    s = re.sub(r"^\s*(?:file|path):\s*", "", s, flags=re.IGNORECASE)
    m = re.match(_PATHLINE_PATTERN, s)
    if m:
        s = m.group("path")
    if "::" in s:
        s = s.split("::", 1)[0].strip()
    while s.startswith("./"):
        s = s[2:]
    # If evidence includes a redundant "<repo_root_name>/" prefix
    # (for example "rust/crates/..."), strip it when safe so paths align
    # with parquet's repo-root-relative universe.
    if repo_root is not None:
        try:
            root_name = str(repo_root.name or "").strip().strip("/")
            if root_name and (s == root_name or s.startswith(root_name + "/")):
                stripped = s[len(root_name):].lstrip("/")
                if stripped:
                    raw_exists = (repo_root / s).exists()
                    stripped_exists = (repo_root / stripped).exists()
                    if stripped_exists and not raw_exists:
                        s = stripped
        except Exception:
            pass
    if not s:
        return ""
    try:
        p = Path(s)
        if p.is_absolute() and repo_root is not None:
            root = repo_root.resolve()
            try:
                s = str(p.resolve().relative_to(root))
            except Exception:
                s = str(p)
    except Exception:
        pass
    return s.replace("\\", "/").strip()


def _iter_scalar_strings(value: Any):
    if value is None:
        return
    if isinstance(value, str):
        txt = value.strip()
        if txt:
            yield txt
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_scalar_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _iter_scalar_strings(v)
        return
    if isinstance(value, (int, float, bool)):
        return
    txt = str(value).strip()
    if txt:
        yield txt


def _extract_path_candidates_from_text(value: str) -> List[str]:
    txt = str(value or "").strip()
    if not txt:
        return []
    s = txt.replace("\\", "/")
    s = re.sub(r"^\s*(?:file|path):\s*", "", s, flags=re.IGNORECASE)

    out: List[str] = []
    m = re.match(_PATHLINE_PATTERN, s)
    if m:
        out.append(m.group("path"))

    if "::" in s:
        out.append(s.split("::", 1)[0].strip())

    out.extend(_FILE_PATH_RE.findall(s))
    if not out and _looks_like_repo_path_text(s):
        m_line = re.match(r"^(.*?):\d+(?:-\d+)?$", s)
        if m_line:
            out.append(m_line.group(1))
        else:
            out.append(s)
    return _dedupe_strs([str(x) for x in out if str(x).strip()])


def _is_runner_generated_artifact_path(path_text: str) -> bool:
    p = str(path_text or "").strip()
    if not p:
        return False
    base = Path(p).name
    if base != p:
        return False
    if not base.endswith(".json"):
        return False
    # qid_preflight.json / qid_chat.json / qid_advice.json style artifacts
    if re.match(r"^[A-Z][A-Z0-9_]*_[A-Za-z0-9_.-]+\.json$", base):
        return True
    if base.endswith(("_preflight.json", "_chat.json", "_advice.json")):
        return True
    return False


def _collect_paths_from_any(
    value: Any,
    *,
    out: set[str],
    repo_root: Path | None,
    cap: int,
) -> bool:
    for txt in _iter_scalar_strings(value):
        for cand in _extract_path_candidates_from_text(txt):
            norm = _canonicalize_repo_path(cand, repo_root=repo_root)
            if not norm:
                continue
            if _is_runner_generated_artifact_path(norm):
                continue
            out.add(norm)
            if len(out) >= cap:
                return True
    return False


def _discover_parquet_path_universe(
    *,
    parquet_path: Path,
    repo_root: Path | None,
) -> Tuple[set[str], Dict[str, Any]]:
    info: Dict[str, Any] = {
        "enabled": bool(EVIDENCE_AUDIT_ENABLED_DEFAULT),
        "parquet": str(parquet_path),
        "source": "(none)",
        "candidate_columns": [],
        "selected_columns": [],
        "errors": [],
        "truncated": False,
        "path_universe_cap": int(EVIDENCE_AUDIT_PARQUET_PATH_UNIVERSE_CAP),
    }
    universe: set[str] = set()
    errors: List[str] = []

    parquet_cols = [str(c) for c in (_RUNTIME_PARQUET_COLUMNS or []) if str(c).strip()]
    preferred = [k for k in _effective_path_keys() if k in parquet_cols]
    fallback = [c for c in parquet_cols if _is_path_key_candidate(c)]
    candidate_columns = _dedupe_strs(preferred + fallback)
    info["candidate_columns"] = candidate_columns
    if not candidate_columns:
        errors.append("no candidate path columns discovered from parquet schema and runtime keys")
        info["errors"] = errors
        return universe, info

    cap = int(EVIDENCE_AUDIT_PARQUET_PATH_UNIVERSE_CAP)
    batch_size = int(EVIDENCE_AUDIT_PARQUET_SCAN_BATCH_SIZE)

    try:
        import pyarrow.parquet as pq  # type: ignore

        pf = pq.ParquetFile(str(parquet_path))
        schema_names = [str(n) for n in list(pf.schema.names or [])]
        selected_columns = [c for c in candidate_columns if c in schema_names]
        info["selected_columns"] = selected_columns
        if selected_columns:
            stop = False
            for batch in pf.iter_batches(columns=selected_columns, batch_size=batch_size):
                for col_name in selected_columns:
                    col_idx = batch.schema.get_field_index(col_name)
                    if col_idx < 0:
                        continue
                    values = batch.column(col_idx).to_pylist()
                    if _collect_paths_from_any(values, out=universe, repo_root=repo_root, cap=cap):
                        stop = True
                        break
                if stop:
                    info["truncated"] = True
                    break
            info["source"] = "pyarrow.parquet.iter_batches"
    except Exception as e:
        errors.append(f"pyarrow parquet path scan failed: {e}")

    if not universe and candidate_columns:
        try:
            import polars as pl  # type: ignore

            selected_columns = [c for c in candidate_columns if c in parquet_cols]
            info["selected_columns"] = selected_columns
            if selected_columns:
                df = pl.scan_parquet(str(parquet_path)).select([pl.col(c) for c in selected_columns]).collect(streaming=True)
                for col_name in selected_columns:
                    series = df.get_column(col_name)
                    if _collect_paths_from_any(series.to_list(), out=universe, repo_root=repo_root, cap=cap):
                        info["truncated"] = True
                        break
                if universe:
                    info["source"] = "polars.scan_parquet.collect(streaming=True)"
        except Exception as e:
            errors.append(f"polars parquet path scan failed: {e}")

    if not universe and not info.get("source"):
        info["source"] = "(none)"
    if errors:
        info["errors"] = errors
    return universe, info


def _evidence_audit_artifact_name(qid: str) -> str:
    suffix = str(EVIDENCE_AUDIT_FILENAME_SUFFIX or "_evidence_delivery_audit.json").strip()
    if not suffix:
        suffix = "_evidence_delivery_audit.json"
    if "{qid}" in suffix:
        return suffix.replace("{qid}", qid)
    if suffix.startswith("/"):
        suffix = suffix.lstrip("/")
    if suffix.startswith("_"):
        return f"{qid}{suffix}"
    return f"{qid}_{suffix}"


def _build_question_evidence_audit(
    *,
    qid: str,
    title: str,
    question_text: str,
    answer_mode: str,
    advice_mode: str,
    quote_bypass_mode: str,
    evidence_blocks: List[str],
    preflight_steps: List[Dict[str, Any]],
    parquet_path_universe: set[str],
    parquet_path_meta: Dict[str, Any],
    repo_root: Path | None,
) -> Dict[str, Any]:
    cite_tokens = sorted(_extract_allowed_citation_tokens(evidence_blocks))
    raw_paths = sorted(_extract_allowed_paths_from_evidence(evidence_blocks))
    injected_paths: set[str] = set()
    ignored_artifacts: set[str] = set()
    for p in raw_paths:
        norm = _canonicalize_repo_path(p, repo_root=repo_root)
        if not norm:
            continue
        if _is_runner_generated_artifact_path(norm):
            ignored_artifacts.add(norm)
            continue
        if _should_skip_low_confidence_missing_path(
            norm,
            repo_root=repo_root,
            parquet_path_universe=parquet_path_universe,
        ):
            continue
        injected_paths.add(norm)

    matched_paths = sorted(p for p in injected_paths if p in parquet_path_universe)
    missing_paths = sorted(p for p in injected_paths if p not in parquet_path_universe)
    sample_n = int(EVIDENCE_AUDIT_SAMPLE_ITEMS)
    step_names = [str(s.get("name")) for s in preflight_steps if isinstance(s, dict) and s.get("name")]

    return {
        "qid": qid,
        "title": title,
        "question_sha256": _sha256_text(question_text or ""),
        "question_preview": _compact_log_text(question_text, max_chars=DEFAULT_LOG_QUESTION_MAX_CHARS),
        "answer_mode": answer_mode,
        "advice_mode": advice_mode,
        "quote_bypass_mode": quote_bypass_mode,
        "preflight_steps": step_names,
        "preflight_steps_count": len(step_names),
        "evidence_blocks_count": len(evidence_blocks),
        "evidence_citation_tokens_count": len(cite_tokens),
        "evidence_citation_tokens_sample": cite_tokens[:sample_n],
        "evidence_paths_count": len(injected_paths),
        "evidence_paths_sample": sorted(injected_paths)[:sample_n],
        "ignored_generated_artifact_paths": sorted(ignored_artifacts)[:sample_n],
        "parquet_path_universe_count": len(parquet_path_universe),
        "parquet_path_universe_source": parquet_path_meta.get("source"),
        "parquet_path_universe_columns": parquet_path_meta.get("selected_columns", []),
        "paths_matched_to_parquet_count": len(matched_paths),
        "paths_matched_to_parquet_sample": matched_paths[:sample_n],
        "paths_missing_from_parquet_count": len(missing_paths),
        "paths_missing_from_parquet_sample": missing_paths[:sample_n],
        "llm_dispatches": [],
    }


def _append_llm_dispatch_to_audit(
    *,
    audit: Dict[str, Any],
    phase: str,
    prompt_mode: str,
    prompt_text: str,
    prompt_file: Path | None,
    backend: str,
    model: str,
    top_k: int,
    prompt_profile: str | None,
) -> Dict[str, Any]:
    dispatches = audit.setdefault("llm_dispatches", [])
    if not isinstance(dispatches, list):
        dispatches = []
        audit["llm_dispatches"] = dispatches
    sample_n = int(EVIDENCE_AUDIT_SAMPLE_ITEMS)
    rec = {
        "attempt": len(dispatches) + 1,
        "phase": str(phase),
        "prompt_mode": str(prompt_mode),
        "backend": str(backend),
        "model": str(model or "(default)"),
        "top_k": int(top_k),
        "prompt_profile": str(prompt_profile or "(none)"),
        "system_prompt_file": str(prompt_file) if prompt_file else "(none)",
        "prompt_chars": len(prompt_text or ""),
        "prompt_sha256": _sha256_text(prompt_text or ""),
        "prompt_preview": _compact_log_text(prompt_text or "", max_chars=DEFAULT_LOG_PROMPT_MAX_CHARS),
        "prompt_cite_markers": len(re.findall(r"(?mi)^\s*CITE\s*=", prompt_text or "")),
        "evidence_paths_sample": (audit.get("evidence_paths_sample") or [])[:sample_n],
    }
    dispatches.append(rec)
    return rec


def _write_question_evidence_audit(out_dir: Path, qid: str, audit: Dict[str, Any]) -> Path:
    path = out_dir / _evidence_audit_artifact_name(qid)
    write_json(path, audit)
    return path


# =============================================================================
# Mission advice quality gates
# =============================================================================

def _is_mission_pack_type(pack_type: str) -> bool:
    if not ADVICE_GATE_ENABLED:
        return False
    try:
        return bool(ADVICE_MISSION_PACK_TYPE_RE.search(str(pack_type or "")))
    except Exception:
        return "mission" in str(pack_type or "").lower()


def _tokenize_words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]+", str(text or ""))


def _is_placeholder_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    norm = raw.upper()
    placeholders = {"NONE", "N/A", "NA", "UNKNOWN", "INSUFFICIENT", "...", "TBD", "MISSING"}
    return norm in placeholders


def _looks_generic_or_praise(text: str) -> bool:
    raw = str(text or "").strip()
    if _is_placeholder_text(raw):
        return True
    low = raw.lower()
    if ADVICE_PRAISE_PHRASES and any(p in low for p in ADVICE_PRAISE_PHRASES):
        return True
    if ADVICE_GENERIC_ISSUE_PHRASES and any(p in low for p in ADVICE_GENERIC_ISSUE_PHRASES):
        return True
    if len(_tokenize_words(raw)) < max(1, ADVICE_MIN_ISSUE_WORDS):
        return True
    if ADVICE_IMPERATIVE_VERBS:
        if not any(re.search(rf"\b{re.escape(v)}\b", low) for v in ADVICE_IMPERATIVE_VERBS):
            return True
    return False


def _extract_numbered_issue_blocks(advice_text: str) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for ln in str(advice_text or "").splitlines():
        m = re.match(r"^\s*([A-Z_]+)_(\d+)\s*=\s*(.*?)\s*$", ln)
        if not m:
            continue
        key = m.group(1).strip().upper()
        idx = int(m.group(2))
        val = (m.group(3) or "").strip()
        out.setdefault(idx, {})[key] = val
    return out


def _parse_citations_value(raw: str) -> List[str]:
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    out: List[str] = []
    for p in parts:
        nt = _normalize_citation_token_for_provenance(p)
        if nt:
            out.append(nt)
    return out


def _validate_advice_quality(
    *,
    advice_text: str,
    evidence_blocks: List[str],
) -> List[str]:
    issues: List[str] = []
    text = str(advice_text or "").strip()
    if not text:
        return ["Advice output is empty"]

    issue_blocks = _extract_numbered_issue_blocks(text)
    if not issue_blocks:
        return ["Advice output must include numbered ISSUE_n fields"]

    allowed = _extract_allowed_citation_tokens(evidence_blocks)
    pathline_re = re.compile(_PATHLINE_PATTERN)
    concrete_issue_count = 0
    generic_or_praise_issue_count = 0

    for idx in sorted(issue_blocks.keys()):
        block = issue_blocks[idx]
        issue_text = block.get("ISSUE", "")
        if _looks_generic_or_praise(issue_text):
            generic_or_praise_issue_count += 1
            issues.append(f"ISSUE_{idx} is generic/praise-only or non-actionable")

        missing = [fld for fld in ADVICE_REQUIRED_FIELDS if _is_placeholder_text(block.get(fld, ""))]
        if missing:
            issues.append(f"ISSUE_{idx} missing required fields: {missing}")
            continue

        citation_tokens = _parse_citations_value(block.get("CITATIONS", ""))
        if not citation_tokens:
            issues.append(f"ISSUE_{idx} CITATIONS is empty or unparsable")
            continue

        bad_tokens = [t for t in citation_tokens if not pathline_re.match(t)]
        if bad_tokens:
            issues.append(
                f"ISSUE_{idx} CITATIONS contains invalid tokens (expected path:line(-line)): {bad_tokens[:int(ISSUE_CAPS.get('invalid_citations', 6))]}"
            )
            continue

        if not allowed:
            issues.append("Advice provenance check failed: no citeable evidence tokens extracted for this question")
            continue
        unknown = _unknown_citation_tokens(citation_tokens, allowed=allowed)
        if unknown:
            issues.append(
                f"ISSUE_{idx} CITATIONS not backed by evidence: {unknown[:int(ISSUE_CAPS.get('unknown_citations', 8))]}"
            )
            continue

        concrete_issue_count += 1

    if generic_or_praise_issue_count and generic_or_praise_issue_count == len(issue_blocks):
        issues.append("Advice is praise-only or generic across all issues")

    # Require minimum issue count only when citeable evidence tokens exist.
    if allowed and concrete_issue_count < max(1, ADVICE_MIN_CONCRETE_ISSUES):
        issues.append(
            f"Advice must provide at least {max(1, ADVICE_MIN_CONCRETE_ISSUES)} concrete issues when citeable evidence exists (found {concrete_issue_count})"
        )

    return issues


# =============================================================================
# Reports + manifest
# =============================================================================

def _get_git_info(repo_root: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "root": str(repo_root.resolve()),
        "commit_sha": None,
        "commit_short": None,
        "branch": None,
        "dirty": None,
    }
    try:
        cmd = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_root)
        if cmd.returncode == 0:
            result["commit_sha"] = cmd.stdout.strip()
            result["commit_short"] = cmd.stdout.strip()[: int(ISSUE_CAPS.get("git_short_sha", 7))]
        cmd = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        if cmd.returncode == 0:
            result["branch"] = cmd.stdout.strip()
        cmd = run_cmd(["git", "status", "--porcelain"], cwd=repo_root)
        if cmd.returncode == 0:
            result["dirty"] = len(cmd.stdout.strip()) > 0
    except Exception:
        pass
    return result


def _find_repo_root(start: Path) -> Path:
    repo_root = start
    while repo_root != repo_root.parent:
        if (repo_root / ".git").exists():
            return repo_root
        repo_root = repo_root.parent
    return start


def _resolve_existing_path(
    raw_value: str,
    *,
    script_dir: Path,
    aliases: Dict[str, str] | None = None,
    label: str = "path",
) -> Path:
    raw = Path(str(raw_value)).expanduser()
    repo_root = _find_repo_root(script_dir)

    candidates: List[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([
            Path.cwd() / raw,
            script_dir / raw,
            repo_root / raw,
        ])

    alias_val: str | None = None
    if aliases:
        alias_val = aliases.get(str(raw)) or aliases.get(raw.name)
        if alias_val:
            alias_path = Path(alias_val).expanduser()
            if alias_path.is_absolute():
                candidates.append(alias_path)
            else:
                if raw.parent != Path("."):
                    candidates.extend([
                        Path.cwd() / raw.parent / alias_path.name,
                        script_dir / raw.parent / alias_path.name,
                        repo_root / raw.parent / alias_path.name,
                    ])
                candidates.extend([
                    Path.cwd() / alias_path,
                    script_dir / alias_path,
                    repo_root / alias_path,
                ])

    seen: set = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.exists():
            try:
                return cand.resolve()
            except Exception:
                return cand

    if alias_val:
        raise SystemExit(
            f"Missing {label}: {raw}. Legacy alias '{alias_val}' was checked but no file was found."
        )
    raise SystemExit(f"Missing {label}: {raw}")


def _resolve_optional_existing_path(
    raw_value: str | None,
    *,
    script_dir: Path,
    aliases: Dict[str, str] | None = None,
) -> str | None:
    if not raw_value:
        return None
    raw = Path(str(raw_value)).expanduser()
    if raw.is_absolute() and raw.exists():
        return str(raw)

    repo_root = _find_repo_root(script_dir)
    candidates: List[Path] = []
    if not raw.is_absolute():
        candidates.extend([Path.cwd() / raw, script_dir / raw, repo_root / raw])

    if aliases:
        alias_val = aliases.get(str(raw)) or aliases.get(raw.name)
        if alias_val:
            alias_path = Path(alias_val).expanduser()
            if alias_path.is_absolute():
                candidates.append(alias_path)
            else:
                candidates.extend([Path.cwd() / alias_path, script_dir / alias_path, repo_root / alias_path])

    seen: set = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.exists():
            try:
                return str(cand.resolve())
            except Exception:
                return str(cand)

    # Keep behavior fail-open for optional prompt args.
    return str(raw if raw.is_absolute() else (script_dir / raw))


def _resolve_out_dir(raw_value: str, *, default_base: Path) -> Path:
    p = Path(str(raw_value)).expanduser()
    if p.is_absolute():
        return p
    # Multi-segment relative paths are interpreted from cwd; single leaf names
    # keep backward-compatible behavior under the default output base.
    if len(p.parts) > 1 or str(p).startswith("."):
        return Path.cwd() / p
    return default_base / p


def _slugify_out_dir_component(value: str, *, max_chars: int = 64, fallback: str = "x") -> str:
    s = str(value or "").strip().lower()
    if not s:
        return fallback
    s = s.replace(":", "_")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return fallback
    return s[: max(1, int(max_chars))]


def _best_effort_pack_engine_slug(pack_path: Path) -> str:
    engine = ""
    try:
        if pack_path.exists():
            obj = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                engine = str(obj.get("engine") or "").strip().lower()
    except Exception:
        engine = ""
    if not engine:
        stem = pack_path.stem.lower()
        if "rsqt" in stem:
            engine = "rsqt"
        elif "raqt" in stem:
            engine = "raqt"
    return _slugify_out_dir_component(engine or "engine", max_chars=12, fallback="engine")


def _best_effort_pack_label(pack_path: Path, *, engine_slug: str) -> str:
    name = pack_path.name
    if name.lower().endswith(".yaml"):
        name = name[:-5]
    elif name.lower().endswith(".yml"):
        name = name[:-4]
    s = name.replace(".", "_")
    s = _slugify_out_dir_component(s, max_chars=DEFAULT_OUT_DIR_PACK_MAX_CHARS + 24, fallback="pack")
    for pref in ("pack_", "rust_audit_"):
        if s.startswith(pref):
            s = s[len(pref):]
    if s.startswith("rsqt_"):
        s = s[len("rsqt_") :]
    elif s.startswith("raqt_"):
        s = s[len("raqt_") :]
    elif engine_slug and s.startswith(f"{engine_slug}_"):
        s = s[len(engine_slug) + 1 :]
    if engine_slug and s == engine_slug:
        s = "default"
    return _slugify_out_dir_component(s, max_chars=DEFAULT_OUT_DIR_PACK_MAX_CHARS, fallback="pack")


def _auto_out_dir_leaf(*, model: str, pack_path: Path) -> str:
    ts = datetime.now().strftime(DEFAULT_OUT_DIR_TIMESTAMP_FORMAT)
    engine_slug = _best_effort_pack_engine_slug(pack_path)
    pack_label = _best_effort_pack_label(pack_path, engine_slug=engine_slug)

    parts = [ts]
    if DEFAULT_OUT_DIR_INCLUDE_MODEL:
        model_label = _slugify_out_dir_component(model, max_chars=DEFAULT_OUT_DIR_MODEL_MAX_CHARS, fallback="model")
        if model_label.endswith("_latest"):
            model_label = model_label[: -len("_latest")] or model_label
        parts.append(model_label)
    parts.append(engine_slug)
    parts.append(pack_label)
    return "_".join([p for p in parts if p])


def generate_run_manifest(
    *,
    out_dir: Path,
    pack_path: Path,
    parquet_path: Path,
    index_path: Path,
    run_id: str,
    pack: Pack,
    score_ok: int,
    total_questions: int,
    report_path: Path,
    extra_outputs: Dict[str, Any] | None = None,
) -> Path:
    now = datetime.now(timezone.utc)
    repo_root = _find_repo_root(pack_path.parent)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": now.isoformat(),
        "pack": {
            "pack_type": pack.pack_type,
            "engine": pack.engine,
            "version": pack.version,
        },
        "repo": _get_git_info(repo_root),
        "tools": {
            "runner_version": RUNNER_VERSION,
        },
        "inputs": {
            "pack": {"path": str(pack_path.resolve()), "sha256": _sha256_file(pack_path)},
            "parquet": {"path": str(parquet_path.resolve()), "sha256": _sha256_file(parquet_path)},
            "index": {"path": str(index_path.resolve()), "sha256": _sha256_file(index_path)},
        },
        "outputs": {
            "score_ok": score_ok,
            "total_questions": total_questions,
            "ok_percentage": round(100 * score_ok / total_questions, 1) if total_questions else 0,
            "output_dir": str(out_dir.resolve()),
            "report": str(report_path.name),
        },
    }
    if extra_outputs:
        manifest["outputs"].update(extra_outputs)
    path = out_dir / MANIFEST_FILENAME
    write_text(path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return path


def _parse_report_ok_count(
    report_path: Path,
    *,
    include_advice_issues: bool = False,
) -> Tuple[int, int, int]:
    """Return (ok_count, total_questions, issue_count) from REPORT.md.

    Score semantics:
      - Always count answer-level validator issues.
      - Count advice-level validator issues only when include_advice_issues=True
        (for mission packs with advice gate semantics).
    """
    if not report_path.exists():
        return (0, 0, 0)

    content = report_path.read_text(encoding="utf-8")
    q_matches = list(re.finditer(r"^## ([A-Z0-9_]+):", content, re.MULTILINE))
    question_count = len(q_matches)
    if question_count == 0:
        return (0, 0, 0)

    issue_qids: set[str] = set()
    for i, m in enumerate(q_matches):
        qid = m.group(1)
        start = m.end()
        end = q_matches[i + 1].start() if (i + 1) < len(q_matches) else len(content)
        block = content[start:end]

        has_answer_issues = ("**Validator issues:**" in block) or ("**Answer validator issues:**" in block)
        has_advice_issues = "**Advice validator issues:**" in block
        if has_answer_issues or (include_advice_issues and has_advice_issues):
            issue_qids.add(qid)

    issue_count = len(issue_qids)
    ok_count = question_count - issue_count
    return (ok_count, question_count, issue_count)


def generate_stability_summary(
    results: List[Tuple[int, Path, int, int, int]],
    out_dir: Path,
    *,
    title: str = PACK_STABILITY_TITLE,
) -> Path:
    if not results:
        return out_dir / STABILITY_FILE

    scores = [r[2] for r in results]
    totals = [r[3] for r in results]
    issues = [r[4] for r in results]
    total_questions = totals[0] if totals else 0

    lines = [
        f"# {title}\n\n",
        f"**Generated**: {datetime.now(timezone.utc).isoformat()}\n",
        f"**Replicates**: {len(results)}\n",
        f"**Seeds**: {[r[0] for r in results]}\n\n",
        "---\n\n",
        "## Aggregate Metrics\n\n",
        "| Metric | OK | Validator Issues |\n",
        "|--------|----|------------------|\n",
        f"| **Minimum** | {min(scores)}/{total_questions} | {min(issues)} |\n",
        f"| **Maximum** | {max(scores)}/{total_questions} | {max(issues)} |\n",
        f"| **Median** | {statistics.median(scores):.1f}/{total_questions} | {statistics.median(issues):.1f} |\n",
        f"| **Mean** | {statistics.mean(scores):.2f}/{total_questions} | {statistics.mean(issues):.2f} |\n\n",
        "---\n\n",
        "## Per-Replicate Results\n\n",
        "| Seed | OK | Issues | Report |\n",
        "|------|----|--------|--------|\n",
    ]
    for seed, path, ok, total, issue_count in results:
        lines.append(f"| {seed} | {ok}/{total} | {issue_count} | [{path.name}]({path.name}) |\n")

    summary_path = out_dir / STABILITY_FILE
    write_text(summary_path, "".join(lines))
    return summary_path


# =============================================================================
# Evidence injection (BC-001)
# =============================================================================

_DEFAULT_TEST_PATH_PATTERNS_FALLBACK = list(DEFAULT_TEST_PATH_PATTERNS) or [
    r"(^|/)(tests)(/|$)",
    r"(^|/)(testdata|fixtures)(/|$)",
    r"(^|/)[^/]*_tests?\.rs$",
    r"(^|/)test_[^/]+\.rs$",
]

_TEST_PATH_REGEX_CACHE: Dict[Tuple[str, ...], List[re.Pattern[str]]] = {}
_TRANSFORM_REGEX_CACHE: Dict[Tuple[str, ...], List[re.Pattern[str]]] = {}


def _compile_test_regexes(patterns: List[str]) -> List[re.Pattern[str]]:
    """Compile and cache a list of regex patterns."""
    key = tuple(patterns)
    if key in _TEST_PATH_REGEX_CACHE:
        return _TEST_PATH_REGEX_CACHE[key]
    compiled: List[re.Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(str(p)))
        except re.error as e:
            raise SystemExit(f"Invalid test_path_patterns regex: {p!r} ({e})")
    _TEST_PATH_REGEX_CACHE[key] = compiled
    return compiled


def _compile_transform_regexes(
    patterns: List[str],
    *,
    context: str = "transform regex",
) -> List[re.Pattern[str]]:
    """Compile and cache regexes used by transform include/exclude path filters.

    Fail-closed: invalid regex patterns are treated as pack-invalid configuration.
    """
    key = (context,) + tuple(patterns)
    if key in _TRANSFORM_REGEX_CACHE:
        return _TRANSFORM_REGEX_CACHE[key]
    compiled: List[re.Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(str(p)))
        except re.error as e:
            _log_event(
                logging.ERROR,
                "preflight.transform.invalid_regex",
                fn="_compile_transform_regexes",
                pattern=str(p),
                context=context,
                error=str(e),
            )
            raise SystemExit(
                f"Invalid {context} regex: {p!r} ({e})"
            )
    _TRANSFORM_REGEX_CACHE[key] = compiled
    return compiled


_DEFAULT_TEST_PATTERNS_BY_PATH: Dict[str, List[str]] = {}
_QUESTION_VALIDATORS_CFG_BY_PATH: Dict[str, Dict[str, Any]] = {}


def _load_default_test_path_patterns(pack: "Pack", pack_path: Path) -> List[str]:
    """Load default test_path_patterns from validator YAML (SSOT).

    Resolution order:
      1) pack.runner.plugin_config.question_validators_path (relative to pack dir)
      2) pack_path.with_name(question_validators_default_filename)
      3) fallback hardcoded list (only if YAML missing)
    """
    runner = getattr(pack, "runner", {}) or {}
    pcfg = runner.get("plugin_config") if isinstance(runner.get("plugin_config"), dict) else {}

    vpath = pcfg.get("question_validators_path")
    if vpath:
        p = Path(str(vpath))
        if not p.is_absolute():
            p = (pack_path.parent / p).resolve()
    else:
        p = pack_path.with_name(QUESTION_VALIDATORS_DEFAULT_FILENAME)

    cache_key = str(p)
    if cache_key in _DEFAULT_TEST_PATTERNS_BY_PATH:
        return _DEFAULT_TEST_PATTERNS_BY_PATH[cache_key]

    patterns = list(_DEFAULT_TEST_PATH_PATTERNS_FALLBACK)
    if p.exists():
        try:
            obj = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                d = obj.get("defaults")
                if isinstance(d, dict) and isinstance(d.get("test_path_patterns"), list):
                    patterns = [str(x) for x in d["test_path_patterns"] if str(x).strip()]
        except Exception:
            pass

    _DEFAULT_TEST_PATTERNS_BY_PATH[cache_key] = patterns
    return patterns


def _load_question_validators_cfg(pack: "Pack", pack_path: Path) -> Dict[str, Any] | None:
    runner = getattr(pack, "runner", {}) or {}
    pcfg = runner.get("plugin_config") if isinstance(runner.get("plugin_config"), dict) else {}

    vpath = pcfg.get("question_validators_path")
    if vpath:
        p = Path(str(vpath))
        if not p.is_absolute():
            p = (pack_path.parent / p).resolve()
    else:
        p = pack_path.with_name(QUESTION_VALIDATORS_DEFAULT_FILENAME)

    cache_key = str(p)
    if cache_key in _QUESTION_VALIDATORS_CFG_BY_PATH:
        cfg = _QUESTION_VALIDATORS_CFG_BY_PATH[cache_key]
        return cfg if cfg else None

    cfg: Dict[str, Any] = {}
    if p.exists():
        try:
            obj = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                cfg = obj
        except Exception:
            cfg = {}

    _QUESTION_VALIDATORS_CFG_BY_PATH[cache_key] = cfg
    return cfg if cfg else None


_RS_FILELINE_RE = re.compile(r"[A-Za-z0-9_.\-/]+\.rs:\d+(?:-\d+)?")


def _apply_question_validators(*, qid: str, answer_text: str, cfg: Dict[str, Any], default_test_path_patterns: List[str]) -> List[str]:
    if not answer_text or not isinstance(cfg, dict):
        return []
    validators = cfg.get("validators")
    if not isinstance(validators, dict):
        return []
    rules = validators.get(qid)
    if not isinstance(rules, list) or not rules:
        return []
    issues: List[str] = []
    def _compile(pattern: str, *, label: str):
        try:
            return re.compile(str(pattern))
        except Exception as e:
            issues.append(f"{qid}: invalid {label} regex: {pattern!r} ({e})")
            return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = str(rule.get("type") or "").strip()
        if rtype == "ban_regex":
            cre = _compile(str(rule.get("regex") or ""), label="ban_regex.regex")
            if cre and cre.search(answer_text):
                issues.append(str(rule.get("message") or f"{qid}: banned regex matched"))
        elif rtype == "require_min_inline_regex_count":
            cre = _compile(str(rule.get("regex") or ""), label="require_min_inline_regex_count.regex")
            min_count = int(rule.get("min_count") or 0)
            if cre and sum(1 for _ in cre.finditer(answer_text)) < min_count:
                issues.append(str(rule.get("message") or f"{qid}: require_min_inline_regex_count failed"))
        elif rtype == "require_min_inline_regex_count_if_regex":
            cif = _compile(str(rule.get("if_regex") or ""), label="require_min_inline_regex_count_if_regex.if_regex")
            cre = _compile(str(rule.get("regex") or ""), label="require_min_inline_regex_count_if_regex.regex")
            min_count = int(rule.get("min_count") or 0)
            if cif and cre and cif.search(answer_text) and sum(1 for _ in cre.finditer(answer_text)) < min_count:
                issues.append(str(rule.get("message") or f"{qid}: require_min_inline_regex_count_if_regex failed"))
        elif rtype == "require_non_test_fileline_citations_if_regex":
            ctrig = _compile(str(rule.get("trigger_regex") or ""), label="require_non_test_fileline_citations_if_regex.trigger_regex")
            cclean = _compile(str(rule.get("clean_outcome_regex") or ""), label="require_non_test_fileline_citations_if_regex.clean_outcome_regex") if rule.get("clean_outcome_regex") else None
            if ctrig and ctrig.search(answer_text):
                hits = _RS_FILELINE_RE.findall(answer_text)
                if not hits:
                    issues.append(str(rule.get("message_no_citations") or f"{qid}: missing required non-test citations"))
                    continue
                test_pats = rule.get("test_path_patterns")
                patterns = [str(x) for x in test_pats if str(x).strip()] if isinstance(test_pats, list) else list(default_test_path_patterns)
                any_non_test = any(not _is_test_file(tok.split(":",1)[0], patterns=patterns) for tok in hits)
                if not any_non_test and not (cclean and cclean.search(answer_text)):
                    issues.append(str(rule.get("message_all_test") or f"{qid}: citations are all in test paths"))
    return issues


def _is_test_file(path: str, *, patterns: List[str]) -> bool:
    """Return True if *path* matches any test-path regex pattern."""
    norm = (path or "").replace("\\", "/")
    regs = _compile_test_regexes(patterns)
    return any(r.search(norm) for r in regs)


def _is_comment_line(text: str) -> bool:
    """Return True if *text* is a Rust comment line."""
    stripped = text.strip()
    return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("* ")


def _has_real_doc(row: Dict[str, Any]) -> bool:
    """Return True if *row* has a non-empty documentation comment."""
    doc = row.get("doc")
    if isinstance(doc, dict):
        if not doc.get("has_doc", False):
            return False
        text = doc.get("text") or doc.get("content") or ""
        return bool(text.strip())
    if isinstance(doc, str):
        return bool(doc.strip())
    return bool(row.get("has_doc", False))


def _coerce_transform_regex_list(raw: Any) -> List[str]:
    """Coerce transform regex field to a normalized string list."""
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, list):
        out: List[str] = []
        for it in raw:
            if isinstance(it, str):
                s = it.strip()
                if s:
                    out.append(s)
        return out
    return []


def _unique_paths(rows: List[Dict[str, Any]]) -> List[str]:
    """Return deduplicated, order-preserving path list from preflight rows."""
    out: List[str] = []
    seen: set[str] = set()
    for r in rows:
        p = _get_path(r)
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _summarize_transform_filters(transform: Dict[str, Any]) -> Dict[str, Any]:
    """Build compact diagnostics for transform filters used in logs."""
    include_patterns = _coerce_transform_regex_list(transform.get("include_path_regex"))

    if "exclude_path_regex" in transform:
        exclude_raw = transform.get("exclude_path_regex")
        exclude_pattern_source = "explicit"
    else:
        exclude_raw = transform.get("_default_exclude_path_regex")
        exclude_pattern_source = "default" if exclude_raw else "none"
    exclude_patterns = _coerce_transform_regex_list(exclude_raw)

    exclude_test_files = bool(transform.get("exclude_test_files"))
    test_patterns: List[str] = []
    if exclude_test_files:
        tp = transform.get("test_path_patterns")
        default_tp = transform.get("_default_test_path_patterns")
        if isinstance(tp, list) and tp:
            test_pattern_source = "explicit"
            test_patterns = [str(x) for x in tp if str(x).strip()]
        elif isinstance(default_tp, list) and default_tp:
            test_pattern_source = "default"
            test_patterns = [str(x) for x in default_tp if str(x).strip()]
        else:
            test_pattern_source = "fallback"
            test_patterns = list(_DEFAULT_TEST_PATH_PATTERNS_FALLBACK)
    else:
        test_pattern_source = "disabled"

    require_contains = transform.get("require_contains")
    if not isinstance(require_contains, str) or not require_contains.strip():
        require_contains = None

    require_regex_patterns = _coerce_transform_regex_list(transform.get("require_regex"))
    group_by_path_top_n = transform.get("group_by_path_top_n")
    if not isinstance(group_by_path_top_n, dict):
        group_by_path_top_n = None

    filter_fn = transform.get("filter_fn")
    if not isinstance(filter_fn, str) or not filter_fn.strip():
        filter_fn = None

    return {
        "filters_used": [k for k in TRANSFORM_FILTER_KEYS if bool(transform.get(k))],
        "include_pattern_count": len(include_patterns),
        "include_patterns": include_patterns[:8],
        "exclude_pattern_count": len(exclude_patterns),
        "exclude_patterns": exclude_patterns[:8],
        "exclude_pattern_source": exclude_pattern_source,
        "exclude_test_files": exclude_test_files,
        "test_pattern_count": len(test_patterns),
        "test_pattern_source": test_pattern_source,
        "exclude_comments": bool(transform.get("exclude_comments")),
        "require_contains": require_contains,
        "require_regex_count": len(require_regex_patterns),
        "require_regex_patterns": require_regex_patterns[:8],
        "group_by_path_top_n": group_by_path_top_n,
        "filter_fn": filter_fn,
    }


def _validate_group_by_path_dependencies(preflights: List[Any]) -> List[str]:
    """Validate that group_by_path_top_n.from references a prior step."""
    seen: set[str] = set()
    issues: List[str] = []
    for idx, step in enumerate(preflights or [], start=1):
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "").strip()
        transform = step.get("transform")
        if isinstance(transform, dict):
            gbp = transform.get("group_by_path_top_n")
            if isinstance(gbp, dict):
                from_name = str(gbp.get("from") or "").strip()
                step_label = name or "(unnamed)"
                if not from_name:
                    issues.append(
                        f"step[{idx}] '{step_label}': group_by_path_top_n.from is required"
                    )
                elif from_name not in seen:
                    issues.append(
                        f"step[{idx}] '{step_label}': group_by_path_top_n.from='{from_name}' "
                        "references a missing or later step"
                    )
        if name:
            seen.add(name)
    return issues


def _safe_int(v: Any, default: int = 0) -> int:
    """Coerce *v* to int; return *default* on failure."""
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _row_count_maybe(row: Dict[str, Any]) -> int:
    """Extract an integer count from a preflight aggregate row.

    Looks for ``count``, ``total``, ``unwraps``, ``expects`` — the field
    names produced by ``rsqt prod-unwraps`` / ``rsqt prod-expects``.
    """
    for key in _effective_row_count_keys():
        v = row.get(key)
        if v is not None:
            return _safe_int(v)
    for key, val in row.items():
        lk = str(key).strip().lower()
        if isinstance(val, (int, float)) and (
            "count" in lk or lk in ("total", "matches", "matched", "num_results", "result_count", "hit_count")
        ):
            return _safe_int(val)
    return 0


def _top_paths_from_aggregate_rows(
    rows: List[Dict[str, Any]],
    top_n: int = int(GROUP_BY_PATH_DEFAULTS.get("top_n", 5)),
    sort_key: Optional[str] = None,
) -> List[str]:
    """Return the *top_n* file paths with the highest aggregate count.

    If *sort_key* is given (e.g. ``"prod_expects"``), use that field
    directly instead of the generic ``_row_count_maybe`` heuristic.
    """
    scored: List[tuple] = []
    for r in rows:
        p = _get_path(r)
        if not p:
            continue
        if sort_key:
            scored.append((p, _safe_int(r.get(sort_key, 0))))
        else:
            scored.append((p, _row_count_maybe(r)))
    scored.sort(key=lambda t: t[1], reverse=True)
    return [p for p, _ in scored[:top_n]]


def _group_rows_by_path_and_limit(
    rows: List[Dict[str, Any]],
    allowed_paths: List[str],
    per_path: int = int(GROUP_BY_PATH_DEFAULTS.get("per_path", 5)),
) -> List[Dict[str, Any]]:
    """Keep up to *per_path* rows for each path in *allowed_paths*."""
    counts: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    allowed_set = set(allowed_paths)
    for r in rows:
        p = _get_path(r)
        if p not in allowed_set:
            continue
        c = counts.get(p, 0)
        if c >= per_path:
            continue
        counts[p] = c + 1
        out.append(r)
    return out


def _apply_transform_filters(rows: List[Dict[str, Any]], transform: Dict[str, Any], *, ctx: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Apply semantic filters from a step's transform block.

    Supported filters:
        include_path_regex      — keep rows whose path matches any regex
        exclude_path_regex      — drop rows whose path matches any regex
        exclude_test_files      — drop rows whose file path matches test patterns
        test_path_patterns      — per-step override of test-path regexes
        _default_test_path_patterns — injected by caller (SSOT from validator YAML)
        _default_exclude_path_regex — injected by caller (runner policy defaults)
        exclude_comments        — drop rows that are pure comment lines
        require_contains        — keep only rows whose line text contains the string
        require_regex           — keep only rows whose path or line text matches any regex pattern
        group_by_path_top_n     — cross-preflight: keep rows whose path is in top-N of another preflight
        filter_fn               — named filter; currently only "compact_docs"

    The optional *ctx* dict may carry ``preflight_rows_by_name`` for
    cross-preflight transforms like ``group_by_path_top_n``.
    """
    include_path_regex = transform.get("include_path_regex")
    include_patterns = _coerce_transform_regex_list(include_path_regex)
    if include_patterns:
        include_res = _compile_transform_regexes(include_patterns, context="include_path_regex")
        if include_res:
            kept: List[Dict[str, Any]] = []
            for r in rows:
                p = _get_path(r)
                if p and any(rx.search(p) for rx in include_res):
                    kept.append(r)
            rows = kept

    # If a step explicitly sets exclude_path_regex (even to an empty list),
    # honor that value and do not fall back to runner defaults.
    if "exclude_path_regex" in transform:
        exclude_path_regex = transform.get("exclude_path_regex")
    else:
        exclude_path_regex = transform.get("_default_exclude_path_regex")
    exclude_patterns = _coerce_transform_regex_list(exclude_path_regex)
    if exclude_patterns:
        exclude_res = _compile_transform_regexes(exclude_patterns, context="exclude_path_regex")
        if exclude_res:
            rows = [r for r in rows if not any(rx.search(_get_path(r)) for rx in exclude_res)]

    if transform.get("exclude_test_files"):
        tp = transform.get("test_path_patterns")
        default_tp = transform.get("_default_test_path_patterns")
        pats = tp if isinstance(tp, list) and tp else (default_tp or _DEFAULT_TEST_PATH_PATTERNS_FALLBACK)
        rows = [r for r in rows if not _is_test_file(_get_path(r), patterns=[str(x) for x in pats])]
    if transform.get("exclude_comments"):
        rows = [r for r in rows if not _is_comment_line(_extract_line_text(r))]
    rc = transform.get("require_contains")
    if rc and isinstance(rc, str):
        rows = [r for r in rows if rc in _extract_line_text(r)]
    rr = transform.get("require_regex")
    if rr:
        pats: List[str] = []
        if isinstance(rr, str):
            pats = [rr]
        elif isinstance(rr, list):
            pats = [p for p in rr if isinstance(p, str)]
        if pats:
            compiled = _compile_transform_regexes(pats, context="require_regex")
            rows = [
                r
                for r in rows
                if any(c.search(f"{_get_path(r) or ''}\n{_extract_line_text(r)}") for c in compiled)
            ]
    gbp = transform.get("group_by_path_top_n")
    if isinstance(gbp, dict) and ctx:
        from_name = gbp.get("from", "")
        top_n_default = int(GROUP_BY_PATH_DEFAULTS.get("top_n", 5))
        per_path_default = int(GROUP_BY_PATH_DEFAULTS.get("per_path", 5))
        top_n = _safe_int(gbp.get("top_n", top_n_default), top_n_default)
        per_path = _safe_int(gbp.get("per_path", per_path_default), per_path_default)
        sort_key = gbp.get("sort_key")  # e.g. "prod_expects"
        ref_map = ctx.get("preflight_rows_by_name") or {}
        if from_name and from_name not in ref_map:
            _log_event(
                logging.WARNING,
                "preflight.transform.group_by_path_top_n.missing_from",
                fn="_apply_transform_filters",
                from_name=str(from_name),
            )
        ref_rows = ref_map.get(from_name, [])
        if ref_rows:
            allowed_paths = _top_paths_from_aggregate_rows(ref_rows, top_n, sort_key=sort_key)
            rows = _group_rows_by_path_and_limit(rows, allowed_paths, per_path)
    fn_name = transform.get("filter_fn")
    if fn_name == "compact_docs":
        rows = [r for r in rows if _has_real_doc(r)]
    return rows


def _iter_rows(obj: Any) -> List[Dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        key = _detect_row_container_key(obj)
        if key:
            rows = [x for x in (obj.get(key) or []) if isinstance(x, dict)]
            if rows:
                _runtime_add_key("iter_rows_keys", str(key))
                return rows
    return []


def _detect_row_container_key(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    hinted = str(obj.get("_stdout_row_container_key") or "").strip()
    if hinted and isinstance(obj.get(hinted), list):
        return hinted
    for k in _effective_iter_rows_keys():
        v = obj.get(k)
        if not isinstance(v, list):
            continue
        if any(isinstance(x, dict) for x in v):
            _runtime_add_key("iter_rows_keys", str(k))
            return str(k)
    # fallback: first list-valued dict key with dict rows
    for k, v in obj.items():
        if not isinstance(v, list):
            continue
        if any(isinstance(x, dict) for x in v):
            _runtime_add_key("iter_rows_keys", str(k))
            return str(k)
    return None


def _replace_rows_in_stdout(
    stdout_data: Any,
    rows: List[Dict[str, Any]],
    *,
    row_container_key: str | None = None,
    filtered_to_zero: bool = False,
) -> Any:
    """Preserve stdout shape while replacing row-container rows with filtered rows."""
    if isinstance(stdout_data, list):
        return list(rows)
    if isinstance(stdout_data, dict):
        out = dict(stdout_data)
        key = row_container_key or _detect_row_container_key(stdout_data)
        # Always expose filtered rows via a stable sibling key so downstream
        # code can consume filtered evidence without guessing dict shape.
        out["stdout_rows_filtered"] = list(rows)
        if key:
            out[key] = list(rows)
            out["_stdout_row_container_key"] = str(key)
        out["_stdout_rows_filtered_count"] = len(rows)
        out["_stdout_filtered_to_zero"] = bool(filtered_to_zero)
        return out
    return stdout_data


def _nonempty_stdout(stdout_data: Any) -> bool:
    if stdout_data is None:
        return False
    if isinstance(stdout_data, str):
        return bool(stdout_data.strip())
    if isinstance(stdout_data, list):
        return len(stdout_data) > 0
    rows = _iter_rows(stdout_data)
    if rows:
        return True
    if isinstance(stdout_data, dict):
        if bool(stdout_data.get("_stdout_filtered_to_zero")):
            return False
        return len(stdout_data.keys()) > 0
    return False


def _has_preflight_hits(stdout_data: Any) -> bool:
    """Return True when preflight output indicates at least one concrete hit."""
    if stdout_data is None:
        return False
    if isinstance(stdout_data, str):
        parsed = parse_json_maybe(stdout_data)
        if parsed is not None:
            return _has_preflight_hits(parsed)
        return bool(stdout_data.strip())
    if isinstance(stdout_data, list):
        return len(stdout_data) > 0
    if isinstance(stdout_data, dict):
        if bool(stdout_data.get("_stdout_filtered_to_zero")):
            return False
        rows = _iter_rows(stdout_data)
        if rows:
            return True
        for k in _effective_has_hits_count_keys():
            v = stdout_data.get(k)
            try:
                if int(v) > 0:
                    _runtime_add_key("has_hits_count_keys", str(k))
                    return True
            except Exception:
                continue
        for k, v in stdout_data.items():
            lk = str(k).strip().lower()
            if not (isinstance(v, (int, float)) and ("count" in lk or lk in ("total", "matched", "matches", "num_results", "result_count", "hit_count"))):
                continue
            if int(v) > 0:
                _runtime_add_key("has_hits_count_keys", str(k))
                _runtime_add_key("row_count_keys", str(k))
                return True
        # Some preflights (for example: health summaries) return dict-shaped
        # deterministic evidence instead of row arrays. Treat such structured
        # payloads as usable evidence when non-empty.
        if _dict_has_substantive_payload(stdout_data):
            return True
        return False
    return False


def _dict_has_substantive_payload(obj: Dict[str, Any], *, _depth: int = 0) -> bool:
    """Return True if dict contains non-metadata values usable as evidence."""
    if not isinstance(obj, dict):
        return False
    if _depth > 4:
        return False
    for k, v in obj.items():
        key = str(k).strip()
        if key.startswith("_"):
            continue
        # Side-channel row bucket is metadata for dict-style payloads.
        if key == "stdout_rows_filtered":
            continue
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                return True
            continue
        if isinstance(v, (int, float, bool)):
            return True
        if isinstance(v, dict):
            if _dict_has_substantive_payload(v, _depth=_depth + 1):
                return True
            continue
        if isinstance(v, list):
            for it in v:
                if it is None:
                    continue
                if isinstance(it, str):
                    if it.strip():
                        return True
                    continue
                if isinstance(it, (int, float, bool)):
                    return True
                if isinstance(it, dict) and _dict_has_substantive_payload(it, _depth=_depth + 1):
                    return True
    return False


def _shorten(s: str, max_len: int = int(EVIDENCE_SHORTEN.get("default", 200))) -> str:
    """Truncate *s* to *max_len* chars, appending '...' if truncated."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "..."


def _get_path(row: Dict[str, Any]) -> str:
    for k in _effective_path_keys():
        v = row.get(k)
        if v:
            s = str(v).strip().replace("\\", "/")
            if not s:
                continue
            if k == "title":
                # RAG rows frequently use `title=path::symbol_kind ...`; keep only
                # the concrete file path prefix so path-gating/citation filters work.
                s = s.split("::", 1)[0].strip()
            s = re.sub(r"^\s*file:\s*", "", s, flags=re.IGNORECASE)
            if s:
                return s
    for k, v in row.items():
        if not isinstance(v, str) or not v.strip():
            continue
        lk = str(k).strip().lower()
        if not _is_path_key_candidate(lk):
            continue
        s = str(v).strip().replace("\\", "/")
        s = re.sub(r"^\s*file:\s*", "", s, flags=re.IGNORECASE)
        if _looks_like_repo_path_text(s):
            _runtime_add_key("path_keys", str(k))
            return s
    return ""


def _get_line_start(row: Dict[str, Any]) -> str:
    for k in _effective_line_keys():
        v = row.get(k)
        if v is not None:
            return str(v)
    for k, v in row.items():
        if isinstance(v, (int, float)) and _is_line_key_candidate(str(k).strip().lower()):
            _runtime_add_key("line_keys", str(k))
            return str(v)
    return ""


def _get_line_end(row: Dict[str, Any]) -> str:
    v = row.get("line_end")
    if v is not None:
        return str(v)
    return ""


def _loc_str(row: Dict[str, Any]) -> str:
    """Format 'path:line' or 'path:start-end' location string."""
    fp = _get_path(row)
    ls = _get_line_start(row)
    le = _get_line_end(row)
    if fp and ls and le and le != ls:
        return f"{fp}:{ls}-{le}"
    if fp and ls:
        return f"{fp}:{ls}"
    if fp:
        return f"{fp}:1"
    return ""


def _extract_counts(row: Dict[str, Any]) -> Dict[str, int]:
    """Extract numeric fields matching *_count pattern."""
    counts = {}
    for k, v in row.items():
        if k.endswith("_count") and isinstance(v, (int, float)):
            counts[k] = int(v)
    return counts


def _extract_signature(row: Dict[str, Any]) -> str:
    """Extract function/type signature."""
    return str(row.get("signature") or row.get("signature_meta") or "")


def _extract_doc_text(row: Dict[str, Any]) -> str:
    """Extract doc comment text (handles nested doc.text and flat doc fields)."""
    doc = row.get("doc")
    if isinstance(doc, dict):
        return str(doc.get("text") or doc.get("content") or "")
    if isinstance(doc, str):
        return doc
    has_doc = row.get("has_doc")
    if isinstance(has_doc, bool):
        return f"(has_doc={has_doc})"
    return ""


def _extract_line_text(row: Dict[str, Any]) -> str:
    """Extract source line text (search results, pub use lines, etc.)."""
    for k in _effective_snippet_keys():
        v = row.get(k)
        if v and isinstance(v, str):
            return v
    for k, v in row.items():
        if isinstance(v, str) and v.strip() and _is_snippet_key_candidate(str(k).strip().lower()):
            _runtime_add_key("snippet_keys", str(k))
            return v
    return ""


def _remaining_fields(row: Dict[str, Any], extracted_keys: set) -> Dict[str, Any]:
    """Return non-extracted, non-path/line fields (unknown-key fallback)."""
    skip = frozenset(_effective_path_keys() + _effective_line_keys() + ("line_end",)) | extracted_keys
    remaining = {}
    for k, v in row.items():
        if k not in skip and v is not None and v != "" and v != 0:
            remaining[k] = v
    return dict(list(remaining.items())[: int(ISSUE_CAPS.get("unknown_key_fields", 5))])




def _format_list_rows(
    rows: List[Dict[str, Any]],
    max_chars: int = int(EVIDENCE_MAX_CHARS.get("list", 1600)),
) -> str:
    """Compact per-row summaries with counts, signature, doc text, line text."""
    out: List[str] = []
    for i, r in enumerate(rows):
        loc = _loc_str(r)
        parts = [f"{i+1}. {loc}"] if loc else [f"{i+1}."]

        counts = _extract_counts(r)
        if counts:
            parts.append(" ".join(f"{k}={v}" for k, v in counts.items()))

        sig = _extract_signature(r)
        if sig:
            parts.append(f"sig: {_shorten(sig, int(EVIDENCE_SHORTEN.get('signature', 120)))}")

        doc = _extract_doc_text(r)
        if doc:
            parts.append(f"doc: {_shorten(doc, int(EVIDENCE_SHORTEN.get('doc', 100)))}")

        line_text = _extract_line_text(r)
        if line_text:
            s = line_text.replace("\n", " ").strip()
            parts.append(_shorten(s, int(EVIDENCE_SHORTEN.get("line_text", 200))))

        # Unknown-key fallback (prevent future field-drop bugs)
        extracted_keys = set()
        if counts:
            extracted_keys.update(counts.keys())
        if sig:
            extracted_keys.update(("signature", "signature_meta"))
        if doc:
            extracted_keys.update(("doc", "has_doc"))
        if line_text:
            extracted_keys.update(_effective_snippet_keys())
        remaining = _remaining_fields(r, extracted_keys)
        if remaining:
            parts.append("+" + ",".join(f"{k}={v!r}" for k, v in remaining.items()))

        out.append("  " + " | ".join(parts))

    result = "\n".join(out)
    return result[:max_chars]


def _format_block_rows(
    rows: List[Dict[str, Any]],
    fence_lang: str = "",
    max_chars: int = int(EVIDENCE_MAX_CHARS.get("block", 8000)),
) -> str:
    """Fenced code block rendering — one block per row's source_text."""
    out: List[str] = []
    total = 0
    for i, r in enumerate(rows):
        loc = _loc_str(r)
        source = _extract_line_text(r)
        if not source:
            source = str(r.get("source_text") or r.get("text") or "")
        if not source:
            continue

        header = f"### {loc}" if loc else f"### Block {i+1}"
        fence = f"```{fence_lang}" if fence_lang else "```"
        block = f"{header}\n{fence}\n{source}\n```\n"

        if total + len(block) > max_chars:
            out.append(f"... ({len(rows) - i} more rows truncated)")
            break
        out.append(block)
        total += len(block)

    return "\n".join(out)


def _format_lines_rows(
    rows: List[Dict[str, Any]],
    max_chars: int = int(EVIDENCE_MAX_CHARS.get("lines", 4000)),
) -> str:
    """Bare source-text lines with location prefix — validator-friendly format.

    Output format:  ``  [location] source_text``
    The location prefix ensures the source text (e.g. ``pub use ...;``)
    ends cleanly at end-of-line, matching regexes like ``pub use .+;\\s*$``.
    """
    parts: List[str] = []
    total = 0
    for row in rows:
        loc = _loc_str(row)
        text = _extract_line_text(row)
        if not text:
            text = _extract_signature(row)
        if not text:
            continue
        text = text.replace("\n", " ").strip()
        line = f"  [{loc}] {text}" if loc else f"  {text}"
        total += len(line) + 1
        if total > max_chars:
            parts.append(f"  ... ({len(rows) - len(parts)} more)")
            break
        parts.append(line)
    return "\n".join(parts)


def _format_json_rows(
    rows: List[Dict[str, Any]],
    max_chars: int = int(EVIDENCE_MAX_CHARS.get("json", 10000)),
) -> str:
    """Compact JSON array rendering — preserves full structure for the LLM."""
    s = json.dumps(rows, ensure_ascii=False, indent=1)
    if len(s) > max_chars:
        s = s[:max_chars] + "\n... (truncated)"
    return s


def format_evidence_block(
    name: str,
    stdout_data: Any,
    *,
    max_chars: int = int(EVIDENCE_MAX_CHARS.get("evidence_block", 1600)),
    render_mode: str = DEFAULT_RENDER_MODE,
    fence_lang: str = "",
) -> str:
    """Format preflight evidence for LLM injection.

    render_mode:
        "list"  — compact per-row summaries (default, backward compatible)
        "block" — fenced code block excerpts (for source_text heavy results)
        "lines" — bare source lines with location suffix (for regex matching)
        "json"  — compact JSON array (preserves full structure)
    """
    rows = _iter_rows(stdout_data)
    if rows:
        header = f"[{name}] {len(rows)} results:"
        budget = max_chars - len(header) - 2
        if render_mode == "block":
            body = _format_block_rows(rows, fence_lang=fence_lang, max_chars=budget)
        elif render_mode == "lines":
            body = _format_lines_rows(rows, max_chars=budget)
        elif render_mode == "json":
            body = _format_json_rows(rows, max_chars=budget)
        else:
            body = _format_list_rows(rows, max_chars=budget)
        return f"{header}\n{body}"
    # Fallback for non-row data
    try:
        s = json.dumps(stdout_data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        s = str(stdout_data)
    return s[:max_chars]



def _select_prompt_files(pack: Pack, args: argparse.Namespace) -> Tuple[Path | None, Path | None]:
    if args.system_prompt_file:
        p = Path(args.system_prompt_file)
        return (p, p)

    grounding = Path(args.system_prompt_grounding_file) if args.system_prompt_grounding_file else None
    analyze = Path(args.system_prompt_analyze_file) if args.system_prompt_analyze_file else None

    if (grounding is None or analyze is None) and isinstance(pack.runner.get("prompts"), dict):
        pr = pack.runner.get("prompts") or {}
        if grounding is None and pr.get("grounding"):
            grounding = Path(pr.get("grounding"))
        if analyze is None and pr.get("analyze"):
            analyze = Path(pr.get("analyze"))

    if grounding is not None and not grounding.exists():
        grounding = None
    if analyze is not None and not analyze.exists():
        analyze = None

    return (grounding, analyze or grounding)


def _build_augmented_question(question_text: str, evidence_blocks: List[str], quote_bypass: bool, response_schema: str = "") -> str:
    if not evidence_blocks:
        return question_text

    injected = []
    for block in evidence_blocks:
        injected.append(block)

    mandatory_procedure = ""
    if not quote_bypass:
        mandatory_text = str(
            PROMPTS.get(
                "mandatory_procedure",
                (
                    "MANDATORY PROCEDURE:\n"
                    "1) Before any explanation, paste the required quoted code/text verbatim from the Sections above.\n"
                    "2) If you cannot quote it verbatim, output NOT FOUND and stop.\n"
                    "3) After quoting, provide the answer body."
                ),
            )
        ).strip()
        mandatory_procedure = mandatory_text + "\n\n"

    response_schema_section = ""
    if response_schema:
        response_header = str(PROMPTS.get("response_format_header", "RESPONSE FORMAT (MUST FOLLOW EXACTLY)")).rstrip(":")
        cite_rule = str(
            PROMPTS.get(
                "response_format_cite_rule",
                "If evidence provides CITE=..., cite that token verbatim (without the CITE= prefix).",
            )
        )
        response_schema_section = (
            f"{response_header}:\n"
            f"{cite_rule}\n\n"
            f"{response_schema}\n\n"
        )

    return (
        str(PROMPTS.get("retrieved_sources_header", "RETRIEVED SOURCES (authoritative; cite these sections):")) + "\n\n"
        + "\n\n".join(injected)
        + "\n\n---\n\n"
        + mandatory_procedure
        + response_schema_section
        + str(PROMPTS.get("question_header", "QUESTION:"))
        + "\n\n"
        + question_text
    )


def _build_quote_bypass_prompt(question_text: str, evidence_blocks: List[str], response_schema: str = "") -> str:
    combined = "\n\n---\n\n".join([b.split("]:\n", 1)[-1].strip() for b in evidence_blocks if b.strip()])

    response_schema_section = ""
    if response_schema:
        response_header = str(PROMPTS.get("response_format_header", "RESPONSE FORMAT (MUST FOLLOW EXACTLY)")).rstrip(":")
        cite_rule = str(
            PROMPTS.get(
                "response_format_cite_rule",
                "If evidence provides CITE=..., cite that token verbatim (without the CITE= prefix).",
            )
        )
        response_schema_section = (
            f"---\n\n{response_header}:\n"
            f"{cite_rule}\n\n"
            f"{response_schema}\n\n"
        )

    quote_bypass_title = str(PROMPT_QUOTE_BYPASS.get("title", "QUOTE-BYPASS MODE"))
    quote_bypass_preamble = str(
        PROMPT_QUOTE_BYPASS.get(
            "preamble",
            (
                "The following evidence has been deterministically extracted from the corpus.\n"
                "You MUST NOT output 'NOT FOUND' - the evidence IS present below.\n"
                "Your task: Use the evidence and answer the question."
            ),
        )
    )
    quote_bypass_evidence_header = str(PROMPT_QUOTE_BYPASS.get("evidence_header", "EVIDENCE (authoritative)")).rstrip(":")
    quote_bypass_instructions = PROMPT_QUOTE_BYPASS.get(
        "instructions",
        [
            "1. Reference the evidence above to answer the question.",
            "2. If the question asks for text/definitions, repeat the relevant parts from Evidence.",
            "3. If evidence is insufficient, say INSUFFICIENT EVIDENCE and list what's missing.",
        ],
    )
    instructions_text = "\n".join(str(x) for x in quote_bypass_instructions)

    return (
        f"{quote_bypass_title}\n\n"
        f"{quote_bypass_preamble}\n\n"
        "---\n\n"
        f"{quote_bypass_evidence_header}:\n\n"
        f"{combined}\n\n"
        f"{response_schema_section}"
        "---\n\n"
        f"{str(PROMPTS.get('question_header', 'QUESTION:'))}\n\n"
        f"{question_text}\n\n"
        "---\n\n"
        "INSTRUCTIONS:\n"
        f"{instructions_text}\n"
    )


def _extract_answer_and_sources(chat_payload: Any) -> Tuple[str, Any]:
    if isinstance(chat_payload, dict):
        ans = chat_payload.get("answer") or chat_payload.get("response") or chat_payload.get("text") or ""
        return str(ans), chat_payload.get("sources")
    return str(chat_payload), None


def _inject_strict_response_template(base_question: str, strict_template: str) -> str:
    template = str(strict_template or "").strip()
    if not template:
        return base_question
    initial_preamble = str(
        PROMPT_SCHEMA_RETRY.get(
            "initial_preamble",
            (
                "OUTPUT CONTRACT OVERRIDE:\n"
                "- Return plain text only (no markdown headers/bullets).\n"
                "- First line must be VERDICT=...\n"
                "- Second line must be CITATIONS=...\n"
                "- CITATIONS must only use tokens from CITE= evidence lines."
            ),
        )
    ).strip()
    template_header = str(PROMPT_SCHEMA_RETRY.get("template_header", "STRICT RESPONSE TEMPLATE (MUST MATCH)")).rstrip(":")
    return (
        f"{initial_preamble}\n\n"
        f"{template_header}:\n"
        f"{template}\n\n"
        f"{base_question}"
    )


def _build_schema_retry_prompt(
    *,
    base_question: str,
    strict_template: str,
    issues: List[str],
    attempt: int,
    total_attempts: int,
) -> str:
    preamble = str(
        PROMPT_SCHEMA_RETRY.get(
            "preamble",
            (
                "SCHEMA RETRY MODE:\n"
                "- Fix all validation issues listed below.\n"
                "- Preserve factual claims; only repair format/citations as needed.\n"
                "- Return plain text only."
            ),
        )
    ).strip()
    template_header = str(PROMPT_SCHEMA_RETRY.get("template_header", "STRICT RESPONSE TEMPLATE (MUST MATCH)")).rstrip(":")
    issues_header = str(PROMPT_SCHEMA_RETRY.get("issues_header", "Validation issues to fix in this retry:")).rstrip(":")
    cap = int(PROMPT_SCHEMA_RETRY.get("max_issue_bullets", ISSUE_CAPS.get("adaptive_rerun_bullets", 8)))
    issue_bullets = "\n".join(f"- {it}" for it in issues[: max(1, cap)])
    template = str(strict_template or "").strip()

    parts: List[str] = [preamble]
    if template:
        parts.extend(["", f"{template_header}:", template])
    if issue_bullets:
        parts.extend(["", f"{issues_header}:", issue_bullets])
    parts.extend(["", f"RETRY_ATTEMPT={attempt}/{total_attempts}", "", base_question])
    return "\n".join(parts)


def _build_advice_retry_prompt(
    *,
    base_prompt: str,
    issues: List[str],
    attempt: int,
    total_attempts: int,
) -> str:
    preamble = str(
        PROMPT_ADVICE.get(
            "retry_preamble",
            (
                "ADVICE RETRY MODE:\n"
                "- Fix all advice validation issues listed below.\n"
                "- Preserve factual grounding and cite only evidence tokens from CITE= blocks.\n"
                "- Return plain text only and follow ISSUE_n field format exactly."
            ),
        )
    ).strip()
    issues_header = str(
        PROMPT_ADVICE.get("retry_issues_header", "Advice validation issues to fix in this retry:")
    ).rstrip(":")
    issue_cap = max(1, ADVICE_RETRY_ISSUE_BULLETS)
    issue_bullets = "\n".join(f"- {it}" for it in (issues or [])[:issue_cap])
    parts: List[str] = [preamble]
    if issue_bullets:
        parts.extend(["", f"{issues_header}:", issue_bullets])
    parts.extend(["", f"RETRY_ATTEMPT={attempt}/{total_attempts}", "", base_prompt])
    return "\n".join(parts)


def _repair_answer_for_strict_contract(
    *,
    qid: str,
    answer: str,
    strict_template: str,
    evidence_blocks: List[str],
    validation: PackValidation,
) -> Tuple[str, List[str]]:
    """Best-effort schema/citation repair for strict-template questions.

    This is intentionally narrow:
    - only active when question.chat.strict_response_template is configured
    - preserves body content while repairing VERDICT/CITATIONS contract lines
    """
    notes: List[str] = []
    if not strict_template.strip():
        return answer, notes

    clean = (answer or "").replace("**", "")
    required = set(validation.required_verdicts or [])

    mv = re.search(r"^\s*VERDICT\s*[=:]\s*([A-Z_]+)\s*$", clean, flags=re.MULTILINE)
    verdict = mv.group(1).strip() if mv else ""
    if (not verdict) or (required and verdict not in required):
        verdict = "INDETERMINATE" if "INDETERMINATE" in required else (next(iter(required)) if required else "INDETERMINATE")
        notes.append("repaired_verdict")

    tokens = _extract_answer_citation_tokens(clean)
    valid_token_re = re.compile(_PATHLINE_PATTERN)
    citation_placeholders = {"NONE", "N/A", "NA", "INSUFFICIENT", "UNKNOWN", "MISSING"}
    has_placeholder_only = bool(tokens) and all((t or "").strip().upper() in citation_placeholders for t in tokens)
    has_bad_format = any(not valid_token_re.match(t or "") for t in tokens)
    needs_citations_repair = (not tokens) or has_placeholder_only or has_bad_format

    allowed = _extract_allowed_citation_tokens(evidence_blocks)
    if validation.enforce_citations_from_evidence:
        # If provenance check fails, prefer deterministic replacement with allowed evidence tokens.
        prov = validate_citations_from_evidence(clean, allowed=allowed)
        if prov:
            needs_citations_repair = True

    citations_out = ", ".join(tokens) if tokens else ""
    if needs_citations_repair:
        ordered_allowed = sorted(t for t in allowed if valid_token_re.match(t or ""))
        cap = max(1, int(ISSUE_CAPS.get("deterministic_citations", 5)))
        if ordered_allowed:
            citations_out = ", ".join(ordered_allowed[:cap])
        else:
            citations_out = f"{qid}_preflight.json:1"
        notes.append("repaired_citations")

    # Remove existing VERDICT/CITATIONS lines and rebuild with repaired header.
    body_lines: List[str] = []
    for ln in clean.splitlines():
        s = ln.strip()
        if re.match(r"^\s*VERDICT\s*[=:]", s):
            continue
        if re.match(r"^\s*CITATIONS\s*[=:]?", s):
            continue
        body_lines.append(ln)

    repaired = "\n".join([
        f"VERDICT={verdict}",
        f"CITATIONS={citations_out}",
        "",
        "\n".join(body_lines).strip(),
    ]).strip() + "\n"
    return repaired, notes


def _build_deterministic_seed_answer(qid: str, evidence_blocks: List[str]) -> str:
    """Fallback answer body when answer_mode=deterministic and no plugin synthesizer exists."""
    tokens = sorted(_extract_allowed_citation_tokens(evidence_blocks))
    det_citation_cap = int(ISSUE_CAPS.get("deterministic_citations", 5))
    fallback_suffix = str(PROMPT_DETERMINISTIC.get("fallback_suffix", "_preflight.json:1"))
    citations = ", ".join(tokens[:det_citation_cap]) if tokens else f"{qid}{fallback_suffix}"
    deterministic_verdict = str(PROMPT_DETERMINISTIC.get("verdict", "INDETERMINATE"))
    deterministic_note = str(
        PROMPT_DETERMINISTIC.get("note", "question.answer_mode=deterministic; model answer generation was skipped.")
    )
    return (
        f"VERDICT={deterministic_verdict}\n"
        f"CITATIONS={citations}\n\n"
        f"DETERMINISTIC_NOTE={deterministic_note}\n"
    )


def _build_advice_prompt(
    *,
    qid: str,
    question_text: str,
    deterministic_answer: str,
    evidence_blocks: List[str],
) -> str:
    evidence = "\n\n".join(evidence_blocks) if evidence_blocks else str(PROMPT_ADVICE.get("no_evidence_text", "(no evidence blocks available)"))
    advice_text_template = str(
        PROMPT_ADVICE.get(
            "text",
            (
                "RUST IMPROVEMENT ADVICE MODE\n\n"
                "You are reviewing deterministic Rust audit output and must provide implementation guidance.\n"
                "Do not restate the audit answer; provide concrete, actionable improvements.\n\n"
                "REQUIRED OUTPUT FORMAT (plain text):\n"
                "ISSUE_1=...\n"
                "WHY_IT_MATTERS_1=...\n"
                "PATCH_SKETCH_1=...\n"
                "TEST_PLAN_1=...\n"
                "CITATIONS_1=<copy citation tokens from evidence, e.g. crates/engine/src/store.rs:25-30>\n"
                "ISSUE_2=... (optional)\n"
                "WHY_IT_MATTERS_2=... (optional)\n"
                "PATCH_SKETCH_2=... (optional)\n"
                "TEST_PLAN_2=... (optional)\n"
                "CITATIONS_2=<copy citation tokens from evidence> (optional)\n"
                "ISSUE_3=... (optional)\n"
                "WHY_IT_MATTERS_3=... (optional)\n"
                "PATCH_SKETCH_3=... (optional)\n"
                "TEST_PLAN_3=... (optional)\n"
                "CITATIONS_3=<copy citation tokens from evidence> (optional)\n\n"
                "RULES:\n"
                "- Max 3 issues.\n"
                "- Prefer Rust-idiomatic suggestions (error conversions, trait boundaries, async/thread safety, testing seams).\n"
                "- CITATIONS_n must be copied verbatim from evidence tokens (look for lines starting with \"CITE=\"); do NOT invent tokens.\n"
                "- Every issue must include at least one such citation token.\n"
                "- If evidence is insufficient for an issue, do not include that issue.\n\n"
            ),
        )
    )
    template_with_sep = advice_text_template.rstrip() + "\n\n"
    return (
        template_with_sep
        + f"QUESTION_ID={qid}\n\n"
        + "ORIGINAL QUESTION:\n"
        + f"{question_text}\n\n"
        + "DETERMINISTIC AUDIT ANSWER:\n"
        + f"{deterministic_answer}\n\n"
        + "EVIDENCE:\n"
        + f"{evidence}\n"
    )


# =============================================================================
# Plugin selection
# =============================================================================

def _select_plugins(pack: Pack) -> List[PackPlugin]:
    """Select plugins for this pack.

    Selection order (fail-closed when explicitly requested):
    1) pack.runner.plugin or pack.runner.plugins
       - 'none' / '' / null disables plugins explicitly
       - unknown plugin name => hard fail
    2) Back-compat heuristic: (engine=rsqt) AND (pack_type startswith rust_audit) => rsqt_guru
    """
    plugins: List[PackPlugin] = []

    runner = pack.runner or {}
    explicit = None
    if isinstance(runner, dict):
        if "plugins" in runner:
            explicit = runner.get("plugins")
        elif "plugin" in runner:
            explicit = runner.get("plugin")

    def norm(x: Any) -> str:
        return str(x).strip().lower()

    disable_aliases = {norm(x) for x in (PLUGIN_POLICY.get("disable_aliases") or ["", "none", "null", "no", "false", "off"])}
    known_plugins = {norm(x) for x in (PLUGIN_POLICY.get("known_plugins") or ["rsqt_guru"])}

    # Explicit disable
    if explicit is not None:
        if explicit is False:
            return []
        if explicit is None:
            return []
        if isinstance(explicit, str) and norm(explicit) in disable_aliases:
            return []
        names: List[str] = []
        if isinstance(explicit, str):
            names = [explicit.strip()]
        elif isinstance(explicit, list):
            names = [str(i).strip() for i in explicit if str(i).strip()]
        else:
            raise SystemExit(f"Invalid runner.plugin value (expected string/list): {explicit!r}")

        for name in names:
            key = norm(name)
            if key not in known_plugins:
                raise SystemExit(f"Unknown plugin requested by pack: {name}")
            if key == "rsqt_guru":
                if RsqtGuruPlugin is None:
                    raise SystemExit("Plugin rsqt_guru requested but plugins.rsqt_guru could not be imported")
                plugins.append(RsqtGuruPlugin())
        return plugins

    # Backward-compatible heuristic
    if RsqtGuruPlugin is not None:
        p = RsqtGuruPlugin()
        if p.applies(engine=pack.engine, pack_type=pack.pack_type):
            plugins.append(p)
    return plugins


# =============================================================================
# Runner core
# =============================================================================

def _run_single(
    *,
    pack_path: Path,
    pack: Pack,
    spec: EngineSpec,
    args: argparse.Namespace,
    parquet_path: Path,
    index_path: Path,
    out_dir: Path,
    all_specs: Dict[str, EngineSpec] | None = None,
) -> Tuple[int, Dict[str, Any]]:
    ensure_dir(out_dir)
    evidence_key_state = _initialize_runtime_evidence_keys(parquet_path=parquet_path, engine_name=spec.name)
    evidence_key_map_path = _write_evidence_key_map(out_dir=out_dir, parquet_path=parquet_path, engine_name=spec.name)
    run_t0 = time.perf_counter()
    _log_event(
        logging.INFO,
        "run.start",
        fn="_run_single",
        pack=str(pack_path),
        pack_type=pack.pack_type,
        engine=pack.engine,
        backend=args.backend,
        model=args.model or "(default)",
        parquet=str(parquet_path),
        index=str(index_path),
        out_dir=str(out_dir),
        mission_advice_gate=_is_mission_pack_type(pack.pack_type),
        evidence_audit=bool(getattr(args, "evidence_audit", EVIDENCE_AUDIT_ENABLED_DEFAULT)),
        engine_schema_source=evidence_key_state.get("engine_schema_source"),
        engine_columns_count=evidence_key_state.get("engine_columns_count"),
        parquet_schema_source=evidence_key_state.get("parquet_schema_source"),
        parquet_columns_count=evidence_key_state.get("parquet_columns_count"),
        dynamic_keys_enabled=evidence_key_state.get("dynamic_key_discovery_enabled"),
    )
    if evidence_key_state.get("engine_discovery_error"):
        _log_event(
            logging.WARNING,
            "engine.schema.discovery_error",
            fn="_run_single",
            engine=spec.name,
            error=evidence_key_state.get("engine_discovery_error"),
        )
    if evidence_key_state.get("schema_discovery_error"):
        _log_event(
            logging.WARNING,
            "parquet.schema.discovery_error",
            fn="_run_single",
            parquet=str(parquet_path),
            error=evidence_key_state.get("schema_discovery_error"),
        )
    if evidence_key_state.get("missing_required_semantic_categories"):
        _log_event(
            logging.WARNING,
            "engine.schema.missing_semantic_categories",
            fn="_run_single",
            engine=spec.name,
            missing=evidence_key_state.get("missing_required_semantic_categories"),
            required=evidence_key_state.get("required_semantic_categories"),
        )
    if evidence_key_state.get("fatal_schema_contract_error"):
        _log_event(
            logging.ERROR,
            "engine.schema.contract_gate_failed",
            fn="_run_single",
            engine=spec.name,
            error=evidence_key_state.get("fatal_schema_contract_error"),
            source=evidence_key_state.get("engine_schema_source"),
        )
        raise SystemExit(
            "Schema contract gate failed: "
            f"{evidence_key_state.get('fatal_schema_contract_error')}"
        )
    _log_event(
        logging.INFO,
        "evidence.keys.initialized",
        fn="_run_single",
        engine_schema_source=evidence_key_state.get("engine_schema_source"),
        engine_columns_count=evidence_key_state.get("engine_columns_count"),
        parquet_schema_source=evidence_key_state.get("parquet_schema_source"),
        parquet_columns_count=evidence_key_state.get("parquet_columns_count"),
        path_keys=len(_effective_path_keys()),
        line_keys=len(_effective_line_keys()),
        snippet_keys=len(_effective_snippet_keys()),
        iter_rows_keys=len(_effective_iter_rows_keys()),
        key_map=str(evidence_key_map_path),
    )
    repo_root = _find_repo_root(pack_path.parent)
    parquet_path_universe: set[str] = set()
    parquet_path_universe_meta: Dict[str, Any] = {
        "source": "(disabled)",
        "selected_columns": [],
        "errors": [],
        "truncated": False,
    }
    if bool(getattr(args, "evidence_audit", EVIDENCE_AUDIT_ENABLED_DEFAULT)) or PREFLIGHT_CORPUS_SCOPE_GATE_ENABLED:
        parquet_path_universe, parquet_path_universe_meta = _discover_parquet_path_universe(
            parquet_path=parquet_path,
            repo_root=repo_root,
        )
        _log_event(
            logging.INFO,
            "evidence.parquet_path_universe",
            fn="_run_single",
            parquet=str(parquet_path),
            source=parquet_path_universe_meta.get("source"),
            columns=parquet_path_universe_meta.get("selected_columns"),
            count=len(parquet_path_universe),
            truncated=parquet_path_universe_meta.get("truncated"),
            errors=parquet_path_universe_meta.get("errors") or [],
        )

    use_uv = not args.no_uv
    prefix = build_engine_prefix(spec, use_uv=use_uv, target_dir=parquet_path.parent if spec.target_dir_flag else None)
    engine_env_cache: Dict[str, Dict[str, str]] = {}

    def _env_for_engine(engine_name: str) -> Dict[str, str]:
        if engine_name not in engine_env_cache:
            env_map = _compute_runner_env_overrides(engine_name=engine_name)
            engine_env_cache[engine_name] = env_map
            if env_map:
                print(
                    f"Using runner env overrides for engine '{engine_name}': "
                    f"{', '.join(sorted(env_map.keys()))}"
                )
                _log_event(
                    logging.INFO,
                    "runner.env.overrides",
                    fn="_run_single._env_for_engine",
                    engine=engine_name,
                    keys=sorted(env_map.keys()),
                )
            else:
                _log_event(
                    logging.DEBUG,
                    "runner.env.overrides",
                    fn="_run_single._env_for_engine",
                    engine=engine_name,
                    keys=[],
                )
        return engine_env_cache[engine_name]

    main_engine_env = _env_for_engine(spec.name)

    prompt_grounding, prompt_analyze = _select_prompt_files(pack, args)
    _log_event(
        logging.INFO,
        "run.prompts.selected",
        fn="_run_single",
        grounding_prompt=str(prompt_grounding) if prompt_grounding else "(none)",
        analyze_prompt=str(prompt_analyze) if prompt_analyze else "(none)",
    )

    # SSOT: load default test-path patterns once per run from validator YAML
    default_test_path_patterns = _load_default_test_path_patterns(pack, pack_path)
    question_validators_cfg = (
        _load_question_validators_cfg(pack, pack_path)
        if pack.validation.apply_question_validators
        else None
    )

    report_lines: List[str] = []
    path_sample_items = max(1, int(getattr(args, "log_path_sample_items", DEFAULT_LOG_PATH_SAMPLE_ITEMS)))
    report_lines.append("# Pack Report\n")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    report_lines.append(f"Pack: {pack_path.name} (type={pack.pack_type} engine={pack.engine} v{pack.version})\n")
    report_lines.append(f"Backend: {args.backend}  Model: {args.model or '(default)'}\n")
    report_lines.append(f"Index: {index_path}\nParquet: {parquet_path}\n")
    mode_str = QUOTE_BYPASS_MODE_LABELS.get(args._effective_qb_mode, QUOTE_BYPASS_MODE_LABELS.get("off", "STANDARD"))
    report_lines.append(
        f"Mode: {mode_str}  cache_preflights={args.cache_preflights} "
        f"short_circuit_preflights={args.short_circuit_preflights} "
        f"adaptive_top_k={args.adaptive_top_k} chat_top_k_initial={args.chat_top_k_initial}\n"
    )
    if main_engine_env:
        report_lines.append(f"Runner env overrides: {', '.join(sorted(main_engine_env.keys()))}\n")
    report_lines.append(f"evidence_empty_gate={args.evidence_empty_gate}\n")
    report_lines.append(
        f"strict_fail_on_empty_evidence={STRICT_FAIL_ON_EMPTY_EVIDENCE} "
        f"strict_empty_evidence_fail_fast={STRICT_EMPTY_EVIDENCE_FAIL_FAST}\n"
    )
    report_lines.append(
        f"dynamic_key_discovery={DYNAMIC_KEY_DISCOVERY_ENABLED} "
        f"engine_schema_source={evidence_key_state.get('engine_schema_source')} "
        f"engine_columns={evidence_key_state.get('engine_columns_count')} "
        f"schema_source={evidence_key_state.get('parquet_schema_source')} "
        f"parquet_columns={evidence_key_state.get('parquet_columns_count')}\n"
    )
    report_lines.append(
        "engine_schema_contract: "
        f"required={DYNAMIC_REQUIRE_ENGINE_SCHEMA_CONTRACT} "
        f"loaded={evidence_key_state.get('engine_schema_contract_loaded')} "
        f"version={evidence_key_state.get('engine_schema_contract_version') or '(unknown)'} "
        f"strict_missing_semantics={DYNAMIC_FAIL_ON_MISSING_SEMANTIC_CATEGORIES}\n"
    )
    report_lines.append(
        f"effective_evidence_keys: paths={len(_effective_path_keys())} "
        f"lines={len(_effective_line_keys())} snippets={len(_effective_snippet_keys())} "
        f"iter_rows={len(_effective_iter_rows_keys())}\n"
    )
    report_lines.append(
        f"preflight.filtered_to_zero_fail={PREFLIGHT_FILTERED_TO_ZERO_FAIL_ENABLED} "
        f"threshold={PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD} "
        f"fail_fast={PREFLIGHT_FILTERED_TO_ZERO_FAIL_FAST}\n"
    )
    report_lines.append(
        f"preflight.corpus_scope_gate={PREFLIGHT_CORPUS_SCOPE_GATE_ENABLED} "
        f"require_path_universe={PREFLIGHT_CORPUS_SCOPE_REQUIRE_PATH_UNIVERSE} "
        f"forbidden_regex_count={len(PREFLIGHT_CORPUS_SCOPE_FORBIDDEN_REGEX)} "
        f"fail_fast={PREFLIGHT_CORPUS_SCOPE_GATE_FAIL_FAST}\n"
    )
    report_lines.append(f"Evidence key map: {evidence_key_map_path.name}\n")
    if getattr(args, "_run_log_file", None):
        report_lines.append(f"Run log: {args._run_log_file}\n")
    report_lines.append(f"Prompts: grounding={prompt_grounding.name if prompt_grounding else '(none)'} analyze={prompt_analyze.name if prompt_analyze else '(none)'}\n\n")

    fatal_contract_issues: List[str] = []
    fatal_advice_gate_issues: List[str] = []
    mission_advice_gate_enabled = _is_mission_pack_type(pack.pack_type)
    report_lines.append(f"mission_advice_gate_enabled={mission_advice_gate_enabled}\n")

    if PREFLIGHT_CORPUS_SCOPE_GATE_ENABLED:
        forbidden_patterns = _coerce_transform_regex_list(PREFLIGHT_CORPUS_SCOPE_FORBIDDEN_REGEX)
        forbidden_regexes = _compile_transform_regexes(
            forbidden_patterns,
            context="corpus_scope_gate.forbidden_path_regex",
        )
        gate_sample_items = max(1, int(PREFLIGHT_CORPUS_SCOPE_SAMPLE_ITEMS))
        if PREFLIGHT_CORPUS_SCOPE_REQUIRE_PATH_UNIVERSE and not parquet_path_universe:
            msg = (
                "Corpus scope gate failed: parquet path universe is empty/unavailable; "
                "cannot verify contamination denylist. Rebuild corpus/index and rerun."
            )
            report_lines.append("- ⛔ Corpus scope gate: parquet path universe unavailable\n")
            fatal_contract_issues.append(f"CORPUS_SCOPE_GATE: {msg}")
            _log_event(
                logging.ERROR,
                "corpus.scope_gate.path_universe_unavailable",
                fn="_run_single",
                parquet=str(parquet_path),
                source=parquet_path_universe_meta.get("source"),
                errors=parquet_path_universe_meta.get("errors") or [],
            )
            if PREFLIGHT_CORPUS_SCOPE_GATE_FAIL_FAST:
                try:
                    partial_report_path = out_dir / REPORT_FILE
                    write_text(partial_report_path, "".join(report_lines))
                    _log_event(
                        logging.ERROR,
                        "run.abort.corpus_scope_gate",
                        fn="_run_single",
                        reason="path_universe_unavailable",
                        partial_report=str(partial_report_path),
                    )
                except Exception:
                    pass
                raise SystemExit(2)
        elif parquet_path_universe and forbidden_regexes:
            contaminated = sorted(
                p for p in parquet_path_universe if any(rx.search(p) for rx in forbidden_regexes)
            )
            if contaminated:
                sample = contaminated[:gate_sample_items]
                msg = (
                    "Corpus scope gate failed: parquet/index contains denied artifact paths "
                    "(likely run-artifact contamination such as audit_runs/xref_state). "
                    "Rebuild corpus/index with hard excludes before running optimization."
                )
                report_lines.append(
                    f"- ⛔ Corpus scope gate: contaminated path universe ({len(contaminated)} matches)\n"
                )
                report_lines.append(
                    f"  - Sample contaminated paths: {', '.join(sample)}\n"
                )
                fatal_contract_issues.append(f"CORPUS_SCOPE_GATE: {msg}")
                _log_event(
                    logging.ERROR,
                    "corpus.scope_gate.failed",
                    fn="_run_single",
                    parquet=str(parquet_path),
                    forbidden_patterns=forbidden_patterns[:8],
                    contaminated_count=len(contaminated),
                    contaminated_sample=sample,
                )
                if PREFLIGHT_CORPUS_SCOPE_GATE_FAIL_FAST:
                    try:
                        partial_report_path = out_dir / REPORT_FILE
                        write_text(partial_report_path, "".join(report_lines))
                        _log_event(
                            logging.ERROR,
                            "run.abort.corpus_scope_gate",
                            fn="_run_single",
                            reason="contaminated_parquet_path_universe",
                            partial_report=str(partial_report_path),
                        )
                    except Exception:
                        pass
                    raise SystemExit(2)

    if pack.validation.minimum_questions and len(pack.questions) < pack.validation.minimum_questions:
        raise SystemExit(f"Pack validation failed: minimum_questions={pack.validation.minimum_questions} but pack has {len(pack.questions)}")

    if mission_advice_gate_enabled and ADVICE_REQUIRE_LLM_MODE:
        non_llm_advice_qids = [q.id for q in pack.questions if q.advice_mode != "llm"]
        if non_llm_advice_qids:
            raise SystemExit(
                "Mission advice gate failed: advice_mode=llm is required for all mission questions. "
                f"Found non-llm advice_mode in: {non_llm_advice_qids}"
            )

    allowed_citations_by_q: Dict[str, set] = {}
    question_runtime_stats: List[Dict[str, Any]] = []
    evidence_audit_rows: List[Dict[str, Any]] = []
    preflight_sig_cache: Dict[str, Path] = {}

    total_questions = len(pack.questions)
    for q_idx, q in enumerate(pack.questions, start=1):
        q_t0 = time.perf_counter()
        schema_retry_count = 0
        adaptive_rerun_count = 0
        advice_retry_count = 0
        advice_rc: int | str | None = None
        advice_text: str = ""
        advice_quality_issues: List[str] = []
        validator_section_opened = False
        advice_validator_section_opened = False
        _log_event(
            logging.INFO,
            "question.start",
            fn="_run_single",
            question_num=f"{q_idx}/{total_questions}",
            qid=q.id,
            title=q.title,
            answer_mode=q.answer_mode,
            advice_mode=q.advice_mode,
            mission_advice_gate=mission_advice_gate_enabled,
            question_preview=_compact_log_text(
                q.question,
                max_chars=max(32, int(getattr(args, "log_question_max_chars", DEFAULT_LOG_QUESTION_MAX_CHARS))),
            ),
        )
        report_lines.append(f"\n## {q.id}: {q.title}\n")
        report_lines.append(f"\n**Question:**\n\n{q.question}\n")
        report_lines.append(
            f"- Question modes: answer_mode={q.answer_mode} advice_mode={q.advice_mode}\n"
        )

        preflights = q.preflight or []
        evidence_blocks: List[str] = []
        preflight_filtered_to_zero_failures: List[Dict[str, Any]] = []

        dependency_issues = _validate_group_by_path_dependencies(preflights)
        if dependency_issues:
            msg = (
                "Preflight transform dependency validation failed: "
                "group_by_path_top_n.from must reference a prior step in the same question."
            )
            if not validator_section_opened:
                report_lines.append("\n**Validator issues:**\n\n")
                validator_section_opened = True
            report_lines.append(f"- Evidence gate: {msg}\n")
            for it in dependency_issues[: int(ISSUE_CAPS.get("unknown_paths", 10))]:
                report_lines.append(f"- Preflight transform: {it}\n")
            fatal_contract_issues.extend([f"{q.id}: {it}" for it in dependency_issues])
            _log_event(
                logging.ERROR,
                "question.preflight.transform_dependency.invalid",
                fn="_run_single",
                qid=q.id,
                issue_count=len(dependency_issues),
                sample=dependency_issues[:3],
            )
            try:
                partial_report_path = out_dir / REPORT_FILE
                write_text(partial_report_path, "".join(report_lines))
                _log_event(
                    logging.ERROR,
                    "run.abort.preflight_transform_dependency",
                    fn="_run_single",
                    qid=q.id,
                    partial_report=str(partial_report_path),
                )
            except Exception:
                pass
            raise SystemExit(2)

        # Preflights
        for step in preflights:
            if not isinstance(step, dict):
                continue
            name = step.get("name")
            cmd = step.get("cmd")
            if not name or not isinstance(cmd, list):
                continue

            out_file = out_dir / f"{q.id}_{name}.json"

            # engine_override: use a different engine's prefix for this preflight step
            step_spec = spec
            step_prefix = prefix
            override_engine = step.get("engine_override")
            if override_engine and all_specs and override_engine in all_specs:
                step_spec = all_specs[override_engine]
                step_prefix = build_engine_prefix(step_spec, use_uv=use_uv, target_dir=parquet_path.parent if step_spec.target_dir_flag else None)
            step_env = _env_for_engine(step_spec.name)

            argv_exact = build_engine_preflight_argv(
                step_spec,
                step_prefix,
                cmd=cmd,
                index_path=index_path,
                parquet_path=parquet_path,
            )
            sig = build_artifact_signature(argv=argv_exact, inputs=[pack_path, parquet_path, index_path])
            cache_hit_file = preflight_sig_cache.get(sig)
            _log_event(
                logging.INFO,
                "preflight.step.start",
                fn="_run_single",
                qid=q.id,
                step=name,
                engine=step_spec.name,
                cmd=_shell_join(argv_exact, max_chars=640),
                cached=bool(
                    (args.cache_preflights and out_file.exists())
                    or (cache_hit_file is not None and cache_hit_file.exists())
                ),
            )

            if args.cache_preflights and out_file.exists():
                try:
                    existing = load_json_file(out_file)
                    if existing.get("_sig") == sig:
                        report_lines.append(f"- Preflight `{name}`: cached → {out_file.name}\n")
                        if (
                            args.short_circuit_preflights
                            and bool(step.get("stop_if_nonempty"))
                            and int(existing.get("returncode", 1)) == 0
                            and _has_preflight_hits(existing.get("stdout"))
                        ):
                            report_lines.append("  - ⤷ short-circuit: stop_if_nonempty (cached)\n")
                            _log_event(
                                logging.INFO,
                                "preflight.step.short_circuit",
                                fn="_run_single",
                                qid=q.id,
                                step=name,
                                cached=True,
                            )
                            break
                        _log_event(
                            logging.INFO,
                            "preflight.step.cached",
                            fn="_run_single",
                            qid=q.id,
                            step=name,
                            returncode=existing.get("returncode"),
                            artifact=str(out_file),
                        )
                        preflight_sig_cache[sig] = out_file
                        continue
                except Exception:
                    pass

            if (not out_file.exists()) and cache_hit_file and cache_hit_file.exists():
                try:
                    shutil.copy2(cache_hit_file, out_file)
                    existing = load_json_file(out_file)
                    if existing.get("_sig") == sig:
                        report_lines.append(f"- Preflight `{name}`: cached(sig) → {out_file.name}\n")
                        if (
                            args.short_circuit_preflights
                            and bool(step.get("stop_if_nonempty"))
                            and int(existing.get("returncode", 1)) == 0
                            and _has_preflight_hits(existing.get("stdout"))
                        ):
                            report_lines.append("  - ⤷ short-circuit: stop_if_nonempty (cached)\n")
                            _log_event(
                                logging.INFO,
                                "preflight.step.short_circuit",
                                fn="_run_single",
                                qid=q.id,
                                step=name,
                                cached=True,
                            )
                            break
                        _log_event(
                            logging.INFO,
                            "preflight.step.cached",
                            fn="_run_single",
                            qid=q.id,
                            step=name,
                            returncode=existing.get("returncode"),
                            artifact=str(out_file),
                        )
                        preflight_sig_cache[sig] = out_file
                        continue
                except Exception:
                    pass

            res = run_engine_preflight(
                step_spec,
                step_prefix,
                cmd=cmd,
                index_path=index_path,
                parquet_path=parquet_path,
                env_overrides=step_env,
            )
            parsed_stdout = parse_json_maybe(res.stdout) if str(res.stdout).lstrip().startswith(("{", "[")) else None
            stdout_data = parsed_stdout if parsed_stdout is not None else res.stdout
            _learn_runtime_keys_from_payload(stdout_data)
            stdout_row_est = len(_iter_rows(stdout_data)) if isinstance(stdout_data, (dict, list)) else 0
            art = {
                "_sig": sig,
                "argv": res.argv,
                "returncode": res.returncode,
                "stdout": stdout_data,
                "stderr": res.stderr,
            }
            write_json(out_file, art)
            preflight_sig_cache[sig] = out_file
            report_lines.append(f"- Preflight `{name}`: rc={res.returncode} → {out_file.name}\n")
            _log_event(
                logging.INFO,
                "preflight.step.done",
                fn="_run_single",
                qid=q.id,
                step=name,
                returncode=res.returncode,
                artifact=str(out_file),
                stdout_row_est=stdout_row_est,
                stdout_chars=len(res.stdout or ""),
                path_keys=len(_effective_path_keys()),
                line_keys=len(_effective_line_keys()),
                snippet_keys=len(_effective_snippet_keys()),
            )
            if res.returncode != 0:
                report_lines.append("  - ⚠️ preflight failed (see stderr in artifact)\n")
                _log_event(
                    logging.WARNING,
                    "preflight.step.failed",
                    fn="_run_single",
                    qid=q.id,
                    step=name,
                    stderr_preview=_compact_log_text(res.stderr or "", max_chars=DEFAULT_LOG_FIELD_MAX_CHARS),
                )
            elif (
                args.short_circuit_preflights
                and bool(step.get("stop_if_nonempty"))
                and _has_preflight_hits(stdout_data)
            ):
                report_lines.append("  - ⤷ short-circuit: stop_if_nonempty\n")
                _log_event(
                    logging.INFO,
                    "preflight.step.short_circuit",
                    fn="_run_single",
                    qid=q.id,
                    step=name,
                    cached=False,
                )
                break

        # Build cross-preflight context for group_by_path_top_n transforms.
        # Materialize filtered rows keyed by step name so downstream
        # deterministic synthesizers/plugins consume the same normalized
        # preflight data that was injected into prompts.
        preflight_rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for _pstep in preflights:
            _pname = _pstep.get("name") if isinstance(_pstep, dict) else None
            if not _pname:
                continue
            _part_path = out_dir / f"{q.id}_{_pname}.json"
            if _part_path.exists():
                try:
                    _part_data = load_json_file(_part_path)
                    if _part_data.get("returncode") == 0:
                        _stdout = _part_data.get("stdout", [])
                        _learn_runtime_keys_from_payload(_stdout)
                        _rows = _iter_rows(_stdout)
                        _raw_count = len(_rows)
                        _raw_paths = _unique_paths(_rows)
                        _row_container_key = _detect_row_container_key(_stdout) if isinstance(_stdout, dict) else None
                        _t = _pstep.get("transform") if isinstance(_pstep, dict) else None
                        if isinstance(_t, dict):
                            _t = dict(_t)
                            _t.setdefault("_default_test_path_patterns", default_test_path_patterns)
                            _t.setdefault("_default_exclude_path_regex", DEFAULT_EXCLUDE_PATH_REGEX)
                        else:
                            _t = {
                                "_default_test_path_patterns": default_test_path_patterns,
                                "_default_exclude_path_regex": DEFAULT_EXCLUDE_PATH_REGEX,
                            }
                        _rows = _apply_transform_filters(_rows, _t, ctx={"preflight_rows_by_name": preflight_rows_by_name})
                        _new_paths = _unique_paths(_rows)
                        _new_path_set = set(_new_paths)
                        _dropped_paths = [p for p in _raw_paths if p not in _new_path_set]
                        _filter_diag = _summarize_transform_filters(_t)
                        preflight_rows_by_name[_pname] = _rows

                        # Persist filtered stdout so plugins/deterministic synthesis
                        # read normalized artifacts instead of raw unfiltered hits.
                        if isinstance(_stdout, (dict, list)):
                            _new_count = len(_rows)
                            if (_raw_count != _new_count) or (_part_data.get("_stdout_filtered") is not True):
                                if "stdout_raw" not in _part_data:
                                    _part_data["stdout_raw"] = _stdout
                                _part_data["stdout"] = _replace_rows_in_stdout(
                                    _stdout,
                                    _rows,
                                    row_container_key=_row_container_key,
                                    filtered_to_zero=bool(_raw_count > 0 and _new_count == 0),
                                )
                                _part_data["_stdout_filtered"] = True
                                _part_data["_stdout_rows_before_filter_count"] = int(_raw_count)
                                _part_data["_stdout_rows_after_filter_count"] = int(_new_count)
                                write_json(_part_path, _part_data)
                                _log_event(
                                    logging.INFO,
                                    "preflight.step.filtered",
                                    fn="_run_single",
                                    qid=q.id,
                                    step=_pname,
                                    rows_before=_raw_count,
                                    rows_after=_new_count,
                                    unique_paths_before=len(_raw_paths),
                                    unique_paths_after=len(_new_paths),
                                    dropped_paths=len(_dropped_paths),
                                    path_sample_before=_raw_paths[:path_sample_items],
                                    path_sample_after=_new_paths[:path_sample_items],
                                    dropped_path_sample=_dropped_paths[:path_sample_items],
                                    artifact=str(_part_path),
                                    **_filter_diag,
                                )
                                if _raw_count > 0 and _new_count == 0:
                                    _log_event(
                                        logging.WARNING,
                                        "preflight.step.filtered_to_zero",
                                        fn="_run_single",
                                        qid=q.id,
                                        step=_pname,
                                        rows_before=_raw_count,
                                        unique_paths_before=len(_raw_paths),
                                        dropped_paths=len(_dropped_paths),
                                        dropped_path_sample=_dropped_paths[:path_sample_items],
                                        artifact=str(_part_path),
                                        **_filter_diag,
                                    )
                except Exception:
                    pass
        transform_ctx: Dict[str, Any] = {"preflight_rows_by_name": preflight_rows_by_name}

        # Evidence injection from preflight artifacts (BC-001: field-aware)
        usable_evidence_blocks = 0
        for step in preflights:
            name = step.get("name") if isinstance(step, dict) else None
            if not name:
                continue
            art_path = out_dir / f"{q.id}_{name}.json"
            if not art_path.exists():
                continue
            try:
                art_data = load_json_file(art_path)
                if art_data.get("returncode") != 0:
                    continue
                stdout_data = art_data.get("stdout")
                stdout_already_filtered = bool(art_data.get("_stdout_filtered"))
                _learn_runtime_keys_from_payload(stdout_data)
                if not _nonempty_stdout(stdout_data):
                    continue

                # ---------------------------------------------------------
                # DOC_SUMMARY injection (deterministic, citeable)
                #
                # Problem: doc evidence triggers "INSUFFICIENT EVIDENCE"
                # refusal when model sees a truncated list. We precompute
                # summary counts from the full JSON to give stable facts.
                # Fields are explicitly *_shown to prevent repo-global claims.
                # ---------------------------------------------------------
                if name == "doc_analysis" and isinstance(stdout_data, dict):
                    cite_tok = f"{q.id}_{name}.json:1"
                    ents = stdout_data.get("entities")
                    mods = stdout_data.get("module_docs")

                    # Capture configured max_items for transparency
                    max_items_cfg = None
                    if isinstance(step, dict) and isinstance(step.get("transform"), dict):
                        mi = step["transform"].get("max_items")
                        try:
                            max_items_cfg = int(mi) if mi is not None else None
                        except Exception:
                            max_items_cfg = None

                    if isinstance(ents, list):
                        entities_shown = len(ents)
                        pub_shown = pub_doc_shown = pub_undoc_shown = 0
                        pub_crate_shown = private_shown = other_vis_shown = 0

                        for e in ents:
                            if not isinstance(e, dict):
                                continue
                            v = (e.get("visibility") or "").strip()
                            if v == "pub":
                                pub_shown += 1
                                d = e.get("doc")
                                has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
                                if has_doc:
                                    pub_doc_shown += 1
                                else:
                                    pub_undoc_shown += 1
                            elif v == "pub(crate)":
                                pub_crate_shown += 1
                            elif v == "private":
                                private_shown += 1
                            else:
                                other_vis_shown += 1

                        module_docs_shown = module_docs_with_shown = module_docs_without_shown = 0
                        if isinstance(mods, list):
                            module_docs_shown = len(mods)
                            for m in mods:
                                if isinstance(m, dict) and bool(m.get("has_doc")):
                                    module_docs_with_shown += 1
                                else:
                                    module_docs_without_shown += 1

                        mi_part = f"max_items_configured={max_items_cfg}" if max_items_cfg is not None else "max_items_configured=(unset)"
                        summary = (
                            f"DOC_SUMMARY: entities_shown={entities_shown} {mi_part} "
                            f"pub_shown={pub_shown} pub_doc_shown={pub_doc_shown} pub_undoc_shown={pub_undoc_shown} "
                            f"pub_crate_shown={pub_crate_shown} private_shown={private_shown} other_vis_shown={other_vis_shown} "
                            f"module_docs_shown={module_docs_shown} module_docs_with_shown={module_docs_with_shown} module_docs_without_shown={module_docs_without_shown}"
                        )
                    else:
                        # Fallback: schema mismatch — still inject citeable marker
                        mi_part = f"max_items_configured={max_items_cfg}" if max_items_cfg is not None else "max_items_configured=(unset)"
                        summary = f"DOC_SUMMARY: unavailable (schema mismatch: missing entities list) {mi_part}"

                    evidence_blocks.append(f"[Preflight DOC_SUMMARY]:\nCITE={cite_tok}\n{summary}")

                # Read step-level render controls from pack YAML
                step_render = step.get("render", DEFAULT_RENDER_MODE)
                step_fence = step.get("fence_lang", "")
                step_max = step.get("block_max_chars", args.preflight_max_chars)

                # Honor transform: filters, limits, render override
                transform = step.get("transform") or {}
                if isinstance(transform, dict):
                    transform = dict(transform)
                else:
                    transform = {}
                transform.setdefault("_default_test_path_patterns", default_test_path_patterns)
                transform.setdefault("_default_exclude_path_regex", DEFAULT_EXCLUDE_PATH_REGEX)
                rows = _iter_rows(stdout_data)
                pre_filter_count = _safe_int(art_data.get("_stdout_rows_before_filter_count"), len(rows))
                row_container_key = (
                    str(art_data.get("_stdout_row_container_key"))
                    if isinstance(art_data.get("_stdout_row_container_key"), str)
                    and str(art_data.get("_stdout_row_container_key")).strip()
                    else (_detect_row_container_key(stdout_data) if isinstance(stdout_data, dict) else None)
                )
                if rows:
                    # Apply semantic filters only when artifact has not already been normalized.
                    if not stdout_already_filtered:
                        rows = _apply_transform_filters(rows, transform, ctx=transform_ctx)

                    t_max_items = transform.get("max_items")
                    if t_max_items and isinstance(t_max_items, int):
                        rows = rows[:t_max_items]

                    t_max_chars = transform.get("max_chars")
                    if t_max_chars and isinstance(t_max_chars, int):
                        step_max = t_max_chars  # transform max_chars overrides default

                    stdout_data = _replace_rows_in_stdout(
                        stdout_data,
                        rows,
                        row_container_key=row_container_key,
                        filtered_to_zero=bool(pre_filter_count > 0 and len(rows) == 0),
                    )

                # transform.render overrides step-level render
                if transform.get("render"):
                    step_render = transform["render"]

                step_has_usable_evidence = _has_preflight_hits(stdout_data)
                if step_has_usable_evidence:
                    usable_evidence_blocks += 1
                elif (
                    PREFLIGHT_FILTERED_TO_ZERO_FAIL_ENABLED
                    and pre_filter_count >= PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD
                ):
                    filter_diag = _summarize_transform_filters(transform)
                    preflight_filtered_to_zero_failures.append(
                        {
                            "step": str(name),
                            "rows_before": int(pre_filter_count),
                            "filters": filter_diag,
                        }
                    )
                    _log_event(
                        logging.ERROR,
                        "preflight.step.filtered_to_zero.fail_gate",
                        fn="_run_single",
                        qid=q.id,
                        step=name,
                        rows_before=pre_filter_count,
                        threshold=PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD,
                        artifact=str(art_path),
                        **filter_diag,
                    )

                # If all rows were filtered away, emit a clear "0 results" block
                if pre_filter_count > 0 and not step_has_usable_evidence:
                    filters_used = [k for k in TRANSFORM_FILTER_KEYS if transform.get(k)]
                    block = f"[{name}] 0 results (filtered {pre_filter_count} raw hits; filters: {', '.join(filters_used)})"
                else:
                    block = format_evidence_block(
                        str(name), stdout_data,
                        max_chars=step_max,
                        render_mode=step_render,
                        fence_lang=step_fence,
                    )
                cite_tok = f"{q.id}_{name}.json:1"
                evidence_blocks.append(
                    f"[Preflight {name}]:\nCITE={cite_tok}\n{block}"
                )
            except Exception:
                continue

        use_quote_bypass = (
            (args._effective_qb_mode == "on")
            or (args._effective_qb_mode == "auto" and usable_evidence_blocks > 0)
        )
        evidence_is_empty = (usable_evidence_blocks == 0)
        _log_event(
            logging.INFO,
            "question.evidence.summary",
            fn="_run_single",
            qid=q.id,
            preflight_steps=len(preflights),
            evidence_blocks=len(evidence_blocks),
            evidence_usable_blocks=usable_evidence_blocks,
            quote_bypass_mode=args._effective_qb_mode,
            use_quote_bypass=use_quote_bypass,
            evidence_empty=evidence_is_empty,
            path_keys=len(_effective_path_keys()),
            line_keys=len(_effective_line_keys()),
            snippet_keys=len(_effective_snippet_keys()),
            iter_rows_keys=len(_effective_iter_rows_keys()),
        )
        _write_evidence_key_map(out_dir=out_dir, parquet_path=parquet_path, engine_name=spec.name)

        # Citation provenance: build allowed token set from injected evidence (optional).
        if pack.validation.enforce_citations_from_evidence:
            allowed_citations_by_q[q.id] = _extract_allowed_citation_tokens(evidence_blocks)

        augmented_question = _build_augmented_question(q.question, evidence_blocks, quote_bypass=use_quote_bypass, response_schema=pack.response_schema)
        augmented_prompt_path = out_dir / f"{q.id}_augmented_prompt.md"
        write_text(augmented_prompt_path, augmented_question)
        question_evidence_audit: Dict[str, Any] | None = None
        question_evidence_audit_path: Path | None = None
        question_evidence_summary_row: Dict[str, Any] | None = None
        if bool(getattr(args, "evidence_audit", EVIDENCE_AUDIT_ENABLED_DEFAULT)):
            question_evidence_audit = _build_question_evidence_audit(
                qid=q.id,
                title=q.title,
                question_text=q.question,
                answer_mode=q.answer_mode,
                advice_mode=q.advice_mode,
                quote_bypass_mode=args._effective_qb_mode,
                evidence_blocks=evidence_blocks,
                preflight_steps=[s for s in preflights if isinstance(s, dict)],
                parquet_path_universe=parquet_path_universe,
                parquet_path_meta=parquet_path_universe_meta,
                repo_root=repo_root,
            )
            question_evidence_audit["evidence_usable_blocks_count"] = int(usable_evidence_blocks)
            question_evidence_audit["evidence_empty"] = bool(evidence_is_empty)
            question_evidence_audit.setdefault("artifacts", {})
            question_evidence_audit["artifacts"]["augmented_prompt"] = str(augmented_prompt_path.name)
            question_evidence_audit_path = _write_question_evidence_audit(
                out_dir=out_dir,
                qid=q.id,
                audit=question_evidence_audit,
            )
            question_evidence_summary_row = {
                "qid": q.id,
                "evidence_blocks_count": int(question_evidence_audit.get("evidence_blocks_count", 0)),
                "evidence_usable_blocks_count": int(question_evidence_audit.get("evidence_usable_blocks_count", 0)),
                "evidence_paths_count": int(question_evidence_audit.get("evidence_paths_count", 0)),
                "paths_missing_from_parquet_count": int(
                    question_evidence_audit.get("paths_missing_from_parquet_count", 0)
                ),
                "artifact": str(question_evidence_audit_path.name),
            }
            evidence_audit_rows.append(question_evidence_summary_row)
            _log_event(
                logging.INFO,
                "question.evidence.audit",
                fn="_run_single",
                qid=q.id,
                artifact=str(question_evidence_audit_path),
                evidence_blocks=question_evidence_audit.get("evidence_blocks_count"),
                evidence_usable_blocks=question_evidence_audit.get("evidence_usable_blocks_count"),
                evidence_paths=question_evidence_audit.get("evidence_paths_count"),
                paths_missing_from_parquet=question_evidence_audit.get("paths_missing_from_parquet_count"),
                path_match_count=question_evidence_audit.get("paths_matched_to_parquet_count"),
                path_missing_sample=question_evidence_audit.get("paths_missing_from_parquet_sample"),
            )

        if preflight_filtered_to_zero_failures:
            steps = ", ".join(f"{f.get('step')}({f.get('rows_before')})" for f in preflight_filtered_to_zero_failures)
            msg = (
                "Preflight starvation gate failed: at least one preflight returned high raw-hit volume but "
                f"filters collapsed to zero usable evidence (threshold={PREFLIGHT_FILTERED_TO_ZERO_FAIL_RAW_ROWS_THRESHOLD}). "
                "This indicates corpus pollution or wrong scope; question execution aborted."
            )
            report_lines.append(
                f"- ⛔ Preflight starvation gate: {len(preflight_filtered_to_zero_failures)} step(s) collapsed to zero "
                f"after filtering ({steps})\n"
            )
            if not validator_section_opened:
                report_lines.append("\n**Validator issues:**\n\n")
                validator_section_opened = True
            report_lines.append(f"- Evidence gate: {msg}\n")
            fatal_contract_issues.append(f"{q.id}: {msg}")
            _log_event(
                logging.ERROR,
                "question.preflight.filtered_to_zero.fail_fast",
                fn="_run_single",
                qid=q.id,
                failures=preflight_filtered_to_zero_failures[:path_sample_items],
                fail_fast=PREFLIGHT_FILTERED_TO_ZERO_FAIL_FAST,
            )
            if PREFLIGHT_FILTERED_TO_ZERO_FAIL_FAST:
                try:
                    partial_report_path = out_dir / REPORT_FILE
                    write_text(partial_report_path, "".join(report_lines))
                    _log_event(
                        logging.ERROR,
                        "run.abort.preflight_filtered_to_zero",
                        fn="_run_single",
                        qid=q.id,
                        partial_report=str(partial_report_path),
                    )
                except Exception:
                    pass
                raise SystemExit(2)

        def _compute_schema_issues_local(answer_text: str) -> List[str]:
            issues_local = validate_response_schema(answer_text or "", pack.validation)
            if pack.validation.enforce_citations_from_evidence:
                allowed = allowed_citations_by_q.get(q.id, set())
                provenance_issues = validate_citations_from_evidence(answer_text or "", allowed=allowed)
                for it in provenance_issues:
                    issues_local.append(f"Citation provenance: {it}")
            gate_issues = validate_path_gates(answer_text or "", evidence_blocks, pack.validation)
            for it in gate_issues:
                issues_local.append(f"Path gates: {it}")
            return issues_local

        top_k_max = q.top_k if q.top_k is not None else int((q.chat or {}).get("top_k", pack.defaults.chat_top_k))
        top_k_max = max(1, int(top_k_max))
        top_k = 0
        question_chat_cfg = q.chat or {}
        strict_response_template = str(question_chat_cfg.get("strict_response_template") or "").strip()
        retry_on_schema_fail = bool(question_chat_cfg.get("retry_on_schema_fail", False))
        try:
            schema_retry_attempts = int(question_chat_cfg.get("schema_retry_attempts", 0) or 0)
        except Exception:
            schema_retry_attempts = 0
        schema_retry_attempts = max(0, schema_retry_attempts)
        if schema_retry_attempts > 0:
            retry_on_schema_fail = True

        # Strict evidence presence gate: do not continue a question without extracted evidence.
        if STRICT_FAIL_ON_EMPTY_EVIDENCE and evidence_is_empty:
            msg = (
                "No usable deterministic evidence extracted for question (all preflight outputs empty or "
                "filtered_to_zero); strict evidence gate requires evidence-backed analysis and aborts this run."
            )
            report_lines.append(
                "- ⛔ Strict evidence gate: no usable deterministic evidence extracted "
                "(model/advice skipped, run aborted)\n"
            )
            if not validator_section_opened:
                report_lines.append("\n**Validator issues:**\n\n")
                validator_section_opened = True
            report_lines.append(f"- Evidence gate: {msg}\n")
            fatal_contract_issues.append(f"{q.id}: {msg}")
            _log_event(
                logging.ERROR,
                "question.evidence.empty.fail_fast",
                fn="_run_single",
                qid=q.id,
                fail_fast=STRICT_EMPTY_EVIDENCE_FAIL_FAST,
                quote_bypass_mode=args._effective_qb_mode,
            )
            if STRICT_EMPTY_EVIDENCE_FAIL_FAST:
                try:
                    partial_report_path = out_dir / REPORT_FILE
                    write_text(partial_report_path, "".join(report_lines))
                    _log_event(
                        logging.ERROR,
                        "run.abort.empty_evidence",
                        fn="_run_single",
                        qid=q.id,
                        partial_report=str(partial_report_path),
                    )
                except Exception:
                    pass
                raise SystemExit(2)

        # Chat / model call (or deterministic synthesis)
        if q.answer_mode == "deterministic":
            report_lines.append("- Deterministic answer_mode: model call skipped by question config.\n")
            _log_event(
                logging.INFO,
                "question.answer.skip",
                fn="_run_single",
                qid=q.id,
                reason="deterministic_answer_mode",
            )
            det_answer = ""
            if callable(synthesize_deterministic_answer):
                try:
                    det_answer = str(synthesize_deterministic_answer(q.id, out_dir) or "")
                except Exception:
                    det_answer = ""
            if not det_answer.strip():
                det_answer = _build_deterministic_seed_answer(q.id, evidence_blocks)
            chat_obj = {
                "answer": det_answer,
                "sources": [],
                "_deterministic_answer": True,
                "_deterministic_reason": "question.answer_mode=deterministic",
            }
            chat_res = CmdResult(argv=[], returncode=0, stdout=json.dumps(chat_obj), stderr="")
            chat_json = chat_obj
        elif args.evidence_empty_gate and evidence_is_empty:
            report_lines.append(f"- ⛔ Evidence-empty gate: no deterministic evidence extracted → NOT FOUND (model call skipped)\n")
            _log_event(
                logging.WARNING,
                "question.answer.skip",
                fn="_run_single",
                qid=q.id,
                reason="evidence_empty_gate",
                quote_bypass_mode=args._effective_qb_mode,
            )
            chat_obj = {
                "answer": str(
                    PROMPTS.get(
                        "evidence_empty_answer",
                        "**NOT FOUND**\n\nDeterministic evidence extraction returned no results. Model call skipped.",
                    )
                ),
                "sources": [],
                "_evidence_empty_gated": True,
            }
            chat_res = CmdResult(argv=[], returncode=0, stdout=json.dumps(chat_obj), stderr="")
            chat_json = chat_obj
        else:
            if use_quote_bypass:
                bypass_prompt = _build_quote_bypass_prompt(q.question, evidence_blocks, response_schema=pack.response_schema)
                bypass_prompt_path = out_dir / f"{q.id}_bypass_prompt.md"
                write_text(bypass_prompt_path, bypass_prompt)
                prompt_file = prompt_analyze
                qtext = bypass_prompt
                prompt_mode = "analyze_only"
                if question_evidence_audit is not None:
                    question_evidence_audit.setdefault("artifacts", {})
                    question_evidence_audit["artifacts"]["bypass_prompt"] = str(bypass_prompt_path.name)
            else:
                prompt_file = prompt_grounding
                qtext = augmented_question
                prompt_mode = "grounding"
                if question_evidence_audit is not None:
                    question_evidence_audit.setdefault("artifacts", {})
                    question_evidence_audit["artifacts"]["grounding_prompt"] = str(augmented_prompt_path.name)

            if strict_response_template:
                qtext = _inject_strict_response_template(qtext, strict_response_template)
                if question_evidence_audit is not None:
                    question_evidence_audit.setdefault("strict_template", {})
                    question_evidence_audit["strict_template"] = {
                        "enabled": True,
                        "template_sha256": _sha256_text(strict_response_template),
                    }

            top_k_initial = max(1, int(args.chat_top_k_initial))
            top_k = min(top_k_initial, top_k_max) if args.adaptive_top_k else top_k_max

            def _record_llm_dispatch(
                *,
                phase: str,
                prompt_mode_local: str,
                prompt_text_local: str,
                prompt_file_local: Path | None,
                top_k_local: int,
            ) -> None:
                if question_evidence_audit is None:
                    return
                rec = _append_llm_dispatch_to_audit(
                    audit=question_evidence_audit,
                    phase=phase,
                    prompt_mode=prompt_mode_local,
                    prompt_text=prompt_text_local,
                    prompt_file=prompt_file_local,
                    backend=args.backend,
                    model=args.model or "(default)",
                    top_k=top_k_local,
                    prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                )
                if question_evidence_audit_path is not None:
                    _write_question_evidence_audit(
                        out_dir=out_dir,
                        qid=q.id,
                        audit=question_evidence_audit,
                    )
                _log_event(
                    logging.INFO,
                    "question.chat.dispatch",
                    fn="_run_single",
                    qid=q.id,
                    phase=phase,
                    prompt_mode=prompt_mode_local,
                    backend=args.backend,
                    model=args.model or "(default)",
                    top_k=top_k_local,
                    prompt_file=str(prompt_file_local) if prompt_file_local else "(none)",
                    prompt_sha256=rec.get("prompt_sha256"),
                    prompt_chars=rec.get("prompt_chars"),
                    prompt_cite_markers=rec.get("prompt_cite_markers"),
                )

            _log_event(
                logging.INFO,
                "question.chat.prepare",
                fn="_run_single",
                qid=q.id,
                prompt_mode=prompt_mode,
                prompt_file=str(prompt_file) if prompt_file else "(none)",
                backend=args.backend,
                model=args.model or "(default)",
                top_k=top_k,
                top_k_max=top_k_max,
                strict_response_template=bool(strict_response_template),
                retry_on_schema_fail=retry_on_schema_fail,
                schema_retry_attempts=schema_retry_attempts,
                prompt_preview=_compact_log_text(
                    qtext,
                    max_chars=max(64, int(getattr(args, "log_prompt_max_chars", DEFAULT_LOG_PROMPT_MAX_CHARS))),
                ),
            )

            _record_llm_dispatch(
                phase="primary",
                prompt_mode_local=prompt_mode,
                prompt_text_local=qtext,
                prompt_file_local=prompt_file,
                top_k_local=int(top_k),
            )
            chat_res = run_engine_chat(
                spec,
                prefix,
                question=qtext,
                index_path=index_path,
                parquet_path=parquet_path,
                backend=args.backend,
                model=args.model,
                top_k=int(top_k),
                prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                system_prompt_file=prompt_file,
                max_tokens=args.max_tokens if args.max_tokens is not None else pack.defaults.max_tokens,
                temperature=args.temperature if args.temperature is not None else pack.defaults.temperature,
                top_p=args.top_p,
                num_ctx=args.num_ctx,
                env_overrides=main_engine_env,
            )
            chat_json = parse_json_maybe(chat_res.stdout) or chat_res.stdout

            # Adaptive rerun: if validators fail at low-k, retry once with max-k.
            if args.adaptive_top_k and top_k < top_k_max:
                ans_probe, _ = _extract_answer_and_sources(chat_json)
                probe_issues = _compute_schema_issues_local(ans_probe or "")
                if probe_issues:
                    report_lines.append(
                        f"- Adaptive rerun: validator issues={len(probe_issues)} "
                        f"(top_k={top_k} → {top_k_max})\n"
                    )
                    _log_event(
                        logging.WARNING,
                        "question.chat.adaptive_rerun",
                        fn="_run_single",
                        qid=q.id,
                        issue_count=len(probe_issues),
                        top_k_before=top_k,
                        top_k_after=top_k_max,
                    )
                    adaptive_rerun_count += 1
                    issue_bullets = "\n".join(
                        f"- {it}" for it in probe_issues[: int(ISSUE_CAPS.get("adaptive_rerun_bullets", 8))]
                    )
                    rerun_preamble = str(
                        PROMPT_ADAPTIVE_RERUN.get(
                            "preamble",
                            (
                                "IMPORTANT:\n"
                                "- Follow the required response schema exactly (VERDICT/CITATIONS first).\n"
                                "- If evidence is present, do not output NOT FOUND.\n"
                                "- Ensure CITATIONS tokens are path:line(-line)."
                            ),
                        )
                    ).strip()
                    rerun_issues_header = str(
                        PROMPT_ADAPTIVE_RERUN.get("issues_header", "Validation issues to fix in this rerun:")
                    )
                    rerun_qtext = (
                        rerun_preamble
                        + "\n\n"
                        + rerun_issues_header
                        + "\n"
                        + issue_bullets
                        + "\n\n"
                        + qtext
                    )
                    top_k = top_k_max
                    _record_llm_dispatch(
                        phase="adaptive_rerun",
                        prompt_mode_local=f"{prompt_mode}:adaptive_rerun",
                        prompt_text_local=rerun_qtext,
                        prompt_file_local=prompt_file,
                        top_k_local=int(top_k),
                    )
                    chat_res = run_engine_chat(
                        spec,
                        prefix,
                        question=rerun_qtext,
                        index_path=index_path,
                        parquet_path=parquet_path,
                        backend=args.backend,
                        model=args.model,
                        top_k=int(top_k),
                        prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                        system_prompt_file=prompt_file,
                        max_tokens=args.max_tokens if args.max_tokens is not None else pack.defaults.max_tokens,
                        temperature=args.temperature if args.temperature is not None else pack.defaults.temperature,
                        top_p=args.top_p,
                        num_ctx=args.num_ctx,
                        env_overrides=main_engine_env,
                    )
                    chat_json = parse_json_maybe(chat_res.stdout) or chat_res.stdout

            # Optional schema retry loop: rerun with strict template + explicit validator errors.
            if retry_on_schema_fail and schema_retry_attempts > 0:
                for retry_idx in range(1, schema_retry_attempts + 1):
                    ans_probe, _ = _extract_answer_and_sources(chat_json)
                    probe_issues = _compute_schema_issues_local(ans_probe or "")
                    if not probe_issues:
                        _log_event(
                            logging.INFO,
                            "question.chat.schema_retry.satisfied",
                            fn="_run_single",
                            qid=q.id,
                            attempt=retry_idx - 1,
                        )
                        break
                    report_lines.append(
                        f"- Schema retry {retry_idx}/{schema_retry_attempts}: validator issues={len(probe_issues)}\n"
                    )
                    _log_event(
                        logging.WARNING,
                        "question.chat.schema_retry",
                        fn="_run_single",
                        qid=q.id,
                        attempt=f"{retry_idx}/{schema_retry_attempts}",
                        issue_count=len(probe_issues),
                    )
                    schema_retry_count += 1
                    retry_prompt = _build_schema_retry_prompt(
                        base_question=qtext,
                        strict_template=strict_response_template,
                        issues=probe_issues,
                        attempt=retry_idx,
                        total_attempts=schema_retry_attempts,
                    )
                    _record_llm_dispatch(
                        phase=f"schema_retry_{retry_idx}",
                        prompt_mode_local=f"{prompt_mode}:schema_retry",
                        prompt_text_local=retry_prompt,
                        prompt_file_local=prompt_file,
                        top_k_local=int(top_k),
                    )
                    chat_res = run_engine_chat(
                        spec,
                        prefix,
                        question=retry_prompt,
                        index_path=index_path,
                        parquet_path=parquet_path,
                        backend=args.backend,
                        model=args.model,
                        top_k=int(top_k),
                        prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                        system_prompt_file=prompt_file,
                        max_tokens=args.max_tokens if args.max_tokens is not None else pack.defaults.max_tokens,
                        temperature=args.temperature if args.temperature is not None else pack.defaults.temperature,
                        top_p=args.top_p,
                        num_ctx=args.num_ctx,
                        env_overrides=main_engine_env,
                    )
                    chat_json = parse_json_maybe(chat_res.stdout) or chat_res.stdout

        chat_file = out_dir / f"{q.id}_chat.json"
        write_json(chat_file, {"argv": chat_res.argv, "returncode": chat_res.returncode, "stdout": chat_json, "stderr": chat_res.stderr})
        report_lines.append(f"- Chat: rc={chat_res.returncode} → {chat_file.name} (top_k={top_k})\n")
        _log_event(
            logging.INFO,
            "question.chat.done",
            fn="_run_single",
            qid=q.id,
            returncode=chat_res.returncode,
            top_k=top_k,
            artifact=str(chat_file),
        )

        ans, sources = _extract_answer_and_sources(chat_json)
        if q.answer_mode == "llm" and strict_response_template:
            repaired_ans, repair_notes = _repair_answer_for_strict_contract(
                qid=q.id,
                answer=ans or "",
                strict_template=strict_response_template,
                evidence_blocks=evidence_blocks,
                validation=pack.validation,
            )
            if repaired_ans != (ans or ""):
                ans = repaired_ans
                if isinstance(chat_json, dict):
                    chat_json["answer"] = ans
                else:
                    chat_json = {"answer": ans, "sources": sources}
                write_json(
                    chat_file,
                    {"argv": chat_res.argv, "returncode": chat_res.returncode, "stdout": chat_json, "stderr": chat_res.stderr},
                )
                if repair_notes:
                    report_lines.append(f"- Strict contract repair applied: {', '.join(repair_notes)}\n")

        # Deterministic Gate B repair: when body paths are present but matching
        # CITATIONS tokens are missing, auto-complete from injected evidence.
        if ans and evidence_blocks and pack.validation.enforce_paths_must_be_cited:
            ans2, added_citations = _auto_complete_citations_for_path_gates(
                ans, evidence_blocks, pack.validation
            )
            if ans2 and ans2 != ans:
                ans = ans2
                if isinstance(chat_json, dict):
                    chat_json["answer"] = ans
                else:
                    chat_json = {"answer": ans, "sources": sources}
                write_json(
                    chat_file,
                    {
                        "argv": chat_res.argv,
                        "returncode": chat_res.returncode,
                        "stdout": chat_json,
                        "stderr": chat_res.stderr,
                    },
                )
                _log_event(
                    logging.INFO,
                    "question.path_gate.autocomplete",
                    fn="_run_single",
                    qid=q.id,
                    added_count=len(added_citations),
                    added=added_citations[: int(ISSUE_CAPS.get("uncited_paths", 10))],
                )

        if ans:
            report_lines.append("\n**Answer:**\n\n")
            report_lines.append(ans + "\n\n")
        if sources is not None:
            report_lines.append("**Sources:**\n\n")
            if isinstance(sources, list):
                for s in sources[: int(ISSUE_CAPS.get("sources", 20))]:
                    report_lines.append(f"- {s}\n")
            else:
                report_lines.append(f"- {sources}\n")

        schema_issues = _compute_schema_issues_local(ans or "")
        if pack.validation.apply_question_validators and ans and question_validators_cfg:
            qv_issues = _apply_question_validators(
                qid=q.id,
                answer_text=ans or "",
                cfg=question_validators_cfg,
                default_test_path_patterns=default_test_path_patterns,
            )
            if qv_issues:
                schema_issues.extend(qv_issues)
                _log_event(
                    logging.WARNING,
                    "question.qvalidators.issues",
                    fn="_run_single",
                    qid=q.id,
                    issue_count=len(qv_issues),
                    sample=qv_issues[:3],
                )
            else:
                _log_event(
                    logging.INFO,
                    "question.qvalidators.ok",
                    fn="_run_single",
                    qid=q.id,
                )
        if schema_issues:
            validator_section_opened = True
            report_lines.append("\n**Validator issues:**\n\n")
            for it in schema_issues:
                report_lines.append(f"- Response schema: {it}\n")
            if pack.validation.fail_on_missing_citations:
                fatal_contract_issues.extend([f"{q.id}: {it}" for it in schema_issues])
            _log_event(
                logging.WARNING,
                "question.validator.issues",
                fn="_run_single",
                qid=q.id,
                issue_count=len(schema_issues),
                sample=schema_issues[:3],
            )
        else:
            _log_event(
                logging.INFO,
                "question.validator.ok",
                fn="_run_single",
                qid=q.id,
            )

        if q.advice_mode == "llm":
            if not evidence_blocks:
                report_lines.append("- Advice: skipped (no evidence blocks available)\n")
                advice_rc = "skipped_no_evidence"
            else:
                advice_prompt = q.advice_prompt or _build_advice_prompt(
                    qid=q.id,
                    question_text=q.question,
                    deterministic_answer=ans or "",
                    evidence_blocks=evidence_blocks,
                )
                advice_prompt_path = out_dir / f"{q.id}_advice_prompt.md"
                write_text(advice_prompt_path, advice_prompt)
                if question_evidence_audit is not None:
                    question_evidence_audit.setdefault("artifacts", {})
                    question_evidence_audit["artifacts"]["advice_prompt"] = str(advice_prompt_path.name)
                    if question_evidence_audit_path is not None:
                        _write_question_evidence_audit(
                            out_dir=out_dir,
                            qid=q.id,
                            audit=question_evidence_audit,
                        )

                advice_top_k_cap = int(ISSUE_CAPS.get("advice_top_k_cap", 8))
                advice_top_k = int((q.chat or {}).get("advice_top_k", min(advice_top_k_cap, top_k_max)))
                advice_top_k = max(1, advice_top_k)
                advice_prompt_file = prompt_grounding or prompt_analyze
                _log_event(
                    logging.INFO,
                    "question.advice.prepare",
                    fn="_run_single",
                    qid=q.id,
                    advice_prompt_file=str(advice_prompt_file) if advice_prompt_file else "(none)",
                    advice_top_k=advice_top_k,
                )
                if question_evidence_audit is not None:
                    rec = _append_llm_dispatch_to_audit(
                        audit=question_evidence_audit,
                        phase="advice_primary",
                        prompt_mode="advice",
                        prompt_text=advice_prompt,
                        prompt_file=advice_prompt_file,
                        backend=args.backend,
                        model=args.model or "(default)",
                        top_k=advice_top_k,
                        prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                    )
                    if question_evidence_audit_path is not None:
                        _write_question_evidence_audit(
                            out_dir=out_dir,
                            qid=q.id,
                            audit=question_evidence_audit,
                        )
                    _log_event(
                        logging.INFO,
                        "question.advice.dispatch",
                        fn="_run_single",
                        qid=q.id,
                        phase="advice_primary",
                        backend=args.backend,
                        model=args.model or "(default)",
                        top_k=advice_top_k,
                        prompt_file=str(advice_prompt_file) if advice_prompt_file else "(none)",
                        prompt_sha256=rec.get("prompt_sha256"),
                        prompt_chars=rec.get("prompt_chars"),
                    )

                advice_res = run_engine_chat(
                    spec,
                    prefix,
                    question=advice_prompt,
                    index_path=index_path,
                    parquet_path=parquet_path,
                    backend=args.backend,
                    model=args.model,
                    top_k=advice_top_k,
                    prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                    system_prompt_file=advice_prompt_file,
                    max_tokens=args.max_tokens if args.max_tokens is not None else pack.defaults.max_tokens,
                    temperature=args.temperature if args.temperature is not None else pack.defaults.temperature,
                    top_p=args.top_p,
                    num_ctx=args.num_ctx,
                    env_overrides=main_engine_env,
                )
                advice_json = parse_json_maybe(advice_res.stdout) or advice_res.stdout
                advice_file = out_dir / f"{q.id}_advice_chat.json"
                write_json(
                    advice_file,
                    {
                        "argv": advice_res.argv,
                        "returncode": advice_res.returncode,
                        "stdout": advice_json,
                        "stderr": advice_res.stderr,
                    },
                )
                report_lines.append(
                    f"- Advice: rc={advice_res.returncode} → {advice_file.name} (top_k={advice_top_k})\n"
                )
                advice_rc = advice_res.returncode
                _log_event(
                    logging.INFO,
                    "question.advice.done",
                    fn="_run_single",
                    qid=q.id,
                    returncode=advice_res.returncode,
                    artifact=str(advice_file),
                )
                if isinstance(advice_json, dict):
                    advice_text = (
                        advice_json.get("answer")
                        or advice_json.get("response")
                        or advice_json.get("text")
                        or ""
                    )
                else:
                    advice_text = str(advice_json or "")

                advice_quality_issues = _validate_advice_quality(
                    advice_text=advice_text,
                    evidence_blocks=evidence_blocks,
                )
                advice_retry_attempts = (
                    ADVICE_RETRY_ATTEMPTS
                    if (mission_advice_gate_enabled and ADVICE_RETRY_ON_VALIDATION_FAIL)
                    else 0
                )
                for retry_idx in range(1, advice_retry_attempts + 1):
                    if not advice_quality_issues:
                        _log_event(
                            logging.INFO,
                            "question.advice.retry.satisfied",
                            fn="_run_single",
                            qid=q.id,
                            attempt=retry_idx - 1,
                        )
                        break
                    report_lines.append(
                        f"- Advice retry {retry_idx}/{advice_retry_attempts}: validator issues={len(advice_quality_issues)}\n"
                    )
                    _log_event(
                        logging.WARNING,
                        "question.advice.retry",
                        fn="_run_single",
                        qid=q.id,
                        attempt=f"{retry_idx}/{advice_retry_attempts}",
                        issue_count=len(advice_quality_issues),
                        sample=advice_quality_issues[:3],
                    )
                    advice_retry_count += 1
                    retry_prompt = _build_advice_retry_prompt(
                        base_prompt=advice_prompt,
                        issues=advice_quality_issues,
                        attempt=retry_idx,
                        total_attempts=advice_retry_attempts,
                    )
                    if question_evidence_audit is not None:
                        rec = _append_llm_dispatch_to_audit(
                            audit=question_evidence_audit,
                            phase=f"advice_retry_{retry_idx}",
                            prompt_mode="advice_retry",
                            prompt_text=retry_prompt,
                            prompt_file=advice_prompt_file,
                            backend=args.backend,
                            model=args.model or "(default)",
                            top_k=advice_top_k,
                            prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                        )
                        if question_evidence_audit_path is not None:
                            _write_question_evidence_audit(
                                out_dir=out_dir,
                                qid=q.id,
                                audit=question_evidence_audit,
                            )
                        _log_event(
                            logging.INFO,
                            "question.advice.dispatch",
                            fn="_run_single",
                            qid=q.id,
                            phase=f"advice_retry_{retry_idx}",
                            backend=args.backend,
                            model=args.model or "(default)",
                            top_k=advice_top_k,
                            prompt_file=str(advice_prompt_file) if advice_prompt_file else "(none)",
                            prompt_sha256=rec.get("prompt_sha256"),
                            prompt_chars=rec.get("prompt_chars"),
                        )
                    advice_res = run_engine_chat(
                        spec,
                        prefix,
                        question=retry_prompt,
                        index_path=index_path,
                        parquet_path=parquet_path,
                        backend=args.backend,
                        model=args.model,
                        top_k=advice_top_k,
                        prompt_profile=args.prompt_profile if spec.prompt_profile_flag else None,
                        system_prompt_file=advice_prompt_file,
                        max_tokens=args.max_tokens if args.max_tokens is not None else pack.defaults.max_tokens,
                        temperature=args.temperature if args.temperature is not None else pack.defaults.temperature,
                        top_p=args.top_p,
                        num_ctx=args.num_ctx,
                        env_overrides=main_engine_env,
                    )
                    advice_json = parse_json_maybe(advice_res.stdout) or advice_res.stdout
                    write_json(
                        advice_file,
                        {
                            "argv": advice_res.argv,
                            "returncode": advice_res.returncode,
                            "stdout": advice_json,
                            "stderr": advice_res.stderr,
                        },
                    )
                    report_lines.append(
                        f"  - Advice retry rc={advice_res.returncode} → {advice_file.name}\n"
                    )
                    advice_rc = advice_res.returncode
                    _log_event(
                        logging.INFO,
                        "question.advice.retry.done",
                        fn="_run_single",
                        qid=q.id,
                        attempt=f"{retry_idx}/{advice_retry_attempts}",
                        returncode=advice_res.returncode,
                        artifact=str(advice_file),
                    )
                    if isinstance(advice_json, dict):
                        advice_text = (
                            advice_json.get("answer")
                            or advice_json.get("response")
                            or advice_json.get("text")
                            or ""
                        )
                    else:
                        advice_text = str(advice_json or "")
                    advice_quality_issues = _validate_advice_quality(
                        advice_text=advice_text,
                        evidence_blocks=evidence_blocks,
                    )

                if advice_text.strip():
                    report_lines.append("\n**Improvement Suggestions (LLM):**\n\n")
                    report_lines.append(advice_text.strip() + "\n\n")

                if advice_quality_issues:
                    if not advice_validator_section_opened:
                        report_lines.append("\n**Advice validator issues:**\n\n")
                        advice_validator_section_opened = True
                    for it in advice_quality_issues:
                        report_lines.append(f"- Advice quality: {it}\n")
                    if mission_advice_gate_enabled:
                        fatal_advice_gate_issues.extend([f"{q.id}: {it}" for it in advice_quality_issues])
                    _log_event(
                        logging.WARNING,
                        "question.advice.validator.issues",
                        fn="_run_single",
                        qid=q.id,
                        issue_count=len(advice_quality_issues),
                        sample=advice_quality_issues[:3],
                    )
                else:
                    _log_event(
                        logging.INFO,
                        "question.advice.validator.ok",
                        fn="_run_single",
                        qid=q.id,
                        retries=advice_retry_count,
                    )

        q_elapsed_s = round(time.perf_counter() - q_t0, 3)
        citations_count = _count_citations_in_answer(ans or "")
        sources_count = 0
        if isinstance(sources, list):
            sources_count = len(sources)
        elif sources:
            sources_count = 1
        evidence_chars = sum(len(str(b or "")) for b in evidence_blocks)
        llm_dispatch_count = 0
        missing_paths_count = 0
        if question_evidence_audit is not None:
            llm_dispatch_count = len(question_evidence_audit.get("llm_dispatches") or [])
            missing_paths_count = int(question_evidence_audit.get("paths_missing_from_parquet_count") or 0)
            question_evidence_audit["result"] = {
                "schema_issues_count": len(schema_issues),
                "advice_quality_issues_count": len(advice_quality_issues),
                "citations_count": citations_count,
                "sources_count": sources_count,
                "answer_chars": len(ans or ""),
                "elapsed_s": q_elapsed_s,
            }
            question_evidence_audit["llm_dispatch_count"] = llm_dispatch_count
            if question_evidence_audit_path is not None:
                _write_question_evidence_audit(
                    out_dir=out_dir,
                    qid=q.id,
                    audit=question_evidence_audit,
                )
        if question_evidence_summary_row is not None:
            question_evidence_summary_row["llm_dispatches"] = llm_dispatch_count
            question_evidence_summary_row["schema_issues_count"] = len(schema_issues)
            question_evidence_summary_row["advice_issues_count"] = len(advice_quality_issues)
            question_evidence_summary_row["elapsed_s"] = q_elapsed_s
        _log_event(
            logging.INFO,
            "question.done",
            fn="_run_single",
            qid=q.id,
            elapsed_s=q_elapsed_s,
            preflight_steps=len(preflights),
            evidence_blocks=len(evidence_blocks),
            evidence_usable_blocks=usable_evidence_blocks,
            evidence_chars=evidence_chars,
            answer_chars=len(ans or ""),
            citations_count=citations_count,
            sources_count=sources_count,
            schema_issue_count=len(schema_issues),
            advice_issue_count=len(advice_quality_issues),
            schema_retries=schema_retry_count,
            advice_retries=advice_retry_count,
            adaptive_reruns=adaptive_rerun_count,
            advice_rc=advice_rc if q.advice_mode == "llm" else "(n/a)",
            llm_dispatches=llm_dispatch_count,
            paths_missing_from_parquet=missing_paths_count,
        )
        question_runtime_stats.append(
            {
                "qid": q.id,
                "elapsed_s": q_elapsed_s,
                "evidence_blocks": len(evidence_blocks),
                "evidence_usable_blocks": usable_evidence_blocks,
                "schema_issues": len(schema_issues),
                "advice_issues": len(advice_quality_issues),
                "schema_retries": schema_retry_count,
                "advice_retries": advice_retry_count,
                "adaptive_reruns": adaptive_rerun_count,
            }
        )

    evidence_summary_path: Path | None = None
    if bool(getattr(args, "evidence_audit", EVIDENCE_AUDIT_ENABLED_DEFAULT)):
        missing_total = sum(int(r.get("paths_missing_from_parquet_count") or 0) for r in evidence_audit_rows)
        usable_total = sum(int(r.get("evidence_usable_blocks_count") or 0) for r in evidence_audit_rows)
        evidence_summary_obj = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parquet": str(parquet_path),
            "parquet_path_universe_count": len(parquet_path_universe),
            "parquet_path_universe_source": parquet_path_universe_meta.get("source"),
            "parquet_path_universe_columns": parquet_path_universe_meta.get("selected_columns", []),
            "parquet_path_universe_errors": parquet_path_universe_meta.get("errors", []),
            "questions": evidence_audit_rows,
            "question_count": len(evidence_audit_rows),
            "total_usable_evidence_blocks": usable_total,
            "total_missing_paths_from_parquet": missing_total,
        }
        evidence_summary_path = out_dir / EVIDENCE_AUDIT_SUMMARY_FILENAME
        write_json(evidence_summary_path, evidence_summary_obj)
        _log_event(
            logging.INFO,
            "evidence.audit.summary",
            fn="_run_single",
            artifact=str(evidence_summary_path),
            questions=len(evidence_audit_rows),
            total_usable_evidence_blocks=usable_total,
            total_missing_paths_from_parquet=missing_total,
        )
        # Fail-closed: evidence paths injected to prompts must exist in parquet path universe.
        if missing_total:
            fatal_contract_issues.append(
                f"Evidence audit: total_missing_paths_from_parquet={missing_total} "
                f"(see {evidence_summary_path.name})"
            )
            _log_event(
                logging.ERROR,
                "evidence.audit.missing_paths.fatal",
                fn="_run_single",
                artifact=str(evidence_summary_path),
                total_missing_paths_from_parquet=missing_total,
                parquet_path_universe_truncated=bool(parquet_path_universe_meta.get("truncated")),
            )

    report_path = out_dir / REPORT_FILE
    write_text(report_path, "".join(report_lines))

    ok, total, issues = _parse_report_ok_count(
        report_path,
        include_advice_issues=mission_advice_gate_enabled,
    )

    # Plugins: post_run outputs
    extra_outputs: Dict[str, Any] = {}
    final_key_map_path = _write_evidence_key_map(out_dir=out_dir, parquet_path=parquet_path, engine_name=spec.name)
    extra_outputs["evidence_key_map"] = {
        "file": str(final_key_map_path.name),
        "parquet_schema_source": _RUNTIME_PARQUET_SCHEMA_SOURCE,
        "parquet_columns_count": len(_RUNTIME_PARQUET_COLUMNS),
        "path_keys": len(_effective_path_keys()),
        "line_keys": len(_effective_line_keys()),
        "snippet_keys": len(_effective_snippet_keys()),
        "iter_rows_keys": len(_effective_iter_rows_keys()),
    }
    if evidence_summary_path is not None:
        extra_outputs["evidence_delivery_audit"] = {
            "file": str(evidence_summary_path.name),
            "enabled": True,
            "question_count": len(evidence_audit_rows),
            "parquet_path_universe_count": len(parquet_path_universe),
        }
    for plugin in _select_plugins(pack):
        if PluginContext is None:
            continue
        _log_event(
            logging.INFO,
            "plugin.run.start",
            fn="_run_single",
            plugin=plugin.name,
        )
        ctx = PluginContext(
            pack_path=pack_path,
            out_dir=out_dir,
            pack=pack,
            args=args,
            engine=pack.engine,
            pack_type=pack.pack_type,
        )
        try:
            out = plugin.post_run(ctx)
            if out:
                extra_outputs.setdefault("plugin_outputs", {})
                extra_outputs["plugin_outputs"][plugin.name] = {
                    "files": out.files,
                    "metrics": out.metrics,
                    "hashes": out.hashes,
                }
                _log_event(
                    logging.INFO,
                    "plugin.run.done",
                    fn="_run_single",
                    plugin=plugin.name,
                    files=list((out.files or {}).keys()),
                    metrics_keys=list((out.metrics or {}).keys()),
                )
        except Exception as e:
            extra_outputs.setdefault("plugin_errors", {})
            extra_outputs["plugin_errors"][plugin.name] = str(e)
            _log_event(
                logging.ERROR,
                "plugin.run.error",
                fn="_run_single",
                plugin=plugin.name,
                error=str(e),
            )

    manifest_path = generate_run_manifest(
        out_dir=out_dir,
        pack_path=pack_path,
        parquet_path=parquet_path,
        index_path=index_path,
        run_id=str(uuid.uuid4()),
        pack=pack,
        score_ok=ok,
        total_questions=total,
        report_path=report_path,
        extra_outputs=extra_outputs if extra_outputs else None,
    )

    print(f"Wrote report: {report_path}")
    print(f"Wrote manifest: {manifest_path}")
    run_elapsed_s = round(time.perf_counter() - run_t0, 3)
    slow_questions = sorted(
        question_runtime_stats,
        key=lambda row: float(row.get("elapsed_s") or 0.0),
        reverse=True,
    )[:3]
    _log_event(
        logging.INFO,
        "run.done",
        fn="_run_single",
        report=str(report_path),
        manifest=str(manifest_path),
        score_ok=ok,
        total=total,
        issues=issues,
        fatal_contract_issues=len(fatal_contract_issues),
        fatal_advice_gate_issues=len(fatal_advice_gate_issues),
        elapsed_s=run_elapsed_s,
        slow_questions=slow_questions,
        evidence_audit_artifact=str(evidence_summary_path) if evidence_summary_path else "(disabled)",
        evidence_audit_questions=len(evidence_audit_rows),
        evidence_audit_missing_paths=sum(
            int(r.get("paths_missing_from_parquet_count") or 0) for r in evidence_audit_rows
        ),
    )

    if fatal_contract_issues or fatal_advice_gate_issues:
        raise SystemExit(2)

    return 0, extra_outputs


def _run_replicates(args: argparse.Namespace) -> int:
    seeds = DEFAULT_REPLICATE_SEEDS
    if args.replicate_seeds:
        seeds = [int(s.strip()) for s in args.replicate_seeds.split(",")]
    _log_event(
        logging.INFO,
        "replicate.start",
        fn="_run_replicates",
        seeds=seeds,
        out_dir=args.out_dir,
    )

    pack_path = Path(args.pack)
    parquet_path = Path(args.parquet)
    index_path = Path(args.index)
    out_dir_base = Path(args.out_dir)

    ensure_dir(out_dir_base)
    pack = _parse_pack(load_pack(pack_path))

    specs = load_engine_specs(Path(args.engine_specs))
    if pack.engine not in specs:
        raise SystemExit(f"Engine '{pack.engine}' not found in engine specs: {args.engine_specs}")
    spec = specs[pack.engine]

    results: List[Tuple[int, Path, int, int, int]] = []
    guru_results: List[Tuple[int, Path, int, int, int]] = []

    for seed in seeds:
        seed_out = out_dir_base / f"{REPLICATE_SEED_PREFIX}{seed}"
        ensure_dir(seed_out)
        _log_event(
            logging.INFO,
            "replicate.seed.start",
            fn="_run_replicates",
            seed=seed,
            out_dir=str(seed_out),
        )
        _run_single(pack_path=pack_path, pack=pack, spec=spec, args=args, parquet_path=parquet_path, index_path=index_path, out_dir=seed_out, all_specs=specs)

        report = seed_out / REPORT_FILE
        ok, total, issue_count = _parse_report_ok_count(
            report,
            include_advice_issues=_is_mission_pack_type(pack.pack_type),
        )
        results.append((seed, report, ok, total, issue_count))
        _log_event(
            logging.INFO,
            "replicate.seed.done",
            fn="_run_replicates",
            seed=seed,
            score_ok=ok,
            total=total,
            issues=issue_count,
        )

        # If plugin produced GURU_METRICS.json, aggregate it too
        gm = seed_out / GURU_METRICS_FILE
        if gm.exists():
            try:
                obj = json.loads(gm.read_text(encoding="utf-8"))
                guru_ok = int(obj.get("guru_score", 0))
                guru_total = int(obj.get("total_questions", total))
                guru_issues = int(obj.get("issues", 0))
                guru_results.append((seed, gm, guru_ok, guru_total, guru_issues))
            except Exception:
                pass

    summary = generate_stability_summary(results, out_dir_base, title=PACK_STABILITY_TITLE)
    print(summary.read_text(encoding="utf-8"))

    if guru_results:
        # Write a separate stability summary for Guru metrics
        guru_summary = out_dir_base / GURU_STABILITY_FILE
        lines = [
            f"# {GURU_STABILITY_TITLE}\n\n",
            f"**Generated**: {datetime.now(timezone.utc).isoformat()}\n",
            f"**Replicates**: {len(guru_results)}\n",
            f"**Seeds**: {[r[0] for r in guru_results]}\n\n",
            "---\n\n",
            "## Per-Replicate Guru Results\n\n",
            "| Seed | Guru Score | Issues | Metrics |\n",
            "|------|------------|--------|---------|\n",
        ]
        for seed, path, gok, gtot, gissues in guru_results:
            lines.append(
                f"| {seed} | {gok}/{gtot} | {gissues} | "
                f"[{path.name}]({REPLICATE_SEED_PREFIX}{seed}/{path.name}) |\n"
            )
        guru_summary.write_text("".join(lines), encoding="utf-8")
        print(guru_summary.read_text(encoding="utf-8"))

    _log_event(
        logging.INFO,
        "replicate.done",
        fn="_run_replicates",
        runs=len(results),
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an audit pack (engine-pluggable; optional plugins).")

    # Script-relative defaults for pack and engine-specs
    _script_dir = Path(__file__).parent
    _default_pack = _script_dir / DEFAULT_PACK_FILE
    _default_engine_specs = _script_dir / DEFAULT_ENGINE_SPECS_FILE
    _default_system_prompt = _script_dir / DEFAULT_SYSTEM_PROMPT_FILE
    _default_grounding_prompt = _script_dir / DEFAULT_GROUNDING_PROMPT_FILE
    _default_analyze_prompt = _script_dir / DEFAULT_ANALYZE_PROMPT_FILE
    _default_out_dir = _script_dir.parent.parent / DEFAULT_OUT_DIR_NAME
    _default_parquet = DEFAULT_PARQUET_FILE
    _default_index = DEFAULT_INDEX_FILE
    
    try:
        if not _default_pack.exists():
            print(f"Warning: Default pack not found at {_default_pack}")
        if not _default_engine_specs.exists():
            print(f"Warning: Default engine specs not found at {_default_engine_specs}")
        if not _default_system_prompt.exists():
            print(f"Warning: Default system prompt not found at {_default_system_prompt}")
        if not _default_grounding_prompt.exists():
            print(f"Warning: Default grounding prompt not found at {_default_grounding_prompt}")
        if not _default_analyze_prompt.exists():
            print(f"Warning: Default analyze prompt not found at {_default_analyze_prompt}")
        if not _default_out_dir.exists():
            print(f"Warning: Default output directory not found at {_default_out_dir}")
    except Exception:
        raise SystemExit("Default pack or engine specs file not found. Please specify --pack and --engine-specs explicitly.")

    ap.add_argument("--pack", default=str(_default_pack), help=f"Path to pack.yaml (default: {_default_pack.name})")
    ap.add_argument("--parquet", default=_default_parquet, help="Path to engine parquet (RSQT.parquet or MD_PARSE.parquet etc.)")
    ap.add_argument("--index", default=_default_index, help="Path to FAISS index")
    ap.add_argument("--rsqt", default=None, help="(Legacy alias) same as --parquet")
    ap.add_argument("--engine-specs", default=str(_default_engine_specs), help=f"Path to engine_specs.yaml (default: {_default_engine_specs.name})")

    ap.add_argument("--backend", default=DEFAULT_BACKEND, help=f"Backend (default: {DEFAULT_BACKEND})")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Model override (default: {DEFAULT_MODEL})")
    ap.add_argument("--prompt-profile", default=DEFAULT_PROMPT_PROFILE, help="Engine prompt profile (if supported)")
    ap.add_argument("--max-tokens", type=int, default=None, help="Max tokens for chat (default: pack.defaults.max_tokens)")
    ap.add_argument("--temperature", type=float, default=None, help="Temperature (default: pack.defaults.temperature)")
    ap.add_argument("--top-p", type=float, default=DEFAULT_TOP_P, help="Top-p (nucleus) sampling")
    ap.add_argument("--num-ctx", type=int, default=None, help="Context window size (engine-specific)")

    ap.add_argument("--system-prompt-file", default=None, help="(Legacy) use same prompt for both grounding/analyze")
    ap.add_argument("--system-prompt-grounding-file", default=str(_default_grounding_prompt), help="Prompt for standard mode")
    ap.add_argument("--system-prompt-analyze-file", default=str(_default_analyze_prompt), help="Prompt for quote-bypass mode")

    ap.add_argument(
        "--out-dir",
        default=None,
        help=(
            "Output directory. If omitted, auto-generates under default output root as "
            "<timestamp>_<model>_<engine>_<pack>."
        ),
    )
    ap.add_argument("--no-uv", action="store_true", help="Use direct CLI instead of uv")

    ap.add_argument("--cache-preflights", action="store_true", help="Cache preflight artifacts")
    ap.add_argument("--short-circuit-preflights", action="store_true", help="Skip later preflights when stop_if_nonempty step already produced hits")
    ap.add_argument("--adaptive-top-k", action="store_true", help="Start chat with smaller top_k and rerun once at max top_k if validators fail")
    ap.add_argument("--chat-top-k-initial", type=int, default=DEFAULT_CHAT_TOP_K_INITIAL, help="Initial top_k when --adaptive-top-k is enabled")
    ap.add_argument("--preflight-max-chars", type=int, default=DEFAULT_PREFLIGHT_MAX_CHARS, help="Max chars per preflight evidence injected")

    ap.add_argument("--quote-bypass", action="store_true", help="Use analyze-only evidence injection when preflight evidence exists (legacy alias for --quote-bypass-mode on)")
    ap.add_argument(
        "--quote-bypass-mode",
        choices=QUOTE_BYPASS_MODE_CHOICES,
        default=QUOTE_BYPASS_DEFAULT_MODE,
        help="auto=enable when evidence exists, on=always, off=never (STANDARD)",
    )
    ap.add_argument("--no-quote-bypass", action="store_const", const="off", dest="quote_bypass_mode", help="Force STANDARD mode (alias for --quote-bypass-mode off)")
    ap.add_argument(
        "--evidence-empty-gate",
        action="store_true",
        default=EVIDENCE_EMPTY_GATE_DEFAULT,
        help=(
            "Legacy empty-evidence behavior toggle. In strict evidence mode "
            "(runner.evidence_presence_gate.fail_on_empty_evidence=true), "
            "empty evidence aborts immediately regardless of this flag."
        ),
    )
    ap.add_argument(
        "--no-evidence-empty-gate",
        action="store_false",
        dest="evidence_empty_gate",
        help=(
            "Disable legacy empty-evidence skip behavior for non-strict policy mode. "
            "Does not bypass strict fail-on-empty-evidence policy."
        ),
    )

    ap.add_argument("--replicate", action="store_true", help="Run replicates with different seeds")
    ap.add_argument("--replicate-seeds", type=str, default=None, help="Comma-separated seeds")
    ap.add_argument(
        "--log-level",
        default=DEFAULT_LOG_LEVEL,
        help=f"Python log level (default: {DEFAULT_LOG_LEVEL})",
    )
    ap.add_argument(
        "--log-question-max-chars",
        type=int,
        default=DEFAULT_LOG_QUESTION_MAX_CHARS,
        help=f"Max chars for question previews in logs (default: {DEFAULT_LOG_QUESTION_MAX_CHARS})",
    )
    ap.add_argument(
        "--log-prompt-max-chars",
        type=int,
        default=DEFAULT_LOG_PROMPT_MAX_CHARS,
        help=f"Max chars for prompt previews in logs (default: {DEFAULT_LOG_PROMPT_MAX_CHARS})",
    )
    ap.add_argument(
        "--log-path-sample-items",
        type=int,
        default=DEFAULT_LOG_PATH_SAMPLE_ITEMS,
        help=f"Max path samples in logging diagnostics (default: {DEFAULT_LOG_PATH_SAMPLE_ITEMS})",
    )
    ap.add_argument(
        "--log-to-file",
        action="store_true",
        default=DEFAULT_LOG_TO_FILE,
        help=f"Write runtime logs to out-dir/{DEFAULT_LOG_FILENAME} (default: {DEFAULT_LOG_TO_FILE})",
    )
    ap.add_argument(
        "--no-log-to-file",
        action="store_false",
        dest="log_to_file",
        help="Disable writing logs into the output directory",
    )
    ap.add_argument(
        "--log-file",
        default=DEFAULT_LOG_FILENAME,
        help=f"Run log file name or absolute path (default: {DEFAULT_LOG_FILENAME})",
    )
    ap.add_argument(
        "--evidence-audit",
        action="store_true",
        default=EVIDENCE_AUDIT_ENABLED_DEFAULT,
        help="Write per-question evidence-delivery audit artifacts and parquet path-match summaries",
    )
    ap.add_argument(
        "--no-evidence-audit",
        action="store_false",
        dest="evidence_audit",
        help="Disable evidence-delivery audit artifacts",
    )

    args = ap.parse_args()
    _setup_logging(args.log_level)
    args.log_question_max_chars = max(32, int(args.log_question_max_chars))
    args.log_prompt_max_chars = max(64, int(args.log_prompt_max_chars))
    args.log_path_sample_items = max(1, int(args.log_path_sample_items))

    # Compute effective quote-bypass mode (BC-002)
    effective_qb_mode = args.quote_bypass_mode  # default: "auto"
    if args.quote_bypass:
        effective_qb_mode = "on"  # legacy flag overrides
    args._effective_qb_mode = effective_qb_mode
    out_dir_auto = False

    if args.rsqt and not args.parquet:
        args.parquet = args.rsqt
    if not args.parquet:
        raise SystemExit("Missing --parquet (or legacy --rsqt)")

    # Resolve primary inputs using cwd/script-dir/repo-root search, with
    # compatibility aliases for renamed pack/config files.
    args.pack = str(_resolve_existing_path(args.pack, script_dir=_script_dir, aliases=PATH_ALIASES, label="pack"))
    args.parquet = str(_resolve_existing_path(args.parquet, script_dir=_script_dir, aliases=PATH_ALIASES, label="parquet"))
    args.index = str(_resolve_existing_path(args.index, script_dir=_script_dir, aliases=PATH_ALIASES, label="index"))
    args.engine_specs = str(
        _resolve_existing_path(args.engine_specs, script_dir=_script_dir, aliases=PATH_ALIASES, label="engine specs")
    )

    if args.out_dir:
        args.out_dir = str(_resolve_out_dir(args.out_dir, default_base=_default_out_dir))
    else:
        out_dir_auto = True
        auto_leaf = _auto_out_dir_leaf(model=str(args.model or DEFAULT_MODEL), pack_path=Path(args.pack))
        args.out_dir = str(_resolve_out_dir(auto_leaf, default_base=_default_out_dir))

    # Optional prompt paths: resolve if present, but remain fail-open if missing.
    args.system_prompt_file = _resolve_optional_existing_path(args.system_prompt_file, script_dir=_script_dir, aliases=PATH_ALIASES)
    args.system_prompt_grounding_file = _resolve_optional_existing_path(
        args.system_prompt_grounding_file, script_dir=_script_dir, aliases=PATH_ALIASES
    )
    args.system_prompt_analyze_file = _resolve_optional_existing_path(
        args.system_prompt_analyze_file, script_dir=_script_dir, aliases=PATH_ALIASES
    )

    run_log_path: Path | None = None
    if args.log_to_file:
        log_path_arg = Path(str(args.log_file)).expanduser()
        if not log_path_arg.is_absolute():
            log_path_arg = Path(args.out_dir) / log_path_arg
        run_log_path = _attach_log_file_handler(log_path_arg, args.log_level)
    args._run_log_file = str(run_log_path) if run_log_path else None

    _log_event(
        logging.INFO,
        "main.args.parsed",
        fn="main",
        log_level=args.log_level,
        log_question_max_chars=args.log_question_max_chars,
        log_prompt_max_chars=args.log_prompt_max_chars,
        log_path_sample_items=args.log_path_sample_items,
        log_to_file=args.log_to_file,
        log_file=args._run_log_file or "(disabled)",
        evidence_audit=args.evidence_audit,
        out_dir=args.out_dir,
        out_dir_auto=out_dir_auto,
    )
    _log_event(
        logging.INFO,
        "main.paths.resolved",
        fn="main",
        pack=args.pack,
        parquet=args.parquet,
        index=args.index,
        engine_specs=args.engine_specs,
        grounding_prompt=args.system_prompt_grounding_file,
        analyze_prompt=args.system_prompt_analyze_file,
        out_dir=args.out_dir,
    )

    print(f"Using pack: {args.pack}")
    print(f"Using engine specs: {args.engine_specs}")
    print(f"Using parquet: {args.parquet}")
    print(f"Using index: {args.index}")
    print(f"Using grounding prompt: {args.system_prompt_grounding_file}")
    print(f"Using analyze prompt: {args.system_prompt_analyze_file}")
    if args.system_prompt_file:
        print(f"Using legacy shared prompt: {args.system_prompt_file}")
    if out_dir_auto:
        print(f"Using auto output directory: {args.out_dir}")
    else:
        print(f"Using output directory: {args.out_dir}")
    print(f"Using evidence audit: {args.evidence_audit}")
    if args._run_log_file:
        print(f"Using run log: {args._run_log_file}")

    if args.replicate:
        return _run_replicates(args)

    pack_path = Path(args.pack)
    parquet_path = Path(args.parquet)
    index_path = Path(args.index)
    out_dir = Path(args.out_dir)
    engine_specs_path = Path(args.engine_specs)

    for p in (pack_path, parquet_path, index_path, engine_specs_path):
        if not p.exists():
            raise SystemExit(f"Missing required path: {p}")

    pack = _parse_pack(load_pack(pack_path))

    specs = load_engine_specs(engine_specs_path)
    if pack.engine not in specs:
        raise SystemExit(f"Engine '{pack.engine}' not found in engine specs: {args.engine_specs}")
    spec = specs[pack.engine]

    _run_single(pack_path=pack_path, pack=pack, spec=spec, args=args, parquet_path=parquet_path, index_path=index_path, out_dir=out_dir, all_specs=specs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
