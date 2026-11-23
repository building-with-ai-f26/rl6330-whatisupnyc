from app import db
from app.models import Score
from tests.conftest import make_event
from tests.test_main import client_for, sign_up_ana


def an_event(conn, **changes):
    fields = dict(name="Jazz <b>Night</b>", url="https://reliable.example/e/jazz", image_url="https://cdn.example/jazz.jpg")
    return db.save_event(conn, make_event(**{**fields, **changes}), Score(40, True))


def test_share_page_shows_the_event_to_anyone(conn, sites):
    event_id = an_event(conn)
    client = client_for(conn, sites)
    html = client.get(f"/e/{event_id}").text
    assert "Jazz &lt;b&gt;Night&lt;/b&gt;" in html and "<b>Night</b>" not in html
    assert "Beacon Theatre" in html
    assert 'href="https://reliable.example/e/jazz"' in html
    assert 'src="https://cdn.example/jazz.jpg"' in html
    assert 'href="/"' in html  # make your own list


def test_share_page_has_link_preview_tags(conn, sites):
    event_id = an_event(conn)
    html = client_for(conn, sites).get(f"/e/{event_id}").text
    assert '<meta property="og:title" content="Jazz &lt;b&gt;Night&lt;/b&gt;">' in html
    assert '<meta property="og:image" content="https://cdn.example/jazz.jpg">' in html
    assert 'property="og:description"' in html


def test_share_page_without_its_own_picture_uses_ours(conn, sites):
    event_id = an_event(conn, image_url=None)
    html = client_for(conn, sites).get(f"/e/{event_id}").text
    assert '<img src="/static/pictures/' in html
    assert '<meta property="og:image" content="https://testserver/static/pictures/' in html


def test_unknown_event_is_404(conn, sites):
    assert client_for(conn, sites).get("/e/999999").status_code == 404


def test_share_page_shows_nothing_about_readers_or_scores(conn, sites):
    event_id = an_event(conn)
    client = client_for(conn, sites)
    sign_up_ana(client, name="Ana Sharer")
    html = client.get(f"/e/{event_id}").text
    assert "Ana Sharer" not in html and "score" not in html.lower()


def test_events_the_site_doesnt_recommend_are_not_shown(conn, sites):
    # Event numbers count up, so anyone could walk them; only eligible events get a public page.
    event_id = db.save_event(conn, make_event(name="Weak", url="https://reliable.example/w"), Score(0, False, "no credibility"))
    assert client_for(conn, sites).get(f"/e/{event_id}").status_code == 404
