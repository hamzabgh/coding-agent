"""
Core Agent Orchestrator — Real Agentic Coding Engine

Flow for every command:
  1. Read target file + related files (context building)
  2. Send to model with chain-of-thought prompt
  3. Parse structured JSON response
  4. Compute unified diff from model's surgical changes
  5. Show diff to user — wait for accept/reject
  6. Apply only if accepted — git backup first

The model is NEVER allowed to write files directly.
Only the user can approve writes.
"""

import re
import json
import difflib
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.prompt import Confirm, Prompt

from agent.model import ModelClient
from tools.file_tools import FileTools
from tools.git_tools import GitTools
from tools.search_tools import SearchTools
from memory.context_manager import ContextManager
from config.prompts import PromptTemplates
from config.settings import Settings

console = Console()
AGENT_OWN_DIR = Path(__file__).resolve().parent.parent


# ── Result dataclass ──────────────────────────────────────────────────────────

class AgentResult:
    """Structured result from the model, parsed from JSON."""
    def __init__(self, raw: dict):
        self.analysis       = raw.get("analysis", "")
        self.plan           = raw.get("plan", [])
        self.changes        = raw.get("changes", [])
        self.summary        = raw.get("summary", "")
        self.no_change      = raw.get("no_change_reason")
        self.raw            = raw

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


# ── Main agent ────────────────────────────────────────────────────────────────

class CodingAgent:
    def __init__(self, project_root: str = "."):
        self.settings     = Settings()
        self.model        = ModelClient(self.settings)
        self.project_root = Path(project_root).resolve()
        self._guard_root()
        self.files   = FileTools(str(self.project_root))
        self.git     = GitTools(str(self.project_root))
        self.search  = SearchTools(str(self.project_root))
        self.memory  = ContextManager(str(self.project_root))

    # ── Safety ────────────────────────────────────────────────────────────────

    def _guard_root(self):
        try:
            self.project_root.relative_to(AGENT_OWN_DIR)
            raise ValueError(
                f"Safety: project root ({self.project_root}) is inside the agent's own directory.\n"
                "Pass --project pointing at your target project."
            )
        except ValueError as e:
            if "Safety" in str(e):
                raise

    def _guard_file(self, fp: str):
        resolved = (self.project_root / fp).resolve()
        try:
            resolved.relative_to(AGENT_OWN_DIR)
            raise PermissionError(f"Blocked: '{fp}' is inside the agent source tree.")
        except ValueError:
            pass

    # ── Public commands ───────────────────────────────────────────────────────

    def fix_bug(self, file_path: str, bug_description: str = ""):
        self._guard_file(file_path)
        console.rule("[bold red]Fix Bug")
        self._run_agentic("fix_bug", file_path,
                          bug_description=bug_description,
                          action_label="fix")

    def improve(self, file_path: str):
        self._guard_file(file_path)
        console.rule("[bold cyan]Improve")
        self._run_agentic("improve", file_path, action_label="improve")

    def add_feature(self, file_path: str, feature: str):
        self._guard_file(file_path)
        console.rule("[bold green]Add Feature")
        self._run_agentic("add_feature", file_path,
                          feature=feature, action_label="feature")

    def refactor(self, file_path: str, instructions: str = ""):
        self._guard_file(file_path)
        console.rule("[bold yellow]Refactor")
        self._run_agentic("refactor", file_path,
                          instructions=instructions, action_label="refactor")

    def explain(self, file_path: str):
        self._guard_file(file_path)
        console.rule("[bold blue]Explain")
        code    = self.files.read(file_path)
        prompt  = PromptTemplates.build("explain", file_path=file_path, code=code)
        result  = self._call_and_parse(prompt)
        self._print_analysis(result)

    def chat(self, message: str, file_path: Optional[str] = None):
        """
        Intelligent chat:
        - Pure questions → answer only
        - Code-change requests → show diff, ask accept/reject
        """
        if file_path:
            self._guard_file(file_path)
        console.rule("[bold magenta]Chat")

        code    = self.files.read(file_path) if file_path else ""
        related = self._gather_related(file_path) if file_path else {}
        context = self.memory.get_project_context()

        prompt = PromptTemplates.build(
            "chat",
            message=message,
            file_path=file_path or "",
            code=code,
            context=context,
            related=related,
        )
        result = self._call_and_parse(prompt)

        # Always show analysis / answer
        self._print_analysis(result)

        # If model proposes code changes → show diff and ask
        if result.has_changes:
            console.print("\n[dim]The agent proposes code changes based on your message.[/dim]")
            self._review_and_apply(result, action="chat")

    def generate(self, output_path: str, description: str):
        self._guard_file(output_path)
        console.rule("[bold green]Generate")

        ext     = Path(output_path).suffix.lstrip(".") or "python"
        context = self.memory.get_project_context()
        prompt  = PromptTemplates.build(
            "generate",
            file_path=output_path,
            description=description,
            context=context,
            lang=ext,
        )
        result = self._call_and_parse(prompt)
        self._print_analysis(result)

        if result.has_changes:
            self._review_and_apply(result, action="generate")

    # ── Agentic loop ──────────────────────────────────────────────────────────

    def _run_agentic(self, mode: str, file_path: str, action_label: str, **kwargs):
        """Core agentic flow: read → think → diff → accept/reject."""

        # 1. Read files
        with console.status("[dim]Reading files...[/dim]"):
            code    = self.files.read(file_path)
            related = self._gather_related(file_path)
            context = self.memory.get_project_context()

        # 2. Build prompt and call model
        prompt = PromptTemplates.build(
            mode,
            file_path=file_path,
            code=code,
            context=context,
            related=related,
            **kwargs,
        )
        result = self._call_and_parse(prompt)

        # 3. Show reasoning
        self._print_analysis(result)

        # 4. No changes → done
        if not result.has_changes:
            console.print(Panel(
                result.no_change or "No changes needed.",
                title="[dim]No Changes[/dim]",
                border_style="dim"
            ))
            return

        # 5. Show diff + ask user
        self._review_and_apply(result, action=action_label)

    def _review_and_apply(self, result: AgentResult, action: str):
        """
        Compute diffs from result.changes, show them, ask accept/reject.
        Applies only after explicit user confirmation.
        """
        # Build diff for each change
        diff_previews = []
        for change in result.changes:
            fp          = change.get("file", "")
            original    = change.get("original", "")
            replacement = change.get("replacement", "")
            reason      = change.get("reason", "")

            # Read current file content
            try:
                current = self.files.read(fp)
            except FileNotFoundError:
                current = ""  # new file

            # Compute new content
            if original == "":
                # New file generation
                new_content = replacement
            else:
                # Surgical replacement
                if original not in current:
                    console.print(f"[yellow]⚠ Could not locate exact original text in {fp}. Showing full replacement.[/yellow]")
                    new_content = replacement
                else:
                    new_content = current.replace(original, replacement, 1)

            # Unified diff
            diff_lines = list(difflib.unified_diff(
                current.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{fp}",
                tofile=f"b/{fp}",
                lineterm="",
            ))
            diff_previews.append({
                "file":        fp,
                "diff":        diff_lines,
                "new_content": new_content,
                "reason":      reason,
                "original":    original,
                "replacement": replacement,
            })

        if not diff_previews:
            console.print("[yellow]No applicable changes found.[/yellow]")
            return

        # Display all diffs
        console.print()
        for dp in diff_previews:
            diff_text = "".join(dp["diff"])
            if not diff_text.strip():
                continue

            # Color the diff
            colored = Text()
            for line in dp["diff"]:
                if line.startswith("+++") or line.startswith("---"):
                    colored.append(line + "\n", style="dim")
                elif line.startswith("+"):
                    colored.append(line + "\n", style="green")
                elif line.startswith("-"):
                    colored.append(line + "\n", style="red")
                elif line.startswith("@@"):
                    colored.append(line + "\n", style="cyan")
                else:
                    colored.append(line + "\n", style="dim")

            reason_str = f"  [dim]reason: {dp['reason']}[/dim]" if dp["reason"] else ""
            console.print(Panel(
                colored,
                title=f"[bold]{dp['file']}[/bold]{reason_str}",
                border_style="cyan",
                padding=(0, 1),
            ))

        # Summary
        n = len([d for d in diff_previews if d["diff"]])
        console.print(f"\n[bold]{result.summary}[/bold]")
        console.print(f"[dim]{n} file(s) will be modified.[/dim]\n")

        # Accept / reject
        if not Confirm.ask("[bold]Apply these changes?[/bold]"):
            console.print("[dim]Changes rejected — nothing written.[/dim]")
            return

        # Apply
        for dp in diff_previews:
            fp = dp["file"]
            self._guard_file(fp)
            backup = self.git.create_backup_commit(fp, action)
            if backup:
                console.print(f"[dim]Git backup created for {fp}[/dim]")
            self.files.write(fp, dp["new_content"])
            self.memory.log_change(fp, action)
            console.print(f"[bold green]✓ {fp} updated.[/bold green]")

    # ── Model call ────────────────────────────────────────────────────────────

    def _call_and_parse(self, prompt: str) -> AgentResult:
        """Call model, parse JSON response, handle errors gracefully."""
        with console.status("[bold green]Thinking...[/bold green]", spinner="dots"):
            raw_response = self.model.generate(prompt)

        parsed = self._parse_json(raw_response)
        return AgentResult(parsed)

    def _parse_json(self, response: str) -> dict:
        """Extract and parse JSON from model response."""
        # Strip markdown fences if present
        clean = re.sub(r"```(?:json)?\s*", "", response)
        clean = clean.replace("```", "").strip()

        # Try direct parse
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON object
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Fallback — model returned plain text (treat as analysis, no changes)
        console.print("[yellow]⚠ Model returned non-JSON response. Displaying as-is.[/yellow]")
        return {
            "analysis": response.strip(),
            "plan": [],
            "changes": [],
            "summary": "Model response was not structured JSON.",
            "no_change_reason": "Could not parse model response.",
        }

    # ── Context helpers ───────────────────────────────────────────────────────

    def _gather_related(self, file_path: str, max_files: int = 2) -> dict:
        """Find and read files related to the target file."""
        related = {}
        try:
            related_paths = self.search.find_related_files(file_path, max_results=max_files)
            for rp in related_paths:
                try:
                    content = self.files.read(rp)
                    related[rp] = content[:2000]  # cap to 2k chars each
                except Exception:
                    pass
        except Exception:
            pass
        return related

    # ── Display helpers ───────────────────────────────────────────────────────

    def _print_analysis(self, result: AgentResult):
        """Show model reasoning to the user."""
        if result.analysis:
            console.print(Panel(
                result.analysis,
                title="[bold]Analysis[/bold]",
                border_style="blue",
            ))

        if result.plan:
            console.print("[bold dim]Plan:[/bold dim]")
            for i, step in enumerate(result.plan, 1):
                console.print(f"  [dim]{i}.[/dim] {step}")
            console.print()