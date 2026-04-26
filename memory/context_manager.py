"""
Context Manager — builds project-level context and logs agent actions.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List
from tools.file_tools import FileTools


AGENT_LOG_FILE = ".agent_log.json"


class ContextManager:
    def __init__(self, project_root: str = "."):
        self.root = Path(project_root).resolve()
        self.files = FileTools(project_root)
        self.log_path = self.root / AGENT_LOG_FILE

    def get_project_context(self, max_chars: int = 3000) -> str:
        """
        Build a project summary for the model:
        - directory tree
        - key config files content (package.json, pyproject.toml, etc.)
        - recent agent actions
        """
        parts = []

        # 1. Directory tree
        parts.append("## Project Structure\n" + self.files.get_project_tree())

        # 2. Config files
        config_candidates = [
            "pyproject.toml", "package.json", "requirements.txt",
            "Cargo.toml", "go.mod", "Makefile", "README.md"
        ]
        for cfg in config_candidates:
            cfg_path = self.root / cfg
            if cfg_path.exists():
                try:
                    content = cfg_path.read_text(encoding="utf-8")[:600]
                    parts.append(f"## {cfg}\n{content}")
                except Exception:
                    pass

        # 3. Recent changes
        history = self._load_log()
        if history:
            recent = history[-5:]
            lines = [f"- {e['timestamp'][:19]} | {e['action']} | {e['file']}" for e in recent]
            parts.append("## Recent Agent Actions\n" + "\n".join(lines))

        full = "\n\n".join(parts)
        return full[:max_chars]

    def log_change(self, file_path: str, action: str):
        """Append an action entry to the agent log."""
        log = self._load_log()
        log.append({
            "timestamp": datetime.now().isoformat(),
            "file": str(file_path),
            "action": action
        })
        # Keep last 100 entries
        self.log_path.write_text(
            json.dumps(log[-100:], indent=2),
            encoding="utf-8"
        )

    def get_history(self, n: int = 10) -> List[Dict]:
        return self._load_log()[-n:]

    def _load_log(self) -> List[Dict]:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except Exception:
                pass
        return []
