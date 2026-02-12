#!/usr/bin/env python3
from pathlib import Path
t=Path('run_pack.py').read_text(encoding='utf-8')
assert '.parquet' in t and '.faiss' in t
print('ok')
