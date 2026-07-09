"""Parse ./blocklist.txt and match emails/domains against it.

One entry per line: a bare domain (example.com) or a full email (a@example.com).
`#` starts a comment; blank lines are ignored. A missing file is valid config
(an empty blocklist), not an error.
"""

from __future__ import annotations

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
