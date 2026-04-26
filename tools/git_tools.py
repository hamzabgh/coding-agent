"""
Git Tools — safety layer before any file modification.
Creates backup commits so every change is reversible.
"""

import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional


class GitTools:
    def __init__(self, project_root: str = "."):
        self.root = Path(project_root).resolve()
        self._git_available = self._check_git()

    def create_backup_commit(self, file_path: str, action: str) -> bool:
        """
        Stage and commit current state before agent modifies it.
        Returns True if backup was created.
        """
        if not self._git_available:
            return False
        try:
            self._run(["git", "add", file_path])
            msg = f"[agent-backup] Before {action} on {file_path} — {datetime.now().isoformat()}"
            self._run(["git", "commit", "-m", msg, "--allow-empty"])
            return True
        except Exception:
            return False

    def get_diff(self, file_path: str) -> str:
        """Return git diff for a file (unstaged changes)."""
        if not self._git_available:
            return ""
        try:
            result = self._run(["git", "diff", file_path])
            return result.stdout
        except Exception:
            return ""

    def rollback_last(self, file_path: str) -> bool:
        """Revert file to previous commit."""
        if not self._git_available:
            return False
        try:
            self._run(["git", "checkout", "HEAD~1", "--", file_path])
            return True
        except Exception:
            return False

    def get_recent_changes(self, n: int = 5) -> str:
        """Return last N agent commits."""
        if not self._git_available:
            return ""
        try:
            result = self._run([
                "git", "log", f"-{n}",
                "--oneline", "--grep=agent-backup"
            ])
            return result.stdout
        except Exception:
            return ""

    def is_clean(self) -> bool:
        """Check if working tree is clean."""
        if not self._git_available:
            return True
        try:
            result = self._run(["git", "status", "--porcelain"])
            return result.stdout.strip() == ""
        except Exception:
            return True

    # ─────────────────────────────────
    # Private
    # ─────────────────────────────────

    def _check_git(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.root, capture_output=True, text=True
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _run(self, cmd: list) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, cwd=self.root,
            capture_output=True, text=True, check=True
        )
