from datetime import date

import pytest

from revenue_squad import crm


def _lead(company, email="", **extra):
    row = crm.empty_row()
    row["Company"] = company
    row["Email"] = email
    row.update(extra)
    return row


def test_columns_exact_order():
    assert crm.COLUMNS[0] == "Company"
    assert crm.COLUMNS[-1] == "Blocked"
    assert len(crm.COLUMNS) == 24
    assert "Service Line" in crm.COLUMNS
    # Email Evidence sits immediately after Email.
    assert crm.COLUMNS[crm.COLUMNS.index("Email") + 1] == "Email Evidence"


def test_roundtrip(tmp_path):
    path = tmp_path / "pipeline.csv"
    rows = [_lead("Acme", "a@acme.com", City="Denver"), _lead("Globex", "g@globex.com")]
    crm.save(rows, path)
    loaded = crm.load(path)
    assert len(loaded) == 2
    assert loaded[0]["Company"] == "Acme"
    assert loaded[0]["City"] == "Denver"
    assert set(loaded[0].keys()) == set(crm.COLUMNS)


def test_load_missing_file_returns_empty(tmp_path):
    assert crm.load(tmp_path / "nope.csv") == []


def test_load_rejects_mismatched_header(tmp_path):
    # A pipeline.csv from an older column set (no "Email Evidence") must fail loudly,
    # not silently default the missing column.
    path = tmp_path / "pipeline.csv"
    stale = [c for c in crm.COLUMNS if c != "Email Evidence"]
    path.write_text(",".join(stale) + "\nAcme,Jane,a@acme.com,555,Denver,,New,,,,,,,,,,,,,,,\n")
    with pytest.raises(ValueError, match="does not match the current CRM schema"):
        crm.load(path)


def test_load_roundtrip_header_matches(tmp_path):
    # A file written by save() always loads back cleanly (header == COLUMNS).
    path = tmp_path / "pipeline.csv"
    crm.save([_lead("Acme", "a@acme.com")], path)
    assert crm.load(path)[0]["Company"] == "Acme"


def test_append_dedupes_by_company_and_email(tmp_path):
    path = tmp_path / "pipeline.csv"
    added1 = crm.append([_lead("Acme", "a@acme.com")], path)
    assert len(added1) == 1
    added2 = crm.append(
        [_lead("Acme", "a@acme.com"), _lead("Acme", "other@acme.com")], path
    )
    # Same Company+Email is a dup; different email is a new row.
    assert len(added2) == 1
    assert crm.load(path)[-1]["Email"] == "other@acme.com"
    assert len(crm.load(path)) == 2


def test_find_by_company_case_insensitive(tmp_path):
    rows = [_lead("Acme Corp", "a@acme.com")]
    assert crm.find_by_company(rows, "acme corp")["Company"] == "Acme Corp"
    assert crm.find_by_company(rows, "missing") is None


def test_mark_sent_day1_sets_status_and_date(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.append([_lead("Acme", "a@acme.com", Status="New")], path)
    row = crm.mark_sent("Acme", day=1, path=path)
    assert row["Status"] == "Contacted"
    assert row["Day 1 Sent"] == date.today().isoformat()
    assert crm.load(path)[0]["Status"] == "Contacted"


def test_mark_sent_day1_does_not_downgrade_non_new(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.append([_lead("Acme", "a@acme.com", Status="Replied")], path)
    row = crm.mark_sent("Acme", day=1, path=path)
    assert row["Status"] == "Replied"


def test_mark_sent_day3_clears_follow_up(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.append([_lead("Acme", "a@acme.com", Status="Contacted", **{"Follow Up Due": "2026-07-11"})], path)
    row = crm.mark_sent("Acme", day=3, path=path)
    assert row["Day 3 Sent"] == date.today().isoformat()
    assert row["Follow Up Due"] == ""
    assert row["Status"] == "Contacted"


def test_mark_sent_bad_day_raises(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.append([_lead("Acme", "a@acme.com")], path)
    with pytest.raises(ValueError, match="day must be"):
        crm.mark_sent("Acme", day=2, path=path)


def test_mark_sent_missing_company_raises(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.save([], path)
    with pytest.raises(ValueError, match="no pipeline row"):
        crm.mark_sent("Ghost", day=1, path=path)


def test_update_row_missing_raises(tmp_path):
    path = tmp_path / "pipeline.csv"
    crm.save([], path)
    with pytest.raises(ValueError, match="no pipeline row"):
        crm.update_row("Ghost", {"City": "X"}, path=path)
