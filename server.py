"""
Coding Agent — FastAPI Server (Agentic, Diff-Based)

Every endpoint returns a structured response with:
  - analysis    : what the agent found
  - plan        : list of reasoning steps
  - changes     : list of surgical diffs (original → replacement)
  - summary     : one-line description
  - applied     : bool — whether changes were written (server auto-applies after user confirms via /apply)

Two-phase flow (for VS Code extension):
  POST /preview   → returns diff, nothing written yet
  POST /apply     → accepts a preview token and writes the files

Single-phase (CLI or direct):
  POST /run       → previews + applies immediately (requires confirm=true in body)
"""

import re
import json
import uuid
import difflib
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from config.prompts import PromptTemplates
from agent.model import ModelClient
from agent.json_parser import extract_json   # robust parser — survives malformed output
from tools.file_tools import FileTools
from tools.git_tools import GitTools
from tools.search_tools import SearchTools
from memory.context_manager import ContextManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger("agent.server")

app = FastAPI(title="Coding Agent API", version="2.0.0",
              description="Agentic coding assistant — diff-based, accept/reject flow.")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_executor   = ThreadPoolExecutor(max_workers=4)
_previews: Dict[str, Any] = {}   # token → preview data (in-memory, TTL not needed for LAN)

AGENT_OWN_DIR = Path(__file__).resolve().parent


# ── Settings ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ── Auth ──────────────────────────────────────────────────────────────────────

def verify_token(authorization: Optional[str] = Header(None),
                 settings: Settings = Depends(get_settings)):
    if not settings.server_api_key:
        return
    if authorization != f"Bearer {settings.server_api_key}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    project:     str   = Field(..., description="Absolute path to the target project root")
    file:        str   = Field(..., description="File path relative to project root")
    mode:        str   = Field(..., description="fix_bug | improve | add_feature | refactor | explain | chat | generate")
    message:     Optional[str] = Field("", description="Bug description / feature / chat message")
    auto_apply:  bool  = Field(False, description="If true, apply changes immediately without /apply step")

class ApplyRequest(BaseModel):
    preview_token: str = Field(..., description="Token from /preview response")
    project:       str = Field(..., description="Must match original request for security")

class DiffChange(BaseModel):
    file:        str
    original:    str
    replacement: str
    reason:      str
    diff_lines:  list[str]  # unified diff

class AgentResponse(BaseModel):
    ok:            bool
    mode:          str
    analysis:      str = ""
    plan:          list[str] = []
    changes:       list[DiffChange] = []
    summary:       str = ""
    no_change:     Optional[str] = None
    preview_token: Optional[str] = None   # set when changes exist and auto_apply=False
    applied:       bool = False
    backend:       str = ""
    model:         str = ""


# ── Core helpers ───────────────────────────────────────────────────────────────

def _guard(project: str, file_path: str):
    resolved = (Path(project) / file_path).resolve()
    try:
        resolved.relative_to(AGENT_OWN_DIR)
        raise HTTPException(status_code=403, detail=f"Blocked: '{file_path}' is inside the agent source tree.")
    except ValueError:
        pass

def _make_tools(project: str, settings: Settings):
    root = str(Path(project).resolve())
    return ModelClient(settings), FileTools(root), GitTools(root), SearchTools(root), ContextManager(root)

def _parse_json(response: str) -> dict:
    """
    Robust multi-strategy parser that handles common small-model JSON failures:
    - trailing commas before ] or }
    - ... comment lines inserted by model
    - duplicate keys, extra braces
    - plain text fallback with regex field extraction
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", response).replace("```", "").strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract largest {...} block and clean it
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        chunk = match.group()
        chunk = re.sub(r",(?:\s*[}\]])", lambda m: m.group().lstrip(","), chunk)  # trailing commas
        chunk = re.sub(r"^\s*\.\.\..*$", "", chunk, flags=re.MULTILINE)          # ... comment lines
        chunk = re.sub(r"//[^\n]*", "", chunk)                                      # JS comments
        chunk = re.sub(r",\s*([}\]])", r"\1", chunk)                               # trailing commas pass 2
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass

    # Strategy 3: regex field extraction (last resort)
    log.warning("JSON parse failed — falling back to regex extraction")
    result = {"analysis": "", "plan": [], "changes": [], "summary": "", "no_change_reason": None}
    m = re.search(r'"\.analysis\"\s*:\s*\"([^\"]*)\"', text)
    if m: result["analysis"] = m.group(1)
    m = re.search(r'"summary\"\s*:\s*\"([^\"]*)\"', text)
    if m: result["summary"] = m.group(1)
    m = re.search(r'"changes\"\s*:\s*(\[.*?\])', text, re.DOTALL)
    if m:
        try:
            chunk = re.sub(r",\s*([}\]])", r"\1", m.group(1))
            result["changes"] = json.loads(chunk)
        except Exception:
            pass
    if not result["analysis"]:
        result["analysis"] = text[:500]
        result["no_change_reason"] = "Model output could not be fully parsed."
    return result

def _compute_diffs(changes: list, file_tools: FileTools, project: str) -> list[DiffChange]:
    result = []
    for c in changes:
        fp          = c.get("file", "")
        original    = c.get("original", "")
        replacement = c.get("replacement", "")
        reason      = c.get("reason", "")
        try:
            current = file_tools.read(fp)
        except FileNotFoundError:
            current = ""

        if original == "":
            new_content = replacement
        elif original in current:
            new_content = current.replace(original, replacement, 1)
        else:
            new_content = replacement  # fallback

        diff_lines = list(difflib.unified_diff(
            current.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{fp}", tofile=f"b/{fp}", lineterm="",
        ))
        result.append(DiffChange(
            file=fp, original=original, replacement=replacement,
            reason=reason, diff_lines=diff_lines
        ))
    return result

def _apply_changes(changes: list[DiffChange], file_tools: FileTools,
                   git_tools: GitTools, memory: ContextManager,
                   project: str, mode: str):
    """Write accepted changes to disk."""
    applied = []
    for c in changes:
        fp = c.file
        try:
            current = file_tools.read(fp)
        except FileNotFoundError:
            current = ""
        if c.original == "":
            new_content = c.replacement
        elif c.original in current:
            new_content = current.replace(c.original, c.replacement, 1)
        else:
            new_content = c.replacement

        out = Path(project) / fp
        out.parent.mkdir(parents=True, exist_ok=True)
        git_tools.create_backup_commit(fp, mode)
        file_tools.write(fp, new_content)
        memory.log_change(fp, mode)
        applied.append(fp)
    return applied

async def _run_in_thread(fn, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn, *args)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health(settings: Settings = Depends(get_settings)):
    model = ModelClient(settings)
    return {"status": "ok", "backend": settings.backend, "model": settings.model_name,
            "model_available": model.is_available(), "url": settings.active_url}


@app.post("/run", response_model=AgentResponse, dependencies=[Depends(verify_token)])
async def run_command(req: RunRequest, settings: Settings = Depends(get_settings)):
    """
    Main endpoint. Returns a structured diff.
    If auto_apply=True, writes immediately.
    If auto_apply=False (default), returns preview_token — call /apply to confirm.
    """
    _guard(req.project, req.file)

    def _work():
        model, files, git, search, memory = _make_tools(req.project, settings)

        # Read files
        try:
            code = files.read(req.file)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        # Related files for context
        related = {}
        try:
            for rp in search.find_related_files(req.file, max_results=2):
                try:
                    related[rp] = files.read(rp)[:2000]
                except Exception:
                    pass
        except Exception:
            pass

        context = memory.get_project_context(max_chars=800)  # small models need short context

        # Build prompt
        kwargs = dict(file_path=req.file, code=code, context=context, related=related)
        if req.mode == "fix_bug":    kwargs["bug_description"] = req.message or ""
        elif req.mode == "add_feature": kwargs["feature"] = req.message or ""
        elif req.mode == "refactor": kwargs["instructions"] = req.message or ""
        elif req.mode == "chat":     kwargs["message"] = req.message or ""
        elif req.mode == "generate": kwargs.update({"description": req.message or "", "lang": Path(req.file).suffix.lstrip(".") or "python"})

        prompt = PromptTemplates.build(req.mode, **kwargs)

        log.info("mode=%s file=%s backend=%s", req.mode, req.file, settings.backend)
        raw = model.generate(prompt)
        # Robust parser — never raises, handles malformed JSON from small models
        parsed = extract_json(raw)

        # Compute diffs
        diffs = _compute_diffs(parsed.get("changes", []), files, req.project)

        # Auto-apply if requested
        applied = False
        if req.auto_apply and diffs:
            _apply_changes(diffs, files, git, memory, req.project, req.mode)
            applied = True

        # Store preview for /apply
        token = None
        if diffs and not applied:
            token = str(uuid.uuid4())
            _previews[token] = {
                "project": req.project,
                "mode":    req.mode,
                "diffs":   diffs,
                "files":   files,
                "git":     git,
                "memory":  memory,
            }

        return AgentResponse(
            ok=True,
            mode=req.mode,
            analysis=parsed.get("analysis", ""),
            plan=parsed.get("plan", []),
            changes=diffs,
            summary=parsed.get("summary", ""),
            no_change=parsed.get("no_change_reason"),
            preview_token=token,
            applied=applied,
            backend=settings.backend,
            model=settings.model_name,
        )

    # Never return a raw 500 — always include detail for the VS Code log
    try:
        return await _run_in_thread(_work)
    except HTTPException:
        raise
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        log.exception("Unexpected error in /run mode=%s file=%s", req.mode, req.file)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/apply", dependencies=[Depends(verify_token)])
async def apply_changes(req: ApplyRequest, settings: Settings = Depends(get_settings)):
    """
    Accept a preview and write the changes to disk.
    Called by VS Code extension when user clicks Accept.
    """
    preview = _previews.pop(req.preview_token, None)
    if not preview:
        raise HTTPException(status_code=404, detail="Preview token not found or already used.")
    if preview["project"] != req.project:
        raise HTTPException(status_code=403, detail="Project path mismatch.")

    def _work():
        applied = _apply_changes(
            preview["diffs"], preview["files"], preview["git"],
            preview["memory"], req.project, preview["mode"]
        )
        return {"ok": True, "applied_files": applied}

    return await _run_in_thread(_work)


@app.delete("/preview/{token}", dependencies=[Depends(verify_token)])
def reject_preview(token: str):
    """Reject a preview — discard it without writing."""
    _previews.pop(token, None)
    return {"ok": True, "message": "Changes rejected and discarded."}


@app.get("/history", dependencies=[Depends(verify_token)])
def history(project: str, n: int = 10):
    memory = ContextManager(project)
    return {"history": memory.get_history(n)}


# ── Error handlers ─────────────────────────────────────────────────────────────

@app.exception_handler(ConnectionError)
async def conn_err(req: Request, exc: ConnectionError):
    return JSONResponse(status_code=503, content={"detail": str(exc), "hint": "Is Ollama/vLLM running?"})

@app.exception_handler(TimeoutError)
async def timeout_err(req: Request, exc: TimeoutError):
    return JSONResponse(status_code=504, content={"detail": str(exc)})