#!/usr/bin/env python3
from pathlib import Path
text=Path('run_pack.py').read_text(encoding='utf-8')
assert 'score_ok' in text
assert 'plugin_advisory_score' in text or 'mission_advice_gate_enabled' in text
print('ok')
