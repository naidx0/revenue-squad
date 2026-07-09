import json
from datetime import date

import httpx
import pytest

from revenue_squad import crm
from revenue_squad.notion import (
    NOTION_VERSION,
    NotionBackend,
    NotionError,
    build_schema,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _backend(handler):
    return NotionBackend("secret_x", "ds_1", client=_client(handler))


def _title(text):
    return {"title": [{"type": "text", "text": {"content": text}, "plain_text": text}]}


def _page(company, **props):
    properties = {"Company": _title(company)}
    properties.update(props)
    return {"object": "page", "id": f"page_{company.lower()}", "properties": properties}


# --- env / config ---

def test_from_env_missing_token_raises(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "ds_1")
    with pytest.raises(NotionError, match="NOTION_TOKEN is not set"):
        NotionBackend.from_env()


def test_from_env_missing_data_source_raises(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_x")
    monkeypatch.delenv("NOTION_DATA_SOURCE_ID", raising=False)
    with pytest.raises(NotionError, match="NOTION_DATA_SOURCE_ID is not set"):
        NotionBackend.from_env()


def test_from_env_blank_token_raises(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "   ")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "ds_1")
    with pytest.raises(NotionError, match="NOTION_TOKEN is not set"):
        NotionBackend.from_env()


# --- query / load ---

def test_load_queries_and_decodes():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/data_sources/ds_1/query"
        return httpx.Response(200, json={"results": [
            _page(
                "Acme",
                Email={"email": "a@acme.com"},
                Status={"select": {"name": "New"}},
                Blocked={"checkbox": True},
                **{"Lead Score": {"number": 8}},
            ),
        ], "has_more": False})

    rows = _backend(handler).load()
    assert rows[0]["Company"] == "Acme"
    assert rows[0]["Email"] == "a@acme.com"
    assert rows[0]["Status"] == "New"
    assert rows[0]["Lead Score"] == "8"
    assert rows[0]["Blocked"] == "yes"


def test_load_paginates():
    calls = {"n": 0}

    def handler(request):
        body = json.loads(request.content)
        calls["n"] += 1
        if "start_cursor" not in body:
            return httpx.Response(200, json={
                "results": [_page("A")], "has_more": True, "next_cursor": "c2",
            })
        assert body["start_cursor"] == "c2"
        return httpx.Response(200, json={"results": [_page("B")], "has_more": False})

    rows = _backend(handler).load()
    assert [r["Company"] for r in rows] == ["A", "B"]
    assert calls["n"] == 2


def test_request_sends_auth_and_version_headers():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["version"] = request.headers.get("Notion-Version")
        return httpx.Response(200, json={"results": [], "has_more": False})

    _backend(handler).load()
    assert seen["auth"] == "Bearer secret_x"
    assert seen["version"] == NOTION_VERSION


def test_http_error_surfaces_status_and_body():
    def handler(request):
        return httpx.Response(500, text="internal boom")

    with pytest.raises(NotionError) as exc:
        _backend(handler).load()
    assert "HTTP 500" in str(exc.value)
    assert "internal boom" in str(exc.value)


# --- create / append ---

def test_append_creates_new_and_skips_dupes():
    created = []

    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [
                _page("Acme", Email={"email": "a@acme.com"}),
            ], "has_more": False})
        assert request.url.path == "/v1/pages"
        body = json.loads(request.content)
        assert body["parent"] == {"type": "data_source_id", "data_source_id": "ds_1"}
        created.append(body["properties"]["Company"]["title"][0]["text"]["content"])
        return httpx.Response(200, json={"id": "new"})

    rows = [
        {**crm.empty_row(), "Company": "Acme", "Email": "a@acme.com"},      # dup -> skipped
        {**crm.empty_row(), "Company": "Globex", "Email": "g@globex.com"},  # new -> created
    ]
    added = _backend(handler).append(rows)
    assert [r["Company"] for r in added] == ["Globex"]
    assert created == ["Globex"]


def test_append_encodes_typed_properties():
    captured = {}

    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        captured.update(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "new"})

    row = {
        **crm.empty_row(),
        "Company": "Globex",
        "Email": "g@globex.com",
        "Phone": "555-1000",
        "Website": "https://globex.com",
        "Lead Score": "8",
        "Blocked": "",
    }
    _backend(handler).append([row])
    assert captured["Email"] == {"email": "g@globex.com"}
    assert captured["Phone"] == {"phone_number": "555-1000"}
    assert captured["Website"] == {"url": "https://globex.com"}
    assert captured["Lead Score"] == {"number": 8}
    assert captured["Status"] == {"select": {"name": "New"}}
    assert captured["Blocked"] == {"checkbox": False}
    assert captured["Deal Value"] == {"number": None}


# --- mark_sent / update ---

def test_mark_sent_day1_patches_status_and_date():
    patched = {}

    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [
                _page("Acme", Status={"select": {"name": "New"}}),
            ], "has_more": False})
        assert request.method == "PATCH"
        assert request.url.path == "/v1/pages/page_acme"
        patched.update(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page_acme"})

    row = _backend(handler).mark_sent("Acme", day=1)
    today = date.today().isoformat()
    assert row["Status"] == "Contacted"
    assert row["Day 1 Sent"] == today
    assert patched["Status"]["select"]["name"] == "Contacted"
    assert patched["Day 1 Sent"]["date"]["start"] == today


def test_mark_sent_day1_does_not_downgrade_non_new():
    patched = {}

    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [
                _page("Acme", Status={"select": {"name": "Replied"}}),
            ], "has_more": False})
        patched.update(json.loads(request.content)["properties"])
        return httpx.Response(200, json={"id": "page_acme"})

    row = _backend(handler).mark_sent("Acme", day=1)
    assert row["Status"] == "Replied"
    assert "Status" not in patched


def test_mark_sent_missing_company_raises():
    def handler(request):
        return httpx.Response(200, json={"results": [], "has_more": False})

    with pytest.raises(ValueError, match="no notion row"):
        _backend(handler).mark_sent("Ghost", day=1)


def test_mark_sent_bad_day_raises():
    def handler(request):  # never called: day is validated first
        raise AssertionError("should not hit the network")

    with pytest.raises(ValueError, match="day must be"):
        _backend(handler).mark_sent("Acme", day=2)


# --- notion-init schema ---

def test_create_database_builds_exact_schema(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_x")
    captured = {}

    def handler(request):
        assert request.url.path == "/v1/databases"
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "db_1", "data_sources": [{"id": "ds_9", "name": "x"}],
        })

    out = NotionBackend.create_database("parent_1", client=_client(handler))
    assert out == {"database_id": "db_1", "data_source_id": "ds_9"}

    assert captured["parent"] == {"type": "page_id", "page_id": "parent_1"}
    props = captured["initial_data_source"]["properties"]
    # Property names are crm.COLUMNS verbatim and in order.
    assert list(props.keys()) == list(crm.COLUMNS)
    assert props["Company"] == {"title": {}}
    assert props["Contact"] == {"rich_text": {}}
    assert props["Email"] == {"email": {}}
    assert props["Phone"] == {"phone_number": {}}
    assert props["Website"] == {"url": {}}
    assert props["Lead Score"] == {"number": {"format": "number"}}
    assert props["Day 1 Sent"] == {"date": {}}
    assert props["Blocked"] == {"checkbox": {}}
    status_opts = [o["name"] for o in props["Status"]["select"]["options"]]
    assert status_opts == list(crm.STATUSES)
    vert_opts = [o["name"] for o in props["Vertical"]["select"]["options"]]
    assert vert_opts[0] == "Law" and vert_opts[-1] == "Other"
    assert props["Service Line"] == {"select": {"options": []}}


def test_create_database_missing_token_raises(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(NotionError, match="NOTION_TOKEN is not set"):
        NotionBackend.create_database("parent_1")


def test_build_schema_covers_every_column():
    assert set(build_schema().keys()) == set(crm.COLUMNS)
