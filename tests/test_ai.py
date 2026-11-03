from datetime import datetime, timezone

import httpx
import pytest

from app import db
from app.ai import AIClient, AIUnavailable

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def client_for(conn, handler, limit=50):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return AIClient(http, "key", "model:free", conn, daily_limit=limit, clock=lambda: NOW)


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_returns_parsed_json(conn):
    ai = client_for(conn, lambda r: completion('{"ok": true}'))
    assert ai.complete_json("s", "u") == {"ok": True}


def test_json_inside_code_fence_is_parsed(conn):
    ai = client_for(conn, lambda r: completion('```json\n{"ok": true}\n```'))
    assert ai.complete_json("s", "u") == {"ok": True}


def test_429_raises_ai_unavailable(conn):
    ai = client_for(conn, lambda r: httpx.Response(429))
    with pytest.raises(AIUnavailable):
        ai.complete_json("s", "u")


def test_missing_model_raises_ai_unavailable(conn):
    ai = client_for(conn, lambda r: httpx.Response(404, json={"error": {"message": "No endpoints"}}))
    with pytest.raises(AIUnavailable):
        ai.complete_json("s", "u")


def test_network_failure_raises_ai_unavailable(conn):
    def fail(request):
        raise httpx.ConnectError("down")

    with pytest.raises(AIUnavailable):
        client_for(conn, fail).complete_json("s", "u")


def test_empty_choices_raises_ai_unavailable(conn):
    ai = client_for(conn, lambda r: httpx.Response(200, json={"error": {"message": "busy"}}))
    with pytest.raises(AIUnavailable):
        ai.complete_json("s", "u")


def test_invalid_json_retried_once_then_raises(conn):
    calls = []

    def handler(request):
        calls.append(request)
        return completion("not json")

    with pytest.raises(AIUnavailable):
        client_for(conn, handler).complete_json("s", "u")
    assert len(calls) == 2


def test_daily_limit_blocks_without_http_call(conn):
    calls = []

    def handler(request):
        calls.append(request)
        return completion("{}")

    db.record_ai_request(conn, NOW)
    with pytest.raises(AIUnavailable):
        client_for(conn, handler, limit=1).complete_json("s", "u")
    assert calls == []


def test_every_request_counts_toward_the_limit(conn):
    ai = client_for(conn, lambda r: completion("not json"))
    with pytest.raises(AIUnavailable):
        ai.complete_json("s", "u")
    assert db.count_ai_requests_today(conn, NOW) == 2
