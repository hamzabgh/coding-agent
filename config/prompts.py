"""
Prompt Templates — simplified for reliable JSON output.

Designed to work with qwen2.5-coder, deepseek-coder, and similar 6-7B code models.
The JSON schema is intentionally simple so small models can follow it reliably.
"""

PERSONA = """You are an expert software engineer. You write clean, precise code.
You always respond with valid JSON only — no markdown, no explanation outside the JSON."""

# Simple, short contract that small models can follow
JSON_CONTRACT = """
Respond with this JSON schema and nothing else:
{
  "analysis": "what you found in 1-3 sentences",
  "plan": ["step 1", "step 2"],
  "changes": [
    {
      "file": "path/to/file.py",
      "original": "exact lines to replace, copied verbatim from the source",
      "replacement": "new lines",
      "reason": "why"
    }
  ],
  "summary": "one sentence summary",
  "no_change_reason": null
}

Rules:
- "original" must be copied character-for-character from the file
- Only include lines you are actually changing — never the whole file
- If nothing needs changing: empty "changes":[] and explain in "no_change_reason"
- Return ONLY the JSON object. No ```json fences, no extra text.
"""


def build_context(project_context: str, file_contents: dict) -> str:
    parts = []
    if project_context:
        parts.append(f"## Project\n{project_context[:800]}")
    for p, c in file_contents.items():
        # Cap each file at 3000 chars to avoid context overflow on small models
        parts.append(f"## File: {p}\n```\n{c[:3000]}\n```")
    return "\n\n".join(parts)


class PromptTemplates:

    @staticmethod
    def build(mode: str, **kwargs) -> str:
        builders = {
            "fix_bug":     PromptTemplates.fix_bug,
            "improve":     PromptTemplates.improve,
            "add_feature": PromptTemplates.add_feature,
            "refactor":    PromptTemplates.refactor,
            "chat":        PromptTemplates.chat,
            "explain":     PromptTemplates.explain,
            "generate":    PromptTemplates.generate,
        }
        return builders.get(mode, PromptTemplates.chat)(**kwargs)

    @staticmethod
    def fix_bug(file_path: str, code: str, context: str = "",
                bug_description: str = "", related: dict = None, **_) -> str:
        ctx = build_context(context, {file_path: code})
        bug = f"\nBug reported: {bug_description}" if bug_description else ""
        return f"""{PERSONA}

{ctx}{bug}

Task: Find and fix bug(s) in `{file_path}`.
- Change ONLY the broken lines
- Do NOT rewrite unrelated code
- Explain root cause in "analysis"

{JSON_CONTRACT}"""

    @staticmethod
    def improve(file_path: str, code: str, context: str = "",
                related: dict = None, **_) -> str:
        ctx = build_context(context, {file_path: code})
        return f"""{PERSONA}

{ctx}

Task: Improve `{file_path}`.
- Fix real problems only (bugs, missing error handling, security issues)
- Do NOT rewrite working code
- Do NOT rename things that are already clear

{JSON_CONTRACT}"""

    @staticmethod
    def add_feature(file_path: str, code: str, feature: str,
                    context: str = "", related: dict = None, **_) -> str:
        ctx = build_context(context, {file_path: code})
        return f"""{PERSONA}

{ctx}

Task: Add this feature to `{file_path}`:
> {feature}

- Integrate naturally with existing code style
- Do not modify unrelated code

{JSON_CONTRACT}"""

    @staticmethod
    def refactor(file_path: str, code: str, instructions: str = "",
                 context: str = "", related: dict = None, **_) -> str:
        ctx = build_context(context, {file_path: code})
        instr = instructions or "Improve structure, naming, and readability."
        return f"""{PERSONA}

{ctx}

Task: Refactor `{file_path}`.
Instructions: {instr}
- Preserve ALL existing behaviour
- Meaningful changes only, not cosmetic

{JSON_CONTRACT}"""

    @staticmethod
    def chat(message: str, file_path: str = "", code: str = "",
             context: str = "", related: dict = None, **_) -> str:
        files = {}
        if code and file_path:
            files[file_path] = code
        ctx = build_context(context, files)
        return f"""{PERSONA}

{ctx}

Developer says: {message}

If this needs a code change: make surgical edits, use the JSON contract.
If this is a question: answer in "analysis", set "changes":[], "no_change_reason":"Question answered."

{JSON_CONTRACT}"""

    @staticmethod
    def explain(file_path: str, code: str, **_) -> str:
        return f"""{PERSONA}

## File: {file_path}
```
{code[:3000]}
```

Task: Explain this file.

Respond with JSON:
{{
  "analysis": "What this file does overall in 2-3 sentences",
  "plan": ["Key component: description", "Key component: description"],
  "changes": [],
  "summary": "One sentence summary",
  "no_change_reason": "Explanation only — no code changes needed"
}}

Return ONLY the JSON. No extra text."""

    @staticmethod
    def generate(file_path: str, description: str, context: str = "",
                 lang: str = "python", **_) -> str:
        ctx = build_context(context, {}) if context else ""
        return f"""{PERSONA}

{ctx}

Task: Generate new file `{file_path}` ({lang}).
Description: {description}

Write complete, production-quality code.

Respond with JSON:
{{
  "analysis": "What this file does and how it's structured",
  "plan": ["Design decision 1", "Design decision 2"],
  "changes": [
    {{
      "file": "{file_path}",
      "original": "",
      "replacement": "<FULL FILE CONTENT>",
      "reason": "New file"
    }}
  ],
  "summary": "One sentence",
  "no_change_reason": null
}}

Return ONLY the JSON. No extra text."""