"""Wheel packaging: the skills/ product feature must ride inside the built wheel, or
a pip/uv-tool install has no skills to load and every research/outreach/propose dies
with "skills directory not found" (see runner._skills_dir)."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory):
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build the wheel")
    out_dir = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


@pytest.mark.slow
def test_wheel_bundles_all_three_skills(built_wheel):
    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())
    for skill in ("research", "outreach", "proposal"):
        member = f"revenue_squad/skills/{skill}/SKILL.md"
        assert member in names, (
            f"{member} missing from wheel; contents:\n" + "\n".join(sorted(names))
        )
