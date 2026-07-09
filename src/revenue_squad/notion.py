"""Notion REST CRM backend (direct httpx, no SDK). Fails loudly (AGENTS.md §5).

Pinned to Notion-Version 2025-09-03 — the version that split databases from
data sources. Rows live in a data source; NOTION_DATA_SOURCE_ID names it.
Property names/types map 1:1 from crm.COLUMNS. No retries, no fallbacks: any
missing env var or non-2xx response raises NotionError immediately.
"""

from __future__ import annotations

import os
from datetime import date

import httpx

from . import crm

NOTION_VERSION = "2025-09-03"
API_BASE = "https://api.notion.com/v1"
NOTION_TIMEOUT = 30.0
DB_TITLE = "Master Lead CRM"
_BODY_TAIL = 2000  # chars of the response body surfaced in errors

# Vertical select seeds (Service Line is operator-specific -> no preset options).
VERTICAL_OPTIONS = (
    "Law",
    "Real Estate",
    "Healthcare",
    "Home Services",
    "Accounting",
    "Auto",
    "Cleaning",
    "Other",
)

# Notion property type for every crm.COLUMNS entry (the schema's single mapping).
PROPERTY_TYPES = {
    "Company": "title",
    "Contact": "rich_text",
    "Email": "email",
    "Phone": "phone_number",
    "City": "rich_text",
    "Website": "url",
    "Status": "select",
    "Vertical": "select",
    "Service Line": "select",
    "Batch": "rich_text",
    "Lead Score": "number",
    "Score Rationale": "rich_text",
    "Deal Value": "number",
    "Day 1 Sent": "date",
    "Day 3 Sent": "date",
    "Day 7 Sent": "date",
    "Follow Up Due": "date",
    "Replied": "checkbox",
    "Reply Date": "date",
    "Call Booked": "checkbox",
    "Call Date": "date",
    "Notes": "rich_text",
    "Blocked": "checkbox",
}

if set(PROPERTY_TYPES) != set(crm.COLUMNS):
    raise RuntimeError("notion.PROPERTY_TYPES is out of sync with crm.COLUMNS")


class NotionError(RuntimeError):
    """Raised on a missing env var or any non-2xx Notion API response."""


def _require_token() -> str:
    token = os.environ.get("NOTION_TOKEN")
    if not token or not token.strip():
        raise NotionError(
            "NOTION_TOKEN is not set — create an internal integration at "
            "https://www.notion.so/my-integrations, copy its token, and "
            "`export NOTION_TOKEN=secret_...`."
        )
    return token.strip()


def _require_data_source_id() -> str:
    dsid = os.environ.get("NOTION_DATA_SOURCE_ID")
    if not dsid or not dsid.strip():
        raise NotionError(
            "NOTION_DATA_SOURCE_ID is not set — run "
            "`squad notion-init --parent-page-id <id>` to create the CRM database "
            "(or copy the data source id of an existing one), share that database "
            "with your integration, and `export NOTION_DATA_SOURCE_ID=...`."
        )
    return dsid.strip()


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _notion_request(client, headers, method, url, payload=None) -> dict:
    resp = client.request(method, url, headers=headers, json=payload)
    if resp.status_code >= 400:
        raise NotionError(
            f"Notion {method} {url} failed: HTTP {resp.status_code}. "
            f"body tail: {resp.text[-_BODY_TAIL:]}"
        )
    return resp.json()


# --- schema (notion-init) ---

def _select_options(col: str) -> list[dict[str, str]]:
    if col == "Status":
        return [{"name": s} for s in crm.STATUSES]
    if col == "Vertical":
        return [{"name": v} for v in VERTICAL_OPTIONS]
    return []  # Service Line: operator-specific, seeded on first write


def build_schema() -> dict:
    """The create-database property schema, keyed by crm.COLUMNS verbatim."""
    props: dict[str, dict] = {}
    for col in crm.COLUMNS:
        ptype = PROPERTY_TYPES[col]
        if ptype == "select":
            props[col] = {"select": {"options": _select_options(col)}}
        elif ptype == "number":
            props[col] = {"number": {"format": "number"}}
        else:
            props[col] = {ptype: {}}
    return props


# --- value <-> Notion property encoding ---

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


def _encode_value(ptype: str, value: str) -> dict:
    if ptype in ("title", "rich_text"):
        content = [{"type": "text", "text": {"content": value}}] if value else []
        return {ptype: content}
    if ptype in ("email", "phone_number", "url"):
        return {ptype: value or None}
    if ptype == "select":
        return {"select": {"name": value} if value else None}
    if ptype == "number":
        return {"number": _to_number(value)}
    if ptype == "date":
        return {"date": {"start": value} if value else None}
    if ptype == "checkbox":
        return {"checkbox": bool(value.strip())}
    raise NotionError(f"unmappable property type: {ptype!r}")  # unreachable given the schema


def _encode_properties(values: dict[str, str]) -> dict:
    return {col: _encode_value(PROPERTY_TYPES[col], val or "") for col, val in values.items()}


def _plain(rich) -> str:
    return "".join(
        (item.get("plain_text") or item.get("text", {}).get("content", ""))
        for item in (rich or [])
    )


def _decode_value(ptype: str, prop: dict) -> str:
    if ptype in ("title", "rich_text"):
        return _plain(prop.get(ptype))
    if ptype in ("email", "phone_number", "url"):
        return prop.get(ptype) or ""
    if ptype == "select":
        return (prop.get("select") or {}).get("name") or ""
    if ptype == "number":
        return _number_to_str(prop.get("number"))
    if ptype == "date":
        return (prop.get("date") or {}).get("start") or ""
    if ptype == "checkbox":
        return "yes" if prop.get("checkbox") else ""
    raise NotionError(f"unmappable property type: {ptype!r}")  # unreachable given the schema


def _decode_page(page: dict) -> dict[str, str]:
    props = page.get("properties", {})
    row = crm.empty_row()
    for col in crm.COLUMNS:
        if col in props:
            row[col] = _decode_value(PROPERTY_TYPES[col], props[col])
    return row


def _dedupe_key(row: dict[str, str]) -> tuple[str, str]:
    # Mirrors crm._key so the Notion append dedupes exactly like the CSV path.
    return (row.get("Company", "").strip().lower(), row.get("Email", "").strip().lower())


class NotionBackend:
    """CRM backend backed by a Notion data source (2025-09-03 API)."""

    def __init__(self, token: str, data_source_id: str, *, client=None) -> None:
        self._data_source_id = data_source_id
        self._headers = _headers(token)
        self._client = client or httpx.Client(timeout=NOTION_TIMEOUT)

    @classmethod
    def from_env(cls, *, client=None) -> "NotionBackend":
        return cls(_require_token(), _require_data_source_id(), client=client)

    def describe(self) -> str:
        return f"Notion data source {self._data_source_id}"

    def _query_all(self) -> list[dict]:
        url = f"{API_BASE}/data_sources/{self._data_source_id}/query"
        pages: list[dict] = []
        payload = {"page_size": 100}
        while True:
            data = _notion_request(self._client, self._headers, "POST", url, payload)
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                return pages
            payload = {"page_size": 100, "start_cursor": data["next_cursor"]}

    def load(self) -> list[dict[str, str]]:
        return [_decode_page(p) for p in self._query_all()]

    def _create_page(self, row: dict[str, str]) -> None:
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": self._data_source_id},
            "properties": _encode_properties({c: row.get(c, "") for c in crm.COLUMNS}),
        }
        _notion_request(self._client, self._headers, "POST", f"{API_BASE}/pages", payload)

    def append(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Create a page per new row, deduping by (Company, Email). Returns rows written."""
        seen = {_dedupe_key(_decode_page(p)) for p in self._query_all()}
        added: list[dict[str, str]] = []
        for row in rows:
            key = _dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            self._create_page(row)
            added.append(row)
        return added

    def _update_page(self, page_id: str, updates: dict[str, str]) -> None:
        payload = {"properties": _encode_properties(updates)}
        _notion_request(self._client, self._headers, "PATCH", f"{API_BASE}/pages/{page_id}", payload)

    def mark_sent(self, company: str, day: int = 1) -> dict[str, str]:
        """Record a follow-up send. Mirrors crm.mark_sent's transition rules on Notion."""
        if day not in (1, 3, 7):
            raise ValueError(f"day must be 1, 3, or 7 (got {day})")
        today = date.today().isoformat()
        if day == 1:
            updates = {"Day 1 Sent": today}
        elif day == 3:
            updates = {"Day 3 Sent": today, "Follow Up Due": ""}
        else:
            updates = {"Day 7 Sent": today, "Follow Up Due": ""}

        target = company.strip().lower()
        page = next(
            (p for p in self._query_all()
             if _decode_page(p).get("Company", "").strip().lower() == target),
            None,
        )
        if page is None:
            raise ValueError(
                f"no notion row for company: {company!r} (data_source={self._data_source_id})"
            )
        row = _decode_page(page)
        row.update(updates)
        if day == 1 and row.get("Status") == "New":
            row["Status"] = "Contacted"
            updates["Status"] = "Contacted"
        self._update_page(page["id"], updates)
        return row

    @classmethod
    def create_database(cls, parent_page_id: str, *, client=None) -> dict[str, str]:
        """Create the CRM database + its data source. Returns their ids."""
        headers = _headers(_require_token())
        client = client or httpx.Client(timeout=NOTION_TIMEOUT)
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": DB_TITLE}}],
            "initial_data_source": {"properties": build_schema()},
        }
        data = _notion_request(client, headers, "POST", f"{API_BASE}/databases", payload)
        sources = data.get("data_sources") or []
        if not sources:
            raise NotionError(f"create database returned no data_sources: {data}")
        return {"database_id": data.get("id", ""), "data_source_id": sources[0].get("id", "")}
