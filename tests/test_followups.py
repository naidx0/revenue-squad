"""The pure `followups_due(rows, today)` matrix: boundary days, guards, day-7
sequencing, and missing/unparseable dates."""

from datetime import date, timedelta

from revenue_squad import crm, pipeline

TODAY = date(2026, 7, 10)


def _row(**over):
    row = crm.empty_row()
    row.update({"Company": "Acme", "Status": "Contacted"})
    row.update(over)
    return row


def _iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def test_day3_due_exactly_at_minus_2d():
    due = pipeline.followups_due([_row(**{"Day 1 Sent": _iso(2)})], TODAY)
    assert due == [("Acme", "Day 3", TODAY.isoformat())]


def test_day3_not_due_at_minus_1d():
    assert pipeline.followups_due([_row(**{"Day 1 Sent": _iso(1)})], TODAY) == []


def test_day3_due_when_older():
    due = pipeline.followups_due([_row(**{"Day 1 Sent": _iso(5)})], TODAY)
    assert due[0][1] == "Day 3"


def test_replied_column_excluded():
    row = _row(**{"Day 1 Sent": _iso(5), "Replied": "yes"})
    assert pipeline.followups_due([row], TODAY) == []


def test_status_replied_excluded():
    row = _row(**{"Day 1 Sent": _iso(5), "Status": "Replied"})
    assert pipeline.followups_due([row], TODAY) == []


def test_blocked_excluded():
    row = _row(**{"Day 1 Sent": _iso(5), "Blocked": "yes"})
    assert pipeline.followups_due([row], TODAY) == []


def test_status_lost_excluded():
    row = _row(**{"Day 1 Sent": _iso(5), "Status": "Lost"})
    assert pipeline.followups_due([row], TODAY) == []


def test_day7_due_at_minus_6d_with_day3_sent():
    row = _row(**{"Day 1 Sent": _iso(6), "Day 3 Sent": _iso(3)})
    due = pipeline.followups_due([row], TODAY)
    assert due == [("Acme", "Day 7", TODAY.isoformat())]


def test_day7_requires_day3_sent_else_day3_surfaces():
    # Old Day 1 but no Day 3 Sent yet -> the Day 3 touch is what's due, not Day 7.
    row = _row(**{"Day 1 Sent": _iso(9)})
    due = pipeline.followups_due([row], TODAY)
    assert due[0][1] == "Day 3"


def test_day7_not_due_before_minus_6d():
    row = _row(**{"Day 1 Sent": _iso(5), "Day 3 Sent": _iso(2)})
    assert pipeline.followups_due([row], TODAY) == []


def test_day7_already_sent_nothing_due():
    row = _row(**{"Day 1 Sent": _iso(9), "Day 3 Sent": _iso(6), "Day 7 Sent": _iso(2)})
    assert pipeline.followups_due([row], TODAY) == []


def test_missing_day1_date_nothing_due():
    assert pipeline.followups_due([_row()], TODAY) == []


def test_unparseable_day1_date_nothing_due():
    assert pipeline.followups_due([_row(**{"Day 1 Sent": "not-a-date"})], TODAY) == []


def test_new_status_not_in_followup_window():
    row = _row(**{"Day 1 Sent": _iso(5), "Status": "New"})
    assert pipeline.followups_due([row], TODAY) == []
