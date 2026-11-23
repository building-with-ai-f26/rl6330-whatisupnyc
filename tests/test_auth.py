from app import db
from tests.test_main import GOOD, ana_id, client_for, sign_up_ana


def test_form_from_another_site_is_refused(conn, sites):
    client = client_for(conn, sites)
    data = {"link": "not a link", "caption": "x"}
    assert client.post("/paste", data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    del client.headers["Origin"]
    assert client.post("/paste", data=data).status_code == 403
    assert client.post("/paste", data=data, headers={"Origin": "https://testserver"}).status_code == 200


def test_every_form_refuses_other_sites(conn, sites):
    client = client_for(conn, sites)
    sign_up_ana(client)
    forms = [
        ("/subscribers", {**GOOD, "email": "bo@x.test"}),
        ("/newsletter/sent", {"event_ids": [1]}),
        ("/paste", {"link": "x", "caption": "x"}),
        ("/signin", {"email": GOOD["email"], "password": GOOD["password"]}),
        ("/signout", {}),
        ("/forgot", {"email": GOOD["email"]}),
        ("/reset", {"token_hash": "x", "password": "long-enough-2"}),
        ("/confirm", {"token_hash": "x", "password": "long-enough-2"}),
    ]
    for origin in ["https://evil.example", "null", "https://evil.example@testserver", "https://sub.testserver"]:
        for path, data in forms:
            assert client.post(path, data=data, headers={"Origin": origin}).status_code == 403, (path, origin)
    assert client.supabase.signups == [GOOD["email"]]
    assert client.supabase.resets_sent == [] and client.supabase.signed_out == []
    assert db.sent_event_ids(conn, ana_id(client)) == set()


def test_form_with_same_site_referer_is_allowed(conn, sites):
    client = client_for(conn, sites)
    del client.headers["Origin"]
    response = client.post("/paste", data={"link": "x", "caption": "x"}, headers={"Referer": "https://testserver/"})
    assert response.status_code == 200


def test_api_docs_pages_are_off(conn, sites):
    client = client_for(conn, sites)
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404
