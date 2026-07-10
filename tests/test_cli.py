"""CLI-level tests for `squad outreach` — the research->outreach plumbing and its
fail-loud behavior when the model returns no (or partial) drafts."""

import json

from typer.testing import CliRunner

from revenue_squad import cli, crm
from revenue_squad.gmail import Bounce, BounceSyncResult
from revenue_squad.supabase import SupabaseError


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


def test_outreach_no_args_none_eligible_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A lead that isn't Status=New is not a target in no-args mode -> nothing eligible.
    # No-args + nothing to do is not a failure: informative message, exit 0.
    crm.append([_lead("Dormant Co", "x@dormant.com", **{"Status": "Contacted"})])

    def fail(*a, **k):
        raise AssertionError("run_skill must not run when nothing is eligible")

    monkeypatch.setattr(cli, "run_skill", fail)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    assert "No eligible leads to draft." in result.output


# --- explicitly-named targets: unfulfilled work must exit nonzero (never read as success) ---


def test_outreach_explicit_all_refused_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # One named company is in the pipeline but ineligible (no verified email); the other
    # isn't in the pipeline at all. Both were explicitly requested -> both unfulfilled.
    crm.append([_lead("Present Co", "")])

    def fail(*a, **k):
        raise AssertionError("run_skill must not run when nothing is eligible")

    monkeypatch.setattr(cli, "run_skill", fail)
    result = CliRunner().invoke(cli.app, ["outreach", "Present Co", "Ghost Co"])
    assert result.exit_code == 1, result.output
    assert "Unfulfilled" in result.output
    assert "Present Co" in result.output
    assert "Ghost Co" in result.output


def test_outreach_explicit_partial_writes_one_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")
    _seed_eligible("Globex")

    def fake_run_skill(task, skill, **kw):
        return {"drafts": [_draft("Acme")]}, "Skipped Globex: thin public info."

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach", "Acme", "Globex"])
    assert result.exit_code == 1, result.output
    # The one draft that came back is written...
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()
    # ...and the unfulfilled named company is surfaced, with no file.
    assert not (tmp_path / "out" / "outreach" / "globex.md").exists()
    assert "Globex" in result.output


def test_outreach_explicit_all_drafted_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")
    _seed_eligible("Globex")

    def fake_run_skill(task, skill, **kw):
        return {"drafts": [_draft("Acme"), _draft("Globex")]}, ""

    monkeypatch.setattr(cli, "run_skill", fake_run_skill)
    result = CliRunner().invoke(cli.app, ["outreach", "Acme", "Globex"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()
    assert (tmp_path / "out" / "outreach" / "globex.md").exists()


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


# --- gmail-sync-bounces: blocklist appends, exit codes, loud missing-token ---


def test_gmail_sync_no_token_exits_nonzero_naming_auth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .gmail-token.json here -> real _load_token raises
    result = CliRunner().invoke(cli.app, ["gmail-sync-bounces"])
    assert result.exit_code == 1
    assert "gmail-auth" in result.output


def test_gmail_sync_unparseable_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw = tmp_path / "out" / "raw" / "gmail-bounce-x.json"
    raw.parent.mkdir(parents=True)
    raw.write_text("{}")
    monkeypatch.setattr(
        cli.gmail, "sync_bounces",
        lambda **kw: BounceSyncResult(bounces=[], unparseable=[raw], scanned=1),
    )
    result = CliRunner().invoke(cli.app, ["gmail-sync-bounces"])
    assert result.exit_code == 1
    assert "UNPARSEABLE" in result.output
    # rich may word-wrap the long path; the basename confirms the path was reported.
    assert "gmail-bounce-x.json" in result.output


def test_gmail_sync_appends_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def fake_sync(**kw):
        return BounceSyncResult(
            bounces=[
                Bounce("jane@acme.test", False, "5.1.1"),
                Bounce("bob@dead.test", True, "5.1.2 Domain not found"),
            ],
            unparseable=[],
            scanned=2,
        )

    monkeypatch.setattr(cli.gmail, "sync_bounces", fake_sync)
    result = CliRunner().invoke(cli.app, ["gmail-sync-bounces"])
    assert result.exit_code == 0, result.output
    lines = [l.strip() for l in (tmp_path / "blocklist.txt").read_text().splitlines()]
    assert "jane@acme.test" in lines
    assert "bob@dead.test" in lines
    assert "dead.test" in lines  # domain-level entry appended for the domain failure
    assert "jane@acme.test" in result.output


def test_gmail_sync_no_bounces_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.gmail, "sync_bounces", lambda **kw: BounceSyncResult())
    result = CliRunner().invoke(cli.app, ["gmail-sync-bounces"])
    assert result.exit_code == 0
    assert "No new bounces" in result.output
    assert not (tmp_path / "blocklist.txt").exists()


# --- outreach --gmail-drafts: files always land; drafts are attempted on top ---


def _write_gmail_token(tmp_path):
    (tmp_path / ".gmail-token.json").write_text(
        json.dumps({"refresh_token": "rt", "client_id": "c", "client_secret": "s"})
    )


def test_outreach_gmail_drafts_creates_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SQUAD_SENDER", raising=False)
    _seed_eligible("Acme")
    _write_gmail_token(tmp_path)
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: ({"drafts": [_draft("Acme")]}, ""))
    calls = []

    def fake_create_draft(to, subject, body, sender, **kw):
        calls.append((to, subject, body, sender))
        return "draft-9"

    monkeypatch.setattr(cli.gmail, "create_draft", fake_create_draft)
    result = CliRunner().invoke(cli.app, ["outreach", "--gmail-drafts"])
    assert result.exit_code == 0, result.output
    # Files always land AND a Gmail draft was attempted with the Day 1 touch + lead email.
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()
    assert calls == [("jane@acme.com", "s", "b", "")]
    assert "draft-9" in result.output


def test_outreach_gmail_drafts_failure_nonzero_files_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")
    _write_gmail_token(tmp_path)
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: ({"drafts": [_draft("Acme")]}, ""))

    def boom(*a, **k):
        raise cli.gmail.GmailError("Gmail draft creation failed: HTTP 403")

    monkeypatch.setattr(cli.gmail, "create_draft", boom)
    result = CliRunner().invoke(cli.app, ["outreach", "--gmail-drafts"])
    assert result.exit_code == 1
    # The file is still on disk, and the failure is loud.
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()
    assert "GMAIL DRAFT FAILED" in result.output


def test_outreach_gmail_drafts_no_token_is_loud_and_preserves_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .gmail-token.json here
    _seed_eligible("Acme")
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: ({"drafts": [_draft("Acme")]}, ""))

    def must_not_call(*a, **k):
        raise AssertionError("create_draft must not run without a token")

    monkeypatch.setattr(cli.gmail, "create_draft", must_not_call)
    result = CliRunner().invoke(cli.app, ["outreach", "--gmail-drafts"])
    assert result.exit_code == 1
    assert "gmail-auth" in result.output
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()  # files preserved


def test_outreach_without_gmail_drafts_flag_never_touches_gmail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_eligible("Acme")
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: ({"drafts": [_draft("Acme")]}, ""))

    def must_not_call(*a, **k):
        raise AssertionError("gmail.create_draft must not run without --gmail-drafts")

    monkeypatch.setattr(cli.gmail, "create_draft", must_not_call)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "outreach" / "acme.md").exists()


# --- supabase-init: prints setup steps + schema path, then verifies the table ---


def test_supabase_init_reachable_says_table_ready(monkeypatch):
    monkeypatch.setattr(cli.supabase, "verify_table", lambda **kw: None)
    result = CliRunner().invoke(cli.app, ["supabase-init"])
    assert result.exit_code == 0, result.output
    assert "table ready" in result.output
    assert "supabase_schema.sql" in result.output   # the file path is printed
    assert "SQL Editor" in result.output             # the paste step is spelled out


def test_supabase_init_table_missing_is_actionable(monkeypatch):
    def boom(**kw):
        raise SupabaseError("Supabase pipeline table not found. ... supabase_schema.sql ...")

    monkeypatch.setattr(cli.supabase, "verify_table", boom)
    result = CliRunner().invoke(cli.app, ["supabase-init"])
    assert result.exit_code == 1
    assert "supabase_schema.sql" in result.output


def test_supabase_init_missing_env_is_loud(monkeypatch):
    def boom(**kw):
        raise SupabaseError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set. ...")

    monkeypatch.setattr(cli.supabase, "verify_table", boom)
    result = CliRunner().invoke(cli.app, ["supabase-init"])
    assert result.exit_code == 1
    assert "SUPABASE_URL" in result.output
