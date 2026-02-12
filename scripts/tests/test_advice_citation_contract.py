#!/usr/bin/env python3
from pathlib import Path
t=Path('prompts/RUST_GURU_ADVICE.md').read_text(encoding='utf-8')
assert 'path::symbol' in t
print('ok')
