import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
#!/usr/bin/env python3
from core.validation import extract_required_keys_from_contract, validate_required_key_lines
c='''VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE\nCITATIONS=path:1\nREAL=\nFAKE=\n'''
keys=extract_required_keys_from_contract(c)
assert keys==['REAL','FAKE']
issues=validate_required_key_lines('VERDICT=TRUE_POSITIVE\nCITATIONS=a:1\nREAL=x\n', keys)
assert any('FAKE' in x for x in issues)
print('ok')
