#!/usr/bin/env python3
"""Offline repo-launcher sync tests; no Google checkout or kernel build."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("config", "scripts", "bin"):
            (self.root / name).mkdir()
        shutil.copy2(ROOT / "scripts/sync-source.sh", self.root / "scripts/sync-source.sh")
        (self.root / "config/manifest-lock.xml").write_text("<manifest/>\n")
        self.script("scripts/verify-source.sh", 'touch "$YOGI_WORKDIR/verified"\n')
        self.script("bin/git", "exit 0\n")
        self.script("bin/curl", 'cat "$TEST_LAUNCHER"\n')
        self.payload = b'#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_REPO_LOG"\n'
        (self.root / "launcher").write_bytes(self.payload)
        (self.root / "config/pins.env").write_text(
            "REPO_LAUNCHER_URL=https://invalid.example/not-used\n"
            f"REPO_LAUNCHER_SHA256={hashlib.sha256(self.payload).hexdigest()}\n"
            "GOOGLE_MANIFEST_REPO=https://invalid.example/not-used\n"
            "GOOGLE_MANIFEST_BRANCH=fixture\nGOOGLE_MANIFEST_REF=fixture\n"
        )
        self.work = self.root / "work"
        self.log = self.root / "repo.log"
        self.env = {**os.environ, "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                    "YOGI_WORKDIR": str(self.work), "TEST_REPO_LOG": str(self.log),
                    "TEST_LAUNCHER": str(self.root / "launcher")}

    def script(self, path, text):
        path = self.root / path
        path.write_text("#!/bin/sh\nset -eu\n" + text)
        path.chmod(0o755)

    def sync(self):
        return subprocess.run(["bash", str(self.root / "scripts/sync-source.sh")],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_reviewed_pin_is_immutable_and_digest_unchanged(self):
        pins = dict(line.split("=", 1) for line in (ROOT / "config/pins.env").read_text().splitlines()
                    if line and not line.startswith("#"))
        self.assertRegex(pins["REPO_LAUNCHER_URL"],
                         r"^https://raw\.githubusercontent\.com/GerritCodeReview/git-repo/[0-9a-f]{40}/repo$")
        self.assertEqual(pins["REPO_LAUNCHER_SHA256"],
                         "1211b57b57e4122a9c546295a59b37d24068f1164d0e87bef096d5323c413e4f")
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertNotIn("      - 'README.md'", workflow)
        self.assertIn("python3 scripts/test-yogi-sync.py", workflow)

    def test_verified_download_reaches_init_sync_and_source_checks(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertEqual([command.split()[0] for command in commands], ["init", "sync"])
        self.assertTrue((self.work / "verified").exists())

    def test_bad_download_never_executes(self):
        (self.root / "launcher").write_bytes(self.payload + b"# wrong bytes\n")
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repo launcher checksum mismatch", result.stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.work / "verified").exists())

    def test_bad_cached_launcher_is_not_executed_or_replaced(self):
        cached = self.work / "bin/repo"
        cached.parent.mkdir(parents=True)
        invalid = self.payload + b"# stale cache\n"
        cached.write_bytes(invalid)
        cached.chmod(0o755)
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repo launcher checksum mismatch", result.stderr)
        self.assertEqual(cached.read_bytes(), invalid)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
