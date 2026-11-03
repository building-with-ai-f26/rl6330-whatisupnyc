from datetime import datetime

import pytest

from app.ai import AIUnavailable
from app.models import Pick, Score, ScoredEvent, Subscriber
from app.newsletter import choose_picks, confirm_picks, render_newsletter
from tests.conftest import NY, make_event
from tests.fakes import FakeAI

NOW = datetime(2026, 11, 2, 8, tzinfo=NY)


def choose(*args, **kwargs):
    return choose_picks(*args, now=NOW, **kwargs)


def render(picks):
    return render_newsletter(picks, now=NOW)


SUB = Subscriber(7, "Ana", "ana@x.test", "jazz and free things", "keep it short", ["Fri"])


def scored(id_, score=40, **overrides):
    return ScoredEvent(id_, make_event(name=f"Event {id_}", **overrides), Score(score, True, None))


EVENTS = [scored(1), scored(2)]


def test_invented_id_removed():
    assert confirm_picks([{"id": 999, "reason": "x"}], EVENTS) == []


def test_duplicate_id_kept_once():
    assert len(confirm_picks([{"id": 1, "reason": "a"}, {"id": 1, "reason": "b"}], EVENTS)) == 1


def test_malformed_picks_ignored():
    assert confirm_picks(["nope", {"reason": "no id"}, {"id": "1"}], EVENTS) == []


@pytest.mark.parametrize("word", ["TikTok", "Instagram", "RedNote", "Xiaohongshu", "小红书", "Luma"])
def test_reason_naming_a_discovery_source_replaced(word):
    reason = confirm_picks([{"id": 1, "reason": f"Viral on {word}"}], EVENTS)[0].reason
    assert word.lower() not in reason.lower()
    assert reason


def test_blank_reason_replaced():
    assert confirm_picks([{"id": 1, "reason": ""}], EVENTS)[0].reason


def test_prompt_has_no_name_or_email():
    ai = FakeAI({"picks": []})
    choose(SUB, EVENTS, ai)
    assert "Ana" not in ai.prompts[0]
    assert "ana@x.test" not in ai.prompts[0]


def test_prompt_includes_interests_and_look():
    ai = FakeAI({"picks": []})
    choose(SUB, EVENTS, ai)
    assert "jazz and free things" in ai.prompts[0]
    assert "keep it short" in ai.prompts[0]


def test_prompt_lists_higher_scores_first():
    ai = FakeAI({"picks": []})
    choose(SUB, [scored(1, score=10), scored(2, score=40)], ai)
    assert ai.prompts[0].index("Event 2") < ai.prompts[0].index("Event 1")


def test_prompt_does_not_reveal_score_to_model():
    ai = FakeAI({"picks": []})
    choose(SUB, [scored(1, score=37)], ai)
    assert "37" not in ai.prompts[0]


def test_already_sent_excluded_by_choose():
    ai = FakeAI({"picks": [{"id": 1, "reason": "x"}]})
    picks = choose(SUB, EVENTS, ai, sent_ids={1})
    assert picks == []
    assert "Event 1" not in ai.prompts[0]


def test_no_events_makes_no_ai_call():
    ai = FakeAI()
    assert choose(SUB, [], ai) == []
    assert ai.calls == 0


def test_max_picks_enforced():
    ai = FakeAI({"picks": [{"id": 1, "reason": "a"}, {"id": 2, "reason": "b"}]})
    assert len(choose(SUB, EVENTS, ai, max_picks=1)) == 1


def test_ai_unavailable_propagates():
    with pytest.raises(AIUnavailable):
        choose(SUB, EVENTS, FakeAI(error=AIUnavailable("busy")))


def pick(id_=1, **overrides):
    return Pick(scored(id_, **overrides), "A good fit for you")


def test_short_issue_says_so():
    assert "only" in render([pick()]).lower()


def test_full_issue_does_not_apologize():
    html = render([pick(1), pick(2), pick(3)]).lower()
    assert "only" not in html


def test_empty_issue_says_so():
    assert "no new picks" in render([]).lower()


def test_pick_shows_name_date_venue_reason_link():
    html = render([pick(url="https://reliable.example/e/9")])
    for expected in ["Event 1", "Sat, Nov 7", "8:00 PM", "Beacon Theatre", "A good fit for you", "https://reliable.example/e/9"]:
        assert expected in html


def test_reader_never_sees_internals():
    html = render([pick(), pick(2)]).lower()
    assert not any(word in html for word in ["score", "sentiment", "unknown", "credibility"])


def test_time_not_stated_is_hidden():
    html = render([pick(start=datetime(2026, 11, 7, 23, 59, tzinfo=NY))])
    assert "11:59" not in html
    assert "Sat, Nov 7" in html


def test_time_is_shown_in_new_york_time():
    start = datetime.fromisoformat("2026-11-08T01:00:00+00:00")
    assert "Sat, Nov 7" in render([pick(start=start)])


def test_luma_link_allowed_when_only_page():
    assert "lu.ma/x" in render([pick(url="https://lu.ma/x")])


def test_event_text_is_escaped():
    html = render([Pick(ScoredEvent(1, make_event(name="<script>x</script>"), Score(40, True)), "fit")])
    assert "<script>x" not in html
    assert "&lt;script&gt;" in html


def test_unsafe_link_is_not_rendered():
    assert "javascript:" not in render([pick(url="javascript:alert(1)")])


def test_invites_reply_by_email():
    assert "reply" in render([pick()]).lower()


def test_issue_carries_the_wordmark_as_text_for_email():
    html = render([pick()])
    assert "whatisupnyc" in html
    assert "What's up, NYC" not in html
    assert "<svg" not in html


def test_pick_reads_date_at_venue():
    assert "Sat, Nov 7, 8:00 PM at Beacon Theatre" in render([pick()])


TODAY = datetime(2026, 11, 2, 19, tzinfo=NY)
LATER = datetime(2026, 11, 9, 19, tzinfo=NY)


def order_of(html, *titles):
    return [html.index(title) for title in titles]


def test_sections_appear_in_order():
    html = render([pick(1, score=10, start=LATER), pick(2, score=40, start=LATER), pick(3, score=10, start=TODAY)])
    positions = order_of(html, "Happening today", "Popular", "Upcoming")
    assert positions == sorted(positions)


def test_today_uses_new_york_date():
    late_utc = datetime.fromisoformat("2026-11-03T01:00:00+00:00")
    html = render([pick(1, score=10, start=late_utc), pick(2, score=10, start=LATER), pick(3, score=10, start=LATER)])
    assert html.index("Happening today") < html.index("Event 1") < html.index("Upcoming")


def test_popular_event_today_goes_in_today_only():
    html = render([pick(1, score=40, start=TODAY), pick(2, score=10, start=LATER), pick(3, score=10, start=LATER)])
    assert "Popular" not in html
    assert html.count("Event 1") == 1


def test_lower_score_goes_in_upcoming():
    html = render([pick(1, score=39, start=LATER), pick(2, score=40, start=LATER), pick(3, score=40, start=LATER)])
    assert html.index("Popular") < html.index("Event 2") < html.index("Upcoming") < html.index("Event 1")


def test_empty_sections_are_left_out():
    html = render([pick(1, score=10, start=LATER), pick(2, score=10, start=LATER), pick(3, score=10, start=LATER)])
    assert "Happening today" not in html
    assert "Popular" not in html


def test_each_section_is_in_date_order():
    html = render([pick(1, score=10, start=LATER), pick(2, score=10, start=datetime(2026, 11, 5, 19, tzinfo=NY)), pick(3, score=10, start=LATER)])
    assert html.index("Event 2") < html.index("Event 1")


def test_issue_shows_todays_date():
    assert "Monday, November 2" in render([pick()])


def test_checker_stripe_uses_cab_colors():
    html = render([pick()]).lower()
    assert "#ffc20e" in html and "#000000" in html


def test_prompt_marks_events_happening_today():
    ai = FakeAI({"picks": []})
    choose(SUB, [scored(1, start=TODAY), scored(2, start=LATER)], ai)
    assert '"today": true' in ai.prompts[0]
    assert '"today": false' in ai.prompts[0]


def test_same_show_on_two_nights_keeps_the_earlier_night():
    events = [
        ScoredEvent(1, make_event(name="Hamilton", start=LATER), Score(20, True)),
        ScoredEvent(2, make_event(name="Hamilton", start=TODAY), Score(20, True)),
    ]
    picks = confirm_picks([{"id": 1, "reason": "a"}, {"id": 2, "reason": "b"}], events)
    assert [pick.event.id for pick in picks] == [2]


def test_default_issue_allows_eight_picks():
    many = [scored(i) for i in range(1, 11)]
    ai = FakeAI({"picks": [{"id": i, "reason": "x"} for i in range(1, 11)]})
    assert len(choose(SUB, many, ai)) == 8


def test_instructions_forbid_unsupported_claims_and_repeating_the_date():
    from app.newsletter import CHOOSE_SYSTEM

    assert "Never claim how popular or loved an event is" in CHOOSE_SYSTEM
    assert "Don't repeat the date" in CHOOSE_SYSTEM
