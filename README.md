# 🤖 Coding Agent

A professional semi-autonomous coding agent that runs locally on your machine using **Ollama + CodeQwen**.

It reads your project, understands context, suggests code changes, and applies them — always with your confirmation first.

---

## 📁 Project Structure

```
coding-agent/
├── main.py                    ← CLI entry point
├── requirements.txt
│
├── agent/
│   ├── orchestrator.py        ← Core agent logic (commands, review loop)
│   └── model.py               ← Ollama API client
│
├── tools/
│   ├── file_tools.py          ← Safe file read/write, project tree
│   ├── git_tools.py           ← Git backup before every change
│   └── search_tools.py        ← grep, find related files, build context
│
├── memory/
│   └── context_manager.py     ← Project context builder + action log
│
├── config/
│   ├── settings.py            ← All configuration (env-overridable)
│   └── prompts.py             ← All prompt templates
│
└── tests/
    └── test_agent.py          ← Unit tests (pytest)
```

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start Ollama and pull model

```bash
ollama serve
ollama pull codeqwen:7b
```

### 3. Run the agent

```bash
# Check status
python main.py status

# Improve a file
python main.py improve src/auth.py

# Fix a bug
python main.py fix src/auth.py "login fails with special characters"

# Add a feature
python main.py feature src/auth.py "add rate limiting to login"

# Refactor
python main.py refactor src/utils.py

# Explain code
python main.py explain src/complex_module.py

# Chat with context
python main.py chat "how should I handle JWT expiry?" --file src/auth.py
```

---

## ⚙️ Configuration

All settings can be overridden via environment variables:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `AGENT_MODEL` | `codeqwen:7b` | Model to use |
| `AGENT_TIMEOUT` | `120` | Request timeout (seconds) |
| `MAX_FILE_KB` | `200` | Max file size to process |
| `AUTO_GIT_BACKUP` | `true` | Create git commit before changes |

Example:
```bash
AGENT_MODEL=deepseek-coder:6.7b python main.py improve app.py
```

---

## 🛡️ Safety Features

1. **Confirmation required** — Agent always asks before writing files
2. **`.bak` backup** — Creates `file.py.bak` before every write
3. **Git backup commit** — Commits current state before changes (if git repo)
4. **No auto-apply** — `require_confirmation = True` is hardcoded

---

## 🔄 Workflow

```
You type command
      ↓
Agent reads file + builds project context
      ↓
Prompt sent to Ollama (CodeQwen)
      ↓
Agent shows suggested changes with syntax highlighting
      ↓
You review → press y/n
      ↓
(if yes) Git backup → file updated
```

---

## 🧪 Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## 🔧 Recommended Models

| Model | Size | Best For |
|---|---|---|
| `codeqwen:7b` | 4.5GB | General coding (default) |
| `deepseek-coder:6.7b` | 3.8GB | Fast, good Python/JS |
| `codellama:13b` | 7.4GB | Better multi-file reasoning |
| `qwen2.5-coder:14b` | 8.5GB | Best quality (needs 16GB RAM) |

---

## 📈 Limitations

- **Large files (>200KB)** — Split into modules first
- **Multi-file refactors** — Do file by file, not all at once  
- **Full project rewrites** — Not recommended; plan manually
- **Auto-acceptance** — Never enable; review every change

---

## 💡 Tips

- Always run `python main.py status` first to verify setup
- Use `explain` before `refactor` to understand the code
- Keep your git repo clean before using the agent
- For teams: commit agent-backup commits to shared history so teammates can see what changed
