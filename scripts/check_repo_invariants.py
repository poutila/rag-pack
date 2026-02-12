#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise RuntimeError(f"YAML_FAIL {path.relative_to(ROOT)} {e}")

def main() -> int:
    errs = []
    for p in ROOT.rglob('*.yaml'):
        try:
            load_yaml(p)
        except RuntimeError as e:
            errs.append(str(e))

    engine_specs = load_yaml(ROOT / 'engine_specs.yaml') or {}
    engines = set((engine_specs.get('engines') or {}).keys())

    for p in ROOT.glob('pack_*.yaml'):
        obj = load_yaml(p) or {}
        qids = [str((q or {}).get('id') or '') for q in (obj.get('questions') or [])]
        seen = set(); dups = []
        for q in qids:
            if q in seen: dups.append(q)
            seen.add(q)
        if dups:
            errs.append(f"DUP_QID {p.name} {sorted(set(dups))}")
        eng = str((obj.get('engine') or {}).get('name') or '')
        if eng and eng not in engines:
            errs.append(f"MISSING_ENGINE {p.name} {eng}")
        if 'extension_3q' in p.name and len([q for q in qids if q]) != 3:
            errs.append(f"NAME_QCOUNT_MISMATCH {p.name} qcount={len([q for q in qids if q])}")

    archived = list((ROOT / 'archive' / 'optimized_yaml').glob('*.yaml'))
    if not archived:
        errs.append('ARCHIVE_MISSING archive/optimized_yaml/*.yaml')

    if errs:
        print('\n'.join(errs))
        return 2
    print('INVARIANTS_OK')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
