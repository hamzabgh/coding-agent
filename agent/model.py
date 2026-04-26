"""
Model Client — unified interface for Ollama and vLLM.

Both backends are supported transparently:
  - Ollama  → uses its native /api/generate endpoint
  - vLLM    → uses the OpenAI-compatible /v1/chat/completions endpoint

Switch with AGENT_BACKEND env var.  No code changes needed.
"""

import requests
import json
from typing import Generator
from config.settings import Settings


class ModelClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = settings.backend          # "ollama" | "vllm"
        self.timeout = settings.request_timeout

    # ── Public API (same for both backends) ──────────────────────────────────

    def generate(self, prompt: str) -> str:
        """Non-streaming generation — returns full response string."""
        if self.backend == "vllm":
            return self._vllm_generate(prompt)
        return self._ollama_generate(prompt)

    def stream_generate(self, prompt: str) -> Generator[str, None, None]:
        """Streaming generation — yields tokens as they arrive."""
        if self.backend == "vllm":
            yield from self._vllm_stream(prompt)
        else:
            yield from self._ollama_stream(prompt)

    def is_available(self) -> bool:
        """Check if the active backend is reachable and has the model loaded."""
        try:
            if self.backend == "vllm":
                return self._vllm_available()
            return self._ollama_available()
        except Exception:
            return False

    @property
    def model(self) -> str:
        return self.settings.model_name

    @property
    def url(self) -> str:
        return self.settings.active_url

    # ── Ollama implementation ────────────────────────────────────────────────

    def _ollama_generate(self, prompt: str) -> str:
        payload = {
            "model": self.settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 4096},
        }
        try:
            resp = requests.post(
                f"{self.settings.ollama_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.settings.ollama_url}. "
                "Is it running?  Try:  ollama serve"
            )
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"Ollama timed out after {self.timeout}s. "
                "Try a smaller file or increase AGENT_TIMEOUT."
            )

    def _ollama_stream(self, prompt: str) -> Generator[str, None, None]:
        payload = {
            "model": self.settings.ollama_model,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": 0.2},
        }
        with requests.post(
            f"{self.settings.ollama_url}/api/generate",
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        yield token
                    if data.get("done"):
                        break

    def _ollama_available(self) -> bool:
        resp = requests.get(
            f"{self.settings.ollama_url}/api/tags", timeout=5
        )
        models = [m["name"] for m in resp.json().get("models", [])]
        return any(self.settings.ollama_model.split(":")[0] in m for m in models)

    # ── vLLM implementation (OpenAI-compatible) ──────────────────────────────

    def _vllm_generate(self, prompt: str) -> str:
        """
        vLLM exposes an OpenAI-compatible /v1/chat/completions endpoint.
        We wrap the prompt as a single user message.
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.vllm_api_key}",
        }
        payload = {
            "model": self.settings.vllm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 4096,
            "stream": False,
        }
        try:
            resp = requests.post(
                f"{self.settings.vllm_url}/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to vLLM at {self.settings.vllm_url}. "
                "Is it running?  Try:  python -m vllm.entrypoints.openai.api_server ..."
            )
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"vLLM timed out after {self.timeout}s. "
                "Try a smaller file or increase AGENT_TIMEOUT."
            )

    def _vllm_stream(self, prompt: str) -> Generator[str, None, None]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.vllm_api_key}",
        }
        payload = {
            "model": self.settings.vllm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 4096,
            "stream": True,
        }
        with requests.post(
            f"{self.settings.vllm_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                text = line.decode("utf-8")
                if text.startswith("data: "):
                    text = text[6:]
                if text.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(text)
                    delta = data["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError):
                    continue

    def _vllm_available(self) -> bool:
        headers = {"Authorization": f"Bearer {self.settings.vllm_api_key}"}
        resp = requests.get(
            f"{self.settings.vllm_url}/v1/models",
            headers=headers,
            timeout=5,
        )
        models = [m["id"] for m in resp.json().get("data", [])]
        return any(self.settings.vllm_model in m for m in models)