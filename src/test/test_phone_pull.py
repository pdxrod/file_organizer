"""Behavioral tests for src/phone_pull.sh (the ADB phone sync script).

phone_pull.sh is bash; these tests copy it into a temp project layout with
a fake `adb` and a throwaway config.yaml, then verify how it behaves when
no phone is attached. Regression coverage: with `set -euo pipefail`, an
empty `adb devices` list used to kill the script silently (grep exits 1
inside a command substitution, which errexit treats as fatal) — it must
print a friendly message instead.
"""

import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
SCRIPT = ROOT / "src" / "phone_pull.sh"

BASH = shutil.which("bash") or "/bin/bash"


class PhonePullScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phone_pull_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # Same layout the script expects: it looks for config.yaml one level
        # above its own directory (the project root) with src/ beside it.
        src = self.tmp / "src"
        src.mkdir()
        self.script = src / "phone_pull.sh"
        shutil.copy2(SCRIPT, self.script)

        # A fake adb that always reports an empty device list.
        fake_adb = self.tmp / "fake-adb"
        fake_adb.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"devices\" ]; then\n"
            "    echo 'List of devices attached'\n"
            "fi\n"
        )
        fake_adb.chmod(
            fake_adb.stat().st_mode
            | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

        (self.tmp / "config.yaml").write_text(
            "drives:\n"
            "  MAIN_DRIVE: {tmp}/main\n"
            "phone_pull:\n"
            "  adb_path: {adb}\n"
            "  local_stage: {tmp}/misc\n"
            "  phone_source_dirs:\n"
            "  - file_organizer_staging\n".format(tmp=self.tmp, adb=fake_adb)
        )

    def _run(self, *args):
        return subprocess.run(
            [BASH, str(self.script)] + list(args),
            capture_output=True, text=True,
        )

    def test_syntax_is_valid(self):
        proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_list_with_no_devices_prints_message(self):
        proc = self._run("--list")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No devices connected.", proc.stdout)
        self.assertIn("Troubleshooting:", proc.stdout)

    def test_default_pull_with_no_devices_prints_message(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn(
            "No devices connected. Connect a phone and enable USB debugging.",
            proc.stdout + proc.stderr,
        )
        # The tip must point at the public `organize` CLI, not the
        # internal script path bash invokes it with ($0).
        self.assertIn(
            "Tip: Run ./organize list to see connected devices.",
            proc.stdout + proc.stderr,
        )
        self.assertNotIn("phone_pull.sh", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
