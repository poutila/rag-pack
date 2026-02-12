#!/usr/bin/env python3
from pathlib import Path
t=Path('prompts/RUST_GURU_ADVICE.md').read_text(encoding='utf-8')
assert 'ISSUE_n' in t and 'CITATIONS_n' in t
print('ok')
