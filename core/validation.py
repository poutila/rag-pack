from __future__ import annotations
import re
from typing import Any, List


def validate_response_schema(answer: str, validation: Any, issue_caps: dict[str, Any] | None = None) -> List[str]:
    issues: List[str] = []
    required_verdicts = set(getattr(validation, 'required_verdicts', []) or [])
    fail_on_missing_citations = bool(getattr(validation, 'fail_on_missing_citations', True))
    citation_format = str(getattr(validation, 'citation_format', 'path:line(-line)'))
    caps = issue_caps or {}
    clean = (answer or '').replace('**', '')
    nonempty_lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
    if not nonempty_lines or not re.match(r"^VERDICT\s*[=:]\s*[A-Z_]+\s*$", nonempty_lines[0]):
        issues.append('First non-empty line must be VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE')
    if len(nonempty_lines) < 2 or not re.match(r"^CITATIONS\s*[=:]\s*.+$", nonempty_lines[1]):
        issues.append('Second non-empty line must be CITATIONS=path:line(-line), ...')
    if re.findall(r"(?mi)^\s*(?:#{1,6}\s*)?(?:analysis|citations)\s*:\s*$", clean):
        issues.append("Markdown/standalone 'Analysis:' or 'CITATIONS:' headers are not allowed")
    verdict_matches = list(re.finditer(r"^\s*VERDICT\s*[=:]\s*([A-Z_]+)\s*$", clean, flags=re.MULTILINE))
    if not verdict_matches:
        issues.append('Missing required line: VERDICT=TRUE_POSITIVE|FALSE_POSITIVE|INDETERMINATE')
    else:
        verdict = verdict_matches[0].group(1)
        if required_verdicts and verdict not in required_verdicts:
            issues.append(f"Invalid VERDICT '{verdict}' (allowed: {sorted(required_verdicts)})")
        if len(verdict_matches) > 1:
            issues.append('VERDICT must appear exactly once')
    citation_matches = list(re.finditer(r"^\s*CITATIONS\s*[=:]\s*(.*)$", clean, flags=re.MULTILINE))
    m = citation_matches[0] if citation_matches else None
    citations_raw = ''
    if not m:
        cit_header = re.search(r"^\s*CITATIONS\s*[=:]?\s*$", clean, flags=re.MULTILINE)
        if cit_header:
            issues.append('CITATIONS must be a single comma-separated line (no standalone CITATIONS section)')
        issues.append('Missing required line: CITATIONS=path:line(-line), ...')
    else:
        citations_raw = (m.group(1) or '').strip()
        if len(citation_matches) > 1:
            issues.append('CITATIONS must appear exactly once as a single line')
    if fail_on_missing_citations and not citations_raw:
        issues.append('CITATIONS is empty but fail_on_missing_citations=true')
    if citations_raw:
        bad=[]
        for t in [x.strip().strip('`') for x in citations_raw.split(',') if x.strip()]:
            t = re.sub(r"^\s*(?:file|path|artifact|section):\s*", "", t, flags=re.IGNORECASE)
            t = re.sub(r"^\s*cite\s*=\s*", "", t, flags=re.IGNORECASE)
            if not re.match(r"^[^\s:]+(?:/[^\s:]+)*:\d+(?:-\d+)?$", t):
                bad.append(t)
        if bad:
            issues.append(f"CITATIONS contains invalid tokens (expected {citation_format}): {bad[:int(caps.get('invalid_citations',6))]}")
    return issues
