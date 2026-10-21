"""Ground and compare clock claims within the existing one-call quality gate."""
from __future__ import annotations
import re

_CLOCK = re.compile(r"(?P<h>[零〇一二三四五六七八九十两\d]{1,3})(?:点(?P<m>一刻|三刻|半|[零〇一二三四五六七八九十两\d]{1,3})?(?:分)?|[:：](?P<colon>\d{2}))")
_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")}
_DIGITS.update({"两": 2, "〇": 0})
_PERIOD = re.compile(r"下午|傍晚|晚上|夜里|今晚|上午|中午|午夜|凌晨|明早|今早|早上")

def _number(value):
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    if value in ("半", "一刻", "三刻"):
        return {"半": 30, "一刻": 15, "三刻": 45}[value]
    if "十" in value:
        head, tail = value.split("十", 1)
        return 10 * (_DIGITS.get(head, 1) if head else 1) + (_DIGITS.get(tail, 0) if tail else 0)
    return _DIGITS.get(value, -1)

def clock_values(text, allow_bare=False):
    values = set()
    for match in _CLOCK.finditer(text):
        if re.match(r"一点(?:一点|一滴|点)", text[match.start():]):
            continue
        hour, minute = _number(match["h"]), _number(match["m"] or match["colon"])
        before, after = text[max(0, match.start() - 8):match.start()], text[match.end():match.end() + 8]
        if re.match(r"杂音|噪音|声音|要求|原因|建议|事项|意见|原则|细节|声|力|分贝|欧|伏|安|毫米|厘米", after):
            continue
        temporal_context = re.search(r"现在|时间|时刻|钟|午夜|凌晨|上午|下午|晚上|明早|今早|定在|约定|截止", before)
        temporal_suffix = re.match(r"整|钟|前|后|了|开播|播音|早新闻", after)
        if (not allow_bare and not match["colon"] and not temporal_context and not temporal_suffix
                and re.search(r"(?:这|那|有|总共|记住|列出|提出|分为|第)$", before)):
            continue
        if (not allow_bare and 0 < hour <= 2 and not match["m"] and not match["colon"]
                and not re.search(r"凌晨|午夜|上午|下午|中午|晚上|夜里|明早|明天|时刻|时间|截止|开播", before)
                and not re.match(r"整|钟|前|后|了", after)):
            continue
        periods = list(_PERIOD.finditer(before))
        if periods:
            period = periods[-1].group()
            if period in ("下午", "傍晚", "晚上", "夜里", "今晚") and 0 < hour < 12:
                hour += 12
            elif period in ("午夜", "凌晨") and hour == 12:
                hour = 0
        if 0 <= hour < 24 and 0 <= minute < 60:
            values.add(f"{hour:02d}:{minute:02d}")
    return values

def _face(time):
    hour, minute = map(int, time.split(":"))
    return (hour % 12, minute)

def _aliases(times, text):
    if _PERIOD.search(text):
        return times
    return times | {f"{(int(t[:2]) + 12) % 24:02d}:{t[3:]}" for t in times}

def _same_time(a, b):
    if a["normalized_time"] == b["normalized_time"]:
        return True
    return (_face(a["normalized_time"]) == _face(b["normalized_time"])
            and not (_PERIOD.search(a["quote"]) and _PERIOD.search(b["quote"])))

def required_clock_mentions(episodes):
    """Provide literal coverage targets without inventing event identity or verdicts."""
    return [{"line_id": u.line_id, "normalized_times": sorted(times), "text": u.text}
            for ep in episodes for scene in ep.scenes for u in scene.utterances
            if (times := clock_values(u.text))]


def timeline_errors(claims, episodes):
    lines = {u.line_id: u.text for ep in episodes for scene in ep.scenes for u in scene.utterances}
    order = {key: index for index, key in enumerate(lines)}
    errors, grounded, covered = [], [], set()
    for raw in claims:
        claim = raw.model_dump() if hasattr(raw, "model_dump") else raw
        line = lines.get(claim["line_id"], "")
        if (not line or claim["quote"] not in line
                or claim["normalized_time"] not in _aliases(clock_values(claim["quote"], allow_bare=True), claim["quote"])
                or claim["normalized_time"] not in _aliases(clock_values(line), line)):
            errors.append(f"TIMELINE_UNGROUNDED_CLAIM: {claim['line_id']}")
            continue
        covered.update((claim["line_id"], t) for t in clock_values(line)
                       if t == claim["normalized_time"] or (not _PERIOD.search(line) and _face(t) == _face(claim["normalized_time"])))
        grounded.append(claim)
    expected = {(key, time) for key, text in lines.items() for time in clock_values(text)}
    if expected - covered:
        errors.append("TIMELINE_CLOCK_CLAIMS_MISSING: " + repr(sorted(expected - covered)))
    scheduled = {}
    for claim in sorted(grounded, key=lambda c: order[c["line_id"]]):
        event, kind = claim["event_id"], claim["kind"]
        if kind == "scheduled":
            if event in scheduled and not _same_time(scheduled[event], claim):
                errors.append(f"TIMELINE_SCHEDULE_CONFLICT: {event}")
            scheduled[event] = claim
        elif kind == "explicit_change":
            if not re.search(r"提前|延后|推迟|改到|改为|改成|改期|调整|变更|重新约定", claim["quote"]):
                errors.append(f"TIMELINE_CHANGE_NOT_EXPLICIT: {event}")
            else:
                scheduled[event] = claim
        elif kind == "actual" and event in scheduled and not _same_time(scheduled[event], claim):
            errors.append(f"TIMELINE_EVENT_TIME_CONFLICT: {event}: scheduled={scheduled[event]['normalized_time']}, actual={claim['normalized_time']}, line={claim['line_id']}")
    return errors
