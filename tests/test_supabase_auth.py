import json
from pathlib import Path

import httpx
import pytest

from app.supabase_auth import AuthRefused, AuthUnavailable, AuthUser, SupabaseAuth

REPLIES = json.loads((Path(__file__).parent / "fixtures" / "supabase_auth.json").read_text())
URL = "https://project.supabase.co"
KEY = "sb_publishable_test"


def auth_replying(*names, seen=None):
    """A client whose Supabase answers with the named saved replies, in order. Requests go into seen."""
    queue = list(names)

    def handle(request):
        if seen is not None:
            seen.append(request)
        reply = REPLIES[queue.pop(0)]
        return httpx.Response(reply["status"], json=reply["body"])

    return SupabaseAuth(httpx.Client(transport=httpx.MockTransport(handle)), URL, KEY)


def test_sign_up_returns_the_new_user():
    seen = []
    user = auth_replying("signup_new", seen=seen).sign_up("ana@x.test", "long-enough-1")
    assert user == AuthUser("8d2f1c3e-5b7a-4c1d-9e2f-0a1b2c3d4e5f", "ana@x.test")
    assert seen[0].url == f"{URL}/auth/v1/signup"
    assert json.loads(seen[0].content) == {"email": "ana@x.test", "password": "long-enough-1"}


def test_sign_up_existing_email_returns_none():
    assert auth_replying("signup_existing").sign_up("ana@x.test", "long-enough-1") is None


def test_sign_in_returns_session():
    seen = []
    session = auth_replying("token_ok", seen=seen).sign_in("ana@x.test", "long-enough-1")
    assert (session.access_token, session.refresh_token, session.user.email) == ("access-1", "refresh-1", "ana@x.test")
    assert seen[0].url == f"{URL}/auth/v1/token?grant_type=password"


def test_wrong_password_raises_refused_with_code():
    with pytest.raises(AuthRefused) as refused:
        auth_replying("token_invalid").sign_in("ana@x.test", "wrong")
    assert refused.value.code == "invalid_credentials"


def test_unconfirmed_email_raises_refused_with_code():
    with pytest.raises(AuthRefused) as refused:
        auth_replying("token_unconfirmed").sign_in("ana@x.test", "long-enough-1")
    assert refused.value.code == "email_not_confirmed"


def test_refresh_sends_the_refresh_token():
    seen = []
    session = auth_replying("token_ok", seen=seen).refresh("refresh-0")
    assert session.access_token == "access-1"
    assert seen[0].url == f"{URL}/auth/v1/token?grant_type=refresh_token"
    assert json.loads(seen[0].content) == {"refresh_token": "refresh-0"}


def test_get_user_sends_the_access_token():
    seen = []
    assert auth_replying("user_ok", seen=seen).get_user("access-1").id == "8d2f1c3e-5b7a-4c1d-9e2f-0a1b2c3d4e5f"
    assert seen[0].headers["authorization"] == "Bearer access-1"


def test_expired_access_token_gives_no_user():
    assert auth_replying("user_expired").get_user("access-old") is None


def test_verify_sends_token_hash_and_type():
    seen = []
    session = auth_replying("verify_ok", seen=seen).verify("abc", "recovery")
    assert session.access_token == "access-1"
    assert seen[0].url == f"{URL}/auth/v1/verify"
    assert json.loads(seen[0].content) == {"type": "recovery", "token_hash": "abc"}


def test_expired_link_raises_refused_with_code():
    with pytest.raises(AuthRefused) as refused:
        auth_replying("verify_expired").verify("abc", "email")
    assert refused.value.code == "otp_expired"


def test_set_password_sends_it_for_the_signed_in_reader():
    seen = []
    auth_replying("user_ok", seen=seen).set_password("access-1", "new-password-1")
    assert (seen[0].method, seen[0].url) == ("PUT", f"{URL}/auth/v1/user")
    assert seen[0].headers["authorization"] == "Bearer access-1"
    assert json.loads(seen[0].content) == {"password": "new-password-1"}


def test_weak_password_raises_refused_with_code():
    with pytest.raises(AuthRefused) as refused:
        auth_replying("weak_password").sign_up("ana@x.test", "short")
    assert refused.value.code == "weak_password"


def test_send_reset_posts_the_email():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={})

    SupabaseAuth(httpx.Client(transport=httpx.MockTransport(handle)), URL, KEY).send_reset("ana@x.test")
    assert seen[0].url == f"{URL}/auth/v1/recover"
    assert json.loads(seen[0].content) == {"email": "ana@x.test"}


def test_sign_out_ignores_an_already_expired_login():
    auth_replying("user_expired").sign_out("access-old")


def test_server_error_raises_unavailable():
    with pytest.raises(AuthUnavailable):
        auth_replying("server_error").sign_in("ana@x.test", "long-enough-1")


def test_timeout_raises_unavailable():
    def handle(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AuthUnavailable):
        SupabaseAuth(httpx.Client(transport=httpx.MockTransport(handle)), URL, KEY).get_user("access-1")


def test_every_call_sends_the_publishable_key_not_a_secret():
    seen = []
    auth = auth_replying("signup_new", "token_ok", "user_ok", seen=seen)
    auth.sign_up("ana@x.test", "long-enough-1")
    auth.sign_in("ana@x.test", "long-enough-1")
    auth.get_user("access-1")
    assert [request.headers["apikey"] for request in seen] == [KEY] * 3


def test_sign_out_others_keeps_this_login():
    seen = []
    auth_replying("user_ok", seen=seen).sign_out_others("access-1")
    assert seen[0].url == f"{URL}/auth/v1/logout?scope=others"
    assert seen[0].headers["authorization"] == "Bearer access-1"


def test_sign_out_everywhere_ends_every_login():
    seen = []
    auth_replying("user_ok", seen=seen).sign_out("access-1", everywhere=True)
    assert seen[0].url == f"{URL}/auth/v1/logout?scope=global"
