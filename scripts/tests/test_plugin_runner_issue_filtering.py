#!/usr/bin/env python3
from pathlib import Path
t=Path('plugins/rsqt_guru.py').read_text(encoding='utf-8')
assert '_runner_issue_still_applies' in t
assert 'filtered_runner_issues' in t
print('ok')
