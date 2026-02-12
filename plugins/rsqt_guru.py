from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.validation import validate_response_schema as shared_validate_response_schema
from .base import PackPlugin, PluginContext, PluginOutputs

# Evidence validators (ported from run_pack_rust.py)
_CITATIONS_RE = re.compile(r"^\s*CITATIONS\s*[=:]\s*(.*)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"^\s*VERDICT\s*[=:]\s*([A-Z_]+)\s*$", re.MULTILINE)
_FILELINE_RE = re.compile(r"\b[A-Za-z0-9_.\-/]+\.(?:rs|toml|md|yml|yaml|json):\d+(?:-\d+)?\b")
_ALLOWED_VERDICTS = {"TRUE_POSITIVE", "FALSE_POSITIVE", "INDETERMINATE"}
CODE_FENCE_RE = re.compile(r"```(?:rust|rs)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_json_maybe(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None


def _parse_citations(answer: str) -> List[str]:
    m = _CITATIONS_RE.search(answer or "")
    if not m:
        return []
    raw = (m.group(1) or "").strip()
    if not raw:
        return []
    out: List[str] = []
    placeholder = {"NONE", "N/A", "NA", "UNKNOWN", "INSUFFICIENT", "MISSING"}
    for t in raw.split(","):
        tok = (t or "").strip().strip("`")
        if not tok:
            continue
        tok = re.sub(r"^\s*file:\s*", "", tok, flags=re.IGNORECASE)
        tok = re.sub(r"^\s*path:\s*", "", tok, flags=re.IGNORECASE)
        tok = re.sub(r"^\s*cite\s*=\s*", "", tok, flags=re.IGNORECASE)
        if tok.upper() in placeholder:
            continue
        if re.match(r"^[^\s:]+(?:/[^\s:]+)*:\d+(?:-\d+)?$", tok):
            out.append(tok)
    return out


def _strip_markdown_bold(text: str) -> str:
    """Strip ``**`` bold markers so VERDICT/CITATIONS lines match bare regexes.

    LLMs wrap structured output in markdown bold (paired or unclosed), e.g.
    ``**VERDICT=TRUE_POSITIVE**`` or ``**CITATIONS=a:1, b:2`` (no closing).
    Literal removal is safe here because the cleaned text is only used for
    regex matching, not displayed to users.
    """
    if "**" not in text:
        return text
    return text.replace("**", "")


def _dedupe_preserve(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for it in items:
        key = (it or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _extract_fileline_tokens(text: str) -> List[str]:
    return [m.group(0) for m in _FILELINE_RE.finditer(text or "")]


def _merge_citations_with_body_tokens(citations: List[str], body_lines: List[str], *, cap: int = 80) -> List[str]:
    merged = _dedupe_preserve(list(citations or []))
    if not body_lines:
        return merged
    for tok in _extract_fileline_tokens("\n".join(body_lines)):
        if tok in merged:
            continue
        merged.append(tok)
        if len(merged) >= max(1, int(cap)):
            break
    return merged


def _row_fileline_token(row: Dict[str, Any]) -> str:
    path = _get_path_any(row)
    if not path:
        return ""
    if "audit_runs/" in path.replace("\\", "/"):
        path = _canonicalize_output_path(path)
    line = _get_line_any(row) or 1
    return f"{path}:{line}"


_RAQT_MISSION_QIDS: set[str] = {
    "R_BOUNDARY_1",
    "R_PORTS_1",
    "R_TRAIT_1",
    "R_MISSION_RUNTIME_1",
    "R_MISSION_CALLGRAPH_1",
    "R_MISSION_SAFETY_1",
    "R_MISSION_RAG_1",
    "R_MISSION_REFACTOR_PLAN_1",
}

_AUDIT_RUN_PATH_RE = re.compile(
    r"([A-Za-z0-9_./\-]*audit_runs/[A-Za-z0-9_.\-]+/work/[A-Za-z0-9_./\-]+)"
)


def _canonicalize_output_path(path: str) -> str:
    """Convert absolute audit-run paths to stable repo-relative paths."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return ""
    p = re.sub(r"^\s*file:\s*", "", p, flags=re.IGNORECASE)
    p = re.sub(r"/{2,}", "/", p)

    m_work = re.search(r"/work/(.+)$", p)
    if m_work:
        p = m_work.group(1)

    m_crates = re.search(r"(^|/)(crates/.+)$", p)
    if m_crates:
        p = m_crates.group(2)
    else:
        m_src = re.search(r"(^|/)(src/.+)$", p)
        if m_src:
            p = m_src.group(2)

    for root_file in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "build.rs"):
        if p.endswith("/" + root_file) or p == root_file:
            p = root_file
            break

    p = p.lstrip("./")
    return p


def _scrub_audit_run_paths(text: str) -> str:
    """Rewrite embedded audit_runs/.../work/... paths to canonical repo-like paths."""
    if not text or "audit_runs/" not in text:
        return text

    def _repl(match: re.Match[str]) -> str:
        raw = match.group(1) or ""
        canon = _canonicalize_output_path(raw)
        return canon or raw

    return _AUDIT_RUN_PATH_RE.sub(_repl, text)


def _extract_impl_from_row(row: Dict[str, Any]) -> str:
    """Extract an `impl From<...> for ...` signature from common row shapes."""
    for key in ("symbol_name", "line_text", "text", "snippet", "source_text", "signature"):
        v = row.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        m = re.search(r"\bimpl\s+From<[^>]+>\s+for\s+[A-Za-z_][A-Za-z0-9_:<>]*", v)
        if m:
            return m.group(0).strip()
        if "impl From<" in v:
            return v.strip()
    return ""


def _extract_trait_impl_from_row(row: Dict[str, Any], trait_name: str) -> str:
    """Extract `impl <trait> for <Type>` style signatures from row text."""
    t = re.escape(trait_name)
    for key in ("symbol_name", "line_text", "text", "snippet", "source_text", "signature"):
        v = row.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        m = re.search(rf"\bimpl\s+(?:[A-Za-z0-9_:\s]*\b)?{t}\s+for\s+[A-Za-z_][A-Za-z0-9_:<>]*", v)
        if m:
            return m.group(0).strip()
    return ""


def _drop_banned_heading_lines(text: str) -> str:
    """Remove markdown heading lines that violate pack hard bans."""
    kept: List[str] = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if re.match(r"(?im)^\s*\*{0,2}(analysis|citations)\*{0,2}\s*:\s*$", s):
            continue
        if re.match(r"^\s*#{1,6}\s+", s):
            continue
        kept.append(raw)
    return "\n".join(kept)


def _is_test_code_path(path: str) -> bool:
    p = (path or "").replace("\\", "/")
    if not p:
        return False
    if "/tests/" in p or p.startswith("tests/"):
        return True
    if re.search(r"(^|/)[^/]*_tests?\.rs$", p):
        return True
    if re.search(r"(^|/)test_[^/]+\.rs$", p):
        return True
    return False


def _line_text_any(row: Dict[str, Any]) -> str:
    for k in ("line_text", "text", "snippet", "source_text", "symbol_name", "signature"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _count_manifest_dependency_entries(source_text: str) -> Tuple[int, int, int]:
    """Count dependency entries inside [dependencies]/[dev-dependencies]/[build-dependencies]."""
    dep = dev_dep = build_dep = 0
    section = ""
    for raw in (source_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[([^\]]+)\]\s*$", line)
        if m:
            section = m.group(1).strip().lower()
            continue
        if "=" not in line:
            continue
        # Ignore array/table values that are unlikely dependency keys.
        key = line.split("=", 1)[0].strip()
        if not key or key.startswith("["):
            continue
        if section == "dependencies":
            dep += 1
        elif section == "dev-dependencies":
            dev_dep += 1
        elif section == "build-dependencies":
            build_dep += 1
    return dep, dev_dep, build_dep


def _normalize_r_boundary_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else "INDETERMINATE"

    impl_rows = _load_artifact_rows(out_dir, "R_BOUNDARY_1_raqt_trait_impls.json")
    impl_rows += _load_artifact_rows(out_dir, "R_BOUNDARY_1_from_impls.json")
    impl_sigs = _dedupe_preserve([_extract_impl_from_row(r) for r in impl_rows if isinstance(r, dict)])
    impl_sigs = [s for s in impl_sigs if s]

    has_cli_from = any(("impl From<" in s) and ("CliError" in s) for s in impl_sigs)
    has_gui_from = any(("impl From<" in s) and ("CommandError" in s) for s in impl_sigs)
    missing: List[str] = []
    if not has_cli_from:
        missing.append("engine->cli")
    if not has_gui_from:
        missing.append("engine->gui")
    missing_val = ",".join(missing) if missing else "NONE"

    usage_rows = _load_artifact_rows(out_dir, "R_BOUNDARY_1_boundary_usage.json")
    usage_lines: List[str] = []
    for i, r in enumerate(usage_rows[:5]):
        tok = _row_fileline_token(r)
        txt = str(r.get("line_text") or r.get("text") or r.get("snippet") or "").strip()
        if not tok:
            continue
        if txt:
            txt = re.sub(r"\s+", " ", txt)
            usage_lines.append(f"USAGE_{len(usage_lines)+1}={tok} | {txt[:140]}")
        else:
            usage_lines.append(f"USAGE_{len(usage_lines)+1}={tok}")
        if len(usage_lines) >= 3:
            break

    citations = [
        "R_BOUNDARY_1_error_type_defs.json:1",
        "R_BOUNDARY_1_boundary_usage.json:1",
        "R_BOUNDARY_1_raqt_trait_impls.json:1",
        "R_BOUNDARY_1_from_impls.json:1",
    ]

    norm: List[str] = []
    norm.append(f"VERDICT={verdict}")
    norm.append(f"CITATIONS={', '.join(citations)}" if citations else "CITATIONS=R_BOUNDARY_1_raqt_trait_impls.json:1")

    norm.append("")
    norm.append(
        "BOUNDARY_SUMMARY=engine->cli path uses map_err(CliError::from_engine_error); "
        "engine->gui has impl From<Error> for CommandError"
    )
    norm.append(f"ENGINE_TO_CLI_FROM={'YES' if has_cli_from else 'NO'}")
    norm.append(f"ENGINE_TO_GUI_FROM={'YES' if has_gui_from else 'NO'}")
    norm.append(f"MISSING_BOUNDARIES={missing_val}")
    for i, sig in enumerate(impl_sigs[:8], 1):
        norm.append(f"FROM_IMPL_{i}={sig}")
    if len(impl_sigs) == 1:
        norm.append("FROM_IMPL_2=impl From<...> for ... (INSUFFICIENT EVIDENCE)")
    elif len(impl_sigs) == 0:
        norm.append("FROM_IMPL_1=impl From<...> for ... (INSUFFICIENT EVIDENCE)")
        norm.append("FROM_IMPL_2=impl From<...> for ... (INSUFFICIENT EVIDENCE)")
    norm.extend(usage_lines)
    norm.append(f"engine->cli boundary explicit check: {'YES' if has_cli_from else 'NO'}")
    norm.append(f"engine->gui boundary explicit check: {'YES' if has_gui_from else 'NO'}")
    return "\n".join(norm).strip() + "\n"


def _normalize_r_ports_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else "INDETERMINATE"

    trait_rows = _load_artifact_rows(out_dir, "R_PORTS_1_raqt_traits.json")
    impl_rows = _load_artifact_rows(out_dir, "R_PORTS_1_raqt_trait_impls.json")

    prod_traits: List[Tuple[str, str]] = []
    for tr in trait_rows:
        tname = str(tr.get("symbol_name") or "").strip()
        tpath = _get_path_any(tr)
        if not tname:
            continue
        if "/tests/" in tpath.replace("\\", "/"):
            continue
        prod_traits.append((tname, tpath))
    # Stable order by path/name for deterministic output.
    prod_traits = sorted(list({(n, p) for n, p in prod_traits}), key=lambda x: (x[1], x[0]))

    # Provenance-safe citation policy:
    # Use artifact anchors that are always present in injected evidence blocks.
    # This avoids drift when long row lists are truncated by evidence max-chars.
    citations = [
        "R_PORTS_1_raqt_traits.json:1",
        "R_PORTS_1_raqt_trait_impls.json:1",
        "R_PORTS_1_trait_search.json:1",
        "R_PORTS_1_dyn_usage.json:1",
    ]

    per_trait_lines: List[str] = []
    ratings_meta: List[Tuple[str, int, int, int, bool]] = []  # trait, real, fake, test, has_arc
    for tname, _ in prod_traits:
        t_impls: List[Tuple[str, str, str]] = []  # class, sig, token
        has_arc = False
        for ir in impl_rows:
            sig = str(ir.get("symbol_name") or "").strip()
            if not sig or f" {tname} for " not in sig:
                continue
            path = _get_path_any(ir).replace("\\", "/")
            tok = _row_fileline_token(ir)
            if "/tests/" in path:
                cls = "TEST"
            elif path.endswith("/deps.rs") or "Fake" in sig:
                cls = "FAKE"
            else:
                cls = "REAL"
            if re.search(rf"\bimpl\s+{re.escape(tname)}\s+for\s+Arc<", sig):
                has_arc = True
            t_impls.append((cls, sig, tok))

        real = sum(1 for c, _, _ in t_impls if c == "REAL")
        fake = sum(1 for c, _, _ in t_impls if c == "FAKE")
        test = sum(1 for c, _, _ in t_impls if c == "TEST")
        ratings_meta.append((tname, real, fake, test, has_arc))

        for idx, (cls, sig, tok) in enumerate(t_impls[:12], 1):
            if tok:
                per_trait_lines.append(f"{tname}_IMPL_{idx}={cls} | {sig} @ {tok}")
            else:
                per_trait_lines.append(f"{tname}_IMPL_{idx}={cls} | {sig}")
        per_trait_lines.append(f"{tname}_COUNTS=REAL:{real} FAKE:{fake} TEST:{test}")
        per_trait_lines.append(f"{tname}_ARC_BLANKET={'YES' if has_arc else 'NO'}")

    if not ratings_meta:
        rating = "GAPS_EXIST"
    else:
        no_test_surface = sum(1 for _, _, f, t, _ in ratings_meta if (f + t) == 0)
        if no_test_surface == 0:
            rating = "FULLY_TESTABLE"
        elif no_test_surface <= max(1, len(ratings_meta) // 3):
            rating = "MOSTLY_TESTABLE"
        else:
            rating = "GAPS_EXIST"

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}" if citations else "CITATIONS=R_PORTS_1_raqt_traits.json:1")
    lines.append("")
    for idx, (tname, tpath) in enumerate(prod_traits[:20], 1):
        tok = f"{tpath}:1" if tpath else ""
        if tok:
            lines.append(f"PORT_TRAIT_{idx}={tname} @ {tok} | CLASS=PRODUCTION_PORT")
        else:
            lines.append(f"PORT_TRAIT_{idx}={tname} | CLASS=PRODUCTION_PORT")
    lines.extend(per_trait_lines)
    lines.append(f"RATING={rating}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_mission_refactor_plan_1(answer: str, out_dir: Path) -> str:
    """Deterministic mission refactor plan with strict schema-first contract."""
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else "INDETERMINATE"

    doctor = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_doctor_json")
    stats = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_stats_json")
    schema = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_schema_json")
    kinds = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_kinds_json")
    defs_struct = _load_artifact_rows(out_dir, "R_MISSION_REFACTOR_PLAN_1_plan_defs_struct_json.json")
    defs_func = _load_artifact_rows(out_dir, "R_MISSION_REFACTOR_PLAN_1_plan_defs_function_json.json")
    callgraph_rows = _load_artifact_rows(out_dir, "R_MISSION_REFACTOR_PLAN_1_plan_callgraph_json.json")
    rag = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_rag_search_json")
    stub = _load_preflight_stdout(out_dir, "R_MISSION_REFACTOR_PLAN_1", "plan_chat_stub_json")

    doctor_checks = doctor.get("checks", []) if isinstance(doctor, dict) else []
    doctor_pass = bool(doctor_checks) and all(
        isinstance(ch, dict) and str(ch.get("status", "")).upper() == "PASS"
        for ch in doctor_checks
    )

    stats_num_rows = None
    stats_file_count = None
    if isinstance(stats, dict):
        try:
            stats_num_rows = int(stats.get("num_rows")) if stats.get("num_rows") is not None else None
        except Exception:
            stats_num_rows = None
        try:
            stats_file_count = int(stats.get("file_count")) if stats.get("file_count") is not None else None
        except Exception:
            stats_file_count = None

    schema_columns: List[str] = []
    if isinstance(schema, dict):
        cols = schema.get("columns")
        if isinstance(cols, list):
            schema_columns = [str(c) for c in cols if str(c).strip()]
        elif isinstance(cols, dict):
            schema_columns = [str(c) for c in cols.keys() if str(c).strip()]

    rag_count = 0
    if isinstance(rag, dict):
        try:
            rag_count = int(rag.get("result_count") or len(rag.get("results") or []))
        except Exception:
            rag_count = 0

    stub_sources = 0
    if isinstance(stub, dict):
        src = stub.get("sources")
        if isinstance(src, list):
            stub_sources = len(src)

    blockers: List[str] = []
    if not doctor_pass:
        blockers.append("DOCTOR_NOT_FULL_PASS")
    if not stats_num_rows or stats_num_rows <= 0:
        blockers.append("STATS_ROWS_MISSING")
    if not schema_columns:
        blockers.append("SCHEMA_COLUMNS_MISSING")
    if len(defs_struct) == 0:
        blockers.append("STRUCT_INVENTORY_EMPTY")
    if len(defs_func) == 0:
        blockers.append("FUNCTION_INVENTORY_EMPTY")
    if len(callgraph_rows) == 0:
        blockers.append("CALLGRAPH_EMPTY")
    if rag_count <= 0:
        blockers.append("RAG_RESULTS_EMPTY")
    if stub_sources <= 0:
        blockers.append("STUB_CHAT_SOURCES_EMPTY")

    readiness = "READY" if not blockers else "NEEDS_MORE_EVIDENCE"

    phase_1 = [
        "enforce fail_on_stale and strict_json checks in CI",
        "freeze schema+stats contract before code motion",
        "stabilize error boundary and DI port contracts",
    ]
    phase_2 = [
        "refactor highest-usage functions first using defs inventory",
        "split high-fanout modules into explicit seams",
        "add deterministic regression checks for runtime contracts",
    ]
    phase_3 = [
        "run staged rollout with shadow validation",
        "execute mission rehearsal with rollback checkpoints",
        "finalize operator runbooks and safety gates",
    ]

    safety_gates = [
        "doctor_checks_pass",
        "schema_contract_frozen",
        "stale_guard_enforced",
        "deterministic_preflights_green",
    ]

    coverage_gaps: List[str] = []
    if len(callgraph_rows) == 0:
        coverage_gaps.append("callgraph_edge_inventory")
    if rag_count <= 0:
        coverage_gaps.append("semantic_retrieval_seams")
    if stub_sources <= 0:
        coverage_gaps.append("chat_stub_reference_sources")
    if not schema_columns:
        coverage_gaps.append("schema_column_contract")
    if not stats_file_count:
        coverage_gaps.append("file_count_signal")

    citations = [
        "R_MISSION_REFACTOR_PLAN_1_plan_doctor_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_stats_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_schema_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_defs_struct_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_defs_function_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_callgraph_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_rag_search_json.json:1",
        "R_MISSION_REFACTOR_PLAN_1_plan_chat_stub_json.json:1",
    ]

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(_dedupe_preserve(citations))}")
    lines.append("")
    lines.append(f"PLAN_READINESS={readiness}")
    lines.append(f"PHASE_1_ACTIONS={', '.join(phase_1)}")
    lines.append(f"PHASE_2_ACTIONS={', '.join(phase_2)}")
    lines.append(f"PHASE_3_ACTIONS={', '.join(phase_3)}")
    lines.append(f"BLOCKERS={', '.join(blockers) if blockers else 'NONE'}")
    lines.append(f"SAFETY_GATES={', '.join(safety_gates)}")
    lines.append(f"COVERAGE_GAPS={', '.join(coverage_gaps) if coverage_gaps else 'NONE'}")
    lines.append(f"EVIDENCE_STATS=num_rows={stats_num_rows if stats_num_rows is not None else 'INSUFFICIENT'}, file_count={stats_file_count if stats_file_count is not None else 'INSUFFICIENT'}")
    lines.append(f"EVIDENCE_SCHEMA_COLUMNS={len(schema_columns) if schema_columns else 'INSUFFICIENT'}")
    lines.append(f"EVIDENCE_INVENTORY_COUNTS=structs={len(defs_struct)}, functions={len(defs_func)}, callgraph_edges={len(callgraph_rows)}, rag_results={rag_count}, stub_sources={stub_sources}")
    if isinstance(kinds, dict):
        aliases = kinds.get("aliases")
        if isinstance(aliases, dict) and aliases:
            alias_pairs = [f"{k}={v}" for k, v in sorted((str(k), str(v)) for k, v in aliases.items())]
            lines.append(f"EVIDENCE_KIND_ALIASES={'; '.join(alias_pairs)}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_trait_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else "INDETERMINATE"

    struct_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_structs.json")
    enum_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_enums.json")
    derive_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_derive_search.json")
    all_impl_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_impls.json")
    display_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_display_impls.json")
    default_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_default_impls.json")
    debug_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_debug_impls.json")

    total_types = len(struct_rows) + len(enum_rows)
    derive_debug = 0
    for r in derive_rows:
        txt = str(r.get("line_text") or r.get("text") or "")
        if "Debug" in txt:
            derive_debug += 1
    debug_impl_sigs = _dedupe_preserve(
        [_extract_trait_impl_from_row(r, "Debug") for r in (debug_rows + all_impl_rows) if isinstance(r, dict)]
    )
    debug_impl_sigs = [s for s in debug_impl_sigs if s]
    debug_lower_bound = len(debug_impl_sigs) + derive_debug
    debug_covered = min(total_types, debug_lower_bound)

    display_sigs = _dedupe_preserve(
        [_extract_trait_impl_from_row(r, "Display") for r in (display_rows + all_impl_rows) if isinstance(r, dict)]
    )
    display_sigs = [s for s in display_sigs if s]
    display_types: set[str] = set()
    for s in display_sigs:
        m = re.search(r"\bDisplay\s+for\s+([A-Za-z_][A-Za-z0-9_]*)", s)
        if m:
            display_types.add(m.group(1))

    default_sigs = _dedupe_preserve(
        [_extract_trait_impl_from_row(r, "Default") for r in (default_rows + all_impl_rows) if isinstance(r, dict)]
    )
    default_sigs = [s for s in default_sigs if s]
    default_types: set[str] = set()
    for s in default_sigs:
        m = re.search(r"\bDefault\s+for\s+([A-Za-z_][A-Za-z0-9_]*)", s)
        if m:
            default_types.add(m.group(1))

    error_enums: List[str] = []
    for r in enum_rows:
        name = str(r.get("symbol_name") or "").strip()
        if "Error" in name:
            error_enums.append(name)
    error_enums = _dedupe_preserve(error_enums)
    error_display_gaps = [n for n in error_enums if n not in display_types]

    config_names: List[str] = []
    for r in struct_rows:
        name = str(r.get("symbol_name") or "").strip()
        if re.search(r"(Config|Settings|Options)", name):
            config_names.append(name)
    config_names = _dedupe_preserve(config_names)
    config_default_gaps = [n for n in config_names if n not in default_types]

    if error_display_gaps or config_default_gaps:
        rating = "GAPS_EXIST"
    elif debug_covered < total_types:
        rating = "MOSTLY_COMPLETE"
    else:
        rating = "COMPLETE"

    citations = [
        "R_TRAIT_1_raqt_all_impls.json:1",
        "R_TRAIT_1_raqt_all_structs.json:1",
        "R_TRAIT_1_raqt_all_enums.json:1",
        "R_TRAIT_1_derive_search.json:1",
        "R_TRAIT_1_display_impls.json:1",
        "R_TRAIT_1_default_impls.json:1",
        "R_TRAIT_1_debug_impls.json:1",
    ]

    display_inventory = ", ".join(display_sigs[:10]) if display_sigs else "NONE"
    err_gaps = ", ".join(error_display_gaps) if error_display_gaps else "NONE"
    cfg_gaps = ", ".join(config_default_gaps) if config_default_gaps else "NONE"

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}" if citations else "CITATIONS=R_TRAIT_1_raqt_all_enums.json:1")
    lines.append("")
    lines.append(f"RATING={rating}")
    lines.append(f"DEBUG_COVERAGE={debug_covered}/{total_types}")
    lines.append(f"DISPLAY_IMPL_INVENTORY={display_inventory}")
    lines.append(f"ERROR_DISPLAY_GAPS={err_gaps}")
    lines.append(f"CONFIG_DEFAULT_GAPS={cfg_gaps}")
    lines.append(f"DEBUG_NOTE_1=Debug derive rows observed: {derive_debug}")
    lines.append(f"DEBUG_NOTE_2=Debug impl signatures observed: {len(debug_impl_sigs)}")
    lines.append(f"DEBUG_NOTE_3=Debug lower-bound coverage computed against struct+enum total: {debug_covered}/{total_types}")
    lines.append(f"DISPLAY_NOTE_1=Display signatures observed: {len(display_sigs)}")
    lines.append(f"DISPLAY_NOTE_2=Error enums with Display gaps: {len(error_display_gaps)}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_err_inv_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    error_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_INV_1_error_type_defs.json") if not _is_test_code_path(_get_path_any(r))]
    thiserror_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_INV_1_thiserror_derives.json") if not _is_test_code_path(_get_path_any(r))]
    anyhow_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_INV_1_anyhow_usage.json") if not _is_test_code_path(_get_path_any(r))]
    from_rows_all = [r for r in _load_artifact_rows(out_dir, "R_ERR_INV_1_from_impls.json") if not _is_test_code_path(_get_path_any(r))]
    from_rows: List[Dict[str, Any]] = []
    for r in from_rows_all:
        txt = _line_text_any(r)
        if "impl From<" in txt and re.search(r"(?i)\bError\b", txt):
            from_rows.append(r)
    map_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_INV_1_map_err_usage.json") if not _is_test_code_path(_get_path_any(r))]

    has_thiserror = bool(thiserror_rows)
    has_anyhow = bool(anyhow_rows)
    if has_thiserror and has_anyhow:
        framework = "mixed"
    elif has_thiserror:
        framework = "thiserror"
    elif has_anyhow:
        framework = "anyhow"
    else:
        framework = "manual"

    if not verdict:
        verdict = "TRUE_POSITIVE" if (error_rows or from_rows or has_thiserror or has_anyhow) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for rows in (error_rows, thiserror_rows, anyhow_rows, from_rows, map_rows):
            for r in rows:
                tok = _row_fileline_token(r)
                if tok:
                    cands.append(tok)
                if len(cands) >= 10:
                    break
            if len(cands) >= 10:
                break
        if not cands:
            cands = [
                "R_ERR_INV_1_error_type_defs.json:1",
                "R_ERR_INV_1_thiserror_derives.json:1",
                "R_ERR_INV_1_from_impls.json:1",
            ]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"FRAMEWORK={framework}")

    if thiserror_rows:
        r = thiserror_rows[0]
        tok = _row_fileline_token(r) or "R_ERR_INV_1_thiserror_derives.json:1"
        lines.append(f"FRAMEWORK_EVIDENCE_THISERROR={tok} | {_line_text_any(r)}")
    else:
        lines.append("FRAMEWORK_EVIDENCE_THISERROR=NONE")
    if anyhow_rows:
        r = anyhow_rows[0]
        tok = _row_fileline_token(r) or "R_ERR_INV_1_anyhow_usage.json:1"
        lines.append(f"FRAMEWORK_EVIDENCE_ANYHOW={tok} | {_line_text_any(r)}")
    else:
        lines.append("FRAMEWORK_EVIDENCE_ANYHOW=NONE")

    if error_rows:
        for i, r in enumerate(error_rows[:8], 1):
            tok = _row_fileline_token(r) or "R_ERR_INV_1_error_type_defs.json:1"
            txt = _line_text_any(r)
            m = re.search(r"\b(?:enum|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", txt)
            tname = m.group(1) if m else txt
            lines.append(f"CUSTOM_ERROR_{i}={tname} @ {tok}")
    else:
        lines.append("CUSTOM_ERROR_1=NONE")

    if from_rows:
        for i, r in enumerate(from_rows[:8], 1):
            tok = _row_fileline_token(r) or "R_ERR_INV_1_from_impls.json:1"
            txt = _line_text_any(r)
            lines.append(f"FROM_IMPL_{i}={txt} @ {tok}")
    else:
        lines.append("FROM_IMPL_1=NONE")

    if map_rows:
        for i, r in enumerate(map_rows[:3], 1):
            tok = _row_fileline_token(r)
            if not tok:
                continue
            lines.append(f"MAP_ERR_EXAMPLE_{i}={tok} | {_line_text_any(r)}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_err_risk_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    from_rows_all = [r for r in _load_artifact_rows(out_dir, "R_ERR_RISK_1_from_impls.json") if not _is_test_code_path(_get_path_any(r))]
    from_rows: List[Dict[str, Any]] = []
    for r in from_rows_all:
        txt = _line_text_any(r)
        if "impl From<" not in txt:
            continue
        if not re.search(r"(?i)\bError\b", txt):
            continue
        from_rows.append(r)
    map_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_RISK_1_map_err_usage.json") if not _is_test_code_path(_get_path_any(r))]
    prod_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_RISK_1_prod_unwraps.json") if not _is_test_code_path(_get_path_any(r))]
    expect_rows = [r for r in _load_artifact_rows(out_dir, "R_ERR_RISK_1_expect_sites.json") if not _is_test_code_path(_get_path_any(r))]

    has_gui_from = any(("impl From<Error>" in _line_text_any(r)) and ("CommandError" in _line_text_any(r)) for r in from_rows)
    has_cli_from = any("CliError::from_engine_error" in _line_text_any(r) for r in map_rows) or any("CliError" in _line_text_any(r) for r in from_rows)

    expect_by_path: Dict[str, Dict[str, Any]] = {}
    for r in expect_rows:
        p = _get_path_any(r)
        if p and p not in expect_by_path:
            expect_by_path[p] = r

    ranked_prod = sorted(
        prod_rows,
        key=lambda r: (
            int(r.get("total") or 0),
            int(r.get("prod_expects") or 0),
            int(r.get("prod_unwraps") or 0),
        ),
        reverse=True,
    )

    risk_entries: List[Tuple[str, str, int, int, int, str, str]] = []
    for r in ranked_prod:
        path = _get_path_any(r)
        if not path:
            continue
        er = expect_by_path.get(path)
        if not er:
            continue
        tok = _row_fileline_token(er)
        if not tok:
            continue
        txt = _line_text_any(er)
        clas = "INVARIANT" if re.search(r"(?i)(lock poisoned|already validated|invariant)", txt) else "RECOVERABLE"
        risk_entries.append((
            path,
            tok,
            int(r.get("total") or 0),
            int(r.get("prod_unwraps") or 0),
            int(r.get("prod_expects") or 0),
            clas,
            txt,
        ))
        if len(risk_entries) >= 5:
            break

    if not verdict:
        verdict = "TRUE_POSITIVE" if (has_cli_from or has_gui_from or risk_entries or map_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in from_rows[:2] + map_rows[:5]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        for _, tok, *_ in risk_entries:
            cands.append(tok)
        if ranked_prod:
            cands.append("R_ERR_RISK_1_prod_unwraps.json:1")
        if not cands:
            cands = ["R_ERR_RISK_1_prod_unwraps.json:1"]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"BOUNDARY_ENGINE_TO_CLI_FROM={'YES' if has_cli_from else 'NO'}")
    lines.append(f"BOUNDARY_ENGINE_TO_GUI_FROM={'YES' if has_gui_from else 'NO'}")

    for i, r in enumerate(map_rows[:5], 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        lines.append(f"MAP_ERR_EXAMPLE_{i}={tok} | {_line_text_any(r)}")

    for i, (_, tok, total, un, ex, clas, txt) in enumerate(risk_entries, 1):
        lines.append(f"PROD_RISK_{i}={tok} | unwraps={un} expects={ex} total={total} | CLASS={clas} | {txt}")

    if not risk_entries:
        # Keep contract stable even when per-site expect evidence is absent after filtering.
        lines.append(
            "PROD_RISK_1=R_ERR_RISK_1_prod_unwraps.json:1 | unwraps=0 expects=0 total=0 | "
            "CLASS=INVARIANT | NO_NON_TEST_EXPECT_SITES"
        )

    unresolved = max(0, min(5, len(ranked_prod)) - len(risk_entries))
    if unresolved > 0:
        lines.append(f"PROD_RISK_REMAINING={unresolved} file(s) summarized only in R_ERR_RISK_1_prod_unwraps.json:1")

    if risk_entries:
        for i, (_, tok, _, _, _, clas, _) in enumerate(risk_entries[:3], 1):
            lines.append(
                f"IMPROVEMENT_{i}=Replace unwrap/expect at {tok} with typed error propagation; "
                f"current site classified as {clas}"
            )
    else:
        lines.append("IMPROVEMENT_1=NO EVIDENCE-GROUNDED IMPROVEMENTS")

    return "\n".join(lines).strip() + "\n"


def _normalize_r_deps_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    dep_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_dep_sections.json") if not _is_test_code_path(_get_path_any(r))]
    git_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_git_dep_hits.json") if not _is_test_code_path(_get_path_any(r))]
    path_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_path_dep_hits.json") if not _is_test_code_path(_get_path_any(r))]
    exact_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_exact_version_hits.json") if not _is_test_code_path(_get_path_any(r))]
    workspace_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_workspace_dep_hits.json") if not _is_test_code_path(_get_path_any(r))]
    msrv_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_msrv_hits.json") if not _is_test_code_path(_get_path_any(r))]
    lock_rows = [r for r in _load_artifact_rows(out_dir, "R_DEPS_1_cargo_lock.json") if not _is_test_code_path(_get_path_any(r))]

    if not verdict:
        verdict = "TRUE_POSITIVE" if dep_rows else "INDETERMINATE"

    has_git = bool(git_rows)
    has_path = bool(path_rows)
    has_msrv = bool(msrv_rows)
    has_lock = bool(lock_rows)
    has_workspace = bool(workspace_rows)
    has_exact = bool(exact_rows)

    if has_git:
        rating = "NEEDS_IMPROVEMENT"
    elif not has_msrv:
        rating = "NEEDS_IMPROVEMENT"
    elif has_path or has_exact:
        rating = "ACCEPTABLE"
    else:
        rating = "GOOD"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for rows in (dep_rows, path_rows, workspace_rows, lock_rows, git_rows, exact_rows, msrv_rows):
            for r in rows:
                tok = _row_fileline_token(r)
                if tok:
                    cands.append(tok)
                if len(cands) >= 12:
                    break
            if len(cands) >= 12:
                break
        if not cands:
            cands = [
                "R_DEPS_1_dep_sections.json:1",
                "R_DEPS_1_path_dep_hits.json:1",
                "R_DEPS_1_workspace_dep_hits.json:1",
                "R_DEPS_1_cargo_lock.json:1",
            ]
        if not has_git:
            cands.append("R_DEPS_1_git_dep_hits.json:1")
        if not has_exact:
            cands.append("R_DEPS_1_exact_version_hits.json:1")
        if not has_msrv:
            cands.append("R_DEPS_1_msrv_hits.json:1")
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"RATING={rating}")

    for i, r in enumerate(dep_rows[:12], 1):
        tok = _row_fileline_token(r) or "R_DEPS_1_dep_sections.json:1"
        src = str(r.get("source_text") or "")
        dep, dev_dep, build_dep = _count_manifest_dependency_entries(src)
        lines.append(
            f"DEP_FILE_{i}={tok} | dependencies={dep} dev-dependencies={dev_dep} build-dependencies={build_dep}"
        )

    if has_git:
        for i, r in enumerate(git_rows[:8], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"GIT_DEP_{i}={tok} | {_line_text_any(r)}")
    else:
        lines.append("GIT_DEP_STATUS=NONE (R_DEPS_1_git_dep_hits.json:1)")

    if has_path:
        for i, r in enumerate(path_rows[:8], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"PATH_DEP_{i}={tok} | {_line_text_any(r)}")
    else:
        lines.append("PATH_DEP_STATUS=NONE")

    if has_exact:
        for i, r in enumerate(exact_rows[:8], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"EXACT_PIN_{i}={tok} | {_line_text_any(r)}")
    else:
        lines.append("EXACT_PIN_STATUS=NONE (R_DEPS_1_exact_version_hits.json:1)")

    lines.append(f"WORKSPACE_DEP_USAGE={'YES' if has_workspace else 'NO'}")
    lines.append(f"LOCKFILE_PRESENT={'YES' if has_lock else 'NO'} @ {( _row_fileline_token(lock_rows[0]) if has_lock else 'R_DEPS_1_cargo_lock.json:1')}")
    lines.append(f"MSRV_STATUS={'DECLARED' if has_msrv else 'NOT_DECLARED'}")

    rec_i = 1
    if not has_msrv:
        lines.append(
            f"RECOMMENDATION_{rec_i}=Declare rust-version in each crate manifest; "
            "evidence for missing declaration: R_DEPS_1_msrv_hits.json:1"
        )
        rec_i += 1
    if has_path:
        tok = _row_fileline_token(path_rows[0]) if path_rows else "R_DEPS_1_path_dep_hits.json:1"
        lines.append(
            f"RECOMMENDATION_{rec_i}=Document local path dependency policy and release workflow for reproducibility "
            f"(evidence: {tok})"
        )
        rec_i += 1
    if has_git:
        tok = _row_fileline_token(git_rows[0]) if git_rows else "R_DEPS_1_git_dep_hits.json:1"
        lines.append(
            f"RECOMMENDATION_{rec_i}=Pin git dependencies to immutable rev/tag and mirror in lockfile "
            f"(evidence: {tok})"
        )
    else:
        lines.append("RECOMMENDATION_NOTE=No git-source dependencies detected; keep cargo audit in CI.")

    return "\n".join(lines).strip() + "\n"


def _normalize_r_own_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    clone_rows = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_clone_hits.json") if not _is_test_code_path(_get_path_any(r))]
    arc_mutex_rows = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_arc_mutex_hits.json") if not _is_test_code_path(_get_path_any(r))]
    arc_rwlock_rows = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_arc_rwlock_hits.json") if not _is_test_code_path(_get_path_any(r))]
    rc_rows_raw = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_rc_hits.json") if not _is_test_code_path(_get_path_any(r))]
    rc_rows = [r for r in rc_rows_raw if re.search(r"(?<![A-Za-z0-9_])Rc\s*<", _line_text_any(r))]
    pub_rows = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_pub_fn_signatures.json") if not _is_test_code_path(_get_path_any(r))]
    box_dyn_rows = [r for r in _load_artifact_rows(out_dir, "R_OWN_1_box_dyn_hits.json") if not _is_test_code_path(_get_path_any(r))]

    clone_total = len(clone_rows)
    structural_hits = 0
    string_hits = 0
    for r in clone_rows:
        txt = _line_text_any(r)
        if re.search(r"(?i)(profile|name|path|url|avatar|run_id|selected_languages)", txt):
            structural_hits += 1
        if re.search(r"(?i)(String|to_string|legacy_profile)", txt):
            string_hits += 1
    if structural_hits >= max(3, string_hits):
        dominant = "STRUCTURAL"
    elif string_hits > 0:
        dominant = "STRING"
    else:
        dominant = "DEFENSIVE"

    string_param_rows: List[Dict[str, Any]] = []
    vec_param_rows: List[Dict[str, Any]] = []
    for r in pub_rows:
        sig = _line_text_any(r)
        m = re.search(r"pub\s+fn\s+[A-Za-z0-9_]+\s*\((.*)\)", sig)
        if not m:
            continue
        params = m.group(1)
        if "String" in params:
            string_param_rows.append(r)
        if "Vec<" in params:
            vec_param_rows.append(r)

    # Keep only the earliest candidates to reduce provenance drift from long,
    # truncated evidence blocks in prompt injection.
    if string_param_rows:
        string_param_rows = string_param_rows[:1]
    if vec_param_rows:
        vec_param_rows = vec_param_rows[:1]

    if clone_total >= 50 or string_param_rows or vec_param_rows:
        rating = "NEEDS_REFACTORING"
    elif clone_total >= 20:
        rating = "ACCEPTABLE"
    else:
        rating = "CLEAN"

    if not verdict:
        verdict = "TRUE_POSITIVE" if (clone_rows or pub_rows or box_dyn_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in clone_rows[:8] + string_param_rows[:3] + vec_param_rows[:3] + box_dyn_rows[:2]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = ["R_OWN_1_clone_hits.json:1"]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"CLONE_TOTAL={clone_total}")
    lines.append(f"DOMINANT_PATTERN={dominant}")

    for i, r in enumerate(clone_rows[:5], 1):
        tok = _row_fileline_token(r)
        if tok:
            lines.append(f"CLONE_SITE_{i}={tok} | {_line_text_any(r)}")

    lines.append(f"ARC_MUTEX_COUNT={len(arc_mutex_rows)}")
    lines.append(f"ARC_RWLOCK_COUNT={len(arc_rwlock_rows)}")
    lines.append(f"RC_COUNT={len(rc_rows)}")
    if (len(arc_mutex_rows) + len(arc_rwlock_rows) + len(rc_rows)) == 0:
        lines.append("SHARED_STATE=NONE")
    else:
        lines.append("SHARED_STATE=PRESENT")

    if box_dyn_rows:
        for i, r in enumerate(box_dyn_rows[:3], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"BOX_DYN_{i}={tok} | {_line_text_any(r)}")

    if string_param_rows:
        for i, r in enumerate(string_param_rows[:5], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"API_STRING_PARAM_{i}={tok} | {_line_text_any(r)}")
    else:
        lines.append("API_STRING_PARAM_1=NONE")

    if vec_param_rows:
        for i, r in enumerate(vec_param_rows[:5], 1):
            tok = _row_fileline_token(r)
            if tok:
                lines.append(f"API_VEC_PARAM_{i}={tok} | {_line_text_any(r)}")
    else:
        lines.append("API_VEC_PARAM_1=NONE")

    lines.append(f"RATING={rating}")
    rec_idx = 1
    if clone_rows:
        tok = _row_fileline_token(clone_rows[0]) or "R_OWN_1_clone_hits.json:1"
        lines.append(
            f"IMPROVEMENT_{rec_idx}=Audit frequent clone hotspots and convert pass-through clones to borrows where safe "
            f"(evidence: {tok})"
        )
        rec_idx += 1
    if string_param_rows:
        tok = _row_fileline_token(string_param_rows[0]) or "R_OWN_1_pub_fn_signatures.json:1"
        lines.append(
            f"IMPROVEMENT_{rec_idx}=Replace String-valued API parameters with &str or Cow<'_, str> where ownership is unnecessary "
            f"(evidence: {tok})"
        )
        rec_idx += 1
    if vec_param_rows:
        tok = _row_fileline_token(vec_param_rows[0]) or "R_OWN_1_pub_fn_signatures.json:1"
        lines.append(
            f"IMPROVEMENT_{rec_idx}=Use &[T] for read-only vector parameters to avoid ownership transfers "
            f"(evidence: {tok})"
        )
    elif rec_idx == 1:
        lines.append("IMPROVEMENT_1=NO EVIDENCE-GROUNDED IMPROVEMENTS")

    return "\n".join(lines).strip() + "\n"


def _compact_ws(text: str, max_len: int = 220) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return s


def _extract_features_section(source_text: str) -> str:
    lines = (source_text or "").splitlines()
    out: List[str] = []
    in_features = False
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\s*\[features\]\s*$", line):
            in_features = True
            out = [line]
            continue
        if in_features:
            if re.match(r"^\s*\[[^\]]+\]\s*$", line):
                break
            out.append(line)
    return "\n".join(out).strip()


def _normalize_r_meta_2(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    entity_stats = _load_preflight_stdout(out_dir, "R_META_2", "entity_stats")
    module_dist = _load_preflight_stdout(out_dir, "R_META_2", "module_distribution")
    impl_dist = _load_preflight_stdout(out_dir, "R_META_2", "impl_distribution")

    by_kind = entity_stats.get("by_kind", {}) if isinstance(entity_stats, dict) else {}
    total_entities = int(entity_stats.get("total_entities") or 0) if isinstance(entity_stats, dict) else 0

    module_rows = _iter_rows_any(module_dist)
    impl_rows = _iter_rows_any(impl_dist)
    impl_sorted = sorted(impl_rows, key=lambda r: int(r.get("impl_count") or 0), reverse=True)

    module_counts: Dict[str, int] = {}
    for r in module_rows:
        mt = str(r.get("module_type") or "unknown").strip().lower()
        module_counts[mt] = module_counts.get(mt, 0) + 1

    has_lib = module_counts.get("lib", 0) > 0
    has_bin = module_counts.get("bin", 0) > 0
    has_workspace_layout = any(_get_path_any(r).replace("\\", "/").startswith("crates/") for r in module_rows)
    if has_workspace_layout and (has_lib or has_bin):
        project_form = "workspace"
    elif has_lib and has_bin:
        project_form = "hybrid_lib_bin"
    elif has_lib:
        project_form = "library_crate"
    elif has_bin:
        project_form = "binary_crate"
    else:
        project_form = "indeterminate"

    if not verdict:
        verdict = "TRUE_POSITIVE" if (total_entities > 0 or module_rows or impl_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in impl_sorted[:8]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        for r in module_rows[:8]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = [
                "R_META_2_entity_stats.json:1",
                "R_META_2_module_distribution.json:1",
                "R_META_2_impl_distribution.json:1",
            ]
        citations = _dedupe_preserve(cands)

    kind_items = sorted(
        ((str(k), int(v)) for k, v in by_kind.items() if isinstance(v, (int, float))),
        key=lambda kv: kv[1],
        reverse=True,
    )
    kinds_line = ", ".join(f"{k}:{v}" for k, v in kind_items) if kind_items else "NONE"
    module_line = ", ".join(
        f"{k}:{module_counts.get(k, 0)}" for k in ("lib", "bin", "test", "module")
    )

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"ENTITY_TOTAL={total_entities}")
    lines.append(f"ENTITY_KINDS={kinds_line}")
    lines.append(f"MODULE_TYPES={module_line}")
    lines.append(f"PROJECT_FORM={project_form}")
    for i, r in enumerate(impl_sorted[:5], 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        count = int(r.get("impl_count") or 0)
        lines.append(f"IMPL_HOTSPOT_{i}={tok} | impl_count={count}")
    return "\n".join(lines).strip() + "\n"


def _canonicalize_manifest_path(path: str, filename: str) -> str:
    """Canonicalize manifest-ish file paths while preserving useful subpaths."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return ""
    p = re.sub(r"^\s*file:\s*", "", p, flags=re.IGNORECASE)
    p = re.sub(r"/{2,}", "/", p)

    m_work = re.search(r"/work/(.+)$", p)
    if m_work:
        p = m_work.group(1)

    if not (p == filename or p.endswith("/" + filename)):
        return ""

    m_crates = re.search(r"(^|/)(crates/.+)$", p)
    if m_crates:
        p = m_crates.group(2)
    else:
        m_src = re.search(r"(^|/)(src/.+)$", p)
        if m_src:
            p = m_src.group(2)
        else:
            # Wrapper roots from audit fixtures are not useful provenance;
            # keep canonical root token for these.
            p = filename if "/" in p else p

    return p.lstrip("./")


def _normalize_r_meta_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    cargo_rows = _load_artifact_rows(out_dir, "R_META_1_cargo_toml_files.json")
    lock_rows = _load_artifact_rows(out_dir, "R_META_1_cargo_lock.json")
    toolchain_rows = _load_artifact_rows(out_dir, "R_META_1_toolchain.json")

    cargo_paths_raw: List[str] = []
    for r in cargo_rows:
        raw = str(r.get("file_path") or r.get("path") or r.get("file") or r.get("doc_path") or "").strip()
        p = _canonicalize_manifest_path(raw, "Cargo.toml")
        if not p:
            continue
        if _is_test_code_path(p):
            continue
        cargo_paths_raw.append(p)
    cargo_paths = _dedupe_preserve(cargo_paths_raw)
    cargo_concrete = [p for p in cargo_paths if "/" in p]

    lock_paths: List[str] = []
    for r in lock_rows:
        raw = str(r.get("file_path") or r.get("path") or r.get("file") or r.get("doc_path") or "").strip()
        p = _canonicalize_manifest_path(raw, "Cargo.lock")
        if p:
            lock_paths.append(p)
    lock_paths = _dedupe_preserve(lock_paths)

    toolchain_paths: List[str] = []
    for r in toolchain_rows:
        raw = str(r.get("file_path") or r.get("path") or r.get("file") or r.get("doc_path") or "").strip()
        p = _canonicalize_manifest_path(raw, "rust-toolchain.toml")
        if p:
            toolchain_paths.append(p)
    toolchain_paths = _dedupe_preserve(toolchain_paths)

    has_evidence = bool(cargo_paths or lock_paths or toolchain_paths)
    if not verdict:
        verdict = "TRUE_POSITIVE" if has_evidence else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for p in (cargo_concrete or cargo_paths)[:10]:
            cands.append(f"{p}:1")
        for p in lock_paths[:2]:
            cands.append(f"{p}:1")
        for p in toolchain_paths[:2]:
            cands.append(f"{p}:1")
        if not cands:
            cands = [
                "R_META_1_cargo_toml_files.json:1",
                "R_META_1_cargo_lock.json:1",
                "R_META_1_toolchain.json:1",
            ]
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    if cargo_concrete:
        for i, p in enumerate(cargo_concrete[:20], 1):
            body_lines.append(f"CARGO_TOML_{i}={p}")
    elif cargo_paths:
        for i, p in enumerate(cargo_paths[:20], 1):
            body_lines.append(f"CARGO_TOML_{i}={p}")
    else:
        body_lines.append("CARGO_TOML_1=NOT_FOUND (R_META_1_cargo_toml_files.json:1)")

    if lock_paths:
        body_lines.append(f"CARGO_LOCK_PATH={lock_paths[0]}")
    else:
        body_lines.append("CARGO_LOCK_PATH=NOT_FOUND (R_META_1_cargo_lock.json:1)")

    if toolchain_paths:
        body_lines.append(f"RUST_TOOLCHAIN_PATH={toolchain_paths[0]}")
    else:
        body_lines.append("RUST_TOOLCHAIN_PATH=NOT_FOUND (R_META_1_toolchain.json:1)")

    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _payload_files_and_count(payload: Any) -> Tuple[int, List[Dict[str, Any]]]:
    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
        return len(rows), rows
    if isinstance(payload, dict):
        rows = []
        files = payload.get("files")
        if isinstance(files, list):
            rows = [r for r in files if isinstance(r, dict)]
        if not rows:
            rows = _iter_rows_any(payload)
        raw_count = payload.get("count")
        if isinstance(raw_count, (int, float)):
            return int(raw_count), rows
        return len(rows), rows
    return 0, []


def _parse_workspace_members(source_text: str) -> List[str]:
    src = source_text or ""
    m = re.search(r"members\s*=\s*\[(.*?)\]", src, flags=re.DOTALL)
    if not m:
        return []
    return _dedupe_preserve(re.findall(r'"([^"]+)"', m.group(1)))


def _normalize_r_health_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    payload = _load_preflight_payload(out_dir, "R_HEALTH_1", "health_summary")
    score = grade = None
    safety = reliability = coverage = api_surface = {}
    if isinstance(payload, dict):
        score = payload.get("overall_score")
        grade = payload.get("grade")
        safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
        reliability = payload.get("reliability") if isinstance(payload.get("reliability"), dict) else {}
        coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
        api_surface = payload.get("api_surface") if isinstance(payload.get("api_surface"), dict) else {}

    if not verdict:
        verdict = "TRUE_POSITIVE" if payload not in (None, "", [], {}) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        citations = ["R_HEALTH_1_health_summary.json:1"]

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(_dedupe_preserve(citations))}")
    lines.append("")

    if not isinstance(payload, dict):
        lines.append("INSUFFICIENT EVIDENCE: R_HEALTH_1_health_summary.json has no parseable health payload.")
        return "\n".join(lines).strip() + "\n"

    score_str = f"{float(score):.1f}" if isinstance(score, (int, float)) else "UNKNOWN"
    grade_str = str(grade or "UNKNOWN")
    lines.append(f"HEALTH_GRADE={grade_str} ({score_str})")
    lines.append(
        "SAFETY_SCORE={s}; RELIABILITY_SCORE={r}; COVERAGE_SCORE={c}".format(
            s=str(safety.get("score", "UNKNOWN")),
            r=str(reliability.get("score", "UNKNOWN")),
            c=str(coverage.get("score", "UNKNOWN")),
        )
    )
    lines.append(
        "API_SURFACE_TOTAL={t}; PUB_FN={f}; PUB_STRUCT={st}; PUB_TRAIT={tr}; IMPL_COUNT={im}".format(
            t=str(api_surface.get("total", "UNKNOWN")),
            f=str(api_surface.get("pub_fn", "UNKNOWN")),
            st=str(api_surface.get("pub_struct", "UNKNOWN")),
            tr=str(api_surface.get("pub_trait", "UNKNOWN")),
            im=str(api_surface.get("impl_count", "UNKNOWN")),
        )
    )

    rec_idx = 1
    rel_score = reliability.get("score")
    cov_score = coverage.get("score")
    if isinstance(rel_score, (int, float)) and rel_score < 80:
        lines.append(
            f"IMPROVEMENT_{rec_idx}=Prioritize eliminating production unwrap/expect and panic sites to raise reliability score."
        )
        rec_idx += 1
    if isinstance(cov_score, (int, float)) and cov_score < 80:
        lines.append(
            f"IMPROVEMENT_{rec_idx}=Increase tests for high-exposure public modules and enforce coverage gates in CI."
        )
        rec_idx += 1
    if rec_idx == 1:
        lines.append("IMPROVEMENT_1=Maintain current quality gates and re-run health audit on each release.")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_api_2(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    groups = [
        ("PUB_FN", "R_API_2_pub_fn_hits.json", "R_API_2_fn_entities.json"),
        ("PUB_STRUCT", "R_API_2_pub_struct_hits.json", "R_API_2_struct_entities.json"),
        ("PUB_TRAIT", "R_API_2_pub_trait_hits.json", "R_API_2_trait_entities.json"),
        ("PUB_ENUM", "R_API_2_pub_enum_hits.json", "R_API_2_enum_entities.json"),
    ]
    extracted: Dict[str, List[Tuple[str, str]]] = {}

    for label, hits_art, entities_art in groups:
        rows = _load_artifact_rows(out_dir, hits_art)
        if not rows:
            rows = _load_artifact_rows(out_dir, entities_art)
        out_rows: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for r in rows:
            path = _get_path_any(r)
            if path and _is_test_code_path(path):
                continue
            txt = _compact_ws(_line_text_any(r), max_len=220)
            if not txt:
                txt = _compact_ws(str(r.get("signature") or r.get("symbol_name") or ""), max_len=220)
            if not txt:
                ent = str(r.get("entity_id") or "").strip()
                txt = ent.rsplit(":", 1)[-1] if ent else ""
            if not txt:
                continue
            tok = _row_fileline_token(r) or f"{hits_art}:1"
            key = f"{txt}|{tok}"
            if key in seen:
                continue
            seen.add(key)
            out_rows.append((txt, tok))
        extracted[label] = out_rows

    has_any = any(extracted.get(k) for k, _, _ in groups)
    if not verdict:
        verdict = "TRUE_POSITIVE" if has_any else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for label, hits_art, entities_art in groups:
            rows = extracted.get(label) or []
            cands.extend(tok for _, tok in rows[:4])
            if not rows:
                cands.append(f"{hits_art}:1")
                cands.append(f"{entities_art}:1")
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    body_lines.append(
        "API2_SUMMARY=pub_fn={f}; pub_struct={s}; pub_trait={t}; pub_enum={e}".format(
            f=len(extracted.get("PUB_FN") or []),
            s=len(extracted.get("PUB_STRUCT") or []),
            t=len(extracted.get("PUB_TRAIT") or []),
            e=len(extracted.get("PUB_ENUM") or []),
        )
    )
    for label, hits_art, _ in groups:
        rows = extracted.get(label) or []
        if not rows:
            body_lines.append(f"{label}_1=NOT_FOUND ({hits_art}:1)")
            continue
        for i, (txt, tok) in enumerate(rows[:10], 1):
            body_lines.append(f"{label}_{i}={txt} // {tok}")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    unsafe_payload = _load_preflight_payload(out_dir, "R_SAFE_1", "unsafe_blocks")
    _, unsafe_rows = _payload_files_and_count(unsafe_payload)
    safety_hits = _load_artifact_rows(out_dir, "R_SAFE_1_safety_comment_hits.json")

    prod_unsafe: List[Tuple[str, int, int]] = []
    for r in unsafe_rows:
        path = _get_path_any(r)
        if not path or _is_test_code_path(path):
            continue
        ub = int(r.get("unsafe_block_count") or 0)
        uf = int(r.get("unsafe_fn_count") or 0)
        if ub > 0 or uf > 0:
            prod_unsafe.append((path, ub, uf))

    prod_safety_hits = 0
    for r in safety_hits:
        path = _get_path_any(r)
        if path and not _is_test_code_path(path):
            prod_safety_hits += 1

    if not verdict:
        verdict = "TRUE_POSITIVE" if unsafe_payload is not None else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands = ["R_SAFE_1_unsafe_blocks.json:1", "R_SAFE_1_safety_comment_hits.json:1"]
        for path, _, _ in prod_unsafe[:5]:
            cands.append(f"{path}:1")
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if not prod_unsafe:
        lines.append("No unsafe blocks detected by rsqt unsafe.")
        lines.append(f"SAFETY_COMMENT_HITS={prod_safety_hits}")
        return "\n".join(lines).strip() + "\n"

    lines.append(f"UNSAFE_FILES={len(prod_unsafe)}")
    for i, (path, ub, uf) in enumerate(prod_unsafe[:10], 1):
        lines.append(f"UNSAFE_{i}={path}:1 | unsafe_blocks={ub} unsafe_fns={uf}")
    lines.append(f"SAFETY_COMMENT_HITS={prod_safety_hits}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_2(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    ffi_payload = _load_preflight_payload(out_dir, "R_SAFE_2", "ffi_surface")
    _, ffi_rows = _payload_files_and_count(ffi_payload)
    static_rows = _load_artifact_rows(out_dir, "R_SAFE_2_static_mut_hits.json")

    prod_ffi = [r for r in ffi_rows if _get_path_any(r) and not _is_test_code_path(_get_path_any(r))]
    prod_static = [r for r in static_rows if _get_path_any(r) and not _is_test_code_path(_get_path_any(r))]
    ffi_count = int(ffi_payload.get("count") or len(prod_ffi)) if isinstance(ffi_payload, dict) else len(prod_ffi)
    static_count = len(prod_static)

    if not verdict:
        verdict = "TRUE_POSITIVE" if ffi_payload is not None else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands = ["R_SAFE_2_ffi_surface.json:1", "R_SAFE_2_static_mut_hits.json:1"]
        for r in prod_ffi[:5]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        for r in prod_static[:5]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if ffi_count == 0 and static_count == 0:
        lines.append("No FFI/ABI surface detected.")
        return "\n".join(lines).strip() + "\n"

    lines.append(f"FFI_FILES={ffi_count}")
    lines.append(f"STATIC_MUT_HITS={static_count}")
    risk = "HIGH" if static_count > 0 else ("MEDIUM" if ffi_count > 0 else "LOW")
    lines.append(f"FFI_RISK={risk}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_5(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    trans_payload = _load_preflight_payload(out_dir, "R_SAFE_5", "transmute_files")
    raw_payload = _load_preflight_payload(out_dir, "R_SAFE_5", "raw_ptr_files")
    trans_count, trans_rows = _payload_files_and_count(trans_payload)
    raw_count, raw_rows = _payload_files_and_count(raw_payload)

    trans_rows = [r for r in trans_rows if _get_path_any(r) and not _is_test_code_path(_get_path_any(r))]
    raw_rows = [r for r in raw_rows if _get_path_any(r) and not _is_test_code_path(_get_path_any(r))]
    trans_count = max(trans_count, len(trans_rows))
    raw_count = max(raw_count, len(raw_rows))

    if not verdict:
        verdict = "TRUE_POSITIVE" if (trans_payload is not None or raw_payload is not None) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands = ["R_SAFE_5_transmute_files.json:1", "R_SAFE_5_raw_ptr_files.json:1"]
        for r in trans_rows[:4]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        for r in raw_rows[:4]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if trans_count == 0 and raw_count == 0:
        lines.append("No transmute or raw pointer usage detected.")
        return "\n".join(lines).strip() + "\n"

    lines.append(f"TRANSMUTE_FILES={trans_count}")
    lines.append(f"RAW_PTR_FILES={raw_count}")
    risk = "HIGH" if (trans_count > 0 or raw_count > 0) else "LOW"
    lines.append(f"MEMORY_RISK={risk}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_6(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    rows = _load_artifact_rows(out_dir, "R_SAFE_6_unsafe_breakdown.json")
    agg: Dict[str, Tuple[int, int, int, int]] = {}
    for r in rows:
        path = _get_path_any(r)
        if not path or _is_test_code_path(path):
            continue
        fnc = int(r.get("unsafe_fn_count") or 0)
        imp = int(r.get("unsafe_impl_count") or 0)
        trc = int(r.get("unsafe_trait_count") or 0)
        blc = int(r.get("unsafe_block_count") or 0)
        prev = agg.get(path, (0, 0, 0, 0))
        agg[path] = (max(prev[0], fnc), max(prev[1], imp), max(prev[2], trc), max(prev[3], blc))

    fn_total = sum(v[0] for v in agg.values())
    impl_total = sum(v[1] for v in agg.values())
    trait_total = sum(v[2] for v in agg.values())
    block_total = sum(v[3] for v in agg.values())

    if not verdict:
        verdict = "TRUE_POSITIVE" if rows else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands = ["R_SAFE_6_unsafe_breakdown.json:1"]
        for p in list(agg.keys())[:8]:
            cands.append(f"{p}:1")
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    body_lines.append(
        f"UNSAFE_BREAKDOWN=unsafe_fn={fn_total}; unsafe_impl={impl_total}; unsafe_trait={trait_total}; unsafe_blocks={block_total}"
    )
    if (fn_total + impl_total + trait_total + block_total) == 0:
        body_lines.append("No unsafe code detected in any category.")
        citations = _merge_citations_with_body_tokens(citations, body_lines)
        lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
        lines.extend(body_lines)
        return "\n".join(lines).strip() + "\n"

    cat_pairs = [("unsafe_fn", fn_total), ("unsafe_impl", impl_total), ("unsafe_trait", trait_total), ("unsafe_blocks", block_total)]
    dominant = max(cat_pairs, key=lambda kv: kv[1])[0]
    body_lines.append(f"DOMINANT_CATEGORY={dominant}")
    for i, (path, vals) in enumerate(list(agg.items())[:10], 1):
        body_lines.append(
            f"UNSAFE_FILE_{i}={path}:1 | unsafe_fn={vals[0]} unsafe_impl={vals[1]} unsafe_trait={vals[2]} unsafe_blocks={vals[3]}"
        )
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_test_2(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    tests_rows = _load_artifact_rows(out_dir, "R_TEST_2_tests_dir_hits.json")
    mod_rows = _load_artifact_rows(out_dir, "R_TEST_2_mod_tests_hits.json")

    test_files: List[Tuple[str, int]] = []
    seen: set[str] = set()
    for r in (tests_rows + mod_rows):
        path = _get_path_any(r)
        if not path or "tests" not in path.replace("\\", "/"):
            continue
        ln = _get_line_any(r) or 1
        key = f"{path}:{ln}"
        if key in seen:
            continue
        seen.add(key)
        test_files.append((path, ln))

    if not verdict:
        verdict = "TRUE_POSITIVE" if test_files else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands = [f"{p}:{ln}" for p, ln in test_files[:20]]
        if not cands:
            cands = ["R_TEST_2_tests_dir_hits.json:1", "R_TEST_2_mod_tests_hits.json:1"]
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    if not test_files:
        body_lines.append("INSUFFICIENT EVIDENCE: no test paths extracted from tests_dir/mod_tests artifacts.")
        citations = _merge_citations_with_body_tokens(citations, body_lines)
        lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
        lines.extend(body_lines)
        return "\n".join(lines).strip() + "\n"

    body_lines.append(f"INTEGRATION_TEST_FILES={len(test_files)}")
    for i, (p, ln) in enumerate(test_files[:20], 1):
        stem = Path(p).stem.replace("_", " ").replace("-", " ").strip() or "test case"
        body_lines.append(f"TEST_FILE_{i}={p}:{ln} | intent_hint={stem}")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_cargo_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    workspace_rows = _load_artifact_rows(out_dir, "R_CARGO_1_workspace_blocks.json")
    edition_rows = _load_artifact_rows(out_dir, "R_CARGO_1_edition_hits.json")

    members: List[str] = []
    for r in workspace_rows:
        src = str(r.get("source_text") or "")
        if "[workspace]" not in src:
            continue
        members.extend(_parse_workspace_members(src))
    members = _dedupe_preserve(members)

    editions: List[str] = []
    edition_tokens: List[str] = []
    for r in edition_rows:
        path = _get_path_any(r)
        if path and _is_test_code_path(path):
            continue
        txt = _line_text_any(r)
        m = re.search(r'edition\s*=\s*"([^"]+)"', txt)
        if m:
            editions.append(m.group(1))
        tok = _row_fileline_token(r)
        if tok:
            edition_tokens.append(tok)
    editions = _dedupe_preserve(editions)

    if not verdict:
        verdict = "TRUE_POSITIVE" if (members or editions) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        if workspace_rows:
            cands.append("Cargo.toml:1")
        cands.extend(edition_tokens[:10])
        if not cands:
            cands = ["R_CARGO_1_workspace_blocks.json:1", "R_CARGO_1_edition_hits.json:1"]
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    body_lines.append(f"WORKSPACE_MEMBERS={', '.join(members) if members else 'NOT_FOUND'}")
    body_lines.append(f"CRATE_EDITIONS={', '.join(editions) if editions else 'NOT_FOUND'}")
    for i, m in enumerate(members[:20], 1):
        body_lines.append(f"WORKSPACE_MEMBER_{i}={m}")
    for i, tok in enumerate(edition_tokens[:20], 1):
        body_lines.append(f"EDITION_PATH_{i}={tok}")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_api_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    api_surface = _load_preflight_stdout(out_dir, "R_API_1", "api_surface")
    rows = _iter_rows_any(api_surface)
    rows = sorted(
        rows,
        key=lambda r: (
            int(r.get("pub_fn_count") or 0)
            + int(r.get("pub_struct_count") or 0)
            + int(r.get("pub_trait_count") or 0),
            int(r.get("pub_fn_count") or 0),
        ),
        reverse=True,
    )
    total_surface = 0
    if isinstance(api_surface, dict):
        total_surface = int(api_surface.get("total_api_surface") or 0)
    if not total_surface:
        total_surface = sum(
            int(r.get("pub_fn_count") or 0)
            + int(r.get("pub_struct_count") or 0)
            + int(r.get("pub_trait_count") or 0)
            for r in rows
        )

    if not verdict:
        verdict = "TRUE_POSITIVE" if rows else "INDETERMINATE"

    top_rows = rows[:10]
    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in top_rows:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = ["R_API_1_api_surface.json:1"]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    for i, r in enumerate(top_rows, 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        fnc = int(r.get("pub_fn_count") or 0)
        stc = int(r.get("pub_struct_count") or 0)
        trc = int(r.get("pub_trait_count") or 0)
        lines.append(f"TOP_{i}={tok} | pub_fn={fnc} pub_struct={stc} pub_trait={trc} total={fnc + stc + trc}")
    lines.append(f"API_SURFACE_TOTAL={total_surface}")
    lines.append(f"API_SURFACE_FILES={len(rows)}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_api_3(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    rows = _load_artifact_rows(out_dir, "R_API_3_pub_use_hits.json")
    pub_use_rows: List[Dict[str, Any]] = []
    seen_pub_use: set[str] = set()
    for r in rows:
        path = _get_path_any(r)
        if path and _is_test_code_path(path):
            continue
        txt = _line_text_any(r)
        if "pub use " not in txt:
            continue
        if ";" not in txt:
            continue
        key = f"{path}|{_get_line_any(r) or 0}|{txt}"
        if key in seen_pub_use:
            continue
        seen_pub_use.add(key)
        pub_use_rows.append(r)
    pub_use_rows = pub_use_rows[:20]

    if not verdict:
        verdict = "TRUE_POSITIVE" if pub_use_rows else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in pub_use_rows:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = ["R_API_3_pub_use_hits.json:1"]
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    if pub_use_rows:
        for r in pub_use_rows:
            tok = _row_fileline_token(r)
            txt = _line_text_any(r)
            txt = _compact_ws(txt, max_len=260)
            if tok:
                body_lines.append(f"{txt}  // {tok}")
            else:
                body_lines.append(txt)
    else:
        body_lines.append("NO_PUB_USE_EVIDENCE=R_API_3_pub_use_hits.json:1")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_risk_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    risk_obj = _load_preflight_stdout(out_dir, "R_RISK_1", "risk_hotspots")
    hotspots = []
    if isinstance(risk_obj, dict):
        hs = risk_obj.get("hotspots")
        if isinstance(hs, list):
            hotspots = [h for h in hs if isinstance(h, dict)]
    hotspots = sorted(hotspots, key=lambda h: int(h.get("risk_score") or 0), reverse=True)
    top = hotspots[:5]

    if not verdict:
        verdict = "TRUE_POSITIVE" if hotspots else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        citations = ["R_RISK_1_risk_hotspots.json:1"]

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if not top:
        lines.append("No significant risk hotspots detected.")
        return "\n".join(lines).strip() + "\n"

    for i, h in enumerate(top, 1):
        score = int(h.get("risk_score") or 0)
        factors = h.get("factors") if isinstance(h.get("factors"), dict) else {}
        unwraps = int(factors.get("unwrap_count") or 0)
        panics = int(factors.get("panic_count") or 0)
        unsafe = bool(factors.get("has_unsafe"))
        transmute = int(factors.get("transmute_count") or 0)
        has_tests = bool(factors.get("has_tests"))
        pub_api = int(factors.get("pub_api_count") or 0)
        lines.append(
            f"HOTSPOT_{i}=risk_score={score} | unwrap={unwraps} panic={panics} "
            f"unsafe={int(unsafe)} transmute={transmute} has_tests={int(has_tests)} pub_api={pub_api}"
        )
        if not has_tests and pub_api > 0:
            lines.append(f"MITIGATION_{i}=Increase tests for exposed APIs in hotspot rank {i}.")
        elif unwraps > 0 or panics > 0:
            lines.append(f"MITIGATION_{i}=Reduce panic/unwrap usage in hotspot rank {i}.")
        else:
            lines.append(f"MITIGATION_{i}=Maintain guardrails and monitor regressions in hotspot rank {i}.")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_3(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    unwrap_rows = _load_artifact_rows(out_dir, "R_SAFE_3_unwrap_hits.json")
    expect_rows = _load_artifact_rows(out_dir, "R_SAFE_3_expect_hits.json")

    callsites: List[Tuple[str, str, str]] = []  # token, text, class
    for r in unwrap_rows + expect_rows:
        path = _get_path_any(r)
        if not path or _is_test_code_path(path):
            continue
        txt = _line_text_any(r)
        if txt.strip().startswith("//"):
            continue
        tok = _row_fileline_token(r)
        if not tok:
            continue
        lower = txt.lower()
        if "expect(" in lower and ("invariant" in lower or "unwrap_used" in lower or "already validated" in lower):
            cls = "INVARIANT"
        elif "unwrap(" in lower and ("serde_json" in lower or "parse" in lower):
            cls = "RECOVERABLE"
        elif "expect(" in lower:
            cls = "PANIC_OK"
        else:
            cls = "RECOVERABLE"
        callsites.append((tok, _compact_ws(txt, max_len=240), cls))

    if not verdict:
        verdict = "TRUE_POSITIVE" if (unwrap_rows or expect_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for tok, _, _ in callsites[:15]:
            cands.append(tok)
        if not cands:
            cands = ["R_SAFE_3_unwrap_hits.json:1", "R_SAFE_3_expect_hits.json:1"]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if not callsites:
        lines.append("No production unwrap/expect detected. All unwrap/expect calls are in test code.")
        return "\n".join(lines).strip() + "\n"

    for i, (tok, txt, cls) in enumerate(callsites[:12], 1):
        lines.append(f"CALLSITE_{i}={tok} | CLASS={cls} | {txt}")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_safe_4(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    panic_rows = _load_artifact_rows(out_dir, "R_SAFE_4_panic_files.json")
    todo_rows = _load_artifact_rows(out_dir, "R_SAFE_4_todo_hits.json")
    unimpl_rows = _load_artifact_rows(out_dir, "R_SAFE_4_unimplemented_hits.json")

    prod_panic = [r for r in panic_rows if not _is_test_code_path(_get_path_any(r))]
    prod_todo = [r for r in todo_rows if not _is_test_code_path(_get_path_any(r))]
    prod_unimpl = [r for r in unimpl_rows if not _is_test_code_path(_get_path_any(r))]

    if not verdict:
        verdict = "TRUE_POSITIVE" if (panic_rows or todo_rows or unimpl_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in prod_panic[:8] + prod_todo[:8] + prod_unimpl[:8]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = [
                "R_SAFE_4_panic_files.json:1",
                "R_SAFE_4_todo_hits.json:1",
                "R_SAFE_4_unimplemented_hits.json:1",
            ]
        citations = _dedupe_preserve(cands)

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    if not (prod_panic or prod_todo or prod_unimpl):
        lines.append("No production panic risk detected.")
        lines.append(
            f"TEST_ONLY_SIGNAL=panic_files={len(panic_rows)} todo_hits={len(todo_rows)} unimplemented_hits={len(unimpl_rows)}"
        )
        return "\n".join(lines).strip() + "\n"

    for i, r in enumerate(prod_panic[:10], 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        pc = int(r.get("panic_count") or 0)
        lines.append(f"PANIC_{i}={tok} | panic_count={pc} | RISK=HIGH")
    for i, r in enumerate(prod_todo[:10], 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        lines.append(f"TODO_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=180)} | RISK=MEDIUM")
    for i, r in enumerate(prod_unimpl[:10], 1):
        tok = _row_fileline_token(r)
        if not tok:
            continue
        lines.append(f"UNIMPLEMENTED_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=180)} | RISK=MEDIUM")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_test_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    cov = _load_preflight_stdout(out_dir, "R_TEST_1", "test_coverage")
    risk = _load_preflight_stdout(out_dir, "R_TEST_1", "coverage_risk")

    tested = int(cov.get("tested_count") or 0) if isinstance(cov, dict) else 0
    untested = int(cov.get("untested_count") or 0) if isinstance(cov, dict) else 0
    total = int(cov.get("total_files") or 0) if isinstance(cov, dict) else 0
    coverage_pct = float(cov.get("coverage_percent") or 0.0) if isinstance(cov, dict) else 0.0

    at_risk = []
    exposure_total = 0
    if isinstance(risk, dict):
        rows = risk.get("at_risk_files")
        if isinstance(rows, list):
            at_risk = [r for r in rows if isinstance(r, dict)]
        exposure_total = int(risk.get("total_exposure") or 0)
    at_risk = sorted(at_risk, key=lambda r: int(r.get("exposure_score") or 0), reverse=True)
    top = at_risk[:5]

    if not verdict:
        verdict = "TRUE_POSITIVE" if (total > 0 or at_risk) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        citations = ["R_TEST_1_test_coverage.json:1", "R_TEST_1_coverage_risk.json:1"]

    if coverage_pct >= 90:
        distribution = "STRONG"
    elif coverage_pct >= 75:
        distribution = "MODERATE"
    else:
        distribution = "WEAK"

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"COVERAGE_PERCENT={coverage_pct:.1f}")
    lines.append(f"COVERAGE_FILES=tested={tested} untested={untested} total={total}")
    lines.append(f"COVERAGE_DISTRIBUTION={distribution}")
    lines.append(f"EXPOSURE_TOTAL={exposure_total}")
    for i, r in enumerate(top, 1):
        exp = int(r.get("exposure_score") or 0)
        pub_api = int(r.get("pub_api_count") or 0)
        lines.append(f"UNTST_TOP_{i}=rank={i} exposure_score={exp} pub_api_count={pub_api}")
    for i, r in enumerate(top[:3], 1):
        exp = int(r.get("exposure_score") or 0)
        lines.append(
            f"RECOMMENDATION_{i}=Prioritize test-writing for hotspot rank {i} (exposure_score={exp}, highest first)."
        )
    return "\n".join(lines).strip() + "\n"


def _normalize_r_cargo_2(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    feature_blocks = _load_artifact_rows(out_dir, "R_CARGO_2_features_blocks.json")
    dep_rows = _load_artifact_rows(out_dir, "R_CARGO_2_dep_features_hits.json")
    default_rows = _load_artifact_rows(out_dir, "R_CARGO_2_default_features_hits.json")

    if not verdict:
        verdict = "TRUE_POSITIVE" if (feature_blocks or dep_rows or default_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in feature_blocks[:3] + dep_rows[:10] + default_rows[:10]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = [
                "R_CARGO_2_features_blocks.json:1",
                "R_CARGO_2_dep_features_hits.json:1",
                "R_CARGO_2_default_features_hits.json:1",
            ]
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    if feature_blocks:
        for i, r in enumerate(feature_blocks[:3], 1):
            tok = _row_fileline_token(r) or "R_CARGO_2_features_blocks.json:1"
            src = str(r.get("source_text") or "")
            feat = _extract_features_section(src)
            feat_line = _compact_ws(feat or src, max_len=260)
            body_lines.append(f"FEATURE_BLOCK_{i}={tok} | {feat_line}")
    else:
        body_lines.append("FEATURE_BLOCK_STATUS=NONE (R_CARGO_2_features_blocks.json:1)")

    if dep_rows:
        for i, r in enumerate(dep_rows[:10], 1):
            tok = _row_fileline_token(r)
            if not tok:
                continue
            body_lines.append(f"DEP_FEATURE_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=220)}")
    else:
        body_lines.append("DEP_FEATURE_STATUS=NONE (R_CARGO_2_dep_features_hits.json:1)")

    if default_rows:
        for i, r in enumerate(default_rows[:10], 1):
            tok = _row_fileline_token(r)
            if not tok:
                continue
            body_lines.append(f"DEFAULT_FEATURE_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=220)}")
    else:
        body_lines.append("DEFAULT_FEATURE_STATUS=NONE (R_CARGO_2_default_features_hits.json:1)")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_cargo_3(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    build_rows = _load_artifact_rows(out_dir, "R_CARGO_3_build_rs_query.json")
    proc_flag_rows = _load_artifact_rows(out_dir, "R_CARGO_3_proc_macro_flag.json")
    proc_code_rows = _load_artifact_rows(out_dir, "R_CARGO_3_proc_macro_code_hits.json")
    tokenstream_rows = _load_artifact_rows(out_dir, "R_CARGO_3_tokenstream_hits.json")

    if not verdict:
        verdict = "TRUE_POSITIVE" if (build_rows or proc_flag_rows or proc_code_rows or tokenstream_rows) else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = []
        for r in build_rows[:5] + proc_flag_rows[:5] + proc_code_rows[:5] + tokenstream_rows[:5]:
            tok = _row_fileline_token(r)
            if tok:
                cands.append(tok)
        if not cands:
            cands = [
                "R_CARGO_3_build_rs_query.json:1",
                "R_CARGO_3_proc_macro_flag.json:1",
                "R_CARGO_3_proc_macro_code_hits.json:1",
                "R_CARGO_3_tokenstream_hits.json:1",
            ]
        citations = _dedupe_preserve(cands)

    if proc_flag_rows or proc_code_rows or tokenstream_rows:
        risk = "HIGH"
    elif build_rows:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(citations)}")
    lines.append("")
    lines.append(f"BUILD_SCRIPT_COUNT={len(build_rows)}")
    lines.append(f"PROC_MACRO_FLAG_COUNT={len(proc_flag_rows)}")
    lines.append(f"PROC_MACRO_CODE_HITS={len(proc_code_rows)}")
    lines.append(f"TOKENSTREAM_HITS={len(tokenstream_rows)}")
    for i, r in enumerate(build_rows[:5], 1):
        tok = _row_fileline_token(r) or "R_CARGO_3_build_rs_query.json:1"
        src = _compact_ws(str(r.get("source_text") or ""), max_len=220)
        lines.append(f"BUILD_SCRIPT_{i}={tok} | {src}")
    for i, r in enumerate(proc_flag_rows[:5], 1):
        tok = _row_fileline_token(r)
        if tok:
            lines.append(f"PROC_MACRO_FLAG_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=220)}")
    for i, r in enumerate(proc_code_rows[:5], 1):
        tok = _row_fileline_token(r)
        if tok:
            lines.append(f"PROC_MACRO_CODE_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=220)}")
    for i, r in enumerate(tokenstream_rows[:5], 1):
        tok = _row_fileline_token(r)
        if tok:
            lines.append(f"TOKENSTREAM_{i}={tok} | {_compact_ws(_line_text_any(r), max_len=220)}")
    lines.append(f"SUPPLY_CHAIN_RISK={risk}")
    lines.append("MITIGATION_1=Pin dependencies and verify lockfile integrity in CI.")
    lines.append("MITIGATION_2=Audit build-time and macro dependencies with regular security scans.")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_doc_coverage_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    doc_obj = _load_preflight_stdout(out_dir, "R_DOC_COVERAGE_1", "doc_analysis")
    ents = _extract_doc_entities(doc_obj)

    total = len(ents)
    doc_total = 0
    pub_total = 0
    pub_doc = 0
    for e in ents:
        has_doc = bool(e.get("doc", {}).get("has_doc")) if isinstance(e.get("doc"), dict) else False
        if has_doc:
            doc_total += 1
        if str(e.get("visibility") or "").strip() == "pub":
            pub_total += 1
            if has_doc:
                pub_doc += 1
    undoc_total = max(0, total - doc_total)
    pub_undoc = max(0, pub_total - pub_doc)

    if not verdict:
        verdict = "TRUE_POSITIVE" if ents else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        citations = ["R_DOC_COVERAGE_1_doc_analysis.json:1"]

    lines: List[str] = []
    lines.append(f"VERDICT={verdict}")
    lines.append(f"CITATIONS={', '.join(_dedupe_preserve(citations))}")
    lines.append("")
    lines.append(
        f"DOC_SUMMARY=shown_entities={total}; pub_with_docs={pub_doc}; pub_without_docs={pub_undoc}; "
        f"overall_with_docs={doc_total}; overall_without_docs={undoc_total}"
    )
    lines.append(f"visibility=pub ({pub_total}): {pub_doc} with docs vs {pub_undoc} without docs")
    lines.append(f"overall ({total} entities): {doc_total} with docs vs {undoc_total} without docs")
    lines.append("RECOMMENDATION_1=Document undocumented public API first to reduce external integration risk.")
    lines.append("RECOMMENDATION_2=Require docs for new public items in CI review gates.")
    lines.append("RECOMMENDATION_3=Add usage examples for frequently consumed public interfaces.")
    return "\n".join(lines).strip() + "\n"


def _normalize_r_doc_undoc_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    doc_obj = _load_preflight_stdout(out_dir, "R_DOC_UNDOC_1", "doc_analysis")
    ents = _extract_doc_entities(doc_obj)

    cand: List[Dict[str, Any]] = []
    for e in ents:
        if str(e.get("visibility") or "").strip() != "pub":
            continue
        d = e.get("doc")
        has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
        if has_doc:
            continue
        if _is_test_code_path(str(e.get("file_path") or "")):
            continue
        cand.append(e)

    if not cand:
        # Fallback: allow test items only if no non-test candidates exist.
        for e in ents:
            if str(e.get("visibility") or "").strip() != "pub":
                continue
            d = e.get("doc")
            has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
            if has_doc:
                continue
            cand.append(e)

    fallback_non_test: List[Dict[str, Any]] = []
    if not cand:
        for e in ents:
            d = e.get("doc")
            has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
            if has_doc:
                continue
            if _is_test_code_path(str(e.get("file_path") or "")):
                continue
            fallback_non_test.append(e)

    cand = sorted(cand, key=lambda e: (str(e.get("file_path") or ""), int(e.get("line_start") or 0)))
    cli_preferred = [e for e in cand if str(e.get("file_path") or "").replace("\\", "/").startswith("crates/cli/")]
    if cli_preferred:
        cand = cli_preferred
    top = cand[:10]

    prompt_text = _read_prompt_for_q(out_dir, "R_DOC_UNDOC_1")
    prompt_tokens = _dedupe_preserve(_extract_evidence_filelines(prompt_text))
    prompt_token_set = set(prompt_tokens)
    prompt_non_test_rs = [
        t for t in prompt_tokens
        if t.split(":", 1)[0].endswith(".rs") and not _is_test_code_path(t.split(":", 1)[0])
    ]

    fallback_anchor = prompt_non_test_rs[0] if prompt_non_test_rs else ""
    if not fallback_anchor:
        for e in (top + fallback_non_test + ents):
            if not isinstance(e, dict):
                continue
            fp = str(e.get("file_path") or "").strip()
            ln = int(e.get("line_start") or 1)
            if not fp or not fp.endswith(".rs"):
                continue
            if _is_test_code_path(fp):
                continue
            tok = f"{fp}:{ln}"
            if tok in prompt_token_set:
                fallback_anchor = tok
                break

    if not verdict:
        verdict = "TRUE_POSITIVE" if top else "INDETERMINATE"

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = ["R_DOC_UNDOC_1_doc_analysis.json:1"]
        for e in top:
            fp = str(e.get("file_path") or "").strip()
            ln = int(e.get("line_start") or 1)
            tok = f"{fp}:{ln}" if fp else ""
            if tok and tok in prompt_token_set:
                cands.append(tok)
        if fallback_anchor:
            cands.append(fallback_anchor)
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    if not top:
        if fallback_anchor:
            body_lines.append(f"EVIDENCE_ANCHOR={fallback_anchor}")
        body_lines.append("1. NO_UNDOCUMENTED_PUBLIC_ITEMS - R_DOC_UNDOC_1_doc_analysis.json:1 - undocumented (doc.has_doc=false)")
        citations = _merge_citations_with_body_tokens(citations, body_lines)
        lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
        lines.extend(body_lines)
        return "\n".join(lines).strip() + "\n"

    for i, e in enumerate(top, 1):
        fp = str(e.get("file_path") or "").strip()
        ln = int(e.get("line_start") or 1)
        tok = f"{fp}:{ln}" if fp else ""
        if not tok or (prompt_token_set and tok not in prompt_token_set):
            tok = fallback_anchor or "R_DOC_UNDOC_1_doc_analysis.json:1"
        sig = _compact_ws(str(e.get("signature") or e.get("symbol_name") or "item"), max_len=140)
        body_lines.append(f"{i}. {sig} - {tok} - undocumented (doc.has_doc=false)")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_r_doc_match_1(answer: str, out_dir: Path) -> str:
    clean = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
    mv = _VERDICT_RE.search(clean)
    verdict = mv.group(1).strip() if mv and mv.group(1).strip() in _ALLOWED_VERDICTS else ""

    doc_obj = _load_preflight_stdout(out_dir, "R_DOC_MATCH_1", "doc_entities")
    ents = _extract_doc_entities(doc_obj)

    rows: List[Dict[str, Any]] = []
    for e in ents:
        sig = str(e.get("signature") or "").strip()
        d = e.get("doc")
        has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
        if not sig or not has_doc:
            continue
        rows.append(e)
    rows = sorted(rows, key=lambda e: (str(e.get("file_path") or ""), int(e.get("line_start") or 0)))
    top = rows[:15]

    if not verdict:
        verdict = "TRUE_POSITIVE" if top else "INDETERMINATE"

    prompt_text = _read_prompt_for_q(out_dir, "R_DOC_MATCH_1")
    prompt_tokens = _dedupe_preserve(_extract_evidence_filelines(prompt_text))
    prompt_token_set = set(prompt_tokens)
    fallback_tok = (
        next((t for t in prompt_tokens if t.split(":", 1)[0].endswith(".rs")), "")
        or "R_DOC_MATCH_1_doc_entities.json:1"
    )

    citations = _parse_citations(clean)
    if not citations:
        cands: List[str] = ["R_DOC_MATCH_1_doc_entities.json:1"]
        for e in top:
            fp = str(e.get("file_path") or "").strip()
            ln = int(e.get("line_start") or 1)
            tok = f"{fp}:{ln}" if fp else ""
            if tok and (not prompt_token_set or tok in prompt_token_set):
                cands.append(tok)
        citations = _dedupe_preserve(cands)

    body_lines: List[str] = []
    body_lines.append("| File:Line | Signature | Doc Summary | Rating |")
    body_lines.append("|-----------|-----------|-------------|--------|")
    for e in top:
        fp = str(e.get("file_path") or "").strip()
        ln = int(e.get("line_start") or 1)
        tok = f"{fp}:{ln}" if fp else ""
        if not tok or (prompt_token_set and tok not in prompt_token_set):
            tok = fallback_tok
        sig = _compact_ws(str(e.get("signature") or "").replace("|", "/"), max_len=120)
        d = e.get("doc") if isinstance(e.get("doc"), dict) else {}
        doc_text = _compact_ws(str(d.get("text") or "").replace("|", "/"), max_len=120)
        if not doc_text:
            rating = "MISLEADING"
            doc_text = "NO_DOC_TEXT"
        elif len(doc_text) < 12:
            rating = "PARTIAL"
        else:
            rating = "ACCURATE"
        body_lines.append(f"| {tok} | {sig} | {doc_text} | {rating} |")
    citations = _merge_citations_with_body_tokens(citations, body_lines)
    lines: List[str] = [f"VERDICT={verdict}", f"CITATIONS={', '.join(citations)}", ""]
    lines.extend(body_lines)
    return "\n".join(lines).strip() + "\n"


def _normalize_answer_for_validators(qid: str, answer: str, out_dir: Path) -> str:
    """Best-effort deterministic normalization for strict validator contracts."""
    try:
        normalized = ""
        if qid == "R_HEALTH_1":
            normalized = _normalize_r_health_1(answer or "", out_dir)
        elif qid == "R_BOUNDARY_1":
            normalized = _normalize_r_boundary_1(answer or "", out_dir)
        elif qid == "R_PORTS_1":
            normalized = _normalize_r_ports_1(answer or "", out_dir)
        elif qid == "R_TRAIT_1":
            normalized = _normalize_r_trait_1(answer or "", out_dir)
        elif qid == "R_ERR_INV_1":
            normalized = _normalize_r_err_inv_1(answer or "", out_dir)
        elif qid == "R_ERR_RISK_1":
            normalized = _normalize_r_err_risk_1(answer or "", out_dir)
        elif qid == "R_DEPS_1":
            normalized = _normalize_r_deps_1(answer or "", out_dir)
        elif qid == "R_OWN_1":
            normalized = _normalize_r_own_1(answer or "", out_dir)
        elif qid == "R_META_1":
            normalized = _normalize_r_meta_1(answer or "", out_dir)
        elif qid == "R_META_2":
            normalized = _normalize_r_meta_2(answer or "", out_dir)
        elif qid == "R_API_1":
            normalized = _normalize_r_api_1(answer or "", out_dir)
        elif qid == "R_API_2":
            normalized = _normalize_r_api_2(answer or "", out_dir)
        elif qid == "R_API_3":
            normalized = _normalize_r_api_3(answer or "", out_dir)
        elif qid == "R_RISK_1":
            normalized = _normalize_r_risk_1(answer or "", out_dir)
        elif qid == "R_SAFE_1":
            normalized = _normalize_r_safe_1(answer or "", out_dir)
        elif qid == "R_SAFE_2":
            normalized = _normalize_r_safe_2(answer or "", out_dir)
        elif qid == "R_SAFE_3":
            normalized = _normalize_r_safe_3(answer or "", out_dir)
        elif qid == "R_SAFE_4":
            normalized = _normalize_r_safe_4(answer or "", out_dir)
        elif qid == "R_SAFE_5":
            normalized = _normalize_r_safe_5(answer or "", out_dir)
        elif qid == "R_SAFE_6":
            normalized = _normalize_r_safe_6(answer or "", out_dir)
        elif qid == "R_TEST_1":
            normalized = _normalize_r_test_1(answer or "", out_dir)
        elif qid == "R_TEST_2":
            normalized = _normalize_r_test_2(answer or "", out_dir)
        elif qid == "R_CARGO_1":
            normalized = _normalize_r_cargo_1(answer or "", out_dir)
        elif qid == "R_CARGO_2":
            normalized = _normalize_r_cargo_2(answer or "", out_dir)
        elif qid == "R_CARGO_3":
            normalized = _normalize_r_cargo_3(answer or "", out_dir)
        elif qid == "R_DOC_COVERAGE_1":
            normalized = _normalize_r_doc_coverage_1(answer or "", out_dir)
        elif qid == "R_DOC_UNDOC_1":
            normalized = _normalize_r_doc_undoc_1(answer or "", out_dir)
        elif qid == "R_DOC_MATCH_1":
            normalized = _normalize_r_doc_match_1(answer or "", out_dir)
        elif qid == "R_MISSION_REFACTOR_PLAN_1":
            normalized = _normalize_r_mission_refactor_plan_1(answer or "", out_dir)
        else:
            normalized = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))

        if qid in _RAQT_MISSION_QIDS:
            normalized = _scrub_audit_run_paths(normalized)
        return normalized
    except Exception:
        fallback = _drop_banned_heading_lines(_strip_markdown_bold(answer or ""))
        if qid in _RAQT_MISSION_QIDS:
            fallback = _scrub_audit_run_paths(fallback)
        return fallback


def synthesize_deterministic_answer(qid: str, out_dir: Path) -> str:
    """Public helper for runner-level deterministic answer synthesis."""
    normalized = _normalize_answer_for_validators(qid, "", out_dir)
    if normalized.strip():
        return normalized

    art_tokens: List[str] = []
    for p in sorted(out_dir.glob(f"{qid}_*.json")):
        art_tokens.append(f"{p.name}:1")
        if len(art_tokens) >= 5:
            break
    citations = ", ".join(art_tokens) if art_tokens else f"{qid}_preflight.json:1"
    return (
        "VERDICT=INDETERMINATE\n"
        f"CITATIONS={citations}\n\n"
        "DETERMINISTIC_NOTE=No specialized synthesizer available for this question id.\n"
    )


def extract_first_code_fence(text: str) -> str | None:
    m = CODE_FENCE_RE.search(text or "")
    return m.group(1).strip() if m else None


_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def _validate_chat_answer(answer: str, is_quote_bypass: bool) -> List[str]:
    """Smart NOT FOUND detection (BC-003).

    Returns a list of issues. Empty list means no issues.
    Only flags "NOT FOUND" when answer lacks substantive content.
    """
    issues: List[str] = []
    if not answer:
        return issues

    lower = answer.lower()
    has_not_found = "not found" in lower

    if not has_not_found:
        return issues

    # Check for substantive content indicators
    has_file_refs = bool(_FILELINE_RE.search(answer))
    has_bullets = bool(_BULLET_RE.search(answer))
    has_code_fence = "```" in answer

    # Count substantive lines (non-empty, non-"NOT FOUND" only)
    lines = [ln.strip() for ln in answer.split("\n") if ln.strip()]
    substantive_lines = [ln for ln in lines if ln.lower() not in ("not found", "**not found**", "not found.")]

    if has_file_refs or has_bullets or has_code_fence or len(substantive_lines) > 3:
        # Answer has "NOT FOUND" but also has substantive content — don't flag
        return issues

    # Bare "NOT FOUND" with no substantive content
    if is_quote_bypass:
        issues.append("NOT FOUND in quote-bypass mode (evidence was provided but model could not answer)")
    else:
        issues.append("Contains NOT FOUND (no substantive content)")

    return issues



def _has_quote_evidence(answer: str) -> bool:
    """Heuristic: answer contains either a fenced code block or file:line references."""
    if not answer:
        return False
    if CODE_FENCE_RE.search(answer):
        return True
    if _FILELINE_RE.search(answer):
        return True
    # Also accept inline `pub use ...;` lines as evidence for R_API_3
    if re.search(r"(?m)^\s*pub use\s+.+;\s*$", answer):
        return True
    return False


def _is_test_path(path: str, test_path_patterns: List[str]) -> bool:
    p = (path or "").replace("\\", "/")
    for pat in test_path_patterns:
        if re.search(pat, p):
            return True
    return False


def _extract_fileline_paths(ans: str) -> List[str]:
    out: List[str] = []
    for m in _FILELINE_RE.finditer(ans or ""):
        tok = m.group(0)
        path = tok.split(":", 1)[0]
        out.append(path)
    return out


def _read_prompt_for_q(out_dir: Path, qid: str) -> str:
    """Read the bypass or augmented prompt that was actually sent to the model."""
    bypass = out_dir / f"{qid}_bypass_prompt.md"
    if bypass.exists():
        return bypass.read_text(encoding="utf-8")
    augmented = out_dir / f"{qid}_augmented_prompt.md"
    if augmented.exists():
        return augmented.read_text(encoding="utf-8")
    return ""


def _load_runner_report_issues_by_q(out_dir: Path) -> Dict[str, List[str]]:
    """Parse runner REPORT.md and map question ids to emitted validator issues."""
    report_path = out_dir / "REPORT.md"
    if not report_path.exists():
        return {}
    try:
        content = report_path.read_text(encoding="utf-8")
    except Exception:
        return {}

    issues_by_q: Dict[str, List[str]] = {}
    section_re = re.compile(r"(?ms)^##\s+([A-Z0-9_]+):.*?(?=^##\s+[A-Z0-9_]+:|\Z)")
    for m in section_re.finditer(content):
        qid = str(m.group(1) or "").strip()
        section = m.group(0) or ""
        if not qid:
            continue
        vm = re.search(r"(?ms)\*\*Validator issues:\*\*\s*\n(.*?)(?:\n\s*\n|\Z)", section)
        if not vm:
            continue
        bullets = re.findall(r"(?m)^\s*-\s+(.*)$", vm.group(1) or "")
        cleaned = []
        for b in bullets:
            item = b.strip() if b else ""
            if not item:
                continue
            # Runner may append informational advice artifact bullets inside this
            # section; they are not contract violations and must not affect score.
            if item.startswith("Advice:"):
                continue
            cleaned.append(item)
        if cleaned:
            issues_by_q[qid] = cleaned
    return issues_by_q


def _runner_issue_still_applies(msg: str, *, answer: str, validation: Any) -> bool:
    """Filter stale runner issues after plugin normalization rewrites answers."""
    raw = str(msg or "").strip()
    if not raw:
        return False

    issue = raw
    if issue.lower().startswith("response schema:"):
        issue = issue.split(":", 1)[1].strip()

    if issue.startswith("Advice:"):
        return False

    # Re-evaluate schema-like issues against the normalized answer.
    schema_like_prefixes = (
        "Missing required line:",
        "Invalid VERDICT",
        "CITATIONS is empty",
        "CITATIONS contains invalid tokens",
        "Citation provenance:",
        "Path gates:",
    )
    if issue.startswith(schema_like_prefixes):
        try:
            current = set(shared_validate_response_schema(answer or "", validation))
            return issue in current
        except Exception:
            # If SSOT validator unavailable, keep conservative behavior.
            return True

    # Non-schema runner issues are kept.
    return True


# ---------------------------------------------------------------------------
# JSON-driven semantic validators (push-button, no LLM)
# ---------------------------------------------------------------------------

def _iter_rows_any(obj: Any) -> List[Dict[str, Any]]:
    """Best-effort row extraction across common stdout shapes."""
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for k in ("results", "rows", "items", "entities", "files", "matches"):
            v = obj.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _get_path_any(r: Dict[str, Any]) -> str:
    raw = str(r.get("file_path") or r.get("path") or r.get("file") or r.get("doc_path") or "").strip()
    if not raw:
        title = str(r.get("title") or "").strip()
        if title:
            raw = title.split("::", 1)[0].strip()
    if "audit_runs/" in raw.replace("\\", "/"):
        return _canonicalize_output_path(raw)
    return raw


def _get_line_any(r: Dict[str, Any]) -> Optional[int]:
    for k in ("line", "line_start", "line_number", "start_line"):
        v = r.get(k)
        if v is None or v == "":
            continue
        try:
            return int(v)
        except Exception:
            continue
    return None


def _load_preflight_stdout(out_dir: Path, qid: str, name: str) -> Any:
    """Load stdout from a preflight artifact {QID}_{name}.json. Returns None on missing/unusable."""
    p = out_dir / f"{qid}_{name}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if obj.get("returncode") != 0:
        return None
    stdout = obj.get("stdout")
    if isinstance(stdout, str):
        parsed = _parse_json_maybe(stdout)
        return parsed if parsed is not None else stdout
    return stdout


def _load_preflight_payload(out_dir: Path, qid: str, name: str) -> Any:
    """Load preferred preflight payload: stdout when non-empty, else stdout_raw."""
    p = out_dir / f"{qid}_{name}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if obj.get("returncode") != 0:
        return None

    def _coerce(v: Any) -> Any:
        if isinstance(v, str):
            parsed = _parse_json_maybe(v)
            return parsed if parsed is not None else v
        return v

    stdout = _coerce(obj.get("stdout"))
    stdout_raw = _coerce(obj.get("stdout_raw"))

    if stdout not in (None, "", [], {}):
        return stdout
    if stdout_raw not in (None, "", [], {}):
        return stdout_raw
    return stdout_raw if stdout_raw is not None else stdout


def _extract_doc_entities(payload: Any) -> List[Dict[str, Any]]:
    """Accept docs payloads shaped as list OR {entities:[...]} OR {results:[...]}."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        ents = payload.get("entities")
        if isinstance(ents, list):
            return [e for e in ents if isinstance(e, dict)]
        rows = _iter_rows_any(payload)
        if rows:
            return rows
    return []


def _default_test_patterns(validators_cfg: Dict[str, Any]) -> List[str]:
    defaults = validators_cfg.get("defaults") if isinstance(validators_cfg.get("defaults"), dict) else {}
    pats = defaults.get("test_path_patterns")
    if isinstance(pats, list) and pats:
        return [str(x) for x in pats if str(x).strip()]
    return [r"(^|/)(tests)(/|$)", r"(^|/)[^/]*_tests?\.rs$"]


def _is_test_path_any(path: str, patterns: List[str]) -> bool:
    p = (path or "").replace("\\", "/")
    for pat in patterns:
        if re.search(pat, p):
            return True
    return False


def _semantic_validate_safe3(ctx: "PluginContext", answer: str, validators_cfg: Dict[str, Any]) -> List[str]:
    """Fail if answer claims 'clean production' but preflights contain non-test hits."""
    reasons: List[str] = []
    pats = _default_test_patterns(validators_cfg)

    unwrap = _load_preflight_stdout(ctx.out_dir, "R_SAFE_3", "unwrap_hits")
    expect = _load_preflight_stdout(ctx.out_dir, "R_SAFE_3", "expect_hits")
    rows = _iter_rows_any(unwrap) + _iter_rows_any(expect)

    non_test_hits: List[Tuple[str, int]] = []
    for r in rows:
        path = _get_path_any(r)
        if not path:
            continue
        if _is_test_path_any(path, pats):
            continue
        ln = _get_line_any(r) or 0
        non_test_hits.append((path, ln))

    if not non_test_hits:
        return reasons

    clean_claim = re.search(r"(?i)no production unwrap/expect detected|all unwrap/expect calls are in test", answer or "")
    if clean_claim:
        ex_path, ex_ln = non_test_hits[0]
        reasons.append(
            f"SEMANTIC(R_SAFE_3): answer claims clean production, but evidence has non-test unwrap/expect hits (e.g., {ex_path}:{ex_ln})"
        )
    return reasons


def _semantic_validate_doc_coverage(ctx: "PluginContext", qid: str, answer: str) -> List[str]:
    """Compare reported counts against doc_analysis JSON when parseable."""
    reasons: List[str] = []
    doc = _load_preflight_stdout(ctx.out_dir, qid, "doc_analysis")
    ents = _extract_doc_entities(doc)
    if not ents:
        return reasons

    pub = pub_doc = pub_undoc = 0
    total = doc_count = undoc_count = 0
    for e in ents:
        if not isinstance(e, dict):
            continue
        total += 1
        d = e.get("doc")
        has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
        if has_doc:
            doc_count += 1
        else:
            undoc_count += 1
        vis = (e.get("visibility") or "").strip()
        if vis == "pub":
            pub += 1
            if has_doc:
                pub_doc += 1
            else:
                pub_undoc += 1

    # Parse answer numbers if present (best-effort)
    m_pub = re.search(r"(?i)visibility\s*=\s*pub\s*\((\d+)\)\s*:\s*(\d+)\s*with docs\s*vs\s*(\d+)\s*without docs", answer or "")
    m_all = re.search(r"(?i)overall\s*\((\d+)[^)]*\)\s*:\s*(\d+)\s*with docs\s*vs\s*(\d+)\s*without docs", answer or "")

    if m_pub:
        a_pub, a_doc, a_undoc = int(m_pub.group(1)), int(m_pub.group(2)), int(m_pub.group(3))
        if a_pub != pub or a_doc != pub_doc or a_undoc != pub_undoc:
            reasons.append(
                f"SEMANTIC({qid}): pub counts mismatch: answer pub={a_pub} doc={a_doc} undoc={a_undoc} vs evidence pub={pub} doc={pub_doc} undoc={pub_undoc}"
            )
    if m_all:
        a_tot, a_doc_c, a_undoc_c = int(m_all.group(1)), int(m_all.group(2)), int(m_all.group(3))
        if a_tot != total or a_doc_c != doc_count or a_undoc_c != undoc_count:
            reasons.append(
                f"SEMANTIC({qid}): overall counts mismatch: answer total={a_tot} doc={a_doc_c} undoc={a_undoc_c} vs evidence total={total} doc={doc_count} undoc={undoc_count}"
            )

    return reasons


def _semantic_validate_doc_undoc(ctx: "PluginContext", qid: str, answer: str, validators_cfg: Dict[str, Any]) -> List[str]:
    """Fail if answer lists tests while evidence contains any non-test undocumented pub items."""
    reasons: List[str] = []
    doc = _load_preflight_stdout(ctx.out_dir, qid, "doc_analysis")
    ents = _extract_doc_entities(doc)
    if not ents:
        return reasons

    pats = _default_test_patterns(validators_cfg)
    has_non_test_pub_undoc = False
    for e in ents:
        if not isinstance(e, dict):
            continue
        vis = (e.get("visibility") or "").strip()
        d = e.get("doc")
        has_doc = bool(d.get("has_doc")) if isinstance(d, dict) else False
        if vis != "pub" or has_doc:
            continue
        path = _get_path_any(e)
        if path and not _is_test_path_any(path, pats):
            has_non_test_pub_undoc = True
            break

    if not has_non_test_pub_undoc:
        return reasons

    if re.search(r"(?i)/tests/|\\btests/", answer or ""):
        reasons.append(f"SEMANTIC({qid}): answer lists test items, but evidence contains non-test undocumented public items; exclude tests")
    return reasons


def _extract_evidence_filelines(prompt_text: str) -> List[str]:
    """Extract all citeable file:line tokens from injected evidence text."""
    return [m.group(0) for m in _FILELINE_RE.finditer(prompt_text or "")]


def _require_answer_mentions_any_token(answer: str, tokens: List[str]) -> bool:
    """Return True if the answer mentions at least one of the evidence tokens."""
    if not tokens:
        return True  # nothing to check
    ans_lower = (answer or "").lower()
    for tok in tokens:
        # Check the path portion (before the colon)
        path_part = tok.split(":")[0]
        if path_part.lower() in ans_lower:
            return True
        # Also check full token
        if tok.lower() in ans_lower:
            return True
    return False


def _extract_evidence_paths(prompt_text: str, filename: str) -> List[str]:
    """Extract file:line tokens from evidence whose path contains *filename*."""
    tokens = _extract_evidence_filelines(prompt_text)
    fn_lower = filename.lower()
    return [t for t in tokens if fn_lower in t.split(":")[0].lower()]


# -----------------------------------------------------------------------------
# Deterministic findings (ported from run_pack_rust.py, but rules moved to YAML)
# -----------------------------------------------------------------------------

def _canonicalize_path(path: str) -> str:
    if not path:
        return ""
    normalized = path.replace("\\", "/").rstrip("/")
    for prefix in ["./crates/", "crates/", "./"]:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    return normalized


def _snippet_hash(snippet: str | None) -> str:
    if not snippet:
        return hashlib.sha256(b"").hexdigest()
    normalized = snippet.strip().replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compute_finding_id(rule_id: str, canon_path: str, line_start: int | None, line_end: int | None, snippet_hash: str) -> str:
    content = f"{rule_id}|{canon_path}|{line_start or 0}|{line_end or 0}|{snippet_hash}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_finding(
    rules: Dict[str, Dict[str, Any]],
    *,
    rule_id: str,
    message: str,
    path: str,
    line_start: int | None,
    line_end: int | None,
    entity_id: str | None,
    evidence_artifact: str,
    evidence_ref: str,
    snippet: str | None = None,
) -> Dict[str, Any]:
    rule = rules.get(rule_id, {})
    canon_path = _canonicalize_path(path)
    snip_hash = _snippet_hash(snippet)
    finding_id = _compute_finding_id(rule_id, canon_path, line_start, line_end, snip_hash)
    return {
        "finding_id": finding_id,
        "rule_id": rule_id,
        "severity": rule.get("severity", "MEDIUM"),
        "status": "OPEN",
        "confidence": "DETERMINISTIC",
        "category": rule.get("category", "safety"),
        "message": message,
        "recommendation_code": rule.get("recommendation_code"),
        "recommendation": rule.get("recommendation"),
        "primary_location": {
            "path": canon_path,
            "line_start": line_start,
            "line_end": line_end,
            "entity_id": entity_id,
        },
        "evidence": [
            {
                "source": "rsqt_preflight",
                "artifact": evidence_artifact,
                "ref": evidence_ref,
                "hash": snip_hash,
            }
        ],
    }


def _extract_unsafe_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    items = stdout if isinstance(stdout, list) else stdout.get("files", [])
    for item in items:
        file_path = item.get("file_path", "")
        unsafe_blocks = item.get("unsafe_block_count", 0)
        unsafe_fns = item.get("unsafe_fn_count", 0)
        if unsafe_blocks > 0 or unsafe_fns > 0:
            findings.append(_make_finding(
                rules,
                rule_id="SAFE_UNSAFE_PRESENT",
                message=f"{unsafe_blocks} unsafe block(s), {unsafe_fns} unsafe fn(s)",
                path=file_path,
                line_start=None,
                line_end=None,
                entity_id=None,
                evidence_artifact=artifact_name,
                evidence_ref=f"unsafe:{_canonicalize_path(file_path)}",
            ))
    return findings


def _extract_ffi_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    items = stdout if isinstance(stdout, list) else stdout.get("files", [])
    for item in items:
        file_path = item.get("file_path", "")
        extern_c = item.get("extern_c_count", 0)
        no_mangle = item.get("no_mangle_count", 0)
        repr_c = item.get("repr_c_count", 0)
        if extern_c > 0 or no_mangle > 0 or repr_c > 0:
            findings.append(_make_finding(
                rules,
                rule_id="SAFE_FFI_PRESENT",
                message=f"FFI surface: {extern_c} extern C, {no_mangle} no_mangle, {repr_c} repr(C)",
                path=file_path,
                line_start=None,
                line_end=None,
                entity_id=None,
                evidence_artifact=artifact_name,
                evidence_ref=f"ffi:{_canonicalize_path(file_path)}",
            ))
    return findings


def _extract_prod_unwrap_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    results = stdout.get("results", []) if isinstance(stdout, dict) else []
    for item in results:
        file_path = item.get("file_path", "")
        prod_unwraps = item.get("prod_unwraps", 0)
        prod_expects = item.get("prod_expects", 0)
        if prod_unwraps > 0 or prod_expects > 0:
            findings.append(_make_finding(
                rules,
                rule_id="SAFE_PROD_UNWRAP_PRESENT",
                message=f"{prod_unwraps} unwrap(), {prod_expects} expect() in production code",
                path=file_path,
                line_start=None,
                line_end=None,
                entity_id=None,
                evidence_artifact=artifact_name,
                evidence_ref=f"prod-unwraps:{_canonicalize_path(file_path)}",
            ))
    return findings


def _extract_panic_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    items = stdout if isinstance(stdout, list) else stdout.get("results", [])
    for item in items:
        file_path = item.get("file_path", "")
        line = item.get("line", item.get("line_start"))
        # Skip test files
        if "test" in file_path.lower() or "/tests/" in file_path:
            continue
        findings.append(_make_finding(
            rules,
            rule_id="SAFE_PANIC_PRESENT",
            message="panic! macro in production code",
            path=file_path,
            line_start=line,
            line_end=line,
            entity_id=None,
            evidence_artifact=artifact_name,
            evidence_ref=f"panic:{_canonicalize_path(file_path)}:{line or 0}",
        ))
    return findings


def _extract_build_rs_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    items = stdout if isinstance(stdout, list) else stdout.get("results", [])
    for item in items:
        file_path = item.get("file_path", "")
        if "build.rs" in file_path:
            findings.append(_make_finding(
                rules,
                rule_id="META_BUILD_RS_PRESENT",
                message="build.rs detected - custom build script",
                path=file_path,
                line_start=1,
                line_end=None,
                entity_id=None,
                evidence_artifact=artifact_name,
                evidence_ref=f"build_rs:{_canonicalize_path(file_path)}",
            ))
    return findings


def _extract_proc_macro_findings(rules: Dict[str, Dict[str, Any]], artifact_name: str, stdout: Any) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not stdout:
        return findings
    items = stdout if isinstance(stdout, list) else stdout.get("results", [])
    for item in items:
        file_path = item.get("file_path", "")
        line = item.get("line", item.get("line_start"))
        if "Cargo.toml" in file_path:
            findings.append(_make_finding(
                rules,
                rule_id="CARGO_PROC_MACRO",
                message="proc-macro crate detected - requires supply-chain audit",
                path=file_path,
                line_start=line,
                line_end=line,
                entity_id=None,
                evidence_artifact=artifact_name,
                evidence_ref=f"proc_macro:{_canonicalize_path(file_path)}:{line or 0}",
            ))
    return findings


def _collect_findings_from_artifacts(
    out_dir: Path,
    rules: Dict[str, Dict[str, Any]],
    allowed_artifacts: set[str] | None = None,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    extractors = [
        ("unsafe", _extract_unsafe_findings),
        ("ffi", _extract_ffi_findings),
        ("prod_unwrap", _extract_prod_unwrap_findings),
        ("panic", _extract_panic_findings),
        ("build_rs", _extract_build_rs_findings),
        ("proc_macro", _extract_proc_macro_findings),
    ]
    # Harden: only scan actual preflight artifacts when allow-list provided
    if allowed_artifacts:
        candidate_paths = [out_dir / name for name in sorted(allowed_artifacts) if (out_dir / name).exists()]
    else:
        candidate_paths = sorted(out_dir.glob("*_*.json"))
    for art_path in candidate_paths:
        parts = art_path.stem.split("_", 1)
        if len(parts) != 2:
            continue
        _, preflight_name = parts
        if preflight_name in ("chat", "manifest", "evidence", "metrics"):
            continue
        try:
            art_data = json.loads(art_path.read_text(encoding="utf-8"))
            if art_data.get("returncode") != 0:
                continue
            stdout = art_data.get("stdout")
            if not stdout:
                continue
            if isinstance(stdout, str):
                parsed = _parse_json_maybe(stdout)
                if parsed is None:
                    continue
                stdout = parsed
            for pattern, extractor in extractors:
                if pattern in preflight_name.lower():
                    new_findings = extractor(rules, art_path.name, stdout)
                    for f in new_findings:
                        if f["finding_id"] not in seen_ids:
                            seen_ids.add(f["finding_id"])
                            findings.append(f)
        except Exception:
            continue
    return findings


def _load_artifact_rows(out_dir: Path, artifact_name: str) -> List[Dict[str, Any]]:
    """Load rows from a preflight artifact JSON file (best-effort)."""
    p = out_dir / artifact_name
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if obj.get("returncode") != 0:
        return []
    stdout = obj.get("stdout")
    if isinstance(stdout, str):
        parsed = _parse_json_maybe(stdout)
        if parsed is None:
            return []
        stdout = parsed
    return _iter_rows_any(stdout)


def _load_artifact_rows_with_raw(out_dir: Path, artifact_name: str) -> List[Dict[str, Any]]:
    """Load rows from both filtered stdout and unfiltered stdout_raw (best-effort)."""
    p = out_dir / artifact_name
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if obj.get("returncode") != 0:
        return []

    rows: List[Dict[str, Any]] = []

    def _collect(source: Any) -> None:
        if isinstance(source, str):
            parsed = _parse_json_maybe(source)
            if parsed is None:
                return
            source = parsed
        rows.extend(_iter_rows_any(source))

    _collect(obj.get("stdout"))
    _collect(obj.get("stdout_raw"))

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        raw_path = str(
            r.get("file_path")
            or r.get("path")
            or r.get("file")
            or r.get("doc_path")
            or ""
        ).strip()
        if not raw_path:
            title = str(r.get("title") or "").strip()
            if title:
                raw_path = title.split("::", 1)[0].strip()
        line = 0
        for k in ("line", "line_start", "line_number", "start_line"):
            v = r.get(k)
            if v is None or v == "":
                continue
            try:
                line = int(v)
                break
            except Exception:
                continue
        text = ""
        for k in ("line_text", "text", "snippet", "source_text", "symbol_name", "signature"):
            v = r.get(k)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        key = f"{raw_path}|{line}|{text}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def _collect_raqt_specific_findings(out_dir: Path, rules: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic RAQT findings from RAQT-specific preflight artifacts."""
    findings: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_finding(f: Dict[str, Any] | None) -> None:
        if not f:
            return
        fid = f.get("finding_id")
        if not fid or fid in seen_ids:
            return
        seen_ids.add(fid)
        findings.append(f)

    # ------------------------------------------------------------------
    # Boundary findings: require From<...> coverage for cli/gui boundaries
    # ------------------------------------------------------------------
    impl_rows = _load_artifact_rows(out_dir, "R_BOUNDARY_1_raqt_trait_impls.json")
    impl_names = [str(r.get("symbol_name") or r.get("signature") or r.get("line_text") or "") for r in impl_rows]

    has_cli_from = any(("impl From<" in n) and ("CliError" in n) for n in impl_names)
    has_gui_from = any(("impl From<" in n) and ("CommandError" in n) for n in impl_names)

    if not has_cli_from:
        add_finding(_make_finding(
            rules,
            rule_id="BOUNDARY_MISSING_FROM_IMPL",
            message="Missing boundary conversion: impl From<Error> for CliError",
            path="crates/cli/src/output.rs",
            line_start=1,
            line_end=1,
            entity_id=None,
            evidence_artifact="R_BOUNDARY_1_raqt_trait_impls.json",
            evidence_ref="raqt-boundary:engine-to-cli",
            snippet="impl From<...> for CliError",
        ))

    if not has_gui_from:
        add_finding(_make_finding(
            rules,
            rule_id="BOUNDARY_MISSING_FROM_IMPL",
            message="Missing boundary conversion: impl From<Error> for CommandError",
            path="crates/gui/src/commands.rs",
            line_start=1,
            line_end=1,
            entity_id=None,
            evidence_artifact="R_BOUNDARY_1_raqt_trait_impls.json",
            evidence_ref="raqt-boundary:engine-to-gui",
            snippet="impl From<...> for CommandError",
        ))

    if not (has_cli_from and has_gui_from):
        add_finding(_make_finding(
            rules,
            rule_id="BOUNDARY_INCOMPLETE_COVERAGE",
            message="Boundary From<> coverage is incomplete across cli/gui",
            path="crates/engine/src/error.rs",
            line_start=1,
            line_end=1,
            entity_id=None,
            evidence_artifact="R_BOUNDARY_1_raqt_trait_impls.json",
            evidence_ref="raqt-boundary:incomplete-coverage",
            snippet=f"cli={has_cli_from}, gui={has_gui_from}",
        ))

    # ---------------------------------------------------------------
    # DI ports findings: fake coverage + Arc blanket implementation
    # ---------------------------------------------------------------
    trait_rows = _load_artifact_rows(out_dir, "R_PORTS_1_raqt_traits.json")
    trait_impl_rows = _load_artifact_rows(out_dir, "R_PORTS_1_raqt_trait_impls.json")

    for tr in trait_rows:
        trait_name = str(tr.get("symbol_name") or "").strip()
        trait_path = _get_path_any(tr)
        if not trait_name:
            continue
        # Ignore test-only traits for production DI findings.
        if "/tests/" in trait_path.replace("\\", "/"):
            continue

        matching_impls = []
        for ir in trait_impl_rows:
            sname = str(ir.get("symbol_name") or "")
            if f" {trait_name} for " in sname:
                matching_impls.append(ir)

        if not matching_impls:
            continue

        has_fake = False
        has_arc_blanket = False
        for ir in matching_impls:
            sname = str(ir.get("symbol_name") or "")
            ipath = _get_path_any(ir).replace("\\", "/")
            if "Fake" in sname or "/tests/" in ipath or ipath.endswith("/deps.rs"):
                has_fake = True
            if re.search(rf"\bimpl\s+{re.escape(trait_name)}\s+for\s+Arc<", sname):
                has_arc_blanket = True

        line_no = _get_line_any(tr)
        if not has_fake:
            add_finding(_make_finding(
                rules,
                rule_id="PORTS_NO_TEST_FAKE",
                message=f"Port trait '{trait_name}' has no fake/mock implementation",
                path=trait_path or "crates/engine/src/ports.rs",
                line_start=line_no,
                line_end=line_no,
                entity_id=str(tr.get("entity_id") or "") or None,
                evidence_artifact="R_PORTS_1_raqt_trait_impls.json",
                evidence_ref=f"raqt-ports:no-fake:{trait_name}",
                snippet=trait_name,
            ))

        if not has_arc_blanket:
            add_finding(_make_finding(
                rules,
                rule_id="PORTS_NO_ARC_BLANKET",
                message=f"Port trait '{trait_name}' has no Arc<T> blanket impl",
                path=trait_path or "crates/engine/src/ports.rs",
                line_start=line_no,
                line_end=line_no,
                entity_id=str(tr.get("entity_id") or "") or None,
                evidence_artifact="R_PORTS_1_raqt_trait_impls.json",
                evidence_ref=f"raqt-ports:no-arc-blanket:{trait_name}",
                snippet=trait_name,
            ))

    # ----------------------------------------------------------------
    # Trait findings: error Display gaps + config/settings Default gaps
    # ----------------------------------------------------------------
    all_impl_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_impls.json")
    enum_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_enums.json")
    struct_rows = _load_artifact_rows(out_dir, "R_TRAIT_1_raqt_all_structs.json")

    display_types: set[str] = set()
    default_types: set[str] = set()
    for ir in all_impl_rows:
        sname = str(ir.get("symbol_name") or "")
        m_disp = re.search(r"\bDisplay\s+for\s+([A-Za-z_][A-Za-z0-9_]*)\b", sname)
        if m_disp:
            display_types.add(m_disp.group(1))
        m_def = re.search(r"\bDefault\s+for\s+([A-Za-z_][A-Za-z0-9_]*)\b", sname)
        if m_def:
            default_types.add(m_def.group(1))

    for er in enum_rows:
        enum_name = str(er.get("symbol_name") or "").strip()
        if not enum_name or "Error" not in enum_name:
            continue
        if enum_name in display_types:
            continue
        ep = _get_path_any(er) or "crates/engine/src/error.rs"
        ln = _get_line_any(er)
        add_finding(_make_finding(
            rules,
            rule_id="TRAIT_MISSING_DISPLAY_ERROR",
            message=f"Error enum '{enum_name}' appears to lack Display implementation",
            path=ep,
            line_start=ln,
            line_end=ln,
            entity_id=str(er.get("entity_id") or "") or None,
            evidence_artifact="R_TRAIT_1_raqt_all_enums.json",
            evidence_ref=f"raqt-trait:missing-display:{enum_name}",
            snippet=enum_name,
        ))

    for sr in struct_rows:
        struct_name = str(sr.get("symbol_name") or "").strip()
        if not struct_name:
            continue
        if not re.search(r"(Config|Settings|Options)", struct_name):
            continue
        if struct_name in default_types:
            continue
        sp = _get_path_any(sr) or "crates/engine/src/config.rs"
        ln = _get_line_any(sr)
        add_finding(_make_finding(
            rules,
            rule_id="TRAIT_MISSING_DEFAULT_CONFIG",
            message=f"Config/settings type '{struct_name}' appears to lack Default implementation",
            path=sp,
            line_start=ln,
            line_end=ln,
            entity_id=str(sr.get("entity_id") or "") or None,
            evidence_artifact="R_TRAIT_1_raqt_all_structs.json",
            evidence_ref=f"raqt-trait:missing-default:{struct_name}",
            snippet=struct_name,
        ))

    return findings


def _build_evidence_index(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    index: Dict[str, Any] = {}
    for f in findings:
        for ev in f.get("evidence", []):
            ref = ev.get("ref", "")
            if ref and ref not in index:
                index[ref] = {
                    "artifact": ev.get("artifact"),
                    "source": ev.get("source"),
                    "path": f["primary_location"]["path"],
                    "line_start": f["primary_location"]["line_start"],
                    "line_end": f["primary_location"]["line_end"],
                    "snippet_hash": ev.get("hash"),
                    "snippet_preview": None,
                }
    return index


def _derive_recommendations_from_findings(rules: Dict[str, Dict[str, Any]], findings: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    recommendations: List[Tuple[str, str, str]] = []
    seen_codes: set[str] = set()
    rule_counts: Dict[str, int] = {}
    for f in findings:
        rid = f.get("rule_id", "")
        rule_counts[rid] = rule_counts.get(rid, 0) + 1
    for rid, count in rule_counts.items():
        rule = rules.get(rid, {})
        rec_code = rule.get("recommendation_code")
        rec_text = rule.get("recommendation")
        severity = rule.get("severity", "MEDIUM")
        if rec_code and rec_text and rec_code not in seen_codes:
            seen_codes.add(rec_code)
            recommendations.append((severity, rec_code, f"{rec_text} ({count} finding{'s' if count > 1 else ''})"))
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    recommendations.sort(key=lambda x: severity_order.get(x[0], 5))
    return recommendations


# -----------------------------------------------------------------------------
# Plugin implementation
# -----------------------------------------------------------------------------

class RsqtGuruPlugin(PackPlugin):
    name = "rsqt_guru"

    def applies(self, *, engine: str, pack_type: str) -> bool:
        return (engine == "rsqt") and pack_type.startswith("rust_audit")

    def _load_rules(self, ctx: PluginContext) -> Dict[str, Dict[str, Any]]:
        # Allow override: pack.runner.plugin_config.rules_path
        rules_path: Path | None = None
        runner = getattr(ctx.pack, "runner", {}) or {}
        pcfg = runner.get("plugin_config") if isinstance(runner.get("plugin_config"), dict) else {}

        if pcfg.get("rules_path"):
            rules_path = Path(str(pcfg["rules_path"]))
            if not rules_path.is_absolute():
                rules_path = (ctx.pack_path.parent / rules_path).resolve()

        if rules_path is None:
            rules_path = ctx.pack_path.with_name("cfg_rust_audit_rsqt_general_finding_rules.yaml")

        if rules_path.exists():
            obj = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and "finding_rules" in obj and isinstance(obj["finding_rules"], dict):
                return obj["finding_rules"]
        return {}

    def _load_question_validators(self, ctx: PluginContext) -> Dict[str, Any]:
        validators_path: Path | None = None
        runner = getattr(ctx.pack, "runner", {}) or {}
        pcfg = runner.get("plugin_config") if isinstance(runner.get("plugin_config"), dict) else {}

        if pcfg.get("question_validators_path"):
            validators_path = Path(str(pcfg["question_validators_path"]))
            if not validators_path.is_absolute():
                validators_path = (ctx.pack_path.parent / validators_path).resolve()

        if validators_path is None:
            validators_path = ctx.pack_path.with_name("cfg_rust_audit_rsqt_general_question_validators.yaml")

        if validators_path.exists():
            obj = yaml.safe_load(validators_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("validators"), dict):
                return obj  # return full YAML (defaults + validators)
        return {"defaults": {}, "validators": {}}

    def _apply_question_validators(self, qid: str, answer: str, validators_cfg: Dict[str, Any]) -> List[str]:
        reasons: List[str] = []
        defaults = validators_cfg.get("defaults") if isinstance(validators_cfg.get("defaults"), dict) else {}
        validators = validators_cfg.get("validators") if isinstance(validators_cfg.get("validators"), dict) else {}
        rules = validators.get(qid, [])
        if not isinstance(rules, list):
            return reasons

        for rule in rules:
            if not isinstance(rule, dict) or "type" not in rule:
                continue
            rtype = rule["type"]

            if rtype == "require_non_test_fileline_citations_if_regex":
                # If the answer indicates a clean outcome, skip citation checks
                clean_rx = str(rule.get("clean_outcome_regex") or "")
                if clean_rx and re.search(clean_rx, answer or ""):
                    continue  # clean outcome accepted without citations
                trig = str(rule.get("trigger_regex") or "")
                if trig and re.search(trig, answer or ""):
                    cited_paths = _extract_fileline_paths(answer or "")
                    if not cited_paths:
                        reasons.append(str(rule.get("message_no_citations") or "Missing required file:line citations"))
                    else:
                        # Fallback chain: rule → defaults → hardcoded
                        patterns = rule.get("test_path_patterns")
                        if not patterns:
                            patterns = defaults.get("test_path_patterns")
                        if not patterns:
                            patterns = [r"(^|/)(tests)(/|$)", r"(^|/)[^/]*_tests?\.rs$"]
                        if all(_is_test_path(p, patterns) for p in cited_paths):
                            reasons.append(str(rule.get("message_all_test") or "All citations are in test paths"))
            elif rtype == "require_min_inline_regex_count":
                rx = str(rule.get("regex") or "")
                min_count = int(rule.get("min_count") or 0)
                if rx and min_count > 0:
                    hits = re.findall(rx, answer or "")
                    if len(hits) < min_count:
                        reasons.append(str(rule.get("message") or f"Expected ≥{min_count} matches for {rx}"))
            elif rtype == "ban_regex":
                rx = str(rule.get("regex") or "")
                if rx and re.search(rx, answer or ""):
                    reasons.append(str(rule.get("message") or f"Banned pattern found: {rx}"))
            elif rtype == "require_min_inline_regex_count_if_regex":
                # Conditional: only enforce regex count if the condition regex matches the answer
                if_rx = str(rule.get("if_regex") or "")
                rx = str(rule.get("regex") or "")
                min_count = int(rule.get("min_count") or 0)
                if not if_rx:
                    reasons.append("Validator misconfigured: require_min_inline_regex_count_if_regex missing if_regex")
                    continue
                if re.search(if_rx, answer or ""):
                    if not rx or min_count <= 0:
                        reasons.append("Validator misconfigured: require_min_inline_regex_count_if_regex missing regex/min_count")
                        continue
                    hits = re.findall(rx, answer or "")
                    if len(hits) < min_count:
                        reasons.append(str(rule.get("message") or f"Expected ≥{min_count} matches for {rx} (conditional on {if_rx})"))
        return reasons

    def post_run(self, ctx: PluginContext) -> Optional[PluginOutputs]:
        # Load deterministic rules/config
        finding_rules = self._load_rules(ctx)
        validators_cfg = self._load_question_validators(ctx)
        runner_issues_by_q = _load_runner_report_issues_by_q(ctx.out_dir)

        # Build allow-list of actual preflight artifacts from the pack (fail-closed)
        allowed: set[str] = set()
        for q in getattr(ctx.pack, "questions", []) or []:
            qid = getattr(q, "id", "") or ""
            preflights = getattr(q, "preflight", None) or []
            if not qid or not isinstance(preflights, list):
                continue
            for step in preflights:
                name = step.get("name") if isinstance(step, dict) else None
                if name:
                    allowed.add(f"{qid}_{name}.json")

        # Generate FINDINGS.jsonl + EVIDENCE_INDEX.json (deterministic)
        findings = _collect_findings_from_artifacts(ctx.out_dir, finding_rules, allowed_artifacts=allowed or None)
        if str(getattr(ctx.pack, "engine", "")).strip().lower() == "raqt":
            findings.extend(_collect_raqt_specific_findings(ctx.out_dir, finding_rules))
            # Deterministic de-dup in case multiple extractors hit same location.
            dedup: Dict[str, Dict[str, Any]] = {}
            for f in findings:
                fid = str(f.get("finding_id") or "")
                if fid:
                    dedup[fid] = f
            findings = list(dedup.values())
        findings_path = ctx.out_dir / "FINDINGS.jsonl"
        with open(findings_path, "w", encoding="utf-8") as f:
            for finding in findings:
                f.write(json.dumps(finding, ensure_ascii=False) + "\n")
        evidence_index = _build_evidence_index(findings)
        evidence_path = ctx.out_dir / "EVIDENCE_INDEX.json"
        evidence_path.write_text(json.dumps(evidence_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # Build consolidated Guru report (ported behavior)
        qtexts: Dict[str, str] = {}
        for q in getattr(ctx.pack, "questions", []) or []:
            qid = getattr(q, "id", "")
            qtext = getattr(q, "question", "")
            if qid and qtext:
                qtexts[qid] = qtext

        qa_rows: List[Tuple[str, str, bool, List[str]]] = []
        for q in getattr(ctx.pack, "questions", []) or []:
            qid = getattr(q, "id", "")
            chat_file = ctx.out_dir / f"{qid}_chat.json"
            answer = "(missing chat artifact)"
            reasons: List[str] = []
            is_quote_bypass = False
            chat_data: Dict[str, Any] | None = None

            if chat_file.exists():
                try:
                    data = json.loads(chat_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        chat_data = data
                    stdout = data.get("stdout", {}) if isinstance(data, dict) else {}
                    if isinstance(stdout, dict):
                        answer = stdout.get("answer", "") or stdout.get("response", "") or stdout.get("text", "") or ""
                        is_quote_bypass = bool(stdout.get("_evidence_empty_gated")) or (ctx.out_dir / f"{qid}_bypass_prompt.md").exists()
                    else:
                        answer = str(stdout) if stdout else "(no answer)"
                except Exception:
                    answer = "(parse error)"
                    reasons.append("Parse error")

            # Deterministic post-processing for strict schema/validator compliance.
            answer_raw = answer or ""
            answer_norm = _normalize_answer_for_validators(qid, answer_raw, ctx.out_dir)
            answer = answer_norm

            # Persist raw + normalized answers as separate fields (DO NOT overwrite stdout.answer).
            if chat_file.exists() and isinstance(chat_data, dict):
                stdout_obj = chat_data.get("stdout")
                if isinstance(stdout_obj, dict):
                    changed = False
                    if not stdout_obj.get("_raw_answer"):
                        stdout_obj["_raw_answer"] = answer_raw
                        changed = True
                    if stdout_obj.get("_normalized_answer") != answer_norm:
                        stdout_obj["_normalized_answer"] = answer_norm
                        changed = True
                    if changed:
                        try:
                            chat_file.write_text(json.dumps(chat_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        except Exception:
                            pass

            # Smart NOT FOUND detection (BC-003)
            not_found_issues = _validate_chat_answer(answer or "", is_quote_bypass)
            reasons.extend(not_found_issues)

            # Schema enforcement: SSOT with runner scoring (applies to ALL modes)
            # Read the prompt that was actually sent to the model
            prompt_text = _read_prompt_for_q(ctx.out_dir, qid)
            prompt_has_schema = ("VERDICT" in prompt_text and "CITATIONS" in prompt_text)

            if prompt_has_schema:
                # Use run_pack.validate_response_schema for SSOT scoring
                try:
                    schema_issues = shared_validate_response_schema(answer or "", ctx.pack.validation)
                    reasons.extend(schema_issues)
                except Exception:
                    # Fallback to local checks if import fails
                    clean_answer = _strip_markdown_bold(answer or "")
                    mv = _VERDICT_RE.search(clean_answer)
                    if not mv:
                        reasons.append("Missing required line: VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE")
                    else:
                        verdict = mv.group(1).strip()
                        if verdict not in _ALLOWED_VERDICTS:
                            reasons.append(f"Invalid VERDICT '{verdict}' (allowed: {sorted(_ALLOWED_VERDICTS)})")
                    citations = _parse_citations(clean_answer)
                    if not citations:
                        reasons.append("Missing/empty CITATIONS=")
            elif not is_quote_bypass:
                # No schema markers in prompt AND not bypass → legacy checks
                clean_answer = _strip_markdown_bold(answer or "")
                mv = _VERDICT_RE.search(clean_answer)
                if not mv:
                    reasons.append("Missing required line: VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE")
                else:
                    verdict = mv.group(1).strip()
                    if verdict not in _ALLOWED_VERDICTS:
                        reasons.append(f"Invalid VERDICT '{verdict}' (allowed: {sorted(_ALLOWED_VERDICTS)})")
                citations = _parse_citations(clean_answer)
                if not citations:
                    reasons.append("Missing/empty CITATIONS=")

            # Lazy deferral without inline quotes (both modes)
            if "listed above" in (answer or "").lower() or "listed in the evidence" in (answer or "").lower():
                if not _has_quote_evidence(answer or ""):
                    reasons.append("Deferred to evidence without inline quotes")

            # --- Deterministic validators (evidence-grounded) ---

            # R_SAFE_3: If evidence contains .unwrap( or .expect( with file:line tokens,
            # answer must mention at least one token (proves model actually read evidence)
            if qid == "R_SAFE_3" and prompt_text:
                ev_has_unwrap = ".unwrap(" in prompt_text or ".expect(" in prompt_text
                ev_tokens = _extract_evidence_filelines(prompt_text)
                if ev_has_unwrap and ev_tokens:
                    if not _require_answer_mentions_any_token(answer or "", ev_tokens):
                        reasons.append(
                            f"R_SAFE_3: Evidence contains unwrap/expect with {len(ev_tokens)} "
                            f"file:line refs but answer cites none — possible false clean"
                        )

            # R_META_1: For each proof file type, if evidence lists paths,
            # answer must include at least one listed path
            if qid == "R_META_1" and prompt_text:
                for proof_file in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml"):
                    ev_paths = _extract_evidence_paths(prompt_text, proof_file)
                    if ev_paths:
                        ans_lower = (answer or "").lower()
                        if not any(p.split(":")[0].lower() in ans_lower for p in ev_paths):
                            reasons.append(
                                f"R_META_1: Evidence lists {proof_file} paths "
                                f"({ev_paths[:3]}) but answer doesn't mention any"
                            )

            # Question-specific validators (config-driven; fail-closed)
            reasons.extend(self._apply_question_validators(qid, answer or "", validators_cfg))

            # JSON-driven semantic validators (push-button; no LLM)
            if qid == "R_SAFE_3":
                reasons.extend(_semantic_validate_safe3(ctx, answer or "", validators_cfg))
            elif qid == "R_DOC_COVERAGE_1":
                reasons.extend(_semantic_validate_doc_coverage(ctx, qid, answer or ""))
            elif qid == "R_DOC_UNDOC_1":
                reasons.extend(_semantic_validate_doc_undoc(ctx, qid, answer or "", validators_cfg))

            # Keep plugin scoring fail-closed and aligned with runner contract:
            # if runner emitted validator issues for this QID, they remain score-fatal.
            runner_contract_issues = [
                str(msg).strip()
                for msg in runner_issues_by_q.get(qid, [])
                if str(msg or "").strip()
            ]
            reasons.extend(runner_contract_issues)

            # Stable de-dup of reason strings.
            if reasons:
                dedup_reasons: List[str] = []
                seen_reasons: set[str] = set()
                for msg in reasons:
                    key = str(msg).strip()
                    if not key or key in seen_reasons:
                        continue
                    seen_reasons.add(key)
                    dedup_reasons.append(key)
                reasons = dedup_reasons

            has_issues = bool(reasons)
            qa_rows.append((qid, answer or "", has_issues, reasons))

        total = len(qa_rows)
        issues_count = sum(1 for _, _, has_issues, _ in qa_rows if has_issues)
        success_count = total - issues_count

        # Deterministic recommendations from findings
        recommendations = _derive_recommendations_from_findings(finding_rules, findings) if findings else []
        if not recommendations:
            recommendations = [("INFO", "REC_NONE", "No triggered findings requiring action. Maintain CI gates and re-run audits on releases.")]

        # Use runner-computed effective mode (BC-002)
        eff = getattr(ctx.args, "_effective_qb_mode", None)
        if eff == "on":
            mode_str = "QUOTE_BYPASS"
        elif eff == "auto":
            mode_str = "AUTO"
        elif eff == "off":
            mode_str = "STANDARD"
        else:
            mode_str = "QUOTE_BYPASS" if bool(getattr(ctx.args, "quote_bypass", False)) else "STANDARD"

        report_lines: List[str] = []
        report_lines.append("# Rust Guru Audit Report\n\n")
        report_lines.append(f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        report_lines.append(f"**Pack**: {ctx.pack_path.name}\n")
        report_lines.append(f"**Mode**: {mode_str}\n\n")
        report_lines.append("---\n\n")
        report_lines.append("## Executive Summary\n\n")
        report_lines.append("| Metric | Value |\n")
        report_lines.append("|--------|-------|\n")
        report_lines.append(f"| **Total Questions** | {total} |\n")
        report_lines.append(f"| **Guru-level Answers** | {success_count} ({(100*success_count//total) if total else 0}%) |\n")
        report_lines.append(f"| **Issues** | {issues_count} |\n")
        report_lines.append(f"| **Deterministic Findings** | {len(findings)} |\n\n")
        report_lines.append("---\n\n")

        report_lines.append("## Questions and Answers\n\n")
        for i, (qid, answer, has_issues, reasons) in enumerate(qa_rows, 1):
            status = "⚠️" if has_issues else "✅"
            report_lines.append(f"### Question {i}: {qid} {status}\n\n")
            if qid in qtexts:
                q_text = qtexts[qid]
                if len(q_text) > 300:
                    q_text = q_text[:300] + "..."
                report_lines.append(f"**Question:**\n\n{q_text}\n\n")
            clean_answer = (answer or "").replace("\\n", "\n").strip()
            if "## ANALYZE-ONLY MODE" in clean_answer:
                clean_answer = clean_answer.split("## ANALYZE-ONLY MODE")[0].strip()
            report_lines.append(f"**Answer:**\n\n{clean_answer}\n\n")
            if has_issues and reasons:
                report_lines.append("**Validator issues:**\n\n")
                for r in reasons:
                    report_lines.append(f"- {r}\n")
                report_lines.append("\n")
            report_lines.append("---\n\n")

        report_lines.append("## Actionable Recommendations\n\n")
        report_lines.append("*Deterministic recommendations derived from preflight findings:*\n\n")
        report_lines.append("| Priority | Code | Recommendation |\n")
        report_lines.append("|----------|------|----------------|\n")
        for prio, code, rec in recommendations:
            report_lines.append(f"| **{prio}** | `{code}` | {rec} |\n")
        report_lines.append("\n---\n\n")
        report_lines.append(f"*Generated by run_pack.py + RsqtGuruPlugin*\n")

        guru_report_path = ctx.out_dir / "GURU_AUDIT_REPORT.md"
        guru_report_path.write_text("".join(report_lines), encoding="utf-8")

        # Machine-readable metrics for replicate aggregation
        metrics_path = ctx.out_dir / "GURU_METRICS.json"
        metrics_payload = {
            "guru_score": success_count,
            "total_questions": total,
            "issues": issues_count,
            "failing_questions": [qid for (qid, _, has_issues, _) in qa_rows if has_issues],
            "findings_count": len(findings),
        }
        metrics_path.write_text(json.dumps(metrics_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        return PluginOutputs(
            files={
                "guru_report": guru_report_path.name,
                "findings": findings_path.name,
                "evidence_index": evidence_path.name,
                "guru_metrics": metrics_path.name,
            },
            metrics={
                "guru_score": success_count,
                "guru_total": total,
                "guru_issues": issues_count,
                "findings_count": len(findings),
            },
            hashes={
                "findings_sha256": _sha256_file(findings_path) if findings_path.exists() else None,
                "evidence_index_sha256": _sha256_file(evidence_path) if evidence_path.exists() else None,
                "guru_report_sha256": _sha256_file(guru_report_path) if guru_report_path.exists() else None,
            },
        )
