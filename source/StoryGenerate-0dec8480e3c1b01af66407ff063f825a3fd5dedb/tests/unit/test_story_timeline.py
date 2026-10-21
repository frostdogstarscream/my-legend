"""Grounded schedule comparisons; model pass alone cannot bypass a mismatch."""

import pytest
from drama_engine.workflow.timeline import clock_values, timeline_errors
from drama_engine.workflow.schemas import (
    EpisodeDelivery,
    SceneDelivery,
    Utterance,
    TimelineClaim,
)


def episode(lines):
    return EpisodeDelivery(
        episode_id="ep",
        index=1,
        title="时间核验",
        duration_target_sec=120,
        scenes=[
            SceneDelivery(
                scene_id="scene",
                title="播音",
                utterances=[
                    Utterance(line_id=str(i), kind="narration", text=text)
                    for i, text in enumerate(lines)
                ],
            )
        ],
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("六点早新闻，五点前必须拿下", {"06:00", "05:00"}),
        ("四点五十了，五点整播片头", {"04:50", "05:00"}),
        ("凌晨一点半，下午两点一刻", {"01:30", "14:15"}),
        ("06:00开始，05:30完成", {"06:00", "05:30"}),
        ("一点杂音都没有，调大一点，记住这两点建议", set()),
        ("明白。一点一点的……好，推到位了。把一点一滴都记下来。", set()),
        ("凌晨一点一分，准时验收。", {"01:01"}),
        ("总共四点，记住这三点。现在只有五点整。", {"05:00"}),
        ("下午一点、晚上九点、凌晨十二点", {"13:00", "21:00", "00:00"}),
        ("只要还没到零点，我就得查清楚。", {"00:00"}),
        ("一点杂音也没有，电压零点五伏。", set()),
    ],
)
def test_clock_extraction_distinguishes_clock_and_quantity(text, expected):
    assert clock_values(text) == expected


def test_schedule_actual_conflict_is_blocked_while_deadline_is_distinct():
    ep = episode(["明早六点早新闻，五点前必须拿下。", "五点整。片头曲进来了。"])
    claims = [
        TimelineClaim(
            event_id="news",
            kind="scheduled",
            normalized_time="06:00",
            line_id="0",
            quote="六点早新闻",
        ),
        TimelineClaim(
            event_id="repair",
            kind="deadline",
            normalized_time="05:00",
            line_id="0",
            quote="五点前必须拿下",
        ),
        TimelineClaim(
            event_id="news",
            kind="actual",
            normalized_time="05:00",
            line_id="1",
            quote="五点整。片头曲进来了。",
        ),
    ]
    errors = timeline_errors(claims, [ep])
    assert len(errors) == 1 and "TIMELINE_EVENT_TIME_CONFLICT" in errors[0]


def test_explicit_change_is_allowed_but_unexplained_change_is_not():
    ep = episode(["六点早新闻。", "通知街坊，改为五点播新闻。", "五点整，片头响了。"])
    claims = [
        TimelineClaim(
            event_id="news",
            kind="scheduled",
            normalized_time="06:00",
            line_id="0",
            quote="六点早新闻",
        ),
        TimelineClaim(
            event_id="news",
            kind="explicit_change",
            normalized_time="05:00",
            line_id="1",
            quote="改为五点播新闻",
        ),
        TimelineClaim(
            event_id="news",
            kind="actual",
            normalized_time="05:00",
            line_id="2",
            quote="五点整，片头响了。",
        ),
    ]
    assert not timeline_errors(claims, [ep])


def test_missing_and_fabricated_time_claims_fail():
    ep = episode(["五点整，片头进来了。"])
    assert "MISSING" in timeline_errors([], [ep])[0]
    claim = TimelineClaim(
        event_id="news",
        kind="actual",
        normalized_time="06:00",
        line_id="0",
        quote="五点整",
    )
    assert "UNGROUNDED" in timeline_errors([claim], [ep])[0]


def test_clock_coverage_targets_include_repetition_and_multiple_times_without_verdicts():
    from drama_engine.workflow.timeline import required_clock_mentions

    ep = episode(
        [
            "明早八点验收。",
            "八点整敲铃。",
            "十二点五分测量，十二点八分校准。",
            "一点杂音没有。",
        ]
    )
    targets = required_clock_mentions([ep])
    assert [(t["line_id"], t["normalized_times"]) for t in targets] == [
        ("0", ["08:00"]),
        ("1", ["08:00"]),
        ("2", ["12:05", "12:08"]),
    ]
    assert all("event_id" not in t and "passed" not in t for t in targets)


def test_midnight_deadline_from_real_remix_is_grounded_and_requires_coverage():
    text = "我是维修员。只要还没到零点，我就得查清楚。去拿万用表。"
    ep = episode([text])
    claim = TimelineClaim(
        event_id="investigation_deadline",
        kind="deadline",
        normalized_time="00:00",
        line_id="0",
        quote=text,
    )
    assert not timeline_errors([claim], [ep])
    assert "MISSING" in timeline_errors([], [ep])[0]
