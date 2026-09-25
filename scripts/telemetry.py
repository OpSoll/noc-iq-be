#!/usr/bin/env python3
"""
Release checklist automation — Issue #385 (BE-W5-124).

Cross-checks API docs, config defaults, and routed modules for drift.
Produces machine-readable JSON output categorised by severity and exits
with code 1 when any CRITICAL drift is detected.

Usage:
    python scripts/check_release_drift.py [--output PATH]

Environment:
    REPO_ROOT   Override the repo root (defaults to the parent of this script).
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths (relative to repo root)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
API_MD = REPO_ROOT / "docs" / "API.md"
ROUTER_PY = REPO_ROOT / "app" / "api" / "v1" / "router.py"
CONFIG_PY = REPO_ROOT / "app" / "core" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_documented_endpoints(api_md: Path) -> set[str]:
    """
    Extract HTTP method + path tokens from docs/API.md.

    Looks for Markdown patterns such as:
        ### GET /api/v1/outages
        **POST** `/api/v1/auth/login`
        `GET /api/v1/sla/analytics/dashboard`
    """
    text = api_md.read_text(encoding="utf-8", errors="replace")
    patterns = [
        r"`(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s`]+)`",  # inline code
        r"(?:^|\s)(?:GET|POST|PUT|PATCH|DELETE)\s+(/api[^\s`\)]+)",  # plain text
        r"#{1,4}\s+(?:GET|POST|PUT|PATCH|DELETE)\s+(/api[^\s]+)",  # heading
    ]
    endpoints: set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, text, re.MULTILINE | re.IGNORECASE):
            path = m.group(1).rstrip("/").strip()
            endpoints.add(path)
    return endpoints


def parse_registered_routes(router_py: Path) -> set[str]:
    """
    Extract route prefixes registered in app/api/v1/router.py via
    include_router() calls.  Returns the prefix strings.
    """
    text = router_py.read_text(encoding="utf-8", errors="replace")
    prefixes: set[str] = set()

    # Match include_router(..., prefix="/something", ...)
    for m in re.finditer(r'include_router\([^)]*prefix\s*=\s*"([^"]+)"', text):
        prefixes.add(m.group(1))

    # Also capture include_router calls without a prefix (routers that
    # self-register their prefix internally)
    for m in re.finditer(r'include_router\((\w+)\.router\)', text):
        prefixes.add(f"<no-prefix>:{m.group(1)}")

    return prefixes


def parse_config_fields(config_py: Path) -> set[str]:
    """
    Extract all field names defined in the Settings(BaseSettings) class.
    """
    source = config_py.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
    return fields


def parse_env_example_keys(env_example: Path) -> set[str]:
    """
    Extract key names from .env.example (lines like KEY=value or # KEY).
    """
    if not env_example.exists():
        return set()
    keys: set[str] = set()
    for line in env_example.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().lstrip("#").strip()
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


# ---------------------------------------------------------------------------
# Drift check logic
# ---------------------------------------------------------------------------

def run_checks() -> list[dict[str, Any]]:
    """Run all drift checks and return a flat list of finding dicts."""
    findings: list[dict[str, Any]] = []

    def add(severity: str, category: str, item: str, detail: str):
        findings.append(
            {
                "severity": severity,  # critical | warning | info
                "category": category,
                "item": item,
                "detail": detail,
            }
        )

    # ------------------------------------------------------------------ #
    # 1. API.md vs router: documented paths not reachable via any prefix  #
    # ------------------------------------------------------------------ #
    if API_MD.exists() and ROUTER_PY.exists():
        doc_endpoints = parse_documented_endpoints(API_MD)
        registered_prefixes = parse_registered_routes(ROUTER_PY)

        for endpoint in sorted(doc_endpoints):
            reachable = any(
                endpoint.startswith(p)
                for p in registered_prefixes
                if not p.startswith("<no-prefix>")
            )
            if not reachable:
                add(
                    "warning",
                    "api_docs_vs_router",
                    endpoint,
                    "Endpoint documented in API.md but no matching router prefix found "
                    f"in {ROUTER_PY.relative_to(REPO_ROOT)}.",
                )
    elif not API_MD.exists():
        add("critical", "missing_file", str(API_MD.relative_to(REPO_ROOT)), "docs/API.md is missing.")
    elif not ROUTER_PY.exists():
        add("critical", "missing_file", str(ROUTER_PY.relative_to(REPO_ROOT)), "router.py is missing.")

    # ------------------------------------------------------------------ #
    # 2. Config fields not represented in .env.example                   #
    # ------------------------------------------------------------------ #
    if CONFIG_PY.exists():
        config_fields = parse_config_fields(CONFIG_PY)
        env_keys = parse_env_example_keys(ENV_EXAMPLE)

        # Skip fields that are clearly internal / not env-configurable
        _SKIP_FIELDS = {"Config"}

        for field in sorted(config_fields - _SKIP_FIELDS):
            if field not in env_keys:
                add(
                    "warning",
                    "config_vs_env_example",
                    field,
                    f"Settings.{field} has no entry in .env.example.",
                )
    else:
        add("critical", "missing_file", str(CONFIG_PY.relative_to(REPO_ROOT)), "config.py is missing.")

    # ------------------------------------------------------------------ #
    # 3. router.py includes a module that doesn't exist on disk           #
    # ------------------------------------------------------------------ #
    if ROUTER_PY.exists():
        endpoints_dir = REPO_ROOT / "app" / "api" / "v1" / "endpoints"
        text = ROUTER_PY.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"from app\.api\.v1\.endpoints import (\w+)", text):
            module_name = m.group(1)
            module_path = endpoints_dir / f"{module_name}.py"
            if not module_path.exists():
                add(
                    "critical",
                    "router_vs_filesystem",
                    module_name,
                    f"router.py imports {module_name} but "
                    f"{module_path.relative_to(REPO_ROOT)} does not exist.",
                )

    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Release checklist: detect API/docs/config drift."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write JSON report (stdout if omitted).",
    )
    args = parser.parse_args()

    findings = run_checks()

    report = {
        "schema_version": "1.0",
        "total_findings": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "info": sum(1 for f in findings if f["severity"] == "info"),
        "findings": findings,
    }

    output_json = json.dumps(report, indent=2)

    if args.output:
        Path(args.output).write_text(output_json)
        print(f"Drift report written to {args.output}")
    else:
        print(output_json)

    if report["critical"] > 0:
        print(
            f"\nERROR: {report['critical']} CRITICAL drift finding(s) detected. "
            "Release workflow should fail.",
            file=sys.stderr,
        )
        return 1

    if report["warning"] > 0:
        print(f"\nWARNING: {report['warning']} warning(s) detected. Review recommended.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
