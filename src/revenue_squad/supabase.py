"""Supabase (PostgREST) CRM backend — direct httpx, no SDK. Fails loudly (AGENTS.md §5).

Structurally identical to notion.py: same CrmBackend surface (load/append/mark_sent/
describe), the same Python-side (Company, Email) dedupe, a single column mapping guarded
against crm.COLUMNS at import time, and non-2xx responses that raise SupabaseError with a
body tail. Talks straight PostgREST at {SUPABASE_URL}/rest/v1/pipeline using the
service_role key (which bypasses RLS — the right credential for a trusted local CLI, and
the wrong one to ever expose to a browser). No retries, no fallbacks.
"""

from __future__ import annotations

import os
from datetime import date

import httpx

from . import crm

API_PATH = "/rest/v1/pipeline"
SUPABASE_TIMEOUT = 30.0
PAGE_SIZE = 1000  # PostgREST's default max window; load() pages until a short page.
_BODY_TAIL = 2000  # chars of the response body surfaced in errors

# snake_case DB column for every crm.COLUMNS entry (the schema's single mapping).
COLUMN_MAP = {
    "Company": "company",
    "Contact": "contact",
    "Email": "email",
    "Email Evidence": "email_evidence",
    "Phone": "phone",
    "City": "city",
    "Website": "website",
    "Status": "status",
    "Vertical": "vertical",
    "Service Line": "service_line",
    "Batch": "batch",
    "Lead Score": "lead_score",
    "Score Rationale": "score_rationale",
    "Deal Value": "deal_value",
    "Day 1 Sent": "day_1_sent",
    "Day 3 Sent": "day_3_sent",
    "Day 7 Sent": "day_7_sent",
    "Follow Up Due": "follow_up_due",
    "Replied": "replied",
    "Reply Date": "reply_date",
    "Call Booked": "call_booked",
    "Call Date": "call_date",
    "Notes": "notes",
    "Blocked": "blocked",
}

if set(COLUMN_MAP) != set(crm.COLUMNS):
    raise RuntimeError("supabase.COLUMN_MAP is out of sync with crm.COLUMNS")

# Typed columns; everything else is text. Keyed by crm.COLUMNS names.
_NUMBER_COLUMNS = {"Lead Score", "Deal Value"}
_BOOLEAN_COLUMNS = {"Replied", "Call Booked", "Blocked"}
_DATE_COLUMNS = {"Day 1 Sent", "Day 3 Sent", "Day 7 Sent", "Follow Up Due", "Reply Date", "Call Date"}


class SupabaseError(RuntimeError):
    """Raised on a missing env var or any non-2xx PostgREST response."""


def _require_env() -> tuple[str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SupabaseError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set. Find them in your "
            "Supabase project under Project Settings -> API: SUPABASE_URL is the Project URL, "
            "SUPABASE_SERVICE_ROLE_KEY is the service_role secret (NOT the anon/public key). "
            "Then `export SUPABASE_URL=https://<ref>.supabase.co "
            "SUPABASE_SERVICE_ROLE_KEY=...`."
        )
    return url.rstrip("/"), key


def _headers(key: str, *, write: bool = False) -> dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if write:
        headers["Prefer"] = "return=representation"
    return headers


# --- value <-> PostgREST JSON encoding ---

def _to_number(value: str):
    value = (value or "").strip()
    if not value:
        return None
    num = float(value)
    return int(num) if num.is_integer() else num


def _number_to_str(num) -> str:
    if num is None:
        return ""
    return str(int(num)) if float(num).is_integer() else str(num)


def _encode_cell(col: str, value: str):
    value = value or ""
    if col in _NUMBER_COLUMNS:
        return _to_number(value)
    if col in _BOOLEAN_COLUMNS:
        return bool(value.strip())
    if col in _DATE_COLUMNS:
        return value or None
    return value  # text (email/url/phone are plain text in Postgres)


def _decode_cell(col: str, value) -> str:
    if col in _NUMBER_COLUMNS:
        return _number_to_str(value)
    if col in _BOOLEAN_COLUMNS:
        return "yes" if value else ""
    if col in _DATE_COLUMNS:
        return value or ""
    return value if value is not None else ""


def _encode_row(row: dict[str, str]) -> dict:
    return {COLUMN_MAP[col]: _encode_cell(col, row.get(col, "")) for col in crm.COLUMNS}


def _encode_updates(updates: dict[str, str]) -> dict:
    return {COLUMN_MAP[col]: _encode_cell(col, val) for col, val in updates.items()}


def _decode_row(record: dict) -> dict[str, str]:
    row = crm.empty_row()
    for col in crm.COLUMNS:
        db_col = COLUMN_MAP[col]
        if db_col in record:
            row[col] = _decode_cell(col, record[db_col])
    return row


def _escape_like(value: str) -> str:
    """Escape Postgres LIKE/ILIKE wildcards so a PostgREST ilike filter matches literally.

    Without this a company name containing `_` or `%` (both LIKE wildcards) would match
    unrelated rows — e.g. `A_B Co` would ilike-match `AXB Co`. Backslash is the default
    LIKE escape char, so it is escaped first to avoid double-escaping the escapes we add.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _dedupe_key(row: dict[str, str]) -> tuple[str, str]:
    # Mirrors crm._key so the Supabase append dedupes exactly like the CSV/Notion paths.
    return (row.get("Company", "").strip().lower(), row.get("Email", "").strip().lower())


class SupabaseBackend:
    """CRM backend backed by a Supabase `pipeline` table via PostgREST."""

    def __init__(self, url: str, key: str, *, client=None) -> None:
        self._base = f"{url.rstrip('/')}{API_PATH}"
        self._key = key
        self._client = client or httpx.Client(timeout=SUPABASE_TIMEOUT)

    @classmethod
    def from_env(cls, *, client=None) -> "SupabaseBackend":
        url, key = _require_env()
        return cls(url, key, client=client)

    def describe(self) -> str:
        return f"Supabase pipeline table ({self._base})"

    def _request(self, method: str, *, params=None, json=None, write: bool = False):
        resp = self._client.request(
            method, self._base, headers=_headers(self._key, write=write), params=params, json=json
        )
        if resp.status_code >= 400:
            raise SupabaseError(
                f"Supabase {method} {self._base} failed: HTTP {resp.status_code}. "
                f"body tail: {resp.text[-_BODY_TAIL:]}"
            )
        return resp

    def load(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        offset = 0
        while True:
            resp = self._request(
                "GET",
                params={"select": "*", "order": "id.asc", "limit": PAGE_SIZE, "offset": offset},
            )
            batch = resp.json()
            rows.extend(_decode_row(rec) for rec in batch)
            if len(batch) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def append(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Insert new rows, deduping by (Company, Email) in Python. Returns rows written."""
        seen = {_dedupe_key(r) for r in self.load()}
        added: list[dict[str, str]] = []
        payload: list[dict] = []
        for row in rows:
            key = _dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            added.append(row)
            payload.append(_encode_row(row))
        if payload:
            self._request("POST", json=payload, write=True)
        return added

    def mark_sent(self, company: str, day: int = 1) -> dict[str, str]:
        """Record a follow-up send. Mirrors crm.mark_sent's transition rules on Supabase."""
        if day not in (1, 3, 7):
            raise ValueError(f"day must be 1, 3, or 7 (got {day})")
        today = date.today().isoformat()
        if day == 1:
            updates = {"Day 1 Sent": today}
        elif day == 3:
            updates = {"Day 3 Sent": today, "Follow Up Due": ""}
        else:
            updates = {"Day 7 Sent": today, "Follow Up Due": ""}

        # Case-insensitive company match via PostgREST ilike. Wildcards in the name are
        # escaped so the match is literal (no `_`/`%` fan-out onto other rows).
        resp = self._request(
            "GET",
            params={"select": "*", "company": f"ilike.{_escape_like(company.strip())}"},
        )
        records = resp.json()
        if not records:
            raise ValueError(
                f"no supabase row for company: {company!r} (table={self._base})"
            )
        if len(records) > 1:
            raise SupabaseError(
                f"ambiguous company match for {company!r}: {len(records)} rows matched "
                f"(table={self._base}) — refusing to guess which to update."
            )
        record = records[0]
        row = _decode_row(record)
        row.update(updates)
        if day == 1 and row.get("Status") == "New":
            row["Status"] = "Contacted"
            updates["Status"] = "Contacted"
        self._request(
            "PATCH",
            params={"id": f"eq.{record['id']}"},
            json=_encode_updates(updates),
            write=True,
        )
        return row


# --- schema verification (supabase-init) ---

def verify_table(*, client=None) -> None:
    """GET the pipeline table (limit 1) to confirm it exists and is reachable.

    Missing env -> loud (via _require_env). Table absent (PostgREST 404 / "does not
    exist") -> actionable SupabaseError naming supabase_schema.sql. Any other non-2xx
    raises with a body tail. Success returns None.
    """
    url, key = _require_env()
    http = client or httpx.Client(timeout=SUPABASE_TIMEOUT)
    base = f"{url}{API_PATH}"
    resp = http.get(base, headers=_headers(key), params={"select": "company", "limit": 1})
    if resp.status_code < 400:
        return
    body = resp.text
    if resp.status_code == 404 or "does not exist" in body or "PGRST205" in body:
        raise SupabaseError(
            "Supabase pipeline table not found. Create it by pasting supabase_schema.sql into "
            "the Supabase SQL editor (Project -> SQL Editor -> New query -> paste -> Run), then "
            f"re-run `squad supabase-init`. body tail: {body[-_BODY_TAIL:]}"
        )
    raise SupabaseError(
        f"Supabase table check failed: HTTP {resp.status_code}. body tail: {body[-_BODY_TAIL:]}"
    )
