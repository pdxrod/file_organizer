"""credential_guard.py — files that look like credentials are never copied.

Name patterns plus content sniffing for API keys, private keys and tokens,
so a secret with an innocent filename (e.g. notes.txt containing 'sk-…')
is never synced, pulled, routed or linked anywhere.

Everything here is stdlib-only so it also runs on the phone (Termux).

Deliberate trade-offs (safety first, per the project owner):
- '*.key' also blocks Apple Keynote decks (rename them to .keynote).
- Content sniffing reads only small text-ish files (<= 64 KiB, first 8 KiB).
- Example/placeholder values ('api_key = your-key-here') don't count.
"""

import fnmatch
import re
from pathlib import Path

# Filenames that mark a credential (fnmatch globs, matched case-
# insensitively anywhere in the tree). Deliberately specific —
# 'keyboard.txt' and 'monkey.jpg' must NOT match.
NAME_PATTERNS = (
    # Private keys and key stores
    "*.pem", "*.ppk", "*.pfx", "*.p12", "*.key", "*.keystore", "*.jks",
    "*.kdbx",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*",
    "id_ecdsa", "id_ecdsa.*", "id_dsa", "id_dsa.*",
    ".ssh", "authorized_keys",
    # Environment / config secrets
    ".env", ".env.*", ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    ".git-credentials",
    # Anything that says what it is
    "credentials", "credentials.*", "*credential*", "*credentials*",
    "*password*", "*passwd*", "*secret*",
    "token", "token.*", "*_token*", "access_token*", "refresh_token*",
    "*api*key*",
    "kubeconfig", "kubeconfig.*", "service-account*.json",
)

# Well-known API key / token / private key formats. High precision only:
# these regexes are allowed to decide on their own.
_CONTENT_PATTERNS = [
    # OpenAI / DeepSeek / Anthropic / generic sk-… keys
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # AWS access keys
    re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    # Google API keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    # Slack tokens
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),
    # Stripe keys
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[0-9A-Za-z]{16,}"),
    # Twilio keys
    re.compile(r"\bSK[0-9a-fA-F]{32}\b"),
    # Telegram bot tokens (digits:AA…)
    re.compile(r"\b[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}"),
    # PEM/PGP private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # JWT tokens (three base64url segments, header starts with eyJ)
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
               r"[A-Za-z0-9_-]{8,}"),
]

# 'key = value' assignments for generic secret fields. Medium precision:
# the value must look like a real secret, and obvious placeholders
# ('your-key-here', 'xxx', 'example…') don't count.
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|refresh[_-]?token"
    r"|password|passwd|private[_-]?key)\b\s*[:=]\s*[\"']?"
    r"([A-Za-z0-9+/_.=@-]{16,64})"
)
_PLACEHOLDER = re.compile(
    r"example|your|placeholder|\bxxx+\b"
    r"|<[^>]+>|\.\.\.", re.I,
)

# Extensions worth sniffing for secrets (small text-ish files only).
SNIFF_EXTS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".env", ".key", ".pem", ".log", ".csv",
}
SNIFF_MAX_BYTES = 64 * 1024
HEAD_BYTES = 8 * 1024


def suspicious_name(name: str) -> bool:
    """True when the filename itself marks a credential."""
    low = str(name).lower()
    return any(fnmatch.fnmatch(low, pat) for pat in NAME_PATTERNS)


def looks_like_secret_text(text: str) -> bool:
    """True when text contains an API key, private key or token."""
    if not text:
        return False
    for rx in _CONTENT_PATTERNS:
        if rx.search(text):
            return True
    for m in _ASSIGNMENT.finditer(text):
        if not _PLACEHOLDER.search(m.group(0)):
            return True
    return False


def sniff_file_for_secret(path: Path, ext: str = "") -> bool:
    """Content-only check: True when a small text-ish file holds a
    key/token/secret in its first few KiB."""
    ext = (ext or path.suffix).lower()
    if ext not in SNIFF_EXTS:
        return False
    try:
        st = path.stat()
        if st.st_size > SNIFF_MAX_BYTES:
            return False
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
    except OSError:
        return False
    return looks_like_secret_text(head.decode("utf-8", errors="ignore"))


def file_contains_secret(path: Path, ext: str = "") -> bool:
    """True when the file must never be copied: its name marks it, or
    (for small text-ish files) its content holds a key/token/secret."""
    return suspicious_name(path.name) or sniff_file_for_secret(path, ext)
