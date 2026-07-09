"""Test reconcile_fetch_urls on template literal patterns."""
import re, json
from app.agent.projects.api_contract import _FETCH_URL_PATTERNS, _extract_fetch_urls, reconcile_fetch_urls

contract = {
    'mount_prefix': '/api/v1',
    'endpoints': [
        {'method': 'GET', 'path': '/time/beijing', 'full': '/api/v1/time/beijing'}
    ]
}

test_cases = [
    # Template literal with separate vars (LLM generated)
    "fetch(`${API_BASE}${TIME_ENDPOINT}`)",
    # Template literal with inline path
    "fetch(`${API_BASE}/api/v1/time/beijing`)",
    # String literal with full URL
    "fetch('http://localhost:3001/api/v1/time/beijing')",
    # String literal wrong path
    "fetch('http://localhost:3001/api/v1/todos')",
    # Template with partial path
    "fetch(`${API_BASE}/todos`)",
    # fetch with backtick style and API_BASE variable
    "await fetch(`${API}/api/v1/time/beijing`)",
    # Multiple fetch calls
    """
const API = 'http://localhost:3001';
const r = await fetch(`${API}/api/v1/time/beijing`);
const r2 = await fetch(`${API}/api/v1/wrong`);
""",
]

for i, tc in enumerate(test_cases):
    extracted = _extract_fetch_urls(tc)
    rewritten_page, rewritten = reconcile_fetch_urls(tc, contract)
    print(f"[{i+1}] Input:  {tc[:100]!r}")
    print(f"     Extracted: {extracted}")
    print(f"     Rewritten: {rewritten}")
    if rewritten:
        print(f"     Changed page: {rewritten_page[:200]!r}")
    print()