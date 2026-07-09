"""Local CSV pipeline (./pipeline.csv) mirroring Sequence's Master Lead CRM."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("pipeline.csv")

# Exact column order — mirrors Sequence's Master Lead CRM.
COLUMNS = [
    "Company",
    "Contact",
    "Email",
    "Email Evidence",
    "Phone",
    "City",
    "Website",
    "Status",
    "Vertical",
    "Service Line",
    "Batch",
    "Lead Score",
    "Score Rationale",
    "Deal Value",
    "Day 1 Sent",
    "Day 3 Sent",
    "Day 7 Sent",
    "Follow Up Due",
    "Replied",
    "Reply Date",
    "Call Booked",
    "Call Date",
    "Notes",
    "Blocked",
]

# Exact Status vocabulary — verbatim from the Master Lead CRM.
STATUSES = (
    "New",
    "Contacted",
    "Replied",
    "Call Booked",
    "Proposal Sent",
    "Won",
    "Lost",
    "Nurture",
)


def empty_row() -> dict[str, str]:
    """A row with every column present and blank, Status defaulting to New."""
    row = {col: "" for col in COLUMNS}
    row["Status"] = "New"
    return row


def load(path: Path | str = DEFAULT_PATH) -> list[dict[str, str]]:
    path = Path(path)
    if not path.is_file():
        return []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        # Strict forward-compat: a header that doesn't match COLUMNS exactly is a
        # schema mismatch (e.g. a pipeline.csv from an older column set). Fail loudly
        # instead of silently defaulting the missing/extra columns (AGENTS.md §5).
        if header is not None and list(header) != COLUMNS:
            raise ValueError(
                f"{path} header does not match the current CRM schema.\n"
                f"  expected: {COLUMNS}\n"
                f"  found:    {list(header)}\n"
                "Regenerate the pipeline (re-run `squad research`) or migrate the file "
                "to the current columns."
            )
        return [_conform(row) for row in reader]


def save(rows: list[dict[str, str]], path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_conform(row))


def _conform(row: dict[str, str]) -> dict[str, str]:
    """Ensure a row has exactly COLUMNS keys (missing -> "")."""
    return {col: (row.get(col) or "") for col in COLUMNS}


def _key(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("Company", "").strip().lower(), row.get("Email", "").strip().lower())


def append(
    new_rows: list[dict[str, str]], path: Path | str = DEFAULT_PATH
) -> list[dict[str, str]]:
    """Append rows, deduping by (Company, Email). Returns the rows actually written."""
    existing = load(path)
    seen = {_key(r) for r in existing}
    added: list[dict[str, str]] = []
    for row in new_rows:
        key = _key(row)
        if key in seen:
            continue
        seen.add(key)
        added.append(_conform(row))
    if added:
        save(existing + added, path)
    return added


def find_by_company(
    rows: list[dict[str, str]], company: str
) -> dict[str, str] | None:
    target = company.strip().lower()
    for row in rows:
        if row.get("Company", "").strip().lower() == target:
            return row
    return None


def update_row(
    company: str, updates: dict[str, str], path: Path | str = DEFAULT_PATH
) -> dict[str, str]:
    """Apply updates to the row matching company (case-insensitive). Raises if absent."""
    rows = load(path)
    row = find_by_company(rows, company)
    if row is None:
        raise ValueError(f"no pipeline row for company: {company!r} (path={path})")
    row.update(updates)
    save(rows, path)
    return row


def mark_sent(
    company: str, day: int = 1, path: Path | str = DEFAULT_PATH
) -> dict[str, str]:
    """Record a follow-up send. day1 also moves Status New->Contacted; day3/7 clear Follow Up Due."""
    if day not in (1, 3, 7):
        raise ValueError(f"day must be 1, 3, or 7 (got {day})")
    today = date.today().isoformat()
    updates: dict[str, str]
    if day == 1:
        updates = {"Day 1 Sent": today}
    elif day == 3:
        updates = {"Day 3 Sent": today, "Follow Up Due": ""}
    else:
        updates = {"Day 7 Sent": today, "Follow Up Due": ""}

    rows = load(path)
    row = find_by_company(rows, company)
    if row is None:
        raise ValueError(f"no pipeline row for company: {company!r} (path={path})")
    row.update(updates)
    if day == 1 and row.get("Status") == "New":
        row["Status"] = "Contacted"
    save(rows, path)
    return row
