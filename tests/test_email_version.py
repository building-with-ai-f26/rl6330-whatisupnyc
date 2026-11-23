import json
from datetime import timedelta

import httpx

from app.newsletter import render_newsletter
from app.models import Pick, Score, ScoredEvent
from tests.conftest import make_event
from tests.fakes import ChoosingAI
from tests.test_main import GOOD, NOW, client_for, open_list, sign_up_ana, signed_in_ana
from tests.test_saved_lists import add_events


def pick(name, image=None):
    return Pick(ScoredEvent(1, make_event(name=name, start=NOW + timedelta(days=2), image_url=image), Score(40, True)), "fits")


def test_email_shows_a_small_thumbnail_when_there_is_a_picture():
    html = render_newsletter([pick("Jazz Night", "https://cdn.example/jazz.jpg")], now=NOW)
    assert '<img src="https://cdn.example/jazz.jpg" width="72" height="72"' in html
    assert "max-width: 600px" in html  # still the compact email width


def test_email_without_a_picture_has_no_image():
    assert "<img" not in render_newsletter([pick("Jazz Night")], now=NOW)


def test_email_links_back_to_the_dashboard():
    html = render_newsletter([pick("Jazz Night")], now=NOW, site_url="https://whatisupnyc.com")
    assert 'href="https://whatisupnyc.com/newsletter"' in html
    assert "Reply to this email" not in html


def listed_client(conn, sites, **options):
    add_events(conn, "Jazz Night", "Comedy Cellar")
    client = client_for(conn, sites, ChoosingAI(), **options)
    signed_in_ana(client)
    open_list(client)
    return client


def test_preview_shows_the_email_version_of_the_list(conn, sites):
    client = listed_client(conn, sites)
    html = client.get("/newsletter/email").text
    assert "Jazz Night" in html and "max-width: 600px" in html
    assert "Send it to my email" not in html  # sending isn't set up without a key
    assert client.post("/newsletter/email").status_code == 404


def test_preview_before_any_list(conn, sites):
    client = client_for(conn, sites, ChoosingAI())
    signed_in_ana(client)
    html = client.get("/newsletter/email").text
    assert 'href="/newsletter"' in html and "no list yet" in html.lower()


def test_sending_goes_only_to_the_readers_own_address(conn, sites):
    sent = []

    def resend(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "email-1"})

    client = listed_client(conn, sites, resend_key="re_test", outside=resend)
    assert "Send it to my email" in client.get("/newsletter/email").text
    response = client.post("/newsletter/email", data={"to": "someone-else@x.test"})
    assert response.status_code == 200 and GOOD["email"] in response.text
    (request,) = sent
    body = json.loads(request.content)
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test"
    assert body["to"] == [GOOD["email"]] and body["from"] == "whatisupnyc <list@whatisupnyc.com>"
    assert "Jazz Night" in body["html"]


def test_sending_is_limited_to_three_a_day(conn, sites):
    client = listed_client(conn, sites, resend_key="re_test", outside=lambda r: httpx.Response(200, json={"id": "x"}))
    for _ in range(3):
        assert client.post("/newsletter/email").status_code == 200
    assert client.post("/newsletter/email").status_code == 429


def test_a_failed_send_says_so(conn, sites):
    client = listed_client(conn, sites, resend_key="re_test", outside=lambda r: httpx.Response(500))
    response = client.post("/newsletter/email")
    assert response.status_code == 502 and "send the email just now" in response.text


def test_just_signed_up_readers_sign_in_before_sending(conn, sites):
    add_events(conn, "Jazz Night")
    client = client_for(conn, sites, ChoosingAI(), resend_key="re_test", outside=lambda r: httpx.Response(200, json={}))
    sign_up_ana(client)
    response = client.post("/newsletter/email")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")
