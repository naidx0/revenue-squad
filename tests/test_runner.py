import json
from pathlib import Path

import pytest

from revenue_squad import runner
from revenue_squad.runner import (
    RunnerError,
    extract_last_json_block,
    run_skill,
    strip_frontmatter,
    strip_json_blocks,
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


def test_strip_json_blocks_returns_prose_only():
    text = "Why I refused: no evidence.\n```json\n{\"drafts\": []}\n```\nEnd."
    assert strip_json_blocks(text) == "Why I refused: no evidence.\n\nEnd."


def test_run_skill_return_prose_gives_block_and_prose(skills_dir, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result_text = "I could not draft: no verified email.\n```json\n{\"drafts\": []}\n```"
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: make_proc(0, _envelope(result_text))
    )
    block, prose = run_skill("draft", "outreach", return_prose=True)
    assert block == {"drafts": []}
    assert prose == "I could not draft: no verified email."


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


# --- _skills_dir resolver order: env -> repo-root -> packaged -> loud raise ---


def test_skills_dir_env_wins(tmp_path, monkeypatch):
    env_dir = tmp_path / "env-skills"
    env_dir.mkdir()
    monkeypatch.setenv("SQUAD_SKILLS_DIR", str(env_dir))
    assert runner._skills_dir() == env_dir


def test_skills_dir_repo_root_next(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SKILLS_DIR", raising=False)
    repo_root = tmp_path / "repo"
    repo_skills = repo_root / "skills"
    repo_skills.mkdir(parents=True)
    # runner.py lives at <repo>/src/revenue_squad/runner.py; parents[2] is the repo root.
    monkeypatch.setattr(
        runner, "__file__", str(repo_root / "src" / "revenue_squad" / "runner.py")
    )
    assert runner._skills_dir().resolve() == repo_skills.resolve()


def test_skills_dir_packaged_next(tmp_path, monkeypatch):
    monkeypatch.delenv("SQUAD_SKILLS_DIR", raising=False)
    # Installed layout: no repo-root skills/, but skills/ sits beside runner.py.
    pkg = tmp_path / "site-packages" / "revenue_squad"
    packaged_skills = pkg / "skills"
    packaged_skills.mkdir(parents=True)
    monkeypatch.setattr(runner, "__file__", str(pkg / "runner.py"))
    assert runner._skills_dir().resolve() == packaged_skills.resolve()


def test_skills_dir_all_missing_raises_naming_all_three(tmp_path, monkeypatch):
    env_dir = tmp_path / "missing-env-skills"  # set but does not exist
    monkeypatch.setenv("SQUAD_SKILLS_DIR", str(env_dir))
    base = tmp_path / "x" / "y" / "revenue_squad"
    monkeypatch.setattr(runner, "__file__", str(base / "runner.py"))

    with pytest.raises(RunnerError) as excinfo:
        runner._skills_dir()

    msg = str(excinfo.value)
    resolved = Path(str(base / "runner.py")).resolve()
    assert str(env_dir) in msg  # (1) env candidate
    assert str(resolved.parents[2] / "skills") in msg  # (2) repo-root candidate
    assert str(resolved.parent / "skills") in msg  # (3) packaged candidate
