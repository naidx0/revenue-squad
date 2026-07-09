import json

import pytest

from revenue_squad import runner
from revenue_squad.runner import (
    RunnerError,
    extract_last_json_block,
    run_skill,
    strip_frontmatter,
)
from tests.conftest import make_proc


def _envelope(result_text):
    return json.dumps({"result": result_text, "type": "result"})


def test_strip_frontmatter():
    text = "---\nname: research\ndescription: x\n---\nReal body here.\n"
    assert strip_frontmatter(text) == "Real body here.\n"


def test_strip_frontmatter_no_frontmatter():
    text = "No frontmatter at all.\n"
    assert strip_frontmatter(text) == text


def test_extract_last_json_block_with_surrounding_text():
    text = "Here are the leads I found.\n```json\n{\"leads\": [1, 2]}\n```\nDone."
    assert extract_last_json_block(text) == {"leads": [1, 2]}


def test_extract_last_json_block_takes_last_of_many():
    text = "```json\n{\"n\": 1}\n```\nthen\n```json\n{\"n\": 2}\n```"
    assert extract_last_json_block(text) == {"n": 2}


def test_extract_last_json_block_missing_raises():
    with pytest.raises(ValueError, match="no fenced"):
        extract_last_json_block("no code block here")


def test_run_skill_parses_envelope_and_block(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result_text = "Prose around it.\n```json\n{\"leads\": []}\n```"
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: make_proc(0, _envelope(result_text))
    )
    out = run_skill("do research", "research", allowed_tools=["WebSearch"])
    assert out == {"leads": []}


def test_run_skill_propose_returns_text(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: make_proc(0, _envelope("# Proposal\nbody"))
    )
    out = run_skill("write proposal", "proposal", extract_json=False)
    assert out == "# Proposal\nbody"


def test_run_skill_passes_allowed_tools(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return make_proc(0, _envelope("```json\n{}\n```"))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    run_skill("t", "research", allowed_tools=["WebSearch", "WebFetch"])
    cmd = captured["cmd"]
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert "--append-system-prompt-file" in cmd
    assert "--output-format" in cmd and "json" in cmd


def test_run_skill_no_tools_omits_flag(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    captured = {}
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, *a, **k: (captured.__setitem__("cmd", cmd), make_proc(0, _envelope("t")))[1],
    )
    run_skill("t", "outreach", extract_json=False)
    assert "--allowedTools" not in captured["cmd"]


def test_run_skill_nonzero_exit_raises_and_saves_raw(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: make_proc(2, "partial", "boom stderr")
    )
    with pytest.raises(RunnerError, match="exited 2"):
        run_skill("t", "research", allowed_tools=["WebSearch"])
    raw_files = list((tmp_path / "out" / "raw").glob("*.json"))
    assert raw_files and raw_files[0].read_text() == "partial"


def test_run_skill_bad_json_raises(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: make_proc(0, "not json"))
    with pytest.raises(RunnerError, match="not valid JSON"):
        run_skill("t", "research", allowed_tools=["WebSearch"])


def test_run_skill_missing_block_raises(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: make_proc(0, _envelope("no block at all"))
    )
    with pytest.raises(RunnerError, match="no fenced"):
        run_skill("t", "research", allowed_tools=["WebSearch"])


def test_run_skill_timeout_raises(skills_dir, monkeypatch, tmp_path):
    import subprocess as sp

    monkeypatch.chdir(tmp_path)

    def fake_run(*a, **k):
        raise sp.TimeoutExpired(cmd="claude", timeout=1, output="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises(RunnerError, match="timed out"):
        run_skill("t", "research", allowed_tools=["WebSearch"])


def test_run_skill_unknown_skill_raises(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RunnerError, match="skill not found"):
        run_skill("t", "nonexistent")
