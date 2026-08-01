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

# Indicator files that strongly suggest a gittable project.
# Note: .gitignore, .gitattributes, .editorconfig are intentionally excluded —
# they often exist at collection-folder level (e.g. ~/dev/ml/.gitignore) and
# do not reliably indicate a project root.
_PROJECT_INDICATORS: set[str] = {
    "requirements.txt", "Pipfile", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "yarn.lock", "pnpm-lock.yaml",
    "Gemfile", "Rakefile", "Cargo.toml", "go.mod", "mix.exs",
    "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml",
}

# Directory names that are inherently subdirectories of a project,
# never standalone project roots worth gitting on their own.
_SUBFOLDER_PATTERNS: set[str] = {
    # Rails / Ruby
    "app", "config", "db", "lib", "public", "spec", "test", "tests",
    "controllers", "models", "views", "helpers", "mailers", "jobs",
    "channels", "policies", "validators", "serializers", "decorators",
    "environments", "initializers", "locales", "migrations", "seeds",
    "assets", "stylesheets", "javascripts", "images", "fonts", "icons",
    # Python
    "src", "include",
    # JS / TS
    "components", "pages", "hooks", "services", "stores", "reducers",
    "actions", "selectors", "middleware", "providers", "contexts",
    "utils", "helpers", "common", "core", "modules", "shared",
    "features", "layouts", "plugins", "extensions", "workers",
    # General project infrastructure (never a project root)
    ".github", ".circleci", ".vscode", ".idea", ".cursor",
    "docs", "examples", "notebooks", "benchmarks",
    "scripts", "tools", "bin",
    "routes", "api", "graphql", "schemas", "types", "interfaces",
    "constants", "enums", "fixtures", "mocks", "stubs",
    "static", "public", "templates", "layouts", "partials",
    "forms", "tables",
}

# Patterns that indicate a folder should NOT be gitted
_NO_GIT_INDICATORS: set[str] = {
    "data", "dataset", "datasets", "models", "checkpoints",
    "weights", "logs", "output", "outputs", "results",
    "downloads", "cache", ".cache", "tmp", "temp",
    "__pycache__", ".venv", "venv", "env", "node_modules",
    "dist", "build", "target", ".next", ".nuxt",
    # Additional non-project folders
    "backup", "backups", "archive", "archives", "old",
    "vendor", "bower_components", "packages",
    "uploads", "media", "videos", "audio", "music",
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

    def _count_direct_items(self, folder: Path) -> tuple[int, int]:
        """Count direct children: (source_files, subdirectories)."""
        src_files = 0
        subdirs = 0
        try:
            for item in folder.iterdir():
                if item.name.startswith("."):
                    continue
                if item.is_file():
                    ext = item.suffix.lower()
                    if ext in _SOURCE_EXTENSIONS or item.name in _PROJECT_INDICATORS:
                        src_files += 1
                elif item.is_dir():
                    if item.name not in _NO_GIT_INDICATORS:
                        subdirs += 1
        except (OSError, PermissionError):
            pass
        return src_files, subdirs

    def _is_collection_folder(self, folder: Path) -> bool:
        """Detect folders that are collections of projects, not projects themselves.

        A collection folder has many subdirectories but few direct source files
        and no project indicator files. E.g. ~/dev/ml/ containing many ML projects.
        """
        if self._has_project_indicator(folder):
            return False  # has a Gemfile/package.json/etc — definitely a project

        direct_src, subdirs = self._count_direct_items(folder)
        # More subdirs than source files + no project indicator → collection
        # (e.g. ~/dev/ml/: 27 subdirs vs 16 loose files → collection)
        if subdirs >= 5 and direct_src < subdirs:
            return True
        # Some subdirs + very few direct source files → likely a collection
        # (e.g. ~/dev/rails/: 2 subdirs, 0 source files → collection)
        if subdirs >= 2 and direct_src < 3:
            return True
        return False

    def _has_subfolder_name(self, folder: Path) -> bool:
        """Check if folder name suggests it's a project subdirectory."""
        return folder.name.lower() in _SUBFOLDER_PATTERNS

    def _has_git_ancestor(self, folder: Path) -> bool:
        """Check if any ancestor directory (up to auto_git_folders) has a .git."""
        for ancestor in folder.parents:
            if (ancestor / ".git").exists():
                return True
            if not any(str(ancestor).startswith(base) for base in self._auto_git_folders):
                break
        return False

    def should_git_init(self, folder: Path) -> bool:
        """Determine if a folder is worth initializing git in."""
        if not folder.is_dir():
            return False

        # Already has .git
        if (folder / ".git").exists():
            return False

        # Ancestor already has .git — this folder is already tracked
        if self._has_git_ancestor(folder):
            return False

        # Skip folders matching no-git indicators
        if folder.name in _NO_GIT_INDICATORS:
            return False

        # Skip folders whose name screams "project subdirectory"
        if self._has_subfolder_name(folder):
            return False

        # Skip collection folders (e.g. ~/dev/ml/ with many subprojects)
        if self._is_collection_folder(folder):
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
        """Initialize a git repository: git init, write .gitignore, git add ., git commit."""
        try:
            # 1. git init
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

            # 2. Write .gitignore
            gitignore_path = folder / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text(DEFAULT_GITIGNORE)
                logger.info("Created .gitignore in %s", folder)

            # 3. git add .
            result = subprocess.run(
                ["git", "add", "."],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.warning("git add failed in %s: %s", folder, result.stderr)

            # 4. git commit
            result = subprocess.run(
                ["git", "commit", "-m", "Initial commit (auto-git)"],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                if "nothing to commit" in (result.stderr + result.stdout):
                    logger.info("Nothing to commit in %s (repo initialized, .gitignore created)", folder)
                else:
                    logger.warning("git commit failed in %s: %s", folder, result.stderr)

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

    def _collect_candidates(self) -> list[dict]:
        """Walk auto_git_folders and return list of candidate folders without acting.

        Applies parent-aware filtering: if a parent directory is already a
        candidate, its children are excluded (one .git at the root is enough).
        """
        raw: list[dict] = []
        for base_path in self._auto_git_folders:
            base = Path(base_path)
            if not base.exists():
                logger.warning("Auto-git folder does not exist: %s", base)
                continue

            for dirpath_str, dirnames, _ in os.walk(base, followlinks=False):
                dirpath = Path(dirpath_str)
                depth = len(dirpath.relative_to(base).parts)
                if depth > 2:
                    dirnames.clear()
                    continue

                # Remove no-git folders from walk so we don't descend into them
                for d in dirnames[:]:
                    if d in _NO_GIT_INDICATORS:
                        dirnames.remove(d)

                for d in dirnames[:]:
                    folder = dirpath / d
                    if folder.name.startswith(".") or folder.name in _NO_GIT_INDICATORS:
                        continue

                    if self.should_git_init(folder):
                        has_indicator = self._has_project_indicator(folder)
                        source_count = self._count_source_files(folder)
                        raw.append({
                            "path": str(folder),
                            "has_project_indicator": has_indicator,
                            "source_file_count": source_count,
                            "reason": "project indicator files found" if has_indicator
                                      else f"{source_count} source files detected",
                        })

        # --- Parent-aware filtering ---
        # Sort shallowest-first so we encounter parents before children.
        raw.sort(key=lambda c: c["path"].count(os.sep))

        candidate_paths: set[str] = set()
        filtered: list[dict] = []
        for c in raw:
            cpath = Path(c["path"])
            # Check if any ancestor is already a candidate
            excluded = False
            for ancestor in cpath.parents:
                if str(ancestor) in candidate_paths:
                    excluded = True
                    break
                # Stop walking ancestors once we leave the auto_git_folders
                if not any(
                    str(ancestor).startswith(base)
                    for base in self._auto_git_folders
                ):
                    break
            if not excluded:
                candidate_paths.add(str(cpath))
                filtered.append(c)

        return filtered

    def preview(self) -> dict:
        """Preview which folders would get git init without making changes."""
        if not self.enabled:
            return {"enabled": False, "message": "Auto-git is disabled or no folders configured."}

        candidates = self._collect_candidates()
        return {
            "enabled": True,
            "auto_git_folders": self._auto_git_folders,
            "candidates": candidates,
            "candidate_count": len(candidates),
        }

    def scan_and_init(self) -> dict:
        """Scan auto_git_folders and init git where appropriate. Returns summary."""
        if not self.enabled:
            logger.debug("Auto-git is disabled or no folders configured.")
            return {"scanned": 0, "initialized": 0, "skipped": 0}

        summary = {"scanned": 0, "initialized": 0, "skipped": 0}

        candidates = self._collect_candidates()
        for c in candidates:
            folder = Path(c["path"])
            summary["scanned"] += 1
            if self.git_init(folder):
                summary["initialized"] += 1
            else:
                summary["skipped"] += 1

        return summary
