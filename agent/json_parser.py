"""
json_parser.py — Robust JSON extraction for small/noisy LLM outputs.

Small models like qwen2.5-coder:3b often produce:
  - Trailing garbage after the closing brace
  - Comments inside JSON (// ...)
  - Truncated output
  - Mixed JSON + prose
  - Repeated keys
  - Single quotes instead of double quotes

This module handles all of those cases gracefully.
"""

import re
import json
import logging

log = logging.getLogger("agent.parser")


def extract_json(raw: str) -> dict:
    """
    Try every known strategy to extract a valid JSON object from raw LLM output.
    Returns a safe fallback dict if nothing works.
    """
    # 1. Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", raw)
    text = text.replace("```", "").strip()

    # 2. Try parsing the whole thing directly
    result = _try_parse(text)
    if result:
        return result

    # 3. Extract first {...} block (handles trailing garbage)
    result = _extract_first_brace(text)
    if result:
        return result

    # 4. Try fixing common issues and re-parsing
    result = _try_parse(_fix_common_issues(text))
    if result:
        return result

    # 5. Try extracting from a brace-balanced substring
    result = _extract_balanced(text)
    if result:
        return result

    # 6. Try to salvage at least the analysis field
    analysis = _salvage_analysis(raw)
    log.warning("Could not parse JSON from model output. Returning fallback.")
    return {
        "analysis": analysis or "Model returned malformed output. Try again.",
        "plan": [],
        "changes": [],
        "summary": "Could not parse model response.",
        "no_change_reason": "JSON parsing failed — no changes applied.",
    }


def _try_parse(text: str) -> dict | None:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _normalize(data)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _extract_first_brace(text: str) -> dict | None:
    """Find the first { and try to parse from there, stopping at the matching }."""
    start = text.find("{")
    if start == -1:
        return None
    # Try increasingly larger substrings from the first brace
    for end in range(len(text), start, -1):
        if text[end-1] == "}":
            result = _try_parse(text[start:end])
            if result:
                return result
    return None


def _extract_balanced(text: str) -> dict | None:
    """Find a brace-balanced JSON object by counting braces."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return _try_parse(text[start:i+1])
    return None


def _fix_common_issues(text: str) -> str:
    """Fix common small-model JSON mistakes."""
    # Remove JS-style comments
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    # Replace single quotes with double quotes (careful with apostrophes)
    # Only replace ' used as JSON delimiters (before/after : and ,)
    text = re.sub(r"'([^']*)'(\s*[,:\}\]])", r'"\1"\2', text)

    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Remove ... ellipsis comments
    text = re.sub(r"\.\.\.\s*(?:more[^\n]*)?\n?", "", text)

    # Fix unquoted keys  — basic: word: → "word":
    text = re.sub(r'(?<!["\w])(\w+)(\s*):', r'"\1"\2:', text)

    # Remove duplicate closing braces/brackets at the end
    text = text.rstrip()
    while len(text) > 1 and text[-1] == text[-2] == "}":
        text = text[:-1]

    return text


def _salvage_analysis(raw: str) -> str | None:
    """Try to extract at least the analysis text from broken output."""
    # Look for "analysis": "..." pattern
    m = re.search(r'"analysis"\s*:\s*"([^"]{10,})"', raw)
    if m:
        return m.group(1)
    # Look for analysis: ... pattern without quotes
    m = re.search(r'analysis["\s:]+([A-Z][^",\n]{20,})', raw)
    if m:
        return m.group(1).strip()
    return None


def _normalize(data: dict) -> dict:
    """Ensure all expected keys exist with correct types."""
    return {
        "analysis":        str(data.get("analysis") or ""),
        "plan":            _ensure_list(data.get("plan", [])),
        "changes":         _ensure_changes(data.get("changes", [])),
        "summary":         str(data.get("summary") or ""),
        "no_change_reason": data.get("no_change_reason"),
    }


def _ensure_list(val) -> list:
    if isinstance(val, list):
        # Flatten dicts like {"step": "...", "details": "..."} → "step: details"
        result = []
        for item in val:
            if isinstance(item, dict):
                parts = [str(v) for v in item.values() if v]
                result.append(" — ".join(parts))
            else:
                result.append(str(item))
        return result
    if isinstance(val, str):
        return [val]
    return []


def _fix_indentation(code: str) -> str:
    """Fix broken indentation after block-starting keywords (try/except/if/for/def etc).
    Small 3B models often forget to indent the body after these keywords."""
    import re as _re
    if not code.strip():
        return code
    lines = code.splitlines()
    BLOCK = _re.compile(
        r'^(\s*)(try|except\b.*|else|elif\b.*|finally|with\b.*|for\b.*'
        r'|while\b.*|if\b.*|def\b.*|class\b.*|async\s+\S.*)?:\s*$'
    )
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        m = BLOCK.match(line)
        if m:
            cur_indent  = len(m.group(1))
            nxt         = lines[i + 1]
            nxt_indent  = len(nxt) - len(nxt.lstrip())
            nxt_strip   = nxt.strip()
            is_cont = _re.match(r'\s*(except\b|else\b|elif\b|finally\b)', nxt)
            if nxt_strip and nxt_indent <= cur_indent and not is_cont:
                lines[i + 1] = " " * (cur_indent + 4) + nxt.lstrip()
        i += 1
    return "\n".join(lines)


def _ensure_changes(val) -> list:
    if not isinstance(val, list):
        return []
    clean = []
    for item in val:
        if not isinstance(item, dict):
            continue
        if not item.get("file") or not item.get("replacement"):
            continue
        clean.append({
            "file":        str(item.get("file", "")),
            "original":    _fix_indentation(str(item.get("original", ""))),
            "replacement": _fix_indentation(str(item.get("replacement", ""))),
            "reason":      str(item.get("reason", "")),
        })
    return clean