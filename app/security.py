import re

SQL_INJECTION_PATTERNS = [
    re.compile(r"union\s+select", re.IGNORECASE),
    re.compile(r"or\s+1\s*=\s*1", re.IGNORECASE),
    re.compile(r"/\*|\*/|--", re.IGNORECASE)
]

def scan_for_sql_injection(query: str):
    """Asserts query strings are free of SQL injection structures."""
    for pattern in SQL_INJECTION_PATTERNS:
        if pattern.search(query):
            raise ValueError("SQL injection pattern identified in dynamic query.")

def validate_cors_origin(origin: str, whitelist: list) -> bool:
    """Checks origin against client-side domain authorization array."""
    return origin in whitelist
