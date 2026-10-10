"""All LLM access goes through this module so the provider can be swapped.

Uses the OpenAI-compatible chat completions API over plain HTTP, which Groq
serves. Another compatible provider only needs an entry in PROVIDERS.
"""

import os
import re
import time

import requests

from chaos.config import CHAOS_DIR

ENV_FILE = CHAOS_DIR / ".env"   # git-ignored; real environment variables take precedence

PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1",
}

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def load_env_file(path=ENV_FILE) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class LLMError(Exception):
    """The LLM could not be reached or returned no usable message."""


class LLMClient:
    def __init__(self, cfg: dict):
        load_env_file()
        llm = cfg["llm"]
        self.provider = llm["provider"]
        self.base_url = llm.get("base_url") or PROVIDERS[self.provider]
        self.model = os.getenv("LLM_MODEL") or llm["model"]
        self.api_key = os.getenv(llm.get("api_key_env", "LLM_API_KEY"))
        self.timeout = llm.get("timeout_s", 60)
        self.temperature = llm.get("temperature", 0.2)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             json_mode: bool = False) -> dict:
        """Send one chat request and return the assistant message dict."""
        if not self.api_key:
            raise LLMError("LLM_API_KEY is not set")

        payload = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error = None
        for attempt in range(4):
            wait = 2 * (attempt + 1)
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                last_error = f"request failed: {e}"
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()["choices"][0]["message"]
                    except (ValueError, KeyError, IndexError) as e:
                        raise LLMError(f"malformed response: {e}")
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in RETRYABLE_STATUS:
                    break
                if resp.status_code == 429:     # rate limit: wait as long as the provider asks
                    wait = retry_after_s(resp) or wait
                    if wait > MAX_RATE_LIMIT_WAIT_S:
                        break
                    print(f"  (LLM rate limit; waiting {wait:.0f}s)")
            if attempt < 3:
                time.sleep(wait)
        raise LLMError(last_error)


MAX_RATE_LIMIT_WAIT_S = 60


def retry_after_s(resp) -> float | None:
    """Seconds to wait from a 429: Retry-After header, or 'try again in 17.43s' in the body."""
    header = resp.headers.get("retry-after")
    try:
        if header:
            return float(header) + 1
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", resp.text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2)) + 1) if m else None
