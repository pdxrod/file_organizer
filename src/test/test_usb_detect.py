"""Tests for phone_sync.usb_plugged_in (USB plug-in detection).

usb_plugged_in reads Android sysfs entries; these tests point it at fake
roots so the detection logic is exercised on any platform. The phone's
"push to the computer while plugged in" behavior hangs off this function:
while plugged, the daemon keeps file_organizer_staging fresh for the
computer's auto pull.
"""

import tempfile
import unittest
from pathlib import Path

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from file_organizer import phone_sync  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class UsbPluggedInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _power(self, kind: str, present: str):
        dev = self.root / "psu" / "battery"
        _write(dev / "type", kind)
        _write(dev / "present", present)
        return str(self.root / "psu")

    def _android_usb(self, state: str):
        dev = self.root / "android_usb" / "android0"
        _write(dev / "state", state)
        return str(self.root / "android_usb")

    def test_missing_roots_return_false(self):
        self.assertFalse(
            phone_sync.usb_plugged_in(
                power_supply_root=str(self.root / "nope"),
                android_usb_root=str(self.root / "nope2"),
            )
        )

    def test_usb_present_is_true(self):
        root = self._power("USB", "1")
        self.assertTrue(phone_sync.usb_plugged_in(
            power_supply_root=root,
            android_usb_root=str(self.root / "nope"),
        ))

    def test_usb_not_present_is_false(self):
        root = self._power("USB", "0")
        self.assertFalse(phone_sync.usb_plugged_in(
            power_supply_root=root,
            android_usb_root=str(self.root / "nope"),
        ))

    def test_mains_charger_is_not_usb(self):
        root = self._power("Mains", "1")
        self.assertFalse(phone_sync.usb_plugged_in(
            power_supply_root=root,
            android_usb_root=str(self.root / "nope"),
        ))

    def test_android_usb_configured_is_true(self):
        root = self._android_usb("CONFIGURED")
        self.assertTrue(phone_sync.usb_plugged_in(
            power_supply_root=str(self.root / "nope"),
            android_usb_root=root,
        ))

    def test_android_usb_disconnected_is_false(self):
        root = self._android_usb("DISCONNECTED")
        self.assertFalse(phone_sync.usb_plugged_in(
            power_supply_root=str(self.root / "nope"),
            android_usb_root=root,
        ))


if __name__ == "__main__":
    unittest.main()
