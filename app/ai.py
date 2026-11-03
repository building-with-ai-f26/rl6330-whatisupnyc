import json
import logging
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from app import db

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class AIUnavailable(Exception):
    pass


class AIClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str,
        model: str,
        conn: sqlite3.Connection,
        daily_limit: int = 50,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.http = http
        self.api_key = api_key
        self.model = model
        self.conn = conn
        self.daily_limit = daily_limit
        self.clock = clock

    def complete_json(self, system: str, user: str) -> dict:
        for _ in range(2):
            content = self._request(system, user)
            parsed = _parse_json(content)
            if parsed is not None:
                return parsed
        raise AIUnavailable("The model did not return valid JSON")

    def _request(self, system: str, user: str) -> str:
        now = self.clock()
        if db.count_ai_requests_today(self.conn, now) >= self.daily_limit:
            raise AIUnavailable("Daily AI request limit reached")
        db.record_ai_request(self.conn, now)
        try:
            response = self.http.post(
                API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
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
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
