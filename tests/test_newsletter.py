from datetime import datetime

import pytest

from app.ai import AIUnavailable
from app.models import Pick, Score, ScoredEvent, Subscriber
from app.newsletter import choose_picks, confirm_picks, render_newsletter
from tests.conftest import NY, make_event
from tests.fakes import FakeAI

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
    choose_picks(SUB, EVENTS, ai)
    assert "Ana" not in ai.prompts[0]
    assert "ana@x.test" not in ai.prompts[0]


def test_prompt_includes_interests_and_look():
    ai = FakeAI({"picks": []})
    choose_picks(SUB, EVENTS, ai)
    assert "jazz and free things" in ai.prompts[0]
    assert "keep it short" in ai.prompts[0]


def test_prompt_lists_higher_scores_first():
    ai = FakeAI({"picks": []})
    choose_picks(SUB, [scored(1, score=10), scored(2, score=40)], ai)
    assert ai.prompts[0].index("Event 2") < ai.prompts[0].index("Event 1")


def test_prompt_does_not_reveal_score_to_model():
    ai = FakeAI({"picks": []})
    choose_picks(SUB, [scored(1, score=37)], ai)
    assert "37" not in ai.prompts[0]


def test_already_sent_excluded_by_choose():
    ai = FakeAI({"picks": [{"id": 1, "reason": "x"}]})
    picks = choose_picks(SUB, EVENTS, ai, sent_ids={1})
    assert picks == []
    assert "Event 1" not in ai.prompts[0]


def test_no_events_makes_no_ai_call():
    ai = FakeAI()
    assert choose_picks(SUB, [], ai) == []
    assert ai.calls == 0


def test_max_picks_enforced():
    ai = FakeAI({"picks": [{"id": 1, "reason": "a"}, {"id": 2, "reason": "b"}]})
    assert len(choose_picks(SUB, EVENTS, ai, max_picks=1)) == 1


def test_ai_unavailable_propagates():
    with pytest.raises(AIUnavailable):
        choose_picks(SUB, EVENTS, FakeAI(error=AIUnavailable("busy")))


def pick(id_=1, **overrides):
    return Pick(scored(id_, **overrides), "A good fit for you")


def test_short_issue_says_so():
    assert "only" in render_newsletter([pick()]).lower()


def test_full_issue_does_not_apologize():
    html = render_newsletter([pick(1), pick(2), pick(3)]).lower()
    assert "only" not in html


def test_empty_issue_says_so():
    assert "no new picks" in render_newsletter([]).lower()


def test_pick_shows_name_date_venue_reason_link():
    html = render_newsletter([pick(url="https://reliable.example/e/9")])
    for expected in ["Event 1", "Sat, Nov 7", "8:00 PM", "Beacon Theatre", "A good fit for you", "https://reliable.example/e/9"]:
        assert expected in html


def test_reader_never_sees_internals():
    html = render_newsletter([pick(), pick(2)]).lower()
    assert not any(word in html for word in ["score", "sentiment", "unknown", "credibility"])


def test_time_not_stated_is_hidden():
    html = render_newsletter([pick(start=datetime(2026, 11, 7, 23, 59, tzinfo=NY))])
    assert "11:59" not in html
    assert "Sat, Nov 7" in html


def test_time_is_shown_in_new_york_time():
    start = datetime.fromisoformat("2026-11-08T01:00:00+00:00")
    assert "Sat, Nov 7" in render_newsletter([pick(start=start)])


def test_luma_link_allowed_when_only_page():
    assert "lu.ma/x" in render_newsletter([pick(url="https://lu.ma/x")])


def test_event_text_is_escaped():
    html = render_newsletter([Pick(ScoredEvent(1, make_event(name="<script>x</script>"), Score(40, True)), "fit")])
    assert "<script>x" not in html
    assert "&lt;script&gt;" in html


def test_unsafe_link_is_not_rendered():
    assert "javascript:" not in render_newsletter([pick(url="javascript:alert(1)")])


def test_invites_reply_by_email():
    assert "reply" in render_newsletter([pick()]).lower()


def test_issue_carries_the_wordmark_as_text_for_email():
    html = render_newsletter([pick()])
    assert "whatisupnyc" in html
    assert "What's up, NYC" not in html
    assert "<svg" not in html


def test_pick_reads_date_at_venue():
    assert "Sat, Nov 7, 8:00 PM at Beacon Theatre" in render_newsletter([pick()])
