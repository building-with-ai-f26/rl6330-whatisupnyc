import json
from datetime import datetime

import httpx

from app.ai import AIUnavailable
from app.sources.web import (
    SearchHit,
    TavilyClient,
    collect_web_events,
    extract_with_ai,
    read_schema_events,
)
from tests.conftest import NY
from tests.fakes import FakeAI, FakeTavily

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
JSONLD = json.dumps(
    {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "name": "Schema Show",
        "startDate": "2026-11-07T20:00:00-05:00",
        "location": {"name": "Beacon Theatre", "address": {"addressLocality": "New York"}},
        "organizer": {"name": "Live Nation"},
    }
)
HTML_WITH_JSONLD = f'<script type="application/ld+json">{JSONLD}</script><p>Nov 7 Beacon Theatre</p>'


def test_reads_schema_org_event():
    event = read_schema_events(HTML_WITH_JSONLD, "https://x.test/e", "x")[0]
    assert (event.name, event.venue, event.city, event.organizer) == (
        "Schema Show", "Beacon Theatre", "New York", "Live Nation",
    )
    assert event.url == "https://x.test/e"
    assert event.source_type == "web"


def test_no_schema_returns_empty():
    assert read_schema_events("<p>hi</p>", "https://x.test", "x") == []


def test_broken_json_ld_returns_empty():
    html = '<script type="application/ld+json">{not json</script>'
    assert read_schema_events(html, "https://x.test", "x") == []


def test_schema_date_without_time_zone_uses_new_york():
    html = HTML_WITH_JSONLD.replace("2026-11-07T20:00:00-05:00", "2026-11-07T20:00:00")
    assert read_schema_events(html, "https://x.test", "x")[0].start == datetime(2026, 11, 7, 20, tzinfo=NY)


def test_schema_event_without_location_skipped():
    html = '<script type="application/ld+json">{"@type":"Event","name":"A","startDate":"2026-11-07"}</script>'
    assert read_schema_events(html, "https://x.test", "x") == []


def ai_event(**overrides):
    fields = {"page": 0, "name": "AI Show", "date": "2026-11-07", "time": "20:00", "venue": "The Shed",
              "city": "New York", "organizer": None, "category": "Art"}
    fields.update(overrides)
    return fields


def test_ai_extraction_batches_pages_into_one_request():
    ai = FakeAI({"events": [ai_event()]})
    events = extract_with_ai([("https://a.test", "text a"), ("https://b.test", "text b")], ai)
    assert ai.calls == 1
    assert events[0].url == "https://a.test"
    assert events[0].start == datetime(2026, 11, 7, 20, tzinfo=NY)


def test_more_than_ten_pages_split_into_batches():
    ai = FakeAI({"events": []}, {"events": []})
    extract_with_ai([(f"https://a.test/{i}", "t") for i in range(11)], ai)
    assert ai.calls == 2


def test_ai_event_missing_date_or_venue_discarded():
    ai = FakeAI({"events": [ai_event(date=None), ai_event(venue=""), ai_event(date="soon")]})
    assert extract_with_ai([("https://a.test", "t")], ai) == []


def test_ai_event_with_bad_page_number_discarded():
    assert extract_with_ai([("https://a.test", "t")], FakeAI({"events": [ai_event(page=5)]})) == []


def test_missing_time_uses_end_of_day_marker():
    event = extract_with_ai([("https://a.test", "t")], FakeAI({"events": [ai_event(time=None)]}))[0]
    assert (event.start.hour, event.start.minute) == (23, 59)


def http_for(pages):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages.get(str(request.url), "<p>plain page</p>"))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ai_failure_keeps_schema_events(sites):
    tavily = FakeTavily(
        [SearchHit("https://reliable.example/schema", "t", "c"), SearchHit("https://reliable.example/plain", "t", "c")]
    )
    http = http_for({"https://reliable.example/schema": HTML_WITH_JSONLD})
    events = collect_web_events(tavily, http, sites, FakeAI(error=AIUnavailable("busy")), NOW)
    assert [e.name for e in events] == ["Schema Show"]


def test_pages_with_schema_are_not_sent_to_ai(sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/schema", "t", "c")])
    http = http_for({"https://reliable.example/schema": HTML_WITH_JSONLD})
    ai = FakeAI()
    collect_web_events(tavily, http, sites, ai, NOW)
    assert ai.calls == 0


def test_hit_on_disallowed_site_is_skipped(sites):
    tavily = FakeTavily([SearchHit("https://blocked.example/e", "t", "c")])
    ai = FakeAI()
    assert collect_web_events(tavily, http_for({}), sites, ai, NOW) == []
    assert ai.calls == 0


def test_same_page_from_two_searches_read_once(sites):
    tavily = FakeTavily([SearchHit("https://reliable.example/schema", "t", "c")])
    http = http_for({"https://reliable.example/schema": HTML_WITH_JSONLD})
    assert len(collect_web_events(tavily, http, sites, FakeAI(), NOW)) == 1


def test_search_uses_only_allowed_domains(sites):
    tavily = FakeTavily()
    collect_web_events(tavily, http_for({}), sites, FakeAI(), NOW)
    assert tavily.queries
    assert "blocked.example" not in tavily.include_domains
    assert "reliable.example" in tavily.include_domains


def test_tavily_budget_stops_searches():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [{"url": "https://a.test", "title": "t", "content": "c"}]})

    client = TavilyClient(httpx.Client(transport=httpx.MockTransport(handler)), "k", run_budget=2)
    assert len(client.search("q", [])) == 1
    client.search("q", [])
    assert client.search("q", []) == []
    assert len(calls) == 2


def test_tavily_error_returns_empty():
    client = TavilyClient(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(432))), "k")
    assert client.search("q", []) == []


def test_schema_event_with_non_text_fields_is_skipped():
    html = '<script type="application/ld+json">{"@type":"Event","name":{"x":1},"startDate":"2026-11-07","location":{"name":"V"}}</script>'
    assert read_schema_events(html, "https://x.test", "x") == []


def test_schema_non_text_city_and_organizer_are_ignored():
    html = HTML_WITH_JSONLD.replace('"addressLocality": "New York"', '"addressLocality": 5').replace(
        '"name": "Live Nation"', '"name": ["x"]'
    )
    event = read_schema_events(html, "https://x.test", "x")[0]
    assert (event.city, event.organizer) == ("", None)


def test_ai_event_with_non_text_fields_discarded():
    ai = FakeAI({"events": [ai_event(venue={"x": 1}), ai_event(name=5), "junk"]})
    assert extract_with_ai([("https://a.test", "t")], ai) == []


def test_ai_events_not_a_list_is_ignored():
    assert extract_with_ai([("https://a.test", "t")], FakeAI({"events": "none"})) == []


def test_ai_non_text_optional_fields_become_none():
    event = extract_with_ai(
        [("https://a.test", "t")], FakeAI({"events": [ai_event(organizer=["x"], category=9, city=4)]})
    )[0]
    assert (event.organizer, event.category, event.city) == (None, None, "")


def test_very_long_text_is_capped():
    event = extract_with_ai([("https://a.test", "t")], FakeAI({"events": [ai_event(name="x" * 5000)]}))[0]
    assert len(event.name) <= 200


def test_sponsored_item_is_discarded():
    ai = FakeAI({"events": [ai_event(sponsored=True), ai_event(name="Real Show", sponsored=False)]})
    assert [e.name for e in extract_with_ai([("https://a.test", "t")], ai)] == ["Real Show"]


def test_extraction_prompt_asks_for_sponsored_flag():
    from app.sources.web import EXTRACT_SYSTEM

    assert '"sponsored"' in EXTRACT_SYSTEM
