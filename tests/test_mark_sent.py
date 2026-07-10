"""Bulk `squad mark-sent`: multiple companies, --batch matching, mutual exclusion,
and per-row loud results with a nonzero exit when any row is not-found/ineligible."""

from typer.testing import CliRunner

from revenue_squad import cli, crm


def _seed(company, **over):
    row = crm.empty_row()
    row["Company"] = company
    row["Email"] = f"{company.lower()}@x.com"
    row.update(over)
    crm.append([row])


def _by_company(company):
    return crm.find_by_company(crm.load(), company)


def test_multi_company_mixed_found_and_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme")
    _seed("Globex")
    result = CliRunner().invoke(cli.app, ["mark-sent", "Acme", "Ghost", "Globex", "--day", "1"])
    assert result.exit_code == 1  # Ghost not found -> nonzero
    assert "NOT MARKED Ghost" in result.output
    # The two present companies are still processed.
    assert _by_company("Acme")["Day 1 Sent"]
    assert _by_company("Acme")["Status"] == "Contacted"
    assert _by_company("Globex")["Day 1 Sent"]


def test_multi_company_all_found_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme")
    _seed("Globex")
    result = CliRunner().invoke(cli.app, ["mark-sent", "Acme", "Globex", "--day", "1"])
    assert result.exit_code == 0, result.output
    assert _by_company("Acme")["Status"] == "Contacted"
    assert _by_company("Globex")["Status"] == "Contacted"


def test_batch_matches_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme", Batch="Dentists-2026")
    _seed("Globex", Batch="Dentists-2026")
    _seed("Other", Batch="plumbers-2026")
    result = CliRunner().invoke(cli.app, ["mark-sent", "--batch", "dentists-2026", "--day", "1"])
    assert result.exit_code == 0, result.output
    assert _by_company("Acme")["Status"] == "Contacted"
    assert _by_company("Globex")["Status"] == "Contacted"
    # A row in a different batch is untouched.
    assert _by_company("Other")["Status"] == "New"
    assert not _by_company("Other")["Day 1 Sent"]


def test_batch_ineligible_row_reported_and_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Fresh", Batch="b1")
    _seed("Already", Batch="b1", **{"Day 1 Sent": "2026-07-01", "Status": "Contacted"})
    result = CliRunner().invoke(cli.app, ["mark-sent", "--batch", "b1", "--day", "1"])
    assert result.exit_code == 1  # Already is ineligible for a Day 1 transition
    assert "SKIP Already" in result.output
    # The eligible row is still marked.
    assert _by_company("Fresh")["Status"] == "Contacted"


def test_batch_day3_requires_day1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("HasDay1", Batch="b1", **{"Day 1 Sent": "2026-07-05", "Status": "Contacted"})
    _seed("NoDay1", Batch="b1", **{"Status": "Contacted"})
    result = CliRunner().invoke(cli.app, ["mark-sent", "--batch", "b1", "--day", "3"])
    assert result.exit_code == 1  # NoDay1 has no Day 1 Sent -> ineligible for Day 3
    assert "SKIP NoDay1" in result.output
    assert _by_company("HasDay1")["Day 3 Sent"]


def test_batch_no_match_is_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme", Batch="b1")
    result = CliRunner().invoke(cli.app, ["mark-sent", "--batch", "nope", "--day", "1"])
    assert result.exit_code == 1
    assert "no pipeline rows in batch" in result.output


def test_companies_and_batch_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme", Batch="b1")
    result = CliRunner().invoke(cli.app, ["mark-sent", "Acme", "--batch", "b1", "--day", "1"])
    assert result.exit_code == 2  # BadParameter / usage error
    assert "not both" in result.output


def test_neither_companies_nor_batch_is_usage_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed("Acme")
    result = CliRunner().invoke(cli.app, ["mark-sent", "--day", "1"])
    assert result.exit_code == 2
    assert "at least one company" in result.output
