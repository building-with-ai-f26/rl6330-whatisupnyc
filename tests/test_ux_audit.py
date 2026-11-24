"""Fixes from the UI/UX audit (2026-11-24): focus, saving, undo, navigation, grouping."""
import re
from datetime import timedelta
from pathlib import Path

from app import db
from tests.fakes import ChoosingAI
from tests.test_dashboard import FETCH, ready
from tests.test_main import NOW, client_for, open_list, signed_in_ana
from tests.test_saved_lists import add_events

BASE_CSS = Path("app/templates/partials/base.css").read_text()


def card_html(html, event_id):
    start = html.index(f'id="event-{event_id}"')
    return html[start:html.index("</article>", start)]


# 1. Keyboard focus is visible: black on white pages, yellow on black.
def test_focus_ring_is_black_on_white_and_yellow_on_black():
    assert "--focus-ring: var(--black)" in BASE_CSS
    assert "outline: 3px solid var(--focus-ring)" in BASE_CSS
    assert re.search(r"\.sign[^{]*\{[^}]*--focus-ring: var\(--taxi\)", BASE_CSS)


# 2. Save is one toggle that says whether it's on, and saving is announced.
def test_save_is_a_toggle_with_a_steady_name(conn, sites):
    client, ai, ids = ready(conn, sites)
    jazz = ids["Jazz Night"]
    card = card_html(client.get("/newsletter").text, jazz)
    assert 'aria-label="Save for later" aria-pressed="false"' in card
    client.post(f"/events/{jazz}/save")
    card = card_html(client.get("/newsletter").text, jazz)
    assert 'aria-label="Save for later" aria-pressed="true"' in card and ">Saved<" in card


def test_the_page_has_a_place_to_announce_what_happened(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert 'id="announce" role="status" aria-live="polite"' in html


# 3. Remove offers Undo.
def test_remove_offers_undo(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/newsletter").text
    assert "Undo" in html and "data-undo" in html


# 4. A saved event appears under Saved for later straight away.
def test_each_pick_carries_its_saved_card_for_the_page_to_show(conn, sites):
    # Ready in the page, so saving shows it at once without another database trip.
    client, ai, ids = ready(conn, sites)
    jazz = ids["Jazz Night"]
    html = client.get("/newsletter").text
    template = html[html.index(f'<template id="saved-card-{jazz}">'):]
    template = template[:template.index("</template>")]
    assert f'id="saved-{jazz}"' in template and "Remove" in template and "Jazz Night" in template


# 5. Signed-in pages lead back to the reader's list, not the sign-up page.
def test_signed_in_pages_go_back_to_my_list(conn, sites):
    client, ai, ids = ready(conn, sites)
    for path in ("/preferences", "/newsletter/email"):
        html = client.get(path).text
        header = html[html.index('<header'):html.index('</header>')]
        assert 'href="/newsletter">Back to my list' in header and "Back to the start" not in header, path


# 6. Days on the preferences page look and work like the start page's.
def test_preference_days_are_big_buttons_like_the_start_page(conn, sites):
    client, ai, ids = ready(conn, sites)
    html = client.get("/preferences").text
    assert '<input type="checkbox" name="days" value="Friday" aria-label="Friday" checked><span aria-hidden="true">Fri</span>' in html


# 7. A jump menu reaches every part of the list page.
def test_jump_menu_counts_each_part(conn, sites):
    client, ai, ids = ready(conn, sites)
    client.post(f"/events/{ids['Jazz Night']}/save")
    html = client.get("/newsletter").text
    nav = html[html.index('aria-label="On this page"'):]
    nav = nav[:nav.index("</nav>")]
    assert 'href="#picks">Picks (2)' in nav and 'href="#saved">Saved (1)' in nav and 'href="#rate">To rate (0)' in nav


# 8. Picks are grouped by when they happen.
def test_picks_are_grouped_today_weekend_coming_up(conn, sites):
    add_events(conn, "Tonight Show", start=NOW.replace(hour=20))
    add_events(conn, "Saturday Market", start=NOW + timedelta(days=5))  # Sat Nov 7
    add_events(conn, "Next Week Talk", start=NOW + timedelta(days=9))  # Wed Nov 11
    client = client_for(conn, sites, ChoosingAI(3))
    signed_in_ana(client)
    html = open_list(client).text
    order = [html.index(text) for text in
             ("Happening today", "Tonight Show", "This weekend", "Saturday Market", "Coming up", "Next Week Talk")]
    assert order == sorted(order)


def test_empty_groups_are_left_out(conn, sites):
    client, ai, ids = ready(conn, sites)  # both picks are on Thursday
    html = client.get("/newsletter").text
    assert "Coming up" in html and "Happening today" not in html and "This weekend" not in html


# 9. "More like this" is its own line, apart from the buttons.
def test_more_like_this_sits_apart_from_the_buttons(conn, sites):
    client, ai, ids = ready(conn, sites)
    card = card_html(client.get("/newsletter").text, ids["Jazz Night"])
    tools = card[card.index('class="tools"'):]
    tools = tools[:tools.index("</div>")]
    assert 'name="event_ids"' not in tools
    assert 'class="pick"' in card


def test_hidden_always_hides_even_cards_and_grids():
    # Found by driving the page in Chrome: a removed card stayed visible because .flyer is display:flex.
    assert "[hidden] { display: none !important; }" in BASE_CSS
