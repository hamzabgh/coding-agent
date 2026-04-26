import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── Which backend to use ─────────────────────────────────────────────────
    # "ollama"  →  http://localhost:11434  (Ollama native API)
    # "vllm"    →  http://HOST:8000        (vLLM OpenAI-compatible API)
    backend: str = os.getenv("AGENT_BACKEND", "ollama").lower()

    # ── Ollama ───────────────────────────────────────────────────────────────
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "codeqwen:7b")

    # ── vLLM  ────────────────────────────────────────────────────────────────
    vllm_url: str = os.getenv("VLLM_URL", "http://localhost:8000")
    vllm_model: str = os.getenv("VLLM_MODEL", "Qwen/CodeQwen1.5-7B-Chat")
    vllm_api_key: str = os.getenv("VLLM_API_KEY", "EMPTY")  # vLLM ignores this but openai SDK requires it

    # ── Shared behaviour ─────────────────────────────────────────────────────
    request_timeout: int = int(os.getenv("AGENT_TIMEOUT", "120"))
    auto_git_backup: bool = os.getenv("AUTO_GIT_BACKUP", "true").lower() == "true"
    max_file_size_kb: int = int(os.getenv("MAX_FILE_KB", "200"))
    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "8000"))

    # ── Server ───────────────────────────────────────────────────────────────
    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = int(os.getenv("SERVER_PORT", "8080"))
    server_api_key: str = os.getenv("SERVER_API_KEY", "")   # optional bearer token auth

    # ── Safety ───────────────────────────────────────────────────────────────
    require_confirmation: bool = False   # Always False in server mode (no TTY)
    create_backups: bool = True

    # ── Derived helpers ──────────────────────────────────────────────────────
    @property
    def model_name(self) -> str:
        return self.vllm_model if self.backend == "vllm" else self.ollama_model

    @property
    def active_url(self) -> str:
        return self.vllm_url if self.backend == "vllm" else self.ollama_url

    def __repr__(self):
        return (
            f"Settings(backend={self.backend}, model={self.model_name}, "
            f"url={self.active_url}, timeout={self.request_timeout}s)"
        )