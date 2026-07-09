"""Shared fixtures. No network, no real claude calls."""

import types

import pytest


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """A temp skills/ dir with fixture SKILL.md files (never touches the real skills/)."""
    root = tmp_path / "skills"
    for name in ("research", "outreach", "proposal"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: fixture {name} skill\n---\n"
            f"Body of the {name} skill.\n"
        )
    monkeypatch.setenv("SQUAD_SKILLS_DIR", str(root))
    return root


def make_proc(returncode=0, stdout="", stderr=""):
    """Fake subprocess.CompletedProcess-like object."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
