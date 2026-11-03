from datetime import datetime

import pytest

from app.checks import check_event, date_appears, same_event
from app.pages import PageResult
from tests.conftest import NY, make_event

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)


def page(text):
    return PageResult(200, text, f"<p>{text}</p>", False)


GOOD_PAGE = page("Sat, Nov 7 at Beacon Theatre")


def api_event(**overrides):
    return make_event(source_type="api", **overrides)


@pytest.mark.parametrize(
    "text",
    ["Sat, Nov 7", "November 7, 2026", "11/7/2026", "2026-11-07", "Nov. 7", "7 November 2026", "11/07/26"],
)
def test_date_formats_match(text):
    assert date_appears(text, datetime(2026, 11, 7, 20, tzinfo=NY))


@pytest.mark.parametrize("text", ["Nov 17", "November 27, 2026", "2026-11-17", "11/17/2026", "Nov 9"])
def test_near_miss_dates_do_not_match(text):
    assert not date_appears(text, datetime(2026, 11, 7, 20, tzinfo=NY))


def test_date_uses_new_york_day():
    late_utc = datetime.fromisoformat("2026-11-08T01:00:00+00:00")
    assert date_appears("Sat, Nov 7", late_utc)


def test_good_web_event_passes():
    assert check_event(make_event(), GOOD_PAGE, NOW).passed


def test_wrong_date_fails():
    assert not check_event(make_event(), page("Nov 9 at Beacon Theatre"), NOW).passed


def test_wrong_venue_fails():
    assert not check_event(make_event(), page("Nov 7 at Other Hall"), NOW).passed


def test_venue_match_ignores_case_and_punctuation():
    event = make_event(venue="St. Ann's Warehouse")
    assert check_event(event, page("Nov 7 at ST ANNS WAREHOUSE"), NOW).passed


def test_dead_link_fails():
    assert check_event(make_event(), PageResult(404, "", "", False), NOW).reason == "page did not open"


def test_past_uses_new_york_time():
    event = make_event(start=datetime(2026, 11, 7, 20, tzinfo=NY))
    assert not check_event(event, GOOD_PAGE, datetime(2026, 11, 7, 21, tzinfo=NY)).passed


def test_late_tonight_still_upcoming():
    event = make_event(start=datetime(2026, 11, 7, 23, tzinfo=NY))
    assert check_event(event, GOOD_PAGE, datetime(2026, 11, 7, 21, tzinfo=NY)).passed


def test_outside_nyc_fails():
    assert not check_event(make_event(city="Newark"), GOOD_PAGE, NOW).passed


def test_blocked_web_page_fails():
    assert not check_event(make_event(), PageResult(None, "", "", True), NOW).passed


def test_api_event_passes_when_blocked():
    assert check_event(api_event(), PageResult(None, "", "", True), NOW).passed


def test_api_event_passes_when_network_is_down():
    assert check_event(api_event(), PageResult(None, "", "", False), NOW).passed


def test_api_event_fails_on_404():
    assert not check_event(api_event(), PageResult(404, "", "", False), NOW).passed


def test_api_event_does_not_need_matching_page_text():
    assert check_event(api_event(), page("Loading..."), NOW).passed


def ev(name, day=7, venue="Madison Square Garden"):
    return make_event(name=name, venue=venue, start=datetime(2026, 11, day, 20, tzinfo=NY))


def test_same_event_across_names():
    assert same_event(ev("New York Knicks vs. Boston Celtics"), ev("Knicks v Celtics"))


def test_different_nights_not_same():
    assert not same_event(ev("Show", day=7), ev("Show", day=8))


def test_different_events_same_venue_not_same():
    assert not same_event(ev("Knicks vs Celtics"), ev("Billy Joel in Concert"))


def test_venue_spelling_variation_still_same():
    assert same_event(ev("Knicks vs Celtics", venue="The Madison Square Garden"), ev("Knicks v Celtics"))


def test_same_month_day_in_another_year_does_not_match():
    assert not date_appears("November 7, 2025", datetime(2026, 11, 7, 20, tzinfo=NY))
