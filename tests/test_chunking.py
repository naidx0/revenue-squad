"""Chunked batching for `research -n N` and `outreach`: chunk math, aggregate
reporting, per-chunk failure (continue + nonzero), and single-chunk byte-identity."""

import json

from typer.testing import CliRunner

from revenue_squad import cli, crm


def _flat(output):
    """Collapse rich's line wrapping so long single-line messages match as substrings."""
    return " ".join(output.split())


# --- chunk math ---


def test_split_count_11_is_10_then_1():
    assert cli._split_count(11, 10) == [10, 1]


def test_split_count_exact_multiple():
    assert cli._split_count(10, 10) == [10]
    assert cli._split_count(20, 10) == [10, 10]


def test_split_count_25():
    assert cli._split_count(25, 10) == [10, 10, 5]


def test_split_count_small_and_zero():
    assert cli._split_count(3, 10) == [3]
    assert cli._split_count(0, 10) == []


def test_chunk_groups_of_8():
    assert cli._chunk(list(range(9)), 8) == [[0, 1, 2, 3, 4, 5, 6, 7], [8]]
    assert cli._chunk(list(range(8)), 8) == [[0, 1, 2, 3, 4, 5, 6, 7]]


def test_research_reporter_counts_drops_and_mx_demotions():
    msgs = []
    reporter = cli._ResearchReporter(msgs.append)
    reporter("DROP Acme: domain x is blocklisted")
    reporter("DEMOTE Acme: email a@b.com -> null (MX: NXDOMAIN)")
    reporter("DEMOTE Acme: email a@b.com -> null (no evidence URL)")
    assert reporter.dropped_blocklist == 1
    assert reporter.demoted_mx == 1  # the no-evidence demotion is not counted as MX
    assert len(msgs) == 3  # every message is still forwarded loudly


# --- research chunking (run_skill mocked; emailless leads so no DNS/MX network) ---


def _leads(*companies):
    return {"leads": [{"company": c} for c in companies]}


def test_research_two_chunks_appends_all_and_reports_aggregate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    per_call = [_leads(*[f"C{i}" for i in range(10)]), _leads("Solo")]
    state = {"n": 0}

    def fake(task, skill, **kw):
        out = per_call[state["n"]]
        state["n"] += 1
        return out

    monkeypatch.setattr(cli, "run_skill", fake)
    result = CliRunner().invoke(cli.app, ["research", "Austin", "dentists", "-n", "11"])
    assert result.exit_code == 0, result.output
    assert state["n"] == 2  # ceil(11/10) = 2 claude runs
    assert len(crm.load()) == 11
    flat = _flat(result.output)
    assert "Chunked research across 2 runs" in flat
    assert "requested 11" in flat
    assert "appended 11" in flat


def test_research_single_chunk_has_no_aggregate_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: _leads("A", "B", "C", "D", "E"))
    result = CliRunner().invoke(cli.app, ["research", "Austin", "dentists", "-n", "5"])
    assert result.exit_code == 0, result.output
    assert "Chunked research" not in result.output  # byte-identical small-run path
    assert "5 new lead(s)" in _flat(result.output)


def test_research_dedupe_across_chunks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    same = _leads(*[f"C{i}" for i in range(10)])
    monkeypatch.setattr(cli, "run_skill", lambda *a, **k: same)
    result = CliRunner().invoke(cli.app, ["research", "Austin", "dentists", "-n", "20"])
    assert result.exit_code == 0, result.output
    assert len(crm.load()) == 10  # second chunk fully deduped
    flat = _flat(result.output)
    assert "deduped 10" in flat
    assert "appended 10" in flat


def test_research_chunk_failure_continues_and_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = {"n": 0}

    def fake(task, skill, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise cli.RunnerError("boom in chunk 1")
        return _leads("Solo")

    monkeypatch.setattr(cli, "run_skill", fake)
    result = CliRunner().invoke(cli.app, ["research", "Austin", "dentists", "-n", "11"])
    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "research chunk 1/2 failed" in flat
    assert "chunk(s) failed: 1" in flat
    # The surviving chunk's lead is still appended (partial-loud).
    rows = crm.load()
    assert [r["Company"] for r in rows] == ["Solo"]


def test_research_aggregate_counts_blocklist_drop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "blocklist.txt").write_text("blocked.test\n")
    chunk1 = {"leads": [{"company": f"C{i}"} for i in range(9)] + [
        {"company": "BadCo", "website": "https://blocked.test"}
    ]}
    per_call = [chunk1, _leads("Solo")]
    state = {"n": 0}

    def fake(task, skill, **kw):
        out = per_call[state["n"]]
        state["n"] += 1
        return out

    monkeypatch.setattr(cli, "run_skill", fake)
    result = CliRunner().invoke(cli.app, ["research", "Austin", "dentists", "-n", "11"])
    assert result.exit_code == 0, result.output
    flat = _flat(result.output)
    assert "dropped-blocklist 1" in flat
    assert "appended 10" in flat  # 9 + 1, the blocklisted BadCo dropped


# --- outreach chunking ---


def _draft(company):
    touch = {"subject": "s", "body": "b"}
    return {"company": company, "day1": touch, "day3": touch, "day7": touch}


def _seed_n_eligible(n):
    for i in range(n):
        row = crm.empty_row()
        row.update({
            "Company": f"Co{i}",
            "Email": f"c{i}@x.com",
            "Email Evidence": "https://x.com/team",
            "Website": "https://x.com",
            "Status": "New",
        })
        crm.append([row])


def _draft_payload(task, skill, **kw):
    payload = json.loads(task.split("Leads to draft (JSON array):\n", 1)[1])
    return {"drafts": [_draft(p["company"]) for p in payload]}, ""


def test_outreach_two_chunks_all_drafted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_n_eligible(9)  # 8 + 1
    monkeypatch.setattr(cli, "run_skill", _draft_payload)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    for i in range(9):
        assert (tmp_path / "out" / "outreach" / f"co{i}.md").exists()
    assert "Chunked outreach across 2 runs" in _flat(result.output)


def test_outreach_single_chunk_no_aggregate_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_n_eligible(3)
    monkeypatch.setattr(cli, "run_skill", _draft_payload)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 0, result.output
    assert "Chunked outreach" not in result.output


def test_outreach_chunk_failure_partial_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_n_eligible(9)
    state = {"n": 0}

    def fake(task, skill, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise cli.RunnerError("boom in outreach chunk 1")
        return _draft_payload(task, skill, **kw)

    monkeypatch.setattr(cli, "run_skill", fake)
    result = CliRunner().invoke(cli.app, ["outreach"])
    assert result.exit_code == 1
    flat = _flat(result.output)
    assert "outreach chunk 1/2 failed" in flat
    # The surviving chunk (Co8) is written; the failed chunk's 8 leads are flagged.
    assert (tmp_path / "out" / "outreach" / "co8.md").exists()
    assert not (tmp_path / "out" / "outreach" / "co0.md").exists()
