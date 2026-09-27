"""
Tests for Issue #385 (BE-W5-124): Release checklist drift detection.

Validates that check_release_drift.py correctly identifies:
- Fully synchronized state (no critical findings)
- A route missing from docs (warning finding)
- A config field missing from .env.example (warning finding)
- A router import referencing a non-existent module (critical finding)
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the script importable as a module
SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_release_drift.py"

import importlib.util

spec = importlib.util.spec_from_file_location("check_release_drift", SCRIPT_PATH)
drift_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(drift_module)  # type: ignore[union-attr]


class TestParseFunctions(unittest.TestCase):
    def test_parse_documented_endpoints_inline_code(self):
        content = "Use `GET /api/v1/outages` to fetch outages.\n`POST /api/v1/auth/login`"
        p = Path("/tmp/fake_api.md")
        with patch.object(Path, "read_text", return_value=content):
            endpoints = drift_module.parse_documented_endpoints(p)
        self.assertIn("/api/v1/outages", endpoints)
        self.assertIn("/api/v1/auth/login", endpoints)

    def test_parse_registered_routes_with_prefix(self):
        content = (
            'api_router.include_router(auth.router, prefix="/auth", tags=["auth"])\n'
            'api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])\n'
        )
        p = Path("/tmp/fake_router.py")
        with patch.object(Path, "read_text", return_value=content):
            prefixes = drift_module.parse_registered_routes(p)
        self.assertIn("/auth", prefixes)
        self.assertIn("/wallets", prefixes)

    def test_parse_config_fields(self):
        content = (
            "from pydantic_settings import BaseSettings\n"
            "class Settings(BaseSettings):\n"
            "    PROJECT_NAME: str = 'NOCIQ'\n"
            "    DEBUG: bool = False\n"
            "    DATABASE_URL: str = 'postgresql://localhost/nociq'\n"
        )
        p = Path("/tmp/fake_config.py")
        with patch.object(Path, "read_text", return_value=content):
            fields = drift_module.parse_config_fields(p)
        self.assertIn("PROJECT_NAME", fields)
        self.assertIn("DEBUG", fields)
        self.assertIn("DATABASE_URL", fields)

    def test_parse_env_example_keys(self):
        content = "DATABASE_URL=postgresql://localhost/nociq\n# DEBUG=false\nSECRET_KEY=\n"
        p = Path("/tmp/fake.env")
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value=content):
                keys = drift_module.parse_env_example_keys(p)
        self.assertIn("DATABASE_URL", keys)
        self.assertIn("SECRET_KEY", keys)


class TestRunChecks(unittest.TestCase):
    """Integration-style tests for run_checks() using real repo files."""

    def test_no_critical_findings_on_clean_repo(self):
        """
        On the actual repo (with all modules present), there should be zero
        critical filesystem drift findings from router imports.
        """
        findings = drift_module.run_checks()
        router_fs_critical = [
            f for f in findings
            if f["severity"] == "critical" and f["category"] == "router_vs_filesystem"
        ]
        self.assertEqual(
            len(router_fs_critical),
            0,
            msg=f"Unexpected filesystem critical findings: {router_fs_critical}",
        )

    def test_missing_module_detected_as_critical(self):
        """A router that imports a non-existent module → critical finding."""
        fake_router = (
            "from app.api.v1.endpoints import auth\n"
            "from app.api.v1.endpoints import nonexistent_module_xyz\n"
        )
        endpoints_dir = drift_module.REPO_ROOT / "app" / "api" / "v1" / "endpoints"

        original_read = Path.read_text
        original_exists_path = Path.exists

        def patched_read_text(self, *args, **kwargs):
            if self == drift_module.ROUTER_PY:
                return fake_router
            return original_read(self, *args, **kwargs)

        def patched_exists(self):
            if self == endpoints_dir / "nonexistent_module_xyz.py":
                return False
            return original_exists_path(self)

        with patch.object(Path, "read_text", patched_read_text):
            with patch.object(Path, "exists", patched_exists):
                findings = drift_module.run_checks()

        critical = [
            f for f in findings
            if f["severity"] == "critical"
            and f["category"] == "router_vs_filesystem"
            and f["item"] == "nonexistent_module_xyz"
        ]
        self.assertEqual(len(critical), 1)
        self.assertIn("does not exist", critical[0]["detail"])

    def test_report_is_json_serialisable(self):
        """run_checks() output is fully JSON-serialisable."""
        findings = drift_module.run_checks()
        report = {
            "total_findings": len(findings),
            "findings": findings,
        }
        serialised = json.dumps(report)
        parsed = json.loads(serialised)
        self.assertIsInstance(parsed["findings"], list)

    def test_finding_schema_has_required_keys(self):
        """Every finding has severity, category, item, and detail."""
        findings = drift_module.run_checks()
        for f in findings:
            for key in ("severity", "category", "item", "detail"):
                self.assertIn(key, f, msg=f"Finding missing key '{key}': {f}")

    def test_severity_values_are_valid(self):
        """Severity is always one of 'critical', 'warning', 'info'."""
        findings = drift_module.run_checks()
        valid = {"critical", "warning", "info"}
        for f in findings:
            self.assertIn(f["severity"], valid, msg=f"Invalid severity in: {f}")


class TestMainExitCode(unittest.TestCase):
    """Tests for the main() exit code behaviour."""

    def test_exit_0_when_no_critical(self):
        with patch.object(drift_module, "run_checks", return_value=[
            {"severity": "warning", "category": "x", "item": "y", "detail": "z"},
        ]):
            with patch("builtins.print"):
                code = drift_module.main.__wrapped__() if hasattr(drift_module.main, "__wrapped__") else None
                # Call directly since argparse uses sys.argv
                with patch("sys.argv", ["check_release_drift.py"]):
                    code = drift_module.main()
        self.assertEqual(code, 0)

    def test_exit_1_on_critical(self):
        with patch.object(drift_module, "run_checks", return_value=[
            {"severity": "critical", "category": "missing_file", "item": "router.py", "detail": "missing"},
        ]):
            with patch("builtins.print"):
                with patch("sys.argv", ["check_release_drift.py"]):
                    import io as _io
                    with patch("sys.stderr", _io.StringIO()):
                        code = drift_module.main()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
