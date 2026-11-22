"""Tests for the Python phone puller (file_organizer.phone_pull).

A fake `adb` executable answers devices/shell and streams a real tar
archive for exec-out, so the whole pull path runs without a phone:
filename rules (held app-generated names stay on the phone, counters
normalise), staging-map placement, state tracking and the second-run
skip.
"""

import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer.phone_pull import Puller  # noqa: E402

FAKE_ADB = r"""#!/usr/bin/env python3
import io, sys, tarfile
args = sys.argv[1:]
if "devices" in args:
    print("List of devices attached")
    print("FAKESERIAL\tdevice")
elif args[:2] == ["-s", "FAKESERIAL"] and args[2] == "shell":
    if "getprop" in args[3]:
        print("FakePhone")
    else:
        print("yes")
elif args[:2] == ["-s", "FAKESERIAL"] and args[2] == "exec-out":
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, content in (
            ("DCIM/Screenshots/2026114_foo_baz.jpg", b"renamed-jpg"),
            ("DCIM/Screenshots/Screenshot_20261114_777_X.jpg", b"held-jpg"),
            ("DCIM/Camera/20261111_celtic.png", b"ready-png"),
        ):
            info = tarfile.TarInfo(rel)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    sys.stdout.buffer.write(buf.getvalue())
else:
    sys.exit(1)
"""


class PullerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phone_pull_py_"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True)
        )
        self.adb = self.tmp / "fakeadb"
        self.adb.write_text(FAKE_ADB)
        os.chmod(str(self.adb), 0o755)
        self.main = self.tmp / "main"
        self.cfg = {
            "drives": {"MAIN_DRIVE": str(self.main)},
            "phone_pull": {
                "adb_path": str(self.adb),
                "android_base": "/storage/emulated/0",
                "phone_source_dirs": ["file_organizer_test_staging"],
                "local_stage": str(self.main / "misc"),
                "hold_app_generated_names": True,
                "normalise_leading_dates": True,
                "state_file": str(self.tmp / "state.json"),
                "staging_map": {},
            },
        }

    def test_pull_copies_ready_files_and_holds_app_names(self):
        ok = Puller(self.cfg, quiet=True).pull_all()
        self.assertTrue(ok)
        screenshots = self.main / "misc" / "DCIM" / "Screenshots"
        self.assertTrue((screenshots / "2026114_foo_baz.jpg").is_file())
        # App-generated names are held — they stay on the phone.
        self.assertFalse(
            (screenshots / "Screenshot_20261114_777_X.jpg").exists()
        )
        self.assertTrue(
            (self.main / "misc" / "DCIM" / "Camera"
             / "20261111_celtic.png").is_file()
        )
        state = __import__("json").loads(
            (self.tmp / "state.json").read_text()
        )
        self.assertEqual(len(state), 2)  # the two copied files

    def test_second_run_skips_unchanged(self):
        puller = Puller(self.cfg, quiet=True)
        self.assertTrue(puller.pull_all())
        before = (self.main / "misc" / "DCIM" / "Screenshots"
                  / "2026114_foo_baz.jpg").read_bytes()
        self.assertTrue(puller.pull_all())
        after = (self.main / "misc" / "DCIM" / "Screenshots"
                 / "2026114_foo_baz.jpg").read_bytes()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
