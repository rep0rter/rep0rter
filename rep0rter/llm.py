"""Minimal client for an OpenAI-compatible /v1/chat/completions endpoint."""

from __future__ import annotations

import json
import logging
import re

import requests

from .config import Config

log = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class LLM:
    def __init__(self, cfg: Config):
        if not cfg.llm_enabled:
            raise RuntimeError("LLM is not configured (AI_BASE_URL / AI_API_KEY / AI_MODEL)")
        self.base_url = cfg.ai_base_url.rstrip("/")
        self.api_key = cfg.ai_api_key
        self.model = cfg.ai_model
        self.timeout = cfg.ai_timeout_seconds

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object; tolerate code fences and stray prose."""
        text = self.chat(system + "\n\n只回傳一個 JSON 物件，不要加任何說明或 code fence。", user)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = _JSON_BLOCK.search(text)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        log.warning("LLM returned non-JSON: %.200s", text)
        raise ValueError("LLM response was not JSON")
