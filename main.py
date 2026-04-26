#!/usr/bin/env python3
"""
Coding Agent CLI
Usage:
    python main.py improve   path/to/file.py            --project C:\\path\\to\\your_project
    python main.py fix       path/to/file.py "bug desc" --project C:\\path\\to\\your_project
    python main.py feature   path/to/file.py "add X"    --project C:\\path\\to\\your_project
    python main.py refactor  path/to/file.py            --project C:\\path\\to\\your_project
    python main.py explain   path/to/file.py            --project C:\\path\\to\\your_project
    python main.py generate  new_file.py "description"  --project C:\\path\\to\\your_project
    python main.py chat      "question"  --file f.py    --project C:\\path\\to\\your_project
    python main.py status                               --project C:\\path\\to\\your_project

IMPORTANT
---------
Always pass --project pointing to the PROJECT YOU WANT TO EDIT.
Never point --project at the coding-agent folder itself.
"""

import sys
import argparse
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Add project root to path so imports work from any cwd
sys.path.insert(0, str(Path(__file__).parent))

from agent.orchestrator import CodingAgent
from agent.model import ModelClient
from config.settings import Settings

console = Console()

AGENT_DIR = Path(__file__).resolve().parent


def print_banner():
    console.print(Panel.fit(
        "[bold cyan]🤖 Coding Agent[/bold cyan]\n"
        f"[dim]Powered by Ollama + {Settings.ollama_model}[/dim]",
        border_style="cyan"
    ))


def warn_if_same_dir(project_root: str):
    """Warn the user if they forgot --project."""
    resolved = Path(project_root).resolve()
    if resolved == AGENT_DIR:
        console.print(
            "\n[bold yellow]⚠️  Warning:[/bold yellow] --project is pointing at the "
            "coding-agent directory itself.\n"
            "The agent will refuse to modify its own files.\n"
            "Pass [bold]--project PATH[/bold] to the project you want to edit.\n"
            "Example:\n"
            "  python main.py improve app.py --project C:\\Users\\Hamza\\Desktop\\my_project\n"
        )


def cmd_status(agent: CodingAgent):
    """Show agent status and project info."""
    settings = agent.settings
    model_ok = agent.model.is_available()

    t = Table(title="Agent Status", show_header=False, border_style="dim")
    t.add_column("Key", style="bold")
    t.add_column("Value")

    t.add_row("Model", settings.model_name)
    t.add_row("Ollama URL", settings.ollama_url)
    t.add_row("Model Status",
              "[green]✅ Available[/green]" if model_ok else "[red]❌ Not found[/red]")
    t.add_row("Project Root", str(agent.project_root))
    t.add_row("Git Backup", "Enabled" if settings.auto_git_backup else "Disabled")

    console.print(t)

    # Project tree
    tree = agent.files.get_project_tree()
    console.print(Panel(tree, title="Project Structure", border_style="dim"))

    # Recent history
    history = agent.memory.get_history(5)
    if history:
        console.print("\n[bold]Recent Agent Actions:[/bold]")
        for entry in history:
            console.print(
                f"  [dim]{entry['timestamp'][:19]}[/dim]  "
                f"{entry['action']:10}  {entry['file']}"
            )


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Coding Agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("command", choices=[
        "improve", "fix", "feature", "refactor",
        "explain", "chat", "generate", "status"
    ])
    parser.add_argument("file", nargs="?", help="Target file path (relative to --project)")
    parser.add_argument("message", nargs="?", default="",
                        help="Bug description / feature description / chat message")
    parser.add_argument("--project", default=".",
                        help="Root directory of the project you want to edit "
                             "(default: current directory)")
    parser.add_argument("--model", help="Override model name")
    # --file alias for chat command (keeps original syntax working)
    parser.add_argument("--file", dest="file_opt",
                        help="File context for chat command")

    args = parser.parse_args()

    # Merge --file option into positional 'file' for the chat command
    if args.file_opt and not args.file:
        args.file = args.file_opt

    warn_if_same_dir(args.project)

    # Init agent
    try:
        agent = CodingAgent(project_root=args.project)
    except (ValueError, PermissionError) as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)

    if args.model:
        agent.settings.model_name = args.model
        agent.model.model = args.model

    cmd = args.command

    # ── status doesn't need a file ──────────────────────────
    if cmd == "status":
        cmd_status(agent)
        return

    # ── generate needs file + message ───────────────────────
    if cmd == "generate":
        if not args.file:
            args.file = input("Output file path (e.g. utils/helpers.py): ").strip()
        if not args.message:
            args.message = input("Describe what the file should do: ").strip()
        try:
            agent.generate(args.file, args.message)
        except (ConnectionError, TimeoutError) as e:
            _connection_error(e, agent)
        except PermissionError as e:
            console.print(f"[bold red]Permission Error:[/bold red] {e}")
            sys.exit(1)
        return

    # ── all other commands need a file ──────────────────────
    if cmd != "chat" and not args.file:
        console.print("[red]Error: file argument required for this command.[/red]")
        parser.print_help()
        sys.exit(1)

    try:
        if cmd == "improve":
            agent.improve_file(args.file)

        elif cmd == "fix":
            agent.fix_bug(args.file, args.message)

        elif cmd == "feature":
            if not args.message:
                args.message = input("Describe the feature to add: ").strip()
            agent.add_feature(args.file, args.message)

        elif cmd == "refactor":
            agent.refactor(args.file, args.message)

        elif cmd == "explain":
            agent.explain_file(args.file)

        elif cmd == "chat":
            if not args.message and not args.file:
                args.message = input("What do you want to ask? ").strip()
            agent.chat(args.message or "Review this file.", file_path=args.file)

    except ConnectionError as e:
        _connection_error(e, agent)
    except FileNotFoundError as e:
        console.print(f"[bold red]File Error:[/bold red] {e}")
        sys.exit(1)
    except PermissionError as e:
        console.print(f"[bold red]Permission Error:[/bold red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")


def _connection_error(e, agent):
    console.print(f"[bold red]Connection Error:[/bold red] {e}")
    console.print("\n[yellow]Make sure Ollama is running:[/yellow]")
    console.print("  ollama serve")
    console.print(f"  ollama pull {agent.settings.model_name}")
    sys.exit(1)


if __name__ == "__main__":
    main()