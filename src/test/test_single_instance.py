"""Tests for single_instance: only one organizer daemon may run at a
time, enforced with an exclusive pid-file lock."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_organizer.single_instance import (  # noqa: E402
    acquire,
    pid_alive,
    read_holder,
    release,
)


class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="single_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.pid_file = self.tmp / "file_organizer.pid"

    def test_acquire_writes_pid_and_blocks_second(self):
        lock1 = acquire(self.pid_file)
        self.assertIsNotNone(lock1)
        self.assertEqual(read_holder(self.pid_file), os.getpid())
        # A second start must be refused while the first holds the lock.
        lock2 = acquire(self.pid_file)
        self.assertIsNone(lock2)
        release(lock1, self.pid_file)

    def test_release_removes_pid_file(self):
        lock = acquire(self.pid_file)
        self.assertIsNotNone(lock)
        release(lock, self.pid_file)
        self.assertFalse(self.pid_file.exists())

    def test_release_after_release_allows_reacquire(self):
        lock = acquire(self.pid_file)
        release(lock, self.pid_file)
        lock2 = acquire(self.pid_file)
        self.assertIsNotNone(lock2)
        self.assertEqual(read_holder(self.pid_file), os.getpid())
        release(lock2, self.pid_file)

    def test_stale_pid_file_is_taken_over(self):
        # A dead process's pid file must not block a new daemon.
        self.pid_file.write_text("99999999")
        self.assertEqual(read_holder(self.pid_file), 0)
        lock = acquire(self.pid_file)
        self.assertIsNotNone(lock)
        self.assertEqual(self.pid_file.read_text().strip(),
                         str(os.getpid()))
        release(lock, self.pid_file)

    def test_garbage_pid_file_is_taken_over(self):
        self.pid_file.write_text("not-a-pid")
        self.assertEqual(read_holder(self.pid_file), 0)
        lock = acquire(self.pid_file)
        self.assertIsNotNone(lock)
        release(lock, self.pid_file)

    def test_pid_alive(self):
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(99999999))
        self.assertFalse(pid_alive(0))

    def test_holder_survives_while_locked_and_clears_on_release(self):
        lock = acquire(self.pid_file)
        self.assertEqual(read_holder(self.pid_file), os.getpid())
        release(lock, self.pid_file)
        self.assertEqual(read_holder(self.pid_file), 0)


if __name__ == "__main__":
    unittest.main()
