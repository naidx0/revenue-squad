"""`squad daily`: squad.toml parsing/precedence and the end-to-end bounded loop
(section order, gmail-skip line, summary counts, exit codes) — runner + gmail mocked."""

import json
from datetime import date, timedelta
from pathlib import Path

from typer.testing import CliRunner

from revenue_squad import cli, crm


def _flat(output):
    return " ".join(output.split())


# --- squad.toml parsing / precedence (via the pure resolver) ---


def _write_toml(tmp_path, body):
    p = tmp_path / "squad.toml"
    p.write_text(body)
    return p


def test_config_flags_override_toml(tmp_path):
    cfg_path = _write_toml(
        tmp_path,
        '[daily]\nlocation = "Denver"\nvertical = "dentists"\ncount = 25\nservice_line = "SEO"\n',
    )
    cfg = cli._resolve_daily_config(
        location="Austin", vertical=None, count=3, service_line=None, config_path=cfg_path
    )
    assert cfg.location == "Austin"   # flag wins
    assert cfg.vertical == "dentists"  # toml fills the gap
    assert cfg.count == 3              # flag wins
    assert cfg.service_line == "SEO"


def test_config_toml_only_with_default_count(tmp_path):
    cfg_path = _write_toml(tmp_path, '[daily]\nlocation = "Denver"\nvertical = "dentists"\n')
    cfg = cli._resolve_daily_config(
        location=None, vertical=None, count=None, service_line=None, config_path=cfg_path
    )
    assert (cfg.location, cfg.vertical, cfg.count, cfg.service_line) == (
        "Denver", "dentists", 10, None
    )


def test_config_missing_required_is_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no squad.toml, no flags
    result = CliRunner().invoke(cli.app, ["daily"])
    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "--location" in flat and "location" in flat
    assert "squad.toml" in flat


def test_config_malformed_toml_is_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "squad.toml").write_text("[daily]\nlocation = \n")  # broken TOML
    result = CliRunner().invoke(cli.app, ["daily", "--vertical", "x"])
    assert result.exit_code == 1
    assert "not valid TOML" in _flat(result.output)


# --- daily end-to-end (runner + gmail mocked, no network) ---


def _seed_eligible_new(company="Acme"):
    row = crm.empty_row()
    row.update({
        "Company": company,
        "Email": f"{company.lower()}@x.com",
        "Email Evidence": "https://x.com/team",
        "Website": "https://x.com",
        "Status": "New",
    })
    crm.append([row])


def _seed_followup_due(company="OldCo"):
    d1 = (date.today() - timedelta(days=3)).isoformat()
    row = crm.empty_row()
    row.update({
        "Company": company,
        "Email": f"{company.lower()}@x.com",
        "Status": "Contacted",
        "Day 1 Sent": d1,
    })
    crm.append([row])


def _draft(company):
    touch = {"subject": "s", "body": "b"}
    return {"company": company, "day1": touch, "day3": touch, "day7": touch}


def _daily_run_skill(task, skill, **kw):
    if skill == "research":
        return {"leads": [{"company": "NewCo"}]}  # emailless -> no DNS/MX
    # outreach: draft whatever eligible leads were handed in
    payload = json.loads(task.split("Leads to draft (JSON array):\n", 1)[1])
    return {"drafts": [_draft(p["company"]) for p in payload]}, ""


def test_daily_end_to_end_stages_nothing_sent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    _seed_eligible_new("Acme")
    _seed_followup_due("OldCo")
    (tmp_path / "out" / "outreach").mkdir(parents=True)
    (tmp_path / "out" / "outreach" / "oldco.md").write_text("draft")
    monkeypatch.setattr(cli, "run_skill", _daily_run_skill)

    result = CliRunner().invoke(
        cli.app, ["daily", "--location", "Austin", "--vertical", "dentists"]
    )
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)

    # Section order 1 -> 5.
    order = ["Gmail bounce sync", "Research new prospects", "Follow-ups due",
             "Stage outreach", "Summary"]
    idxs = [flat.index(s) for s in order]
    assert idxs == sorted(idxs), flat

    assert "Gmail bounce sync skipped (--gmail not set)" in flat
    assert "OldCo" in flat and "Day 3" in flat          # follow-up surfaced
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()  # Acme staged
    assert "Outreach drafts staged: 1" in flat
    assert "Nothing was sent" in flat
    # Propose-only: no Status advanced by daily.
    assert crm.find_by_company(crm.load(), "Acme")["Status"] == "New"


def test_daily_gmail_no_token_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .gmail-token.json -> real sync raises GmailError
    result = CliRunner().invoke(
        cli.app, ["daily", "--gmail", "--location", "Austin", "--vertical", "dentists"]
    )
    assert result.exit_code == 1
    assert "gmail-auth" in _flat(result.output)


def test_daily_missing_outreach_file_flagged_not_fatal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_followup_due("OldCo")  # due, but no out/outreach/oldco.md on disk
    monkeypatch.setattr(cli, "run_skill", _daily_run_skill)
    result = CliRunner().invoke(
        cli.app, ["daily", "--location", "Austin", "--vertical", "dentists"]
    )
    assert result.exit_code == 0, result.output  # missing file is loud, not fatal
    flat = _flat(result.output)
    assert "MISSING" in flat
    assert "outreach file missing for OldCo" in flat
