#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[3]
COLLECT = WORKSPACE / "social" / "common" / "scripts" / "collect.py"
SECRETS = WORKSPACE / "social" / "_secrets"


def run_collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COLLECT), *args],
        cwd=WORKSPACE,
        text=True,
        capture_output=True,
        check=False,
    )


class CollectIsolationTest(unittest.TestCase):
    def test_xgame_douyin_paths_are_business_scoped(self) -> None:
        result = run_collect("--business", "xgame", "--platform", "douyin", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)

        self.assertEqual(manifest["business_id"], "xgame")
        self.assertEqual(manifest["platform"], "douyin")
        self.assertIn("/social/xgame/douyin", manifest["workspace_root"])
        self.assertIn("/social/_secrets/xgame/douyin", manifest["secret_root"])
        self.assertNotIn("chapingjun", json.dumps(manifest, ensure_ascii=False))

    def test_chapingjun_bilibili_paths_are_business_scoped(self) -> None:
        result = run_collect("--business", "chapingjun", "--platform", "bilibili", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)

        self.assertEqual(manifest["business_id"], "chapingjun")
        self.assertEqual(manifest["platform"], "bilibili")
        self.assertIn("/social/chapingjun/bilibili", manifest["workspace_root"])
        self.assertIn("/social/_secrets/chapingjun/bilibili", manifest["secret_root"])
        self.assertNotIn("xgame", json.dumps(manifest, ensure_ascii=False))

    def test_unknown_business_is_rejected(self) -> None:
        result = run_collect("--business", "unknown", "--platform", "douyin", "--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown business_id", result.stderr)

    def test_missing_business_is_rejected_by_argparse(self) -> None:
        result = run_collect("--platform", "douyin", "--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--business", result.stderr)

    def test_live_execution_is_blocked_until_backend_is_wired(self) -> None:
        result = run_collect("--business", "xgame", "--platform", "douyin")
        self.assertEqual(result.returncode, 4)
        self.assertIn("live collection requires --execute", result.stderr)

    def test_bilibili_dry_run_reports_business_scoped_credential_path(self) -> None:
        profile = f"missing-unittest-{os.getpid()}"
        result = run_collect("--business", "xgame", "--platform", "bilibili", "--account-profile", profile, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        credential_check = manifest["backend_plan"]["credential_check"]

        self.assertEqual(manifest["backend_plan"]["backend"], "bilibili-api-python")
        self.assertIn(f"/social/_secrets/xgame/bilibili/{profile}.credentials.json", credential_check["credential_path"])
        self.assertFalse(credential_check["usable"])
        self.assertEqual(credential_check["missing_fields"], ["SESSDATA", "bili_jct", "buvid3"])
        self.assertNotIn("chapingjun", credential_check["credential_path"])

    def test_bilibili_rejects_account_profile_path_traversal(self) -> None:
        result = run_collect(
            "--business",
            "xgame",
            "--platform",
            "bilibili",
            "--account-profile",
            "../chapingjun/default",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("account_profile must be a simple profile name", result.stderr)

    def test_bilibili_execute_requires_usable_business_scoped_credentials(self) -> None:
        profile = f"unittest-{os.getpid()}"
        result = run_collect(
            "--business",
            "chapingjun",
            "--platform",
            "bilibili",
            "--account-profile",
            profile,
            "--execute",
        )
        self.assertEqual(result.returncode, 5)
        self.assertIn("B站 credential is not usable", result.stderr)

    def test_bilibili_fake_credentials_are_loaded_only_from_selected_business(self) -> None:
        profile = f"unittest-{os.getpid()}"
        credential_path = SECRETS / "xgame" / "bilibili" / f"{profile}.credentials.json"
        try:
            credential_path.write_text(
                json.dumps(
                    {
                        "SESSDATA": "fake_sessdata",
                        "bili_jct": "fake_bili_jct",
                        "buvid3": "fake_buvid3",
                    }
                ),
                encoding="utf-8",
            )
            credential_path.chmod(0o600)
            result = run_collect(
                "--business",
                "xgame",
                "--platform",
                "bilibili",
                "--account-profile",
                profile,
                "--dry-run",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(result.stdout)
            plan = manifest["backend_plan"]

            self.assertTrue(plan["credential_check"]["usable"])
            self.assertTrue(plan["credential_object"]["constructable"])
            self.assertTrue(plan["credential_object"]["has_sessdata"])
            self.assertIn("/social/_secrets/xgame/bilibili/", plan["credential_check"]["credential_path"])
            self.assertNotIn("chapingjun", plan["credential_check"]["credential_path"])
        finally:
            credential_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
