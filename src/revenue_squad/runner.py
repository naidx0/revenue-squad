"""Drive the Claude CLI headless and parse its output. Fails loudly (AGENTS.md §5).

No retries, no fallbacks: a nonzero exit, a timeout, or missing/unparseable JSON
raises RunnerError with the stderr tail and the path to the saved raw stdout.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_TIMEOUT = 600.0
_STDERR_TAIL = 2000  # chars of stderr surfaced in errors
_JSON_BLOCK = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


class RunnerError(RuntimeError):
    """Raised when the Claude CLI run failed or produced unusable output."""


def _skills_dir() -> Path:
    override = os.environ.get("SQUAD_SKILLS_DIR")
    if override:
        return Path(override)
    # src/revenue_squad/runner.py -> parents[2] is the repo root.
    return Path(__file__).resolve().parents[2] / "skills"


_FRONTMATTER = re.compile(r"---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block (--- ... ---) if present, preserving the body."""
    match = _FRONTMATTER.match(text)
    if match:
        return text[match.end():]
    # No frontmatter, or opened but never closed: leave text untouched rather than guess.
    return text


def _load_skill_body(skill_name: str) -> str:
    path = _skills_dir() / skill_name / "SKILL.md"
    if not path.is_file():
        raise RunnerError(f"skill not found: {path}")
    return strip_frontmatter(path.read_text())


def _save_raw(stdout: str) -> Path:
    raw_dir = Path("out") / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    raw_path = (raw_dir / f"{ts}.json").resolve()
    raw_path.write_text(stdout or "")
    return raw_path


def _tail(text: str | None) -> str:
    text = text or ""
    return text[-_STDERR_TAIL:]


def extract_last_json_block(text: str) -> dict:
    """Return the parsed JSON of the LAST fenced ```json block in text (ignores surrounding prose)."""
    matches = _JSON_BLOCK.findall(text)
    if not matches:
        raise ValueError("no fenced ```json block found in claude result")
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"last ```json block was not valid JSON: {exc}") from exc


def strip_json_blocks(text: str) -> str:
    """Return the result's prose: text with every fenced ```json block removed."""
    return _JSON_BLOCK.sub("", text).strip()


def run_skill(
    task_prompt: str,
    skill_name: str,
    *,
    allowed_tools: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    extract_json: bool = True,
    return_prose: bool = False,
) -> dict | str | tuple[dict, str]:
    """Run `claude -p <task> --append-system-prompt-file <skill> --output-format json`.

    Returns the parsed last ```json block when extract_json is True (research/outreach),
    otherwise the raw `result` text (propose). With return_prose (outreach), returns a
    (parsed_block, prose) tuple so the caller can surface the model's explanation when the
    block is empty. Raises RunnerError on any failure.
    """
    skill_body = _load_skill_body(skill_name)

    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="squad-skill-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(skill_body)

        cmd = [
            "claude",
            "-p",
            task_prompt,
            "--append-system-prompt-file",
            tmp_path,
            "--output-format",
            "json",
        ]
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raw_path = _save_raw(exc.stdout if isinstance(exc.stdout, str) else "")
            raise RunnerError(
                f"claude timed out after {timeout}s (skill={skill_name}). "
                f"stderr tail: {_tail(exc.stderr if isinstance(exc.stderr, str) else '')} | raw: {raw_path}"
            ) from exc
    finally:
        os.unlink(tmp_path)

    raw_path = _save_raw(proc.stdout)

    if proc.returncode != 0:
        raise RunnerError(
            f"claude exited {proc.returncode} (skill={skill_name}). "
            f"stderr tail: {_tail(proc.stderr)} | raw: {raw_path}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(
            f"claude output was not valid JSON (skill={skill_name}): {exc}. "
            f"stderr tail: {_tail(proc.stderr)} | raw: {raw_path}"
        ) from exc

    if not isinstance(envelope, dict) or "result" not in envelope:
        raise RunnerError(
            f"claude JSON envelope missing 'result' (skill={skill_name}) | raw: {raw_path}"
        )

    result_text = envelope["result"]
    if not extract_json:
        return result_text

    try:
        block = extract_last_json_block(result_text)
    except ValueError as exc:
        raise RunnerError(f"{exc} (skill={skill_name}) | raw: {raw_path}") from exc

    if return_prose:
        return block, strip_json_blocks(result_text)
    return block
