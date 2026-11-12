import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app import db

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MINIMAX_URL = "https://api.minimax.io/v1/chat/completions"


class AIUnavailable(Exception):
    pass


class DailyLimitReached(AIUnavailable):
    pass


@dataclass
class Provider:
    """One model to send requests to. A paid backup is tried when the free one is busy or fails."""

    url: str
    api_key: str
    model: str


def backup_from_env() -> Provider | None:
    key = os.environ.get("MINIMAX_API_KEY", "")
    return Provider(MINIMAX_URL, key, os.environ.get("MINIMAX_MODEL", "MiniMax-M3")) if key else None


class AIClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str,
        model: str,
        conn: sqlite3.Connection,
        daily_limit: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        backup: Provider | None = None,
    ):
        self.http = http
        self.conn = conn
        self.daily_limit = daily_limit
        self.clock = clock
        self.providers = [Provider(API_URL, api_key, model)] + ([backup] if backup else [])

    def complete_json(self, system: str, user: str) -> dict:
        for provider in self.providers:
            try:
                return self._json_from(provider, system, user)
            except DailyLimitReached:
                raise
            except AIUnavailable as exc:
                error = exc
                log.warning("AI model %s failed: %s", provider.model, exc)
        raise error

    def _json_from(self, provider: Provider, system: str, user: str) -> dict:
        for _ in range(2):
            parsed = _parse_json(self._request(provider, system, user))
            if parsed is not None:
                return parsed
        raise AIUnavailable("The model did not return valid JSON")

    def _request(self, provider: Provider, system: str, user: str) -> str:
        now = self.clock()
        if db.count_ai_requests_today(self.conn, now) >= self.daily_limit:
            raise DailyLimitReached("Daily AI request limit reached")
        db.record_ai_request(self.conn, now)
        try:
            response = self.http.post(
                provider.url,
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json={
                    "model": provider.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise AIUnavailable(type(exc).__name__) from exc
        if response.status_code != 200:
            raise AIUnavailable(f"HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise AIUnavailable("Unexpected response from the model") from exc


def _parse_json(content: str) -> dict | None:
    # Some models, like MiniMax, write their reasoning first inside <think> tags.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
