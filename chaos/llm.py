"""LLM Provider Interface for Chaos Engineering Multi-Agent System.

Reads the key from the env var `LLM_API_KEY`.
Encapsulates all LLM calls with:
- Configurable provider & model
- Structured JSON output with Pydantic validation
- 1 retry on invalid output
- Deterministic safe fallback so runs NEVER block even without an API key
- Logs every decision to chaos/data/decisions.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel

from chaos.guard import log_decision

logger = logging.getLogger("chaos.llm")

T = TypeVar("T", bound=BaseModel)


def get_llm_credentials() -> Dict[str, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    return {"api_key": api_key, "provider": provider, "model": model}


def extract_json_block(text: str) -> str:
    """Extract JSON from markdown code fence or raw string."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        return m.group(1).strip()
    return text.strip()


async def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    fallback_instance: T,
    agent_name: str,
    reason_desc: str,
) -> T:
    """Invoke LLM, parse & validate JSON using Pydantic, retry once, then fallback."""
    creds = get_llm_credentials()
    api_key = creds["api_key"]
    provider = creds["provider"]
    model = creds["model"]

    if not api_key:
        logger.info(f"[{agent_name}] No LLM_API_KEY set. Using certified safe fallback.")
        log_decision(agent_name, "SAFE_FALLBACK", f"No LLM_API_KEY set; applied {reason_desc}")
        return fallback_instance

    last_error = None
    for attempt in range(2):
        try:
            raw_text = await _dispatch_llm_api(provider, model, api_key, system_prompt, user_prompt, attempt)
            cleaned = extract_json_block(raw_text)
            parsed_data = json.loads(cleaned)
            validated = schema.model_validate(parsed_data)
            log_decision(agent_name, "LLM_SUCCESS", f"Generated valid {schema.__name__} for {reason_desc}")
            return validated
        except Exception as e:
            last_error = e
            logger.warning(f"[{agent_name}] Attempt {attempt + 1} validation error: {e}")
            if attempt == 0:
                user_prompt += f"\n\nERROR ON PREVIOUS ATTEMPT: {e}. Output MUST be valid JSON adhering strictly to schema."

    logger.error(f"[{agent_name}] LLM failed after retry ({last_error}). Falling back to safe default.")
    log_decision(agent_name, "FALLBACK_AFTER_ERROR", f"LLM parsing failed: {last_error}; used fallback")
    return fallback_instance


async def _dispatch_llm_api(
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    attempt: int,
) -> str:
    """Dispatch HTTP request to configured LLM provider."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        if provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {"role": "user", "parts": [{"text": f"{system_prompt}\n\nTask:\n{user_prompt}"}]}
                ],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            }
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        elif provider in ("openai", "openrouter"):
            url = "https://api.openai.com/v1/chat/completions" if provider == "openai" else "https://openrouter.ai/api/v1/chat/completions"
            payload = {
                "model": model if provider != "openai" else "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        else:
            raise ValueError(f"Unsupported provider: {provider}")
