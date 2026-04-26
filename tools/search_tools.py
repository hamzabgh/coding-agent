"""
Search Tools — semantic and text search across the codebase.
Used to build relevant context for model prompts.
"""

import re
from pathlib import Path
from typing import List, Dict, Tuple
from tools.file_tools import FileTools, SUPPORTED_EXTENSIONS


class SearchTools:
    def __init__(self, project_root: str = "."):
        self.root = Path(project_root).resolve()
        self.files = FileTools(project_root)

    def find_related_files(self, target_file: str, max_results: int = 5) -> List[str]:
        """
        Find files related to the target via imports/references.
        Simple heuristic: look for filename references.
        """
        target = Path(target_file).stem
        related = []
        for f in self.files.list_project_files():
            if str(f) == str(Path(target_file).resolve()):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if target in content:
                    related.append(str(f.relative_to(self.root)))
            except Exception:
                pass
            if len(related) >= max_results:
                break
        return related

    def grep(self, pattern: str, file_extensions: set = None) -> List[Dict]:
        """Search for a regex pattern across all code files."""
        exts = file_extensions or SUPPORTED_EXTENSIONS
        results = []
        regex = re.compile(pattern, re.IGNORECASE)
        for f in self.files.list_project_files(extensions=exts):
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if regex.search(line):
                        results.append({
                            "file": str(f.relative_to(self.root)),
                            "line": i,
                            "content": line.strip()
                        })
            except Exception:
                pass
        return results

    def find_function(self, name: str) -> List[Dict]:
        """Find all definitions of a function/class by name."""
        patterns = [
            rf"def {name}\s*\(",          # Python
            rf"function {name}\s*\(",     # JS
            rf"class {name}[\s({{:]",     # Python/JS class
            rf"const {name}\s*=",         # JS const
            rf"func {name}\s*\(",         # Go
        ]
        results = []
        for pattern in patterns:
            results.extend(self.grep(pattern))
        return results

    def get_imports(self, file_path: str) -> List[str]:
        """Extract import/require statements from a file."""
        content = self.files.read(file_path)
        import_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if (stripped.startswith("import ") or
                    stripped.startswith("from ") or
                    "require(" in stripped):
                import_lines.append(stripped)
        return import_lines

    def build_context_bundle(
        self,
        target_file: str,
        max_context_chars: int = 8000
    ) -> str:
        """
        Build a context string: target file + related files.
        Stays within token budget.
        """
        parts = []
        used = 0

        # Primary file
        try:
            code = self.files.read(target_file)
            parts.append(f"# FILE: {target_file}\n{code}")
            used += len(code)
        except Exception:
            pass

        # Related files
        for rel in self.find_related_files(target_file, max_results=3):
            if used >= max_context_chars:
                break
            try:
                code = self.files.read(rel)
                snippet = code[:1500]  # Truncate large related files
                parts.append(f"# RELATED: {rel}\n{snippet}")
                used += len(snippet)
            except Exception:
                pass

        return "\n\n".join(parts)
