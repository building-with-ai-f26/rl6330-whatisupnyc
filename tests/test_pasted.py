from datetime import datetime

import httpx

from app.ai import AIUnavailable
from app.sources.pasted import process_paste
from app.sources.web import SearchHit
from tests.conftest import NY
from tests.fakes import FakeAI, FakeTavily

NOW = datetime(2026, 11, 2, 12, tzinfo=NY)
TIKTOK = "https://www.tiktok.com/@nyc/video/1"
RED = "https://www.xiaohongshu.com/explore/1"
GOOD_PAGE = "<p>Winter Jazz Night on Sat, Nov 7 at Beacon Theatre</p>"
EXTRACTED = {"name": "Winter Jazz Night", "date": "2026-11-07", "time": "20:00", "venue": "Beacon Theatre",
             "city": "New York", "organizer": None}
HIT = SearchHit("https://reliable.example/jazz", "t", "c")


def http_with_log(page=GOOD_PAGE):
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    return httpx.Client(transport=httpx.MockTransport(handler)), hosts


def paste(sites, link=TIKTOK, caption="Winter Jazz Night, Nov 7, Beacon Theatre", ai=None, hits=(HIT,), page=GOOD_PAGE):
    http, hosts = http_with_log(page)
    result = process_paste(link, caption, FakeTavily(hits), http, sites, ai or FakeAI(dict(EXTRACTED)), NOW)
    return result, hosts


def test_paste_never_fetches_the_social_url(sites):
    _, hosts = paste(sites)
    assert "www.tiktok.com" not in hosts


def test_paste_uses_reliable_page_url(sites):
    result, _ = paste(sites)
    assert result.event.url == "https://reliable.example/jazz"
    assert result.event.source_type == "pasted"
    assert "tiktok" not in result.event.source


def test_search_result_on_social_site_is_never_fetched(sites):
    result, hosts = paste(sites, hits=[SearchHit(TIKTOK, "t", "c")])
    assert result.event is None
    assert "www.tiktok.com" not in hosts


def test_paste_with_no_date_gives_clear_message(sites):
    ai = FakeAI({**EXTRACTED, "date": None})
    result, _ = paste(sites, link=RED, caption="很棒的展览", ai=ai)
    assert result.event is None
    assert "date" in result.message


def test_chinese_caption_with_details_works(sites):
    result, _ = paste(sites, link=RED, caption="11月7日 Beacon Theatre 音乐会")
    assert result.event.name == "Winter Jazz Night"


def test_paste_with_no_reliable_page_dropped(sites):
    result, _ = paste(sites, hits=[])
    assert result.event is None
    assert "reliable page" in result.message


def test_invented_date_fails_the_page_check(sites):
    ai = FakeAI({**EXTRACTED, "date": "2026-11-20"})
    assert paste(sites, ai=ai)[0].event is None


def test_page_about_a_different_event_is_rejected(sites):
    result, _ = paste(sites, page="<p>Tax Seminar on Sat, Nov 7 at Beacon Theatre</p>")
    assert result.event is None


def test_blank_caption_asks_for_details_without_ai_call(sites):
    ai = FakeAI()
    result, _ = paste(sites, caption="   ", ai=ai)
    assert result.event is None
    assert "caption" in result.message
    assert ai.calls == 0


def test_bad_link_is_rejected(sites):
    assert "link" in paste(sites, link="not a link")[0].message


def test_ai_down_gives_clear_message(sites):
    result, _ = paste(sites, ai=FakeAI(error=AIUnavailable("busy")))
    assert result.event is None
    assert "try again" in result.message


def test_event_confirmed_on_calendar_page_search_missed(sites):
    sites._entries["reliable.example"]["calendar_url"] = "https://reliable.example/today"
    result, hosts = paste(sites, hits=[])
    assert result.event.url == "https://reliable.example/today"


def test_search_hits_are_tried_before_calendar_pages(sites):
    sites._entries["reliable.example"]["calendar_url"] = "https://reliable.example/today"
    assert paste(sites)[0].event.url == "https://reliable.example/jazz"
