from datetime import date, datetime, timezone

from lawca.deadlines.ics import IcsEvent, escape, fold, to_ics


def test_escape_special_characters():
    assert escape("항소, 상고; 경로\\이름\n둘째 줄") == "항소\\, 상고\\; 경로\\\\이름\\n둘째 줄"


def test_fold_keeps_lines_within_75_octets_without_splitting_hangul():
    line = "SUMMARY:" + "가" * 60  # 8 + 180옥텟
    folded = fold(line)
    physical = folded.split("\r\n")
    assert all(len(p.encode("utf-8")) <= 75 for p in physical)
    assert all(p.startswith(" ") for p in physical[1:])
    # 접은 줄을 다시 펴면 원래 줄이 된다
    assert "".join([physical[0], *(p[1:] for p in physical[1:])]) == line


def test_short_line_is_not_folded():
    assert fold("VERSION:2.0") == "VERSION:2.0"


def test_calendar_has_all_day_event_with_alarms():
    event = IcsEvent(uid="abc@lawca", day=date(2026, 9, 28), summary="[만료] 보정 · 2026가단51234", description="근거")
    ics = to_ics([event], datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc))
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\n" not in ics.replace("\r\n", "")
    assert "DTSTART;VALUE=DATE:20260928\r\n" in ics
    assert "DTEND;VALUE=DATE:20260929\r\n" in ics
    assert "DTSTAMP:20260926T120000Z\r\n" in ics
    assert ics.count("BEGIN:VALARM") == 2
