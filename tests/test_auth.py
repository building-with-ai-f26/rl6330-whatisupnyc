import base64
from datetime import datetime

import httpx
from fastapi.testclient import TestClient

from app.main import AppDeps, create_app
from tests.conftest import NY
from tests.fakes import FakeAI, FakeTavily

PASSWORD = "correct-horse-battery-staple"


def basic(password: str, user: str = "pod") -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def bare_client(conn, sites, password=PASSWORD):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    deps = AppDeps(
        conn=conn, ai=FakeAI(), tavily=FakeTavily(), http=http, sites=sites,
        clock=lambda: datetime(2026, 11, 2, 12, tzinfo=NY), site_password=password,
    )
    return TestClient(create_app(deps), follow_redirects=False)


def test_no_password_is_refused(conn, sites):
    response = bare_client(conn, sites).get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")


def test_wrong_password_is_refused(conn, sites):
    response = bare_client(conn, sites).get("/", headers={"Authorization": basic("wrong")})
    assert response.status_code == 401


def test_broken_header_is_refused(conn, sites):
    for header in ["Basic !!!", "Bearer abc", "Basic " + base64.b64encode(b"no-colon").decode()]:
        assert bare_client(conn, sites).get("/", headers={"Authorization": header}).status_code == 401


def test_right_password_lets_you_in(conn, sites):
    response = bare_client(conn, sites).get("/", headers={"Authorization": basic(PASSWORD)})
    assert response.status_code == 200


def test_missing_password_setting_closes_the_site(conn, sites):
    response = bare_client(conn, sites, password="").get("/", headers={"Authorization": basic("")})
    assert response.status_code == 503


def test_short_password_setting_closes_the_site(conn, sites):
    response = bare_client(conn, sites, password="short").get("/", headers={"Authorization": basic("short")})
    assert response.status_code == 503


def test_form_from_another_site_is_refused(conn, sites):
    client = bare_client(conn, sites)
    auth = {"Authorization": basic(PASSWORD)}
    data = {"link": "not a link", "caption": "x"}
    assert client.post("/paste", data=data, headers={**auth, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/paste", data=data, headers=auth).status_code == 403
    assert client.post("/paste", data=data, headers={**auth, "Origin": "http://testserver"}).status_code == 200


def test_form_with_same_site_referer_is_allowed(conn, sites):
    headers = {"Authorization": basic(PASSWORD), "Referer": "http://testserver/"}
    response = bare_client(conn, sites).post("/paste", data={"link": "x", "caption": "x"}, headers=headers)
    assert response.status_code == 200


def test_api_docs_pages_are_off(conn, sites):
    client = bare_client(conn, sites)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path, headers={"Authorization": basic(PASSWORD)}).status_code == 404
