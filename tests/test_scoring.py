import json

from app.models import Signals
from app.pages import PageResult
from app.scoring import Entry, Reputable, brand_confirmed, load_reputable, reliable_domains, score
from tests.conftest import make_event

FOUR_DOMAINS = {"a.test", "b.test", "c.test", "d.test"}
BRAND = Entry("Acme", ["Acme Live"], "acme.example")


def sig(**overrides):
    return Signals(**overrides)


def test_reputable_event_scores_40():
    assert score(sig(reputable_match=True, brand_confirmed=True)).total == 40


def test_past_coverage_earns_25():
    assert score(sig(past_coverage_count=3)).total == 25


def test_two_past_events_earn_nothing():
    assert not score(sig(past_coverage_count=2)).eligible


def test_no_credibility_is_excluded():
    assert not score(sig()).eligible


def test_unconfirmed_brand_is_excluded():
    assert not score(sig(reputable_match=True, brand_confirmed=False)).eligible


def test_coverage_only_is_eligible():
    result = score(sig(coverage_domains={"timeout.com"}))
    assert (result.total, result.eligible) == (10, True)


def test_coverage_caps_at_30():
    assert score(sig(coverage_domains=FOUR_DOMAINS)).total == 30


def test_interest_caps_at_20():
    assert score(sig(coverage_domains={"a.test"}, pasted_post_count=9)).total == 30


def test_interest_alone_is_excluded():
    assert not score(sig(pasted_post_count=4)).eligible


def test_sentiment_points():
    base = dict(coverage_domains={"a.test"})
    assert score(sig(sentiment="positive", **base)).total == 20
    assert score(sig(sentiment="mixed", **base)).total == 15


def test_negative_is_excluded():
    assert not score(sig(reputable_match=True, brand_confirmed=True, sentiment="negative")).eligible


def test_unknown_still_eligible_with_no_points():
    result = score(sig(coverage_domains={"timeout.com"}, sentiment="unknown"))
    assert (result.total, result.eligible) == (10, True)


def test_luma_only_unknown_host_excluded(sites):
    domains = reliable_domains(["https://listing.example/e/1"], sites)
    assert domains == set()
    assert not score(sig(coverage_domains=domains)).eligible


def test_reliable_domains_merge_subdomains(sites):
    urls = ["https://www.reliable.example/a", "https://reliable.example/b"]
    assert reliable_domains(urls, sites) == {"reliable.example"}


def reputable():
    return Reputable([BRAND, Entry("BAM", [], "bam.example")])


def test_match_on_organizer_alias():
    assert reputable().match(make_event(organizer="acme live")).name == "Acme"


def test_match_on_venue():
    assert reputable().match(make_event(venue="BAM Howard Gilman Opera House")).name == "BAM"


def test_short_name_does_not_match_inside_a_word():
    assert reputable().match(make_event(venue="Bamboo Lounge")) is None


def test_no_match_returns_none():
    assert reputable().match(make_event()) is None


def test_load_reputable_reads_file(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps([{"name": "Acme", "aliases": [], "official_domain": "acme.example"}]))
    assert load_reputable(str(path)).match(make_event(organizer="Acme")).official_domain == "acme.example"


def test_shipped_reputable_list_loads():
    assert load_reputable().match(make_event(venue="Carnegie Hall")).name == "Carnegie Hall"


def page(text):
    return PageResult(200, text, "", False)


def test_api_event_confirms_brand_directly():
    assert brand_confirmed(make_event(source_type="api", organizer="Acme"), BRAND, [])


def test_official_domain_confirms_brand():
    assert brand_confirmed(make_event(url="https://www.acme.example/e", organizer="Acme"), BRAND, [])


def test_name_dropped_on_listing_is_not_confirmed():
    event = make_event(url="https://listing.example/e", organizer="Acme")
    assert not brand_confirmed(event, BRAND, [])


def test_reliable_page_naming_brand_and_event_confirms():
    event = make_event(name="Winter Bash", url="https://listing.example/e", organizer="Acme")
    assert brand_confirmed(event, BRAND, [page("Acme hosts Winter Bash this week")])


def test_reliable_page_naming_only_the_brand_does_not_confirm():
    event = make_event(name="Winter Bash", url="https://listing.example/e", organizer="Acme")
    assert not brand_confirmed(event, BRAND, [page("Acme opens a new store")])


def test_one_word_event_name_cannot_confirm_a_brand():
    event = make_event(name="Show", url="https://listing.example/e", organizer="Acme")
    assert not brand_confirmed(event, BRAND, [page("Acme news. A show opened nearby.")])


def test_empty_page_cannot_confirm_a_brand():
    event = make_event(name="Winter Bash", url="https://listing.example/e", organizer="Acme")
    assert not brand_confirmed(event, BRAND, [page("")])


def test_brand_far_from_event_name_does_not_confirm():
    event = make_event(name="Winter Bash", url="https://listing.example/e", organizer="Acme")
    filler = "unrelated words " * 100
    assert not brand_confirmed(event, BRAND, [page(f"Acme opened a store. {filler} Winter Bash is on.")])
