"""Parse ./blocklist.txt and match emails/domains against it.

One entry per line: a bare domain (example.com) or a full email (a@example.com).
`#` starts a comment; blank lines are ignored. A missing file is valid config
(an empty blocklist), not an error.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("blocklist.txt")


class Blocklist:
    def __init__(self, emails: set[str], domains: set[str]) -> None:
        self._emails = emails
        self._domains = domains

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "Blocklist":
        path = Path(path)
        emails: set[str] = set()
        domains: set[str] = set()
        if not path.is_file():
            return cls(emails, domains)  # missing file == empty blocklist
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip().lower()
            if not line:
                continue
            if "@" in line:
                emails.add(line)
            else:
                domains.add(line)
        return cls(emails, domains)

    def is_blocked(self, value: str) -> bool:
        """True if value (an email or a bare domain) matches the blocklist.

        Email match = exact email entry OR the email's domain is a blocked domain.
        Domain match = the domain is a blocked domain entry.
        """
        value = (value or "").strip().lower()
        if not value:
            return False
        if "@" in value:
            if value in self._emails:
                return True
            domain = value.split("@", 1)[1]
            return domain in self._domains
        return value in self._domains


def _existing_entries(text: str) -> set[str]:
    """Every entry already declared in the file, parsed exactly like Blocklist.load."""
    out: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            out.add(line)
    return out


def append_entries(entries: list[str], path: Path | str = DEFAULT_PATH) -> list[str]:
    """Append new blocklist entries under a dated comment header; return what was written.

    Preserves existing file content and comments verbatim. Dedupes case-insensitively
    against existing entries and within the batch. Atomic (temp file + os.replace).
    Returns the lowercased entries actually written, in order (empty if nothing new).
    """
    path = Path(path)
    existing_text = path.read_text() if path.is_file() else ""
    seen = _existing_entries(existing_text)
    to_add: list[str] = []
    for entry in entries:
        norm = (entry or "").strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        to_add.append(norm)
    if not to_add:
        return []

    block = (
        f"# added by squad gmail-sync-bounces {date.today().isoformat()}\n"
        + "\n".join(to_add)
        + "\n"
    )
    if existing_text:
        prefix = existing_text if existing_text.endswith("\n") else existing_text + "\n"
        new_text = prefix + "\n" + block
    else:
        new_text = block

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".blocklist-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(new_text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return to_add
