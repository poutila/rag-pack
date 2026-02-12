#!/usr/bin/env python3
import yaml
from pathlib import Path
obj=yaml.safe_load(Path('runner_policy.yaml').read_text(encoding='utf-8'))
rules=((obj.get('runner') or {}).get('advice_quality_gate') or {}).get('min_concrete_issues_rules') or []
assert len(rules)>=2
print('ok')
