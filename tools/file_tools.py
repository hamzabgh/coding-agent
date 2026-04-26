"""
File Tools — safe file I/O with validation.
"""

import os
import shutil
from pathlib import Path
from typing import List, Dict
from datetime import datetime

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".rs", ".java", ".cpp", ".c",
    ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".sh", ".env.example"
}

MAX_FILE_SIZE_KB = 200  # Files larger than this get chunked


class FileTools:
    def __init__(self, project_root: str = "."):
        self.root = Path(project_root).resolve()

    def read(self, path: str) -> str:
        """Read a file safely."""
        full_path = self._resolve(path)
        self._validate_read(full_path)
        return full_path.read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str):
        """Write content to a file, creating backup first."""
        full_path = self._resolve(path)
        self._backup(full_path)
        full_path.write_text(content, encoding="utf-8")

    def list_project_files(
        self,
        extensions: set = None,
        exclude_dirs: List[str] = None
    ) -> List[Path]:
        """List all code files in the project."""
        exts = extensions or SUPPORTED_EXTENSIONS
        exclude = set(exclude_dirs or [
            ".git", "__pycache__", "node_modules",
            ".venv", "venv", "dist", "build", ".next"
        ])
        files = []
        for f in self.root.rglob("*"):
            if f.is_file() and f.suffix in exts:
                if not any(part in exclude for part in f.parts):
                    files.append(f)
        return sorted(files)

    def get_project_tree(self, max_depth: int = 3) -> str:
        """Return ASCII tree of project structure."""
        lines = [str(self.root.name) + "/"]
        self._tree_walk(self.root, lines, prefix="", depth=0, max_depth=max_depth)
        return "\n".join(lines)

    def read_multiple(self, paths: List[str]) -> Dict[str, str]:
        """Read multiple files, return dict of path → content."""
        result = {}
        for p in paths:
            try:
                result[p] = self.read(p)
            except Exception as e:
                result[p] = f"# ERROR reading file: {e}"
        return result

    def file_summary(self, path: str) -> Dict:
        """Return metadata about a file."""
        full_path = self._resolve(path)
        stat = full_path.stat()
        return {
            "path": str(path),
            "size_kb": round(stat.st_size / 1024, 2),
            "lines": len(full_path.read_text(encoding="utf-8").splitlines()),
            "extension": full_path.suffix,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }

    # ─────────────────────────────────
    # Private helpers
    # ─────────────────────────────────

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    def _validate_read(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")
        size_kb = path.stat().st_size / 1024
        if size_kb > MAX_FILE_SIZE_KB:
            raise ValueError(
                f"File too large ({size_kb:.0f}KB). "
                f"Max is {MAX_FILE_SIZE_KB}KB. Use chunked mode."
            )

    def _backup(self, path: Path):
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)

    def _tree_walk(self, directory: Path, lines: list, prefix: str, depth: int, max_depth: int):
        if depth >= max_depth:
            return
        items = sorted(directory.iterdir(), key=lambda x: (x.is_file(), x.name))
        skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        items = [i for i in items if i.name not in skip]
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}{'/' if item.is_dir() else ''}")
            if item.is_dir():
                extension = "    " if is_last else "│   "
                self._tree_walk(item, lines, prefix + extension, depth + 1, max_depth)
