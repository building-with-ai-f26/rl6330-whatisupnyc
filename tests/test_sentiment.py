from app.ai import AIUnavailable
from app.scoring import Entry
from app.sentiment import Opinion, classify_opinions, gather_opinions, rate_sentiment
from app.sources.web import SearchHit
from tests.conftest import make_event
from tests.fakes import FakeAI, FakeTavily

TWO_OPS = [Opinion("a.test", "A wonderful and joyful night."), Opinion("b.test", "Loved every minute of it.")]


def test_opinions_exclude_own_page_and_organizer_domain(sites):
    event = make_event(url="https://reliable.example/e")
    tavily = FakeTavily(
        [
            SearchHit("https://reliable.example/e", "own", "from the event page"),
            SearchHit("https://www.reliable.example/review", "same domain", "from the same site"),
        ]
    )
    assert gather_opinions(event, tavily, sites, None) == []


def test_opinions_exclude_official_domain(sites):
    event = make_event(url="https://listing.example/e")
    tavily = FakeTavily([SearchHit("https://reliable.example/x", "t", "official words")])
    entry = Entry("Acme", [], "reliable.example")
    assert gather_opinions(event, tavily, sites, entry) == []


def test_opinions_exclude_listing_sites(sites):
    event = make_event(url="https://reliable.example/e")
    tavily = FakeTavily([SearchHit("https://listing.example/x", "t", "buy tickets")])
    assert gather_opinions(event, tavily, sites, None) == []


def test_opinions_keep_outside_reliable_source(sites):
    event = make_event(url="https://listing.example/e")
    tavily = FakeTavily([SearchHit("https://reliable.example/review", "t", "A great night")])
    assert gather_opinions(event, tavily, sites, None) == [Opinion("reliable.example", "A great night")]


def test_fewer_than_two_domains_is_unknown_without_ai_call():
    ai = FakeAI()
    assert rate_sentiment([Opinion("a.test", "good")], ai) == "unknown"
    assert ai.calls == 0


def test_two_opinions_from_one_domain_is_unknown():
    ai = FakeAI()
    assert rate_sentiment([Opinion("a.test", "good"), Opinion("a.test", "great")], ai) == "unknown"
    assert ai.calls == 0


def test_rating_with_real_quote_is_kept():
    ai = FakeAI({"rating": "positive", "quote": "wonderful and joyful"})
    assert rate_sentiment(TWO_OPS, ai) == "positive"


def test_quote_match_ignores_case_and_spacing():
    ai = FakeAI({"rating": "positive", "quote": "WONDERFUL  and joyful"})
    assert rate_sentiment(TWO_OPS, ai) == "positive"


def test_rating_with_fake_quote_is_unknown():
    ai = FakeAI({"rating": "negative", "quote": "a total disaster"})
    assert rate_sentiment(TWO_OPS, ai) == "unknown"


def test_invalid_rating_is_unknown():
    assert rate_sentiment(TWO_OPS, FakeAI({"rating": "great", "quote": "wonderful"})) == "unknown"


def test_missing_quote_is_unknown():
    assert rate_sentiment(TWO_OPS, FakeAI({"rating": "positive"})) == "unknown"


def test_ai_unavailable_is_unknown():
    assert rate_sentiment(TWO_OPS, FakeAI(error=AIUnavailable("busy"))) == "unknown"


def test_classify_skips_the_two_source_rule_for_evaluation():
    ai = FakeAI({"rating": "mixed", "quote": "good"})
    assert classify_opinions([Opinion("a.test", "good and bad")], ai) == "mixed"
