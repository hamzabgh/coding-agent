"""
Tests for the coding agent.
Run: python -m pytest tests/ -v
"""

import pytest
from pathlib import Path
import tempfile
import os

# ─── File Tools ─────────────────────────────────────────
from tools.file_tools import FileTools


def test_read_write_file():
    with tempfile.TemporaryDirectory() as tmp:
        tools = FileTools(tmp)
        test_file = os.path.join(tmp, "test.py")
        Path(test_file).write_text("print('hello')")

        content = tools.read(test_file)
        assert "print" in content

        tools.write(test_file, "print('updated')")
        assert "updated" in tools.read(test_file)


def test_project_tree():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "main.py").write_text("")
        (Path(tmp) / "utils.py").write_text("")
        tools = FileTools(tmp)
        tree = tools.get_project_tree()
        assert "main.py" in tree


def test_backup_created_on_write():
    with tempfile.TemporaryDirectory() as tmp:
        tools = FileTools(tmp)
        p = Path(tmp) / "code.py"
        p.write_text("original")
        tools.write(str(p), "updated")
        backup = Path(str(p) + ".bak")
        assert backup.exists()
        assert backup.read_text() == "original"


# ─── Search Tools ────────────────────────────────────────
from tools.search_tools import SearchTools


def test_grep():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "app.py").write_text("def login(user):\n    pass\n")
        search = SearchTools(tmp)
        results = search.grep("def login")
        assert len(results) > 0
        assert "login" in results[0]["content"]


def test_find_function():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "auth.py").write_text("def authenticate(user, pwd):\n    return True\n")
        search = SearchTools(tmp)
        results = search.find_function("authenticate")
        assert len(results) > 0


# ─── Context Manager ─────────────────────────────────────
from memory.context_manager import ContextManager


def test_log_and_retrieve():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = ContextManager(tmp)
        mgr.log_change("app.py", "improve")
        history = mgr.get_history()
        assert len(history) == 1
        assert history[0]["action"] == "improve"
        assert history[0]["file"] == "app.py"


# ─── Prompt Templates ────────────────────────────────────
from config.prompts import PromptTemplates


def test_improve_prompt_contains_code():
    prompt = PromptTemplates.build("improve", code="def foo(): pass", context="", path="test.py")
    assert "def foo" in prompt
    assert "test.py" in prompt


def test_fix_bug_prompt():
    prompt = PromptTemplates.build("fix_bug", code="x=1/0", context="", path="math.py", extra="ZeroDivisionError")
    assert "ZeroDivisionError" in prompt
    assert "math.py" in prompt


def test_all_modes_build():
    modes = ["improve", "fix_bug", "add_feature", "refactor", "explain", "chat"]
    for mode in modes:
        prompt = PromptTemplates.build(
            mode, code="pass", context="", path="f.py", extra="test"
        )
        assert len(prompt) > 50, f"Mode {mode} returned empty prompt"
