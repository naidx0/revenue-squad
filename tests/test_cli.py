"""CLI-level tests for `squad outreach` — the research->outreach plumbing and its
fail-loud behavior when the model returns no (or partial) drafts."""

import json

from typer.testing import CliRunner

from revenue_squad import cli, crm


def _lead(company, email="", **extra):
    row = crm.empty_row()
    row["Company"] = company
    row["Email"] = email
    row.update(extra)
    return row


def _draft(company):
    touch = {"subject": "s", "body": "b"}
    return {"company": company, "day1": touch, "day3": touch, "day7": touch}


def _seed_eligible(company="Acme", **extra):
    """Append one outreach-eligible Status=New lead to ./pipeline.csv."""
    row = _lead(
        company,
        "jane@acme.com",
        **{
            "Email Evidence": "https://acme.com/team",
            "Website": "https://acme.com",
            "Status": "New",
        },
    )
    row.update(extra)
    crm.append([row])


def test_outreach_prompt_serializes_notes_and_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible(
        Notes="warm intro from Bob",
        **{"Lead Score": "8", "Score Rationale": "great fit"},
    )
    captured = {}

    def fake_run_skill(task, skill, **kw):
        captured["task"] = task
        return {"drafts": [_draft("Acme")]}, ""

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    json_str = captured["task"].split("Leads to draft (JSON array):\n", 1)[1]
    payload = json.loads(json_str)
    lead = payload[0]
    # Every field the outreach skill needs to source a hook is serialized.
    assert lead["notes"] == "warm intro from Bob"
    assert lead["email_evidence"] == "https://acme.com/team"
    assert lead["lead_score"] == "8"
    assert lead["score_rationale"] == "great fit"
    assert set(lead) >= {
        "company", "contact", "email", "email_evidence", "website", "city",
        "vertical", "service_line", "lead_score", "score_rationale", "notes",
    }


def test_outreach_zero_drafts_exits_nonzero_with_explanation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible()

    def fake_run_skill(task, skill, **kw):
        return (
            {"drafts": []},
            "No research notes to source a personalization hook, so I did not draft.",
        )

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code != 0
    # The operator sees WHY (the model's prose) and WHICH company got no draft.
    assert "No research notes to source a personalization hook" in result.output
    assert "Acme" in result.output
    # No file was written.
    assert not (tmp_path / "out" / "outreach" / "acme.md").exists()


def test_outreach_partial_drafts_writes_some_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")
    _seed_eligible("Globex")

    def fake_run_skill(task, skill, **kw):
        return {"drafts": [_draft("Acme")]}, "Skipped Globex: thin public info."

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code != 0
    # The draft that came back is written...
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()
    # ...and the missing company is named in the failure.
    assert "Globex" in result.output
    assert not (tmp_path / "out" / "outreach" / "globex.md").exists()


def test_outreach_all_drafted_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")

    def fake_run_skill(task, skill, **kw):
        return {"drafts": [_draft("Acme")]}, ""

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()


# --- sender identity: the ambient-identity leak guard (both outreach and propose) ---


def _capture_outreach_task(tmp_path, monkeypatch, argv):
    """Run `squad outreach ...` with one eligible lead; return the task prompt sent."""
    monkeypatch.chdir(tmp_path)
    _seed_eligible()
    captured = {}

    def fake_run_skill(task, skill, **kw):
        captured["task"] = task
        return {"drafts": [_draft("Acme")]}, ""

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    return captured["task"]


def test_outreach_prompt_includes_sender_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    task = _capture_outreach_task(
        tmp_path, monkeypatch, ["outreach", "--sender", "Jane Doe | Acme Consulting"]
    )
    assert "Jane Doe | Acme Consulting" in task
    assert "sign exactly with this identity" in task


def test_outreach_prompt_uses_env_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUAD_SENDER", "Dana Lee | Northlight")
    task = _capture_outreach_task(tmp_path, monkeypatch, ["outreach"])
    assert "Dana Lee | Northlight" in task
    assert "sign exactly with this identity" in task


def test_outreach_prompt_placeholder_when_no_sender(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    task = _capture_outreach_task(tmp_path, monkeypatch, ["outreach"])
    assert "[Your name], [Your business]" in task
    assert "Do NOT infer or substitute" in task


def _capture_propose_task(tmp_path, monkeypatch, extra_argv):
    monkeypatch.chdir(tmp_path)
    notes = tmp_path / "notes.md"
    notes.write_text("Pain (their words): 'manual intake eats the morning'.")
    captured = {}

    def fake_run_skill(task, skill, **kw):
        captured["task"] = task
        return "# Proposal\nbody"

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    argv = ["propose", "Acme", "--notes", str(notes), *extra_argv]
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    return captured["task"]


def test_propose_prompt_includes_sender_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    task = _capture_propose_task(tmp_path, monkeypatch, ["--sender", "Jane Doe | Acme Consulting"])
    assert "Jane Doe | Acme Consulting" in task
    assert "sign exactly with this identity" in task


def test_propose_prompt_placeholder_when_no_sender(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    task = _capture_propose_task(tmp_path, monkeypatch, [])
    assert "[Your name], [Your business]" in task
    assert "Do NOT infer or substitute" in task
