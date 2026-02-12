#!/usr/bin/env python3
from pathlib import Path
t=Path('pack_rust_audit_raqt_mission_set1_v1_0.yaml').read_text(encoding='utf-8')
assert 'R_PORTS_1' in t
print('ok')
