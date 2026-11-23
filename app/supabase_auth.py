"""Supabase Auth, called only from our server.

The browser never talks to Supabase: our pages post to FastAPI, and FastAPI calls these.
The publishable key is the only key used here. The secret key never reaches the web app.
"""
import os
from dataclasses import dataclass

import httpx

TIMEOUT = 10


@dataclass
class AuthUser:
    id: str
    email: str


@dataclass
class Session:
    access_token: str
    refresh_token: str
    user: AuthUser


class AuthRefused(Exception):
    """Supabase said no, for a reason the reader can fix. code is Supabase's error_code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AuthUnavailable(Exception):
    """Supabase didn't answer, or failed on its side."""


class SupabaseAuth:
    def __init__(self, http: httpx.Client, url: str, publishable_key: str):
        self.http = http
        self.url = url.rstrip("/")
        self.key = publishable_key

    def sign_up(self, email: str, password: str) -> AuthUser | None:
        """The new account, or None when the email already has one (Supabase doesn't say so directly)."""
        body = self._call("POST", "/signup", json={"email": email, "password": password})
        user = body.get("user", body)
        return _user(user) if user.get("identities") else None

    def sign_in(self, email: str, password: str) -> Session:
        return _session(self._call("POST", "/token?grant_type=password", json={"email": email, "password": password}))

    def refresh(self, refresh_token: str) -> Session:
        return _session(self._call("POST", "/token?grant_type=refresh_token", json={"refresh_token": refresh_token}))

    def get_user(self, access_token: str) -> AuthUser | None:
        """The signed-in reader, or None when the access token has expired or is not real."""
        try:
            return _user(self._call("GET", "/user", token=access_token))
        except AuthRefused:
            return None

    def send_reset(self, email: str) -> None:
        self._call("POST", "/recover", json={"email": email})

    def verify(self, token_hash: str, kind: str) -> Session:
        """Trades the token from an emailed link for a login. kind is "email" (confirm) or "recovery" (reset)."""
        return _session(self._call("POST", "/verify", json={"type": kind, "token_hash": token_hash}))

    def set_password(self, access_token: str, password: str) -> None:
        self._call("PUT", "/user", token=access_token, json={"password": password})

    def sign_out(self, access_token: str) -> None:
        try:
            self._call("POST", "/logout", token=access_token)
        except AuthRefused:
            pass  # Already expired: nothing left to end.

    def sign_out_others(self, access_token: str) -> None:
        """Ends every other login on this account, keeping this one."""
        self._call("POST", "/logout?scope=others", token=access_token)

    def _call(self, method: str, path: str, token: str | None = None, json: dict | None = None) -> dict:
        headers = {"apikey": self.key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self.http.request(method, f"{self.url}/auth/v1{path}", headers=headers, json=json, timeout=TIMEOUT)
        except httpx.HTTPError as error:
            raise AuthUnavailable(type(error).__name__) from error
        body = _json(response)
        if response.is_success:
            return body
        if 400 <= response.status_code < 500 and body.get("error_code"):
            raise AuthRefused(body["error_code"])
        raise AuthUnavailable(f"status {response.status_code}")


def auth_from_env(http: httpx.Client) -> SupabaseAuth:
    return SupabaseAuth(http, os.environ["SUPABASE_URL"], os.environ["SUPABASE_PUBLISHABLE_KEY"])


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _user(body: dict) -> AuthUser:
    return AuthUser(body["id"], body.get("email", ""))


def _session(body: dict) -> Session:
    return Session(body["access_token"], body["refresh_token"], _user(body["user"]))
