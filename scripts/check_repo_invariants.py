#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"YAML_FAIL {path.relative_to(ROOT)} {exc}")


def check_invariants() -> tuple[bool, list[str]]:
    errs: list[str] = []
    for p in ROOT.rglob("*.yaml"):
        try:
            load_yaml(p)
        except RuntimeError as exc:
            errs.append(str(exc))

    plan_required = [
        Path("prompts/RUST_GURU_ADVICE.md"),
        Path("pack_rust_audit_rsqt_mission_refactor_v1_0.yaml"),
        Path("cfg_rust_audit_rsqt_mission_refactor_question_validators.yaml"),
        Path("cfg_rust_audit_rsqt_mission_refactor_finding_rules.yaml"),
        Path("run_rsqt_mission_refactor.sh"),
    ]
    for req in plan_required:
        if not (ROOT / req).exists():
            errs.append(f"MISSING_REQUIRED_FILE {req}")

    for p in ROOT.glob("pack_*.yaml"):
        obj = load_yaml(p) or {}
        if not isinstance(obj, dict):
            errs.append(f"PACK_NOT_MAPPING {p.name}")
            continue
        questions = obj.get("questions") or []
        if not isinstance(questions, list):
            errs.append(f"PACK_QUESTIONS_INVALID {p.name}")
            continue
        qids = [str((q or {}).get("id") or "") for q in questions if isinstance(q, dict)]
        if len(qids) != len(set(qids)):
            errs.append(f"DUP_QID {p.name}")
        is_mission = "mission" in str(obj.get("pack_type") or "").lower() or "_mission_" in p.name.lower()
        if is_mission:
            for q in questions:
                if not isinstance(q, dict):
                    continue
                if str(q.get("advice_mode") or "").lower() != "llm":
                    errs.append(f"MISSION_ADVICE_MODE_NOT_LLM {p.name}:{q.get('id')}")

        gate = ((obj.get("runner") or {}).get("advice_gate") or {}) if isinstance(obj.get("runner"), dict) else {}
        if gate:
            allowed = {"enabled", "fatal"}
            unknown = sorted(set(gate.keys()) - allowed)
            if unknown:
                errs.append(f"ADVICE_GATE_UNKNOWN_KEYS {p.name} {unknown}")
            for k in ("enabled", "fatal"):
                if k in gate and not isinstance(gate[k], bool):
                    errs.append(f"ADVICE_GATE_TYPE {p.name} {k}")

    return (len(errs) == 0, errs)


def run_script_tests() -> tuple[bool, int, int, int]:
    tests = sorted((ROOT / "scripts" / "tests").glob("test_*.py"))
    total = len(tests)
    passed = 0
    failed = 0
    for t in tests:
        proc = subprocess.run([sys.executable, str(t)], cwd=str(ROOT))
        if proc.returncode == 0:
            passed += 1
        else:
            failed += 1
            print(f"TEST_FAIL {t.relative_to(ROOT)} rc={proc.returncode}")
    return (failed == 0, total, passed, failed)


def main() -> int:
    inv_ok, inv_errs = check_invariants()
    if not inv_ok:
        for e in inv_errs:
            print(e)
    print(f"INVARIANTS_RESULT={'PASS' if inv_ok else 'FAIL'}")

    tests_ok, total, passed, failed = run_script_tests()
    print(f"TESTS_RESULT={'PASS' if tests_ok else 'FAIL'}")
    print(f"TESTS_SUMMARY total={total} passed={passed} failed={failed}")

    status = "PASS" if (inv_ok and tests_ok) else "FAIL"
    print(f"CHECK_REPO_INVARIANTS_SUMMARY status={status} invariants={'PASS' if inv_ok else 'FAIL'} tests={'PASS' if tests_ok else 'FAIL'}")
    if inv_ok and tests_ok:
        print("INVARIANTS_OK")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
