"""Tests for credential_guard: files that look like credentials are
never copied anywhere — by name, and by content for small text files."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_organizer.credential_guard import (  # noqa: E402
    file_contains_secret,
    looks_like_secret_text,
    sniff_file_for_secret,
    suspicious_name,
)


class NamePatternTests(unittest.TestCase):
    def test_credential_names_match(self):
        for name in (
            "deepseek-api-key.txt", "api_key.txt", ".env", ".env.local",
            "id_rsa", "id_rsa.pub", "credentials.json", "secret-notes.md",
            "server.pem", "kubeconfig", "service-account-prod.json",
            "token.json", "my_access_token.txt", "password-list.txt",
        ):
            self.assertTrue(suspicious_name(name), name)

    def test_innocent_names_do_not_match(self):
        for name in (
            "keyboard.jpg", "monkey.png", "keynotes.pdf", "hockey-photo.jpg",
            "turkey-dinner.jpg", "whiskey.jpg", "tokyo-trip.md",
            "tokens-of-friendship.txt",
        ):
            self.assertFalse(suspicious_name(name), name)


class ContentPatternTests(unittest.TestCase):
    def test_known_key_formats_match(self):
        samples = [
            # OpenAI/DeepSeek style
            "sk-" + "a" * 20,
            # GitHub
            "ghp_" + "A" * 25,
            "github_pat_" + "B" * 30,
            # AWS
            "AKIA" + "A" * 16,
            # Google
            "AIza" + "c" * 35,
            # Slack
            "xoxb-123456789012-" + "d" * 15,
            # Stripe
            "sk_live_" + "e" * 20,
            # Twilio
            "SK" + "f" * 32,
            # Telegram bot token
            "123456789:AA" + "g" * 32,
            # PEM private keys
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            # JWT
            "eyJ" + "h" * 10 + "." + "i" * 10 + "." + "j" * 10,
        ]
        for sample in samples:
            self.assertTrue(looks_like_secret_text(sample), sample[:30])

    def test_key_value_assignments_match(self):
        self.assertTrue(
            looks_like_secret_text("api_key = abcdefghijklmnopqrstuvwxyz"))
        self.assertTrue(
            looks_like_secret_text('secret: "ABCDEFGHIJKLMNOPQRSTUVWXYZ"'))
        self.assertTrue(
            looks_like_secret_text(
                "password=correcthorsebatterystaple"))

    def test_placeholders_do_not_match(self):
        for text in (
            "api_key = your-key-here",
            "api_key = your_api_key_here_123",
            "secret: example-secret-value-12345",
            "password: xxx",
            "api_key: <put your key here>",
        ):
            self.assertFalse(looks_like_secret_text(text), text)

    def test_plain_prose_does_not_match(self):
        for text in (
            "The quick brown fox jumps over the lazy dog.",
            "I have a key question about the keyboard.",
            "sk- is a common key prefix but sk- alone is not a key.",
            "AIza nothing to see here",
        ):
            self.assertFalse(looks_like_secret_text(text), text)


class FileSniffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="credguard_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_secret_content_in_innocent_file(self):
        p = self.tmp / "notes.txt"
        p.write_text("reminder: sk-" + "z" * 20)
        self.assertTrue(sniff_file_for_secret(p))
        self.assertTrue(file_contains_secret(p))

    def test_secret_content_in_binary_extension_is_not_sniffed(self):
        p = self.tmp / "photo.jpg"
        p.write_bytes(b"sk-" + b"z" * 20)
        self.assertFalse(sniff_file_for_secret(p))

    def test_large_file_is_not_sniffed(self):
        p = self.tmp / "big.txt"
        p.write_bytes(b"x" * (80 * 1024) + b" sk-" + b"z" * 20)
        self.assertFalse(sniff_file_for_secret(p))

    def test_suspicious_name_wins_even_when_empty(self):
        p = self.tmp / "api-key.txt"
        p.write_text("")
        self.assertTrue(file_contains_secret(p))


class SyncEngineExcludeTests(unittest.TestCase):
    """rsync must exclude credential files found by content too."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="credrsync_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def _engine(self):
        from file_organizer import sync_engine
        tmp = self.tmp

        class StubConfig:
            def __init__(self):
                self.raw_config = {"sync_failure_state_path":
                                   str(tmp / "f.json")}

        return sync_engine.SyncEngine(StubConfig())

    def test_content_sniffed_files_are_excluded(self):
        root = self.tmp / "src"
        (root / "sub").mkdir(parents=True)
        (root / "notes.txt").write_text("sk-" + "z" * 20)
        (root / "sub" / "creds.md").write_text("ghp_" + "A" * 25)
        (root / "ok.txt").write_text("hello world")
        engine = self._engine()
        excl = engine._credential_excludes(root)
        self.assertIn("/notes.txt", excl)
        self.assertIn("/sub/creds.md", excl)
        self.assertNotIn("/ok.txt", excl)
        # And they reach the rsync command line.
        args = engine._build_rsync_args(root, self.tmp / "dst")
        self.assertIn("--exclude", args)
        self.assertIn("/notes.txt", args)

    def test_cloud_source_is_not_sniffed(self):
        # Reading cloud placeholders materialises them — only name-based
        # patterns apply on cloud sides.
        root = self.tmp / "Library" / "CloudStorage" / "Proton" / "misc"
        root.mkdir(parents=True)
        (root / "notes.txt").write_text("sk-" + "z" * 20)
        engine = self._engine()
        self.assertEqual(engine._credential_excludes(root), [])


if __name__ == "__main__":
    unittest.main()
