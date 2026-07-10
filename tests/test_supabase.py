"""SupabaseBackend tests — full CrmBackend matrix mirroring test_notion.py, all mocked
via httpx.MockTransport (no network). Covers env guards, load pagination/decoding,
append dedupe + typed encoding, mark_sent transitions, the column-mapping guard, and
`supabase-init` verification (reachable + table-missing branches)."""

import json
import urllib.parse
from datetime import date

import httpx
import pytest

from revenue_squad import crm, supabase
from revenue_squad.supabase import COLUMN_MAP, SupabaseBackend, SupabaseError


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _backend(handler):
    return SupabaseBackend("https://ref.supabase.co", "svc_key", client=_client(handler))


def _record(company, email="", **db_overrides):
    """A minimal PostgREST record (snake_case db columns) with a stable id."""
    rec = {"id": abs(hash(company)) % 10_000, "company": company, "email": email}
    rec.update(db_overrides)
    return rec


def _params(request):
    return urllib.parse.parse_qs(request.url.query.decode())


# --- env / config ---

def test_from_env_missing_url_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    with pytest.raises(SupabaseError, match="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"):
        SupabaseBackend.from_env()


def test_from_env_missing_key_raises(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://ref.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(SupabaseError, match="service_role"):
        SupabaseBackend.from_env()


def test_from_env_blank_values_raise(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "   ")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    with pytest.raises(SupabaseError):
        SupabaseBackend.from_env()


# --- query / load ---

def test_load_decodes_typed_columns():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/pipeline"
        return httpx.Response(200, json=[_record(
            "Acme", "a@acme.com",
            status="New", lead_score=8, blocked=True, day_1_sent="2026-07-01",
        )])

    rows = _backend(handler).load()
    assert rows[0]["Company"] == "Acme"
    assert rows[0]["Email"] == "a@acme.com"
    assert rows[0]["Status"] == "New"
    assert rows[0]["Lead Score"] == "8"        # numeric -> str
    assert rows[0]["Blocked"] == "yes"          # boolean True -> "yes"
    assert rows[0]["Day 1 Sent"] == "2026-07-01"


def test_load_paginates(monkeypatch):
    monkeypatch.setattr(supabase, "PAGE_SIZE", 2)
    seen_offsets = []

    def handler(request):
        params = _params(request)
        offset = int(params["offset"][0])
        seen_offsets.append(offset)
        assert params["order"] == ["id.asc"]
        if offset == 0:
            return httpx.Response(200, json=[_record("A"), _record("B")])  # full page
        return httpx.Response(200, json=[_record("C")])  # short page -> stop

    rows = _backend(handler).load()
    assert [r["Company"] for r in rows] == ["A", "B", "C"]
    assert seen_offsets == [0, 2]


def test_load_sends_apikey_and_bearer():
    seen = {}

    def handler(request):
        seen["apikey"] = request.headers.get("apikey")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=[])

    _backend(handler).load()
    assert seen["apikey"] == "svc_key"
    assert seen["auth"] == "Bearer svc_key"


def test_http_error_surfaces_status_and_body():
    def handler(request):
        return httpx.Response(500, text="internal boom")

    with pytest.raises(SupabaseError) as exc:
        _backend(handler).load()
    assert "HTTP 500" in str(exc.value)
    assert "internal boom" in str(exc.value)


# --- create / append ---

def test_append_inserts_new_and_skips_dupes():
    inserted = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[_record("Acme", "a@acme.com")])
        assert request.method == "POST"
        assert request.headers.get("Prefer") == "return=representation"
        inserted["body"] = json.loads(request.content)
        return httpx.Response(201, json=inserted["body"])

    rows = [
        {**crm.empty_row(), "Company": "Acme", "Email": "a@acme.com"},      # dup -> skipped
        {**crm.empty_row(), "Company": "Globex", "Email": "g@globex.com"},  # new -> inserted
    ]
    added = _backend(handler).append(rows)
    assert [r["Company"] for r in added] == ["Globex"]
    assert [r["company"] for r in inserted["body"]] == ["Globex"]


def test_append_encodes_typed_columns():
    captured = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=captured["body"])

    row = {
        **crm.empty_row(),
        "Company": "Globex",
        "Email": "g@globex.com",
        "Email Evidence": "https://globex.com/team",
        "Lead Score": "8",
        "Deal Value": "",
        "Blocked": "",
        "Replied": "yes",
    }
    _backend(handler).append([row])
    record = captured["body"][0]
    assert record["company"] == "Globex"
    assert record["email_evidence"] == "https://globex.com/team"  # text
    assert record["lead_score"] == 8            # numeric
    assert record["deal_value"] is None          # empty numeric -> null
    assert record["blocked"] is False            # empty boolean -> False
    assert record["replied"] is True             # non-empty boolean -> True
    assert record["status"] == "New"


def test_append_nothing_new_makes_no_post():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[_record("Acme", "a@acme.com")])
        raise AssertionError("must not POST when there is nothing new to insert")

    added = _backend(handler).append(
        [{**crm.empty_row(), "Company": "Acme", "Email": "a@acme.com"}]
    )
    assert added == []


# --- mark_sent / update ---

def test_mark_sent_day1_patches_status_and_date():
    patched = {}

    def handler(request):
        if request.method == "GET":
            params = _params(request)
            assert params["company"] == ["ilike.Acme"]  # case-insensitive lookup
            return httpx.Response(200, json=[_record("Acme", status="New")])
        assert request.method == "PATCH"
        patched["params"] = _params(request)
        patched["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{}])

    row = _backend(handler).mark_sent("Acme", day=1)
    today = date.today().isoformat()
    assert row["Status"] == "Contacted"
    assert row["Day 1 Sent"] == today
    assert patched["body"]["status"] == "Contacted"
    assert patched["body"]["day_1_sent"] == today
    assert patched["params"]["id"][0].startswith("eq.")


def test_mark_sent_day1_does_not_downgrade_non_new():
    patched = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[_record("Acme", status="Replied")])
        patched["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{}])

    row = _backend(handler).mark_sent("Acme", day=1)
    assert row["Status"] == "Replied"
    assert "status" not in patched["body"]


def test_mark_sent_day3_clears_follow_up_due():
    patched = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[_record("Acme", status="Contacted")])
        patched["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{}])

    row = _backend(handler).mark_sent("Acme", day=3)
    assert row["Day 3 Sent"] == date.today().isoformat()
    assert row["Follow Up Due"] == ""
    assert patched["body"]["day_3_sent"] == date.today().isoformat()
    assert patched["body"]["follow_up_due"] is None  # cleared date -> null


def test_mark_sent_missing_company_raises():
    def handler(request):
        return httpx.Response(200, json=[])

    with pytest.raises(ValueError, match="no supabase row"):
        _backend(handler).mark_sent("Ghost", day=1)


def test_mark_sent_bad_day_raises():
    def handler(request):  # never called: day validated first
        raise AssertionError("should not hit the network")

    with pytest.raises(ValueError, match="day must be"):
        _backend(handler).mark_sent("Acme", day=2)


# --- column-mapping guard + describe ---

def test_column_map_covers_every_crm_column():
    assert set(COLUMN_MAP) == set(crm.COLUMNS)
    assert COLUMN_MAP["Email Evidence"] == "email_evidence"
    assert COLUMN_MAP["Day 1 Sent"] == "day_1_sent"


def test_describe_names_the_table():
    assert "pipeline" in _backend(lambda r: httpx.Response(200, json=[])).describe()


# --- supabase-init verification (reachable + table-missing) ---

def _verify_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://ref.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_key")


def test_verify_table_reachable_returns(monkeypatch):
    _verify_env(monkeypatch)

    def handler(request):
        assert _params(request)["limit"] == ["1"]
        return httpx.Response(200, json=[])

    supabase.verify_table(client=_client(handler))  # no raise == reachable


def test_verify_table_missing_names_schema_file(monkeypatch):
    _verify_env(monkeypatch)

    def handler(request):
        return httpx.Response(404, text='{"code":"PGRST205","message":"table not found"}')

    with pytest.raises(SupabaseError, match="supabase_schema.sql"):
        supabase.verify_table(client=_client(handler))


def test_verify_table_missing_env_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(SupabaseError, match="must both be set"):
        supabase.verify_table()
