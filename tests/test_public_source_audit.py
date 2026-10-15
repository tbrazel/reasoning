"""Exact fixture exemptions must not suppress a new credential-like value.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import os
import re
import secrets
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SourceAuditConfiguration(unittest.TestCase):
    def test_exemptions_keep_default_rules_and_exact_paths_and_values(self):
        config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
        self.assertEqual(config["extend"], {"useDefault": True})
        self.assertTrue(config["allowlists"])
        self.assertFalse((ROOT / ".gitleaksignore").exists())
        files = []
        for directory, children, names in os.walk(ROOT):
            children[:] = [name for name in children if name not in {"node_modules", ".venv", ".git", "build", "dist"}]
            files.extend((Path(directory) / name).relative_to(ROOT).as_posix() for name in names)
        for entry in config["allowlists"]:
            self.assertEqual(entry["targetRules"], ["generic-api-key"])
            self.assertEqual(entry["condition"], "AND")
            self.assertEqual(entry["regexTarget"], "secret")
            self.assertEqual(len(entry["paths"]), 1)
            for pattern in entry["paths"] + entry["regexes"]:
                self.assertTrue(pattern.startswith("^") and pattern.endswith("$"))
                self.assertNotIn(".*", pattern)
            self.assertTrue(any(re.fullmatch(entry["paths"][0], path) for path in files))

    @unittest.skipUnless(os.environ.get("REASONING_GITLEAKS"), "requires the pinned source-audit scanner")
    def test_new_value_in_an_exempted_fixture_is_still_detected(self):
        with tempfile.TemporaryDirectory(prefix="reasoning-audit-canary-") as temporary:
            root = Path(temporary)
            candidate = root / "tests/test_cloud_client.py"
            candidate.parent.mkdir()
            # Generated only for this scanner test; never a provider credential.
            candidate.write_text('api_key = "' + secrets.token_hex(24) + '"\n')
            report = root / "report.json"
            result = subprocess.run([os.environ["REASONING_GITLEAKS"], "dir", str(root), "--config", str(ROOT / ".gitleaks.toml"),
                                     "--redact=100", "--no-banner", "--log-level", "error", "--report-format", "json",
                                     "--report-path", str(report)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(any(row["RuleID"] == "generic-api-key" for row in json.loads(report.read_text())))


if __name__ == "__main__":
    unittest.main()
