""" Auto-git: intelligent git initialization for development folders.

Only initializes git in folders that actually contain source code,
not in data directories, build outputs, or nested dependency folders.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# File extensions that indicate a "gittable" source folder
_SOURCE_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rb", ".php", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".swift", ".kt",
    ".ex", ".exs", ".erl", ".hrl", ".clj", ".cljs", ".scm", ".hs",
    ".ml", ".mli", ".nim", ".zig", ".odin", ".r", ".rmd",
    ".sh", ".bash", ".zsh", ".fish",
    ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".md", ".rst", ".tex", ".org",
    ".sql", ".graphql",
    ".dockerfile", ".makefile", ".cmake",
}

# Indicator files that strongly suggest a gittable project
_PROJECT_INDICATORS: set[str] = {
    "requirements.txt", "Pipfile", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "yarn.lock", "pnpm-lock.yaml",
    "Gemfile", "Rakefile", "Cargo.toml", "go.mod", "mix.exs",
    "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml",
    ".gitignore", ".gitattributes", ".editorconfig",
}

# Patterns that indicate a folder should NOT be gitted
_NO_GIT_INDICATORS: set[str] = {
    "data", "dataset", "datasets", "models", "checkpoints",
    "weights", "logs", "output", "outputs", "results",
    "downloads", "cache", ".cache", "tmp", "temp",
    "__pycache__", ".venv", "venv", "env", "node_modules",
    "dist", "build", "target", ".next", ".nuxt",
}

DEFAULT_GITIGNORE = """\
# Dependencies
node_modules/
.venv/
venv/
env/
__pycache__/
*.pyc
*.pyo

# Build outputs
dist/
build/
target/
*.egg-info/

# IDE
.vscode/
.idea/
.cursor/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.local
.env.*.local

# Logs
*.log
logs/

# Database
*.db
*.sqlite
*.sqlite3

# Large files
*.zip
*.tar.gz
*.7z
*.rar
*.mp4
*.mkv
*.avi
*.mov
*.iso
*.dmg
*.pkg
"""


class AutoGit:
    """Intelligently initializes git repositories in development folders."""

    def __init__(self, config):
        self.config = config
        self._auto_git: bool = config.raw_config.get("auto_git", False)
        self._auto_git_folders: list[str] = (
            config.raw_config.get("auto_git_folders", []) or []
        )

    @property
    def enabled(self) -> bool:
        return self._auto_git and bool(self._auto_git_folders)

    def _count_source_files(self, folder: Path, max_depth: int = 2) -> int:
        """Count source files in folder (limited depth)."""
        count = 0
        try:
            for item in folder.iterdir():
                if item.is_file():
                    ext = item.suffix.lower()
                    if ext in _SOURCE_EXTENSIONS:
                        count += 1
                    if item.name in _PROJECT_INDICATORS:
                        count += 5  # strong signal
                elif item.is_dir() and max_depth > 0:
                    if item.name not in _NO_GIT_INDICATORS and not item.name.startswith("."):
                        count += self._count_source_files(item, max_depth - 1)
        except (OSError, PermissionError):
            pass
        return count

    def _has_project_indicator(self, folder: Path) -> bool:
        """Check if folder contains project indicator files."""
        for indicator in _PROJECT_INDICATORS:
            if (folder / indicator).exists():
                return True
        return False

    def should_git_init(self, folder: Path) -> bool:
        """Determine if a folder is worth initializing git in."""
        if not folder.is_dir():
            return False

        # Already has .git
        if (folder / ".git").exists():
            return False

        # Skip folders matching no-git indicators
        if folder.name in _NO_GIT_INDICATORS:
            return False

        # Strong signals
        if self._has_project_indicator(folder):
            return True

        # Count source files
        source_count = self._count_source_files(folder)
        if source_count >= 3:
            return True

        return False

    def git_init(self, folder: Path) -> bool:
        """Initialize a git repository in the given folder."""
        try:
            # git init
            result = subprocess.run(
                ["git", "init"],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.error("git init failed in %s: %s", folder, result.stderr)
                return False

            # Write .gitignore
            gitignore_path = folder / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text(DEFAULT_GITIGNORE)
                logger.info("Created .gitignore in %s", folder)

            logger.info("Initialized git repository in %s", folder)
            return True

        except FileNotFoundError:
            logger.error("git command not found — please install git")
            return False
        except subprocess.TimeoutExpired:
            logger.error("git init timed out in %s", folder)
            return False
        except OSError as e:
            logger.error("Failed to git init %s: %s", folder, e)
            return False

    def scan_and_init(self) -> dict:
        """Scan auto_git_folders and init git where appropriate. Returns summary."""
        if not self.enabled:
            logger.debug("Auto-git is disabled or no folders configured.")
            return {"scanned": 0, "initialized": 0, "skipped": 0}

        summary = {"scanned": 0, "initialized": 0, "skipped": 0}

        for base_path in self._auto_git_folders:
            base = Path(base_path)
            if not base.exists():
                logger.warning("Auto-git folder does not exist: %s", base)
                continue

            # Walk subdirectories (depth 2: e.g., dev/project-name)
            for dirpath_str, dirnames, _ in os.walk(base, followlinks=False):
                dirpath = Path(dirpath_str)

                # Don't go too deep
                depth = len(dirpath.relative_to(base).parts)
                if depth > 2:
                    dirnames.clear()
                    continue

                for d in dirnames[:]:
                    folder = dirpath / d
                    if folder.name.startswith(".") or folder.name in _NO_GIT_INDICATORS:
                        continue

                    summary["scanned"] += 1

                    if self.should_git_init(folder):
                        if self.git_init(folder):
                            summary["initialized"] += 1
                        else:
                            summary["skipped"] += 1
                    else:
                        summary["skipped"] += 1

        return summary
