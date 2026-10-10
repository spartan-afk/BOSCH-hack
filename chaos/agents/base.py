"""Shared agent loop: optional tool use, JSON output validated with pydantic,
one retry on invalid output, then a safe fallback so a run never blocks."""

import json
import re
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from chaos.decisions import log_decision
from chaos.llm import LLMClient, LLMError

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
MAX_TOOL_RESULT_CHARS = 12000


class Tool:
    def __init__(self, name: str, description: str, parameters: dict, fn: Callable[..., Any]):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}

    def call(self, arguments: str) -> str:
        try:
            result = self.fn(**json.loads(arguments or "{}"))
        except Exception as e:  # tool errors go back to the model, not up the stack
            result = {"error": str(e)}
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return text[:MAX_TOOL_RESULT_CHARS]


class Agent:
    name = "agent"
    system_prompt = ""
    schema: type[BaseModel] = BaseModel
    max_tool_rounds = 6

    def __init__(self, llm: LLMClient, cfg: dict):
        self.llm = llm
        self.cfg = cfg

    # ── Overridden by each agent ─────────────────────────────────

    def tools(self) -> list[Tool]:
        return []

    def check(self, result: BaseModel, inputs: dict) -> None:
        """Semantic checks beyond the schema. Raise ValueError to trigger the retry."""

    def fallback(self, inputs: dict) -> BaseModel:
        raise NotImplementedError

    def summarize(self, result: BaseModel) -> tuple[str, str]:
        """(decision, reason) for decisions.jsonl."""
        return "produced output", ""

    # ── Loop ─────────────────────────────────────────────────────

    def _system(self) -> str:
        schema = json.dumps(self.schema.model_json_schema())
        return (f"{self.system_prompt}\n\nRespond with a single JSON object only, "
                f"no prose and no code fences, matching this JSON schema:\n{schema}")

    def _tool_phase(self, messages: list[dict]) -> list[dict]:
        """Works on a copy so a failure leaves the original conversation intact."""
        messages = list(messages)
        tools = {t.name: t for t in self.tools()}
        specs = [t.spec() for t in tools.values()]
        for _ in range(self.max_tool_rounds):
            msg = self.llm.chat(messages, tools=specs)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                fn = call["function"]
                tool = tools.get(fn["name"])
                output = tool.call(fn.get("arguments")) if tool else json.dumps({"error": "unknown tool"})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        messages.append({"role": "user", "content": "Now return the final JSON object."})
        return messages

    def _parse(self, raw: str, inputs: dict) -> BaseModel:
        result = self.schema.model_validate_json(FENCE_RE.sub("", raw.strip()))
        self.check(result, inputs)
        return result

    def run(self, user_content: str, inputs: dict | None = None) -> tuple[BaseModel, str]:
        """Returns (result, source) where source is llm | llm_retry | fallback."""
        inputs = inputs or {}
        messages = [{"role": "system", "content": self._system()},
                    {"role": "user", "content": user_content}]
        result, source, error = None, "fallback", None

        try:
            if self.tools():
                try:
                    messages = self._tool_phase(messages)
                except LLMError as e:  # tool calling failed; inputs are already in the prompt
                    error = f"tool phase skipped: {e}"
            for attempt in (1, 2):
                raw = self.llm.chat(messages, json_mode=True).get("content") or ""
                try:
                    result = self._parse(raw, inputs)
                    source = "llm" if attempt == 1 else "llm_retry"
                    break
                except (ValidationError, ValueError) as e:
                    error = f"invalid output (attempt {attempt}): {str(e)[:1500]}"
                    messages += [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": f"That output was invalid:\n{str(e)[:1500]}\n"
                                                    "Return a corrected JSON object only."},
                    ]
        except LLMError as e:
            error = f"LLM unavailable: {e}"

        if result is None:
            result = self.fallback(inputs)
            source = "fallback"

        decision, reason = self.summarize(result)
        if source == "fallback":
            reason = f"{reason} [fallback because: {error}]"
        log_decision(self.name, decision, reason, source=source, model=self.llm.model)
        return result, source
