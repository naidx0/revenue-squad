import pytest
from typer.testing import CliRunner

from revenue_squad import crm
from revenue_squad.backend import CrmChoice, CsvBackend, get_backend
from revenue_squad.cli import app
from revenue_squad.notion import NotionBackend


def _lead(company, email="", **extra):
    row = crm.empty_row()
    row["Company"] = company
    row["Email"] = email
    row.update(extra)
    return row


def test_get_backend_csv_is_default_type():
    assert isinstance(get_backend(CrmChoice.csv.value), CsvBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="unknown crm backend"):
        get_backend("sqlite")


def test_get_backend_notion_routes_via_env(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_x")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "ds_1")
    assert isinstance(get_backend(CrmChoice.notion.value), NotionBackend)


def test_csv_backend_delegates_to_crm(tmp_path):
    path = tmp_path / "pipeline.csv"
    backend = CsvBackend(path)
    added = backend.append([_lead("Acme", "a@acme.com", Status="New")])
    assert len(added) == 1
    assert backend.load()[0]["Company"] == "Acme"
    row = backend.mark_sent("Acme", day=1)
    assert row["Status"] == "Contacted"
    assert backend.describe() == str(path)


def test_csv_backend_append_dedupes_like_crm(tmp_path):
    path = tmp_path / "pipeline.csv"
    backend = CsvBackend(path)
    backend.append([_lead("Acme", "a@acme.com")])
    assert backend.append([_lead("Acme", "a@acme.com")]) == []


def test_cli_default_crm_is_csv_and_unchanged(tmp_path, monkeypatch):
    # `mark-sent` with no --crm uses the local ./pipeline.csv exactly as before.
    monkeypatch.chdir(tmp_path)
    crm.append([_lead("Acme", "a@acme.com", Status="New")])
    result = CliRunner().invoke(app, ["mark-sent", "Acme"])
    assert result.exit_code == 0
    assert crm.load()[0]["Status"] == "Contacted"
