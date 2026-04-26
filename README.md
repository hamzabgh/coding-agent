# local-code-agent

> Self-hosted AI coding assistant — runs entirely on your machine via Ollama or vLLM.  
> Fix bugs, refactor, generate and chat about code directly from VS Code.  
> **No cloud. No API keys. No data sent anywhere.**

---

## What it does

local-code-agent is a fully local AI coding assistant with a FastAPI backend and a VS Code extension. You point it at any project, and it can:

- **Fix bugs** — finds the root cause, shows you a precise diff, waits for your approval
- **Improve** — adds error handling, cleans anti-patterns, never rewrites working code
- **Add features** — integrates new functionality into your existing code style
- **Refactor** — restructures and renames with full behaviour preservation
- **Explain** — describes what any file does in plain language
- **Generate** — creates new files from a plain-English description
- **Chat** — ask anything about the current file; if code changes are needed it shows a diff

Every change goes through an **accept / reject** step. Nothing is written to disk without your approval.

---

## Architecture

```
┌─────────────────────────────────┐
│        VS Code Extension        │  ← sidebar UI, diff viewer, accept/reject
└────────────────┬────────────────┘
                 │ HTTP
┌────────────────▼────────────────┐
│      FastAPI Server (server.py) │  ← multi-user, preview tokens, /apply
└────────────────┬────────────────┘
                 │
       ┌─────────▼─────────┐
       │   Ollama  │  vLLM  │  ← switchable via .env, no GPU required
       └───────────────────┘
```

**Two-phase flow — nothing is auto-written:**

1. `POST /run` → model thinks, returns structured diff + preview token  
2. You click **Accept** in VS Code → `POST /apply` → files written  
3. Or click **Reject** → `DELETE /preview/{token}` → nothing changes

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.10+ |
| RAM | 8 GB (16 GB recommended) |
| GPU | Not required |
| Ollama | 0.1.x+ |
| VS Code | 1.70+ |
| Node.js | 16+ (for extension packaging) |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/local-code-agent.git
cd local-code-agent
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — the only required field is the model name:

```dotenv
AGENT_BACKEND=ollama
OLLAMA_MODEL=qwen2.5-coder:3b
AGENT_TIMEOUT=300
```

### 3. Pull a model

```bash
ollama pull qwen2.5-coder:3b
```

### 4. Start the server

```bash
uvicorn server:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080/health` — you should see:

```json
{
  "status": "ok",
  "backend": "ollama",
  "model": "qwen2.5-coder:3b",
  "model_available": true
}
```

### 5. Install the VS Code extension

```bash
cd coding-agent-ext
npm install -g @vscode/vsce
vsce package --no-dependencies
code --install-extension coding-agent-1.0.0.vsix
```

Reload VS Code (`Ctrl+Shift+P` → **Developer: Reload Window**).

### 6. Configure the extension

Open VS Code settings (`Ctrl+,`) and search **Coding Agent**:

```
codingAgent.mode        → server
codingAgent.serverUrl   → http://localhost:8080
codingAgent.backend     → ollama
codingAgent.ollamaModel → qwen2.5-coder:3b
```

Click the robot icon in the activity bar to open the sidebar.

---

## Project structure

```
local-code-agent/
├── server.py              # FastAPI server — main entry point
├── main.py                # CLI entry point (single-user alternative)
├── .env.example           # Config template
├── requirements.txt
│
├── agent/
│   ├── model.py           # Unified Ollama / vLLM client
│   ├── orchestrator.py    # Agentic loop — read, think, diff, apply
│   └── json_parser.py     # Robust JSON extractor for noisy LLM output
│
├── config/
│   ├── settings.py        # Loads .env, exposes typed config
│   └── prompts.py         # All prompt templates in one place
│
├── tools/
│   ├── file_tools.py      # Safe file I/O with size validation
│   ├── git_tools.py       # Git backup commits before every write
│   └── search_tools.py    # Related-file discovery, grep, import analysis
│
├── memory/
│   └── context_manager.py # Project context + action history log
│
├── tests/
│   └── test_agent.py
│
└── coding-agent-ext/      # VS Code extension
    ├── extension.js        # Extension host — commands, sidebar, HTTP client
    └── package.json        # Extension manifest
```

---

## Model recommendations

| Model | RAM | Speed (CPU) | JSON reliability | Best for |
|---|---|---|---|---|
| `qwen2.5-coder:3b` | 2 GB | ~60–90s | good | **Best CPU-only choice** |
| `deepseek-coder:6.7b` | 4.5 GB | ~3–4 min | very good | CPU with 16 GB RAM |
| `qwen2.5-coder:7b` | 5 GB | ~3–5 min | very good | CPU with 16 GB RAM |
| `qwen2.5-coder:14b` | 10 GB | ~8–12 min | excellent | GPU or high-end CPU |

All models run locally via Ollama. No internet connection needed after the initial pull.

---

## Using vLLM (GPU server)

If you have access to a GPU machine, you can run vLLM for much faster responses:

```bash
# On the GPU server
pip install vllm
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-Coder-7B-Instruct \
  --host 0.0.0.0 --port 8000
```

Then in `.env` on the agent machine:

```dotenv
AGENT_BACKEND=vllm
VLLM_URL=http://YOUR_GPU_SERVER_IP:8000
VLLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct
```

Restart the server — no other changes needed.

---

## CLI mode (single user, no server)

```bash
python main.py improve app.py --project C:\Users\you\myproject
python main.py fix app.py "TypeError on line 42" --project C:\Users\you\myproject
python main.py chat "add type hints to all functions" --project C:\Users\you\myproject
python main.py generate utils/auth.py "JWT auth helpers" --project C:\Users\you\myproject
python main.py status --project C:\Users\you\myproject
```

In CLI mode the diff is printed in the terminal and the agent asks `Apply? [y/n]` before writing anything.

---

## Multi-user / team setup

Run the server on a shared machine on your LAN. Every developer sets `codingAgent.serverUrl` in their VS Code settings to point at that machine.

To require authentication, set in `.env`:

```dotenv
SERVER_API_KEY=your_secret_token
```

Each developer then sets `codingAgent.serverApiKey` in their VS Code settings. The server handles up to 4 concurrent requests.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_BACKEND` | `ollama` | `ollama` or `vllm` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Model name |
| `VLLM_URL` | — | vLLM server URL |
| `VLLM_MODEL` | — | vLLM model ID |
| `AGENT_TIMEOUT` | `300` | Request timeout in seconds |
| `AUTO_GIT_BACKUP` | `false` | Git commit before every write |
| `MAX_FILE_KB` | `100` | Max file size the agent will read |
| `MAX_CONTEXT_CHARS` | `3000` | Max project context sent to model |
| `SERVER_HOST` | `0.0.0.0` | Server bind address |
| `SERVER_PORT` | `8080` | Server port |
| `SERVER_API_KEY` | _(empty)_ | Bearer token auth — leave empty to disable |

---

## How the agent thinks

```
1. Read target file + related files for context
2. Build prompt — strict JSON output contract
3. Call Ollama / vLLM
4. Parse response with robust JSON extractor
   (handles broken indentation, malformed JSON, trailing garbage)
5. Compute unified diff (original → proposed)
6. Return preview token — NOTHING written yet
7. User clicks Accept → files written + optional git backup
   User clicks Reject → preview discarded, nothing changes
```

---

## Safety

- The agent **cannot modify its own source files** — a path guard blocks any request targeting the `local-code-agent` directory
- **Nothing is written without your confirmation** — every change goes through preview → accept
- **Git backup** — if `AUTO_GIT_BACKUP=true`, a commit is created before every write
- **No outbound network calls** — all model inference is local

---

## Troubleshooting

**`model_available: false` on `/health`**
```bash
ollama serve
ollama pull qwen2.5-coder:3b
```

**HTTP 504 timeout** — increase `AGENT_TIMEOUT` in `.env` or use a smaller model.

**HTTP 500 Internal Server Error** — check the server terminal, the error detail is always logged.

**Wrong file being sent** — click directly inside a `.py` or `.js` file before running any command. The extension blocks requests when the Output Channel or terminal is focused.

**Change skipped — `original` text not found** — the model copied the original text slightly differently. Use chat mode and describe the change more precisely.

---

## Contributing

Pull requests welcome.

```bash
pytest tests/
```

To develop the VS Code extension without packaging, open `coding-agent-ext/` in VS Code and press `F5`.

---

## License

MIT