"""Gmail OAuth + bounce-sync tests. No network beyond in-process localhost (loopback
capture), no real browser: httpx is driven through MockTransport; the loopback server
is exercised with a real 127.0.0.1 GET fired from a thread."""

import base64
import hashlib
import json
import re
import stat
import threading
import urllib.parse

import httpx
import pytest

from revenue_squad import gmail


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def _dsn_part(body: str) -> dict:
    return {"mimeType": "message/delivery-status", "body": {"data": _b64url(body)}}


def _text_part(text: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": _b64url(text)}}


def _message(headers=None, parts=None, mime="multipart/report") -> dict:
    return {"id": "m1", "payload": {"mimeType": mime, "headers": headers or [], "parts": parts or []}}


# --- PKCE + auth URL ---

def test_make_pkce_pair_is_s256():
    verifier, challenge = gmail.make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)  # unreserved, URL-safe
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == expected
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge


def test_build_auth_url_params():
    url = gmail.build_auth_url("cid.apps", "http://127.0.0.1:5000", "chal-abc", "st-1")
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "https" and parsed.netloc == "accounts.google.com"
    q = urllib.parse.parse_qs(parsed.query)
    assert q["client_id"] == ["cid.apps"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5000"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge"] == ["chal-abc"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st-1"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    scope = q["scope"][0]
    assert "gmail.readonly" in scope and "gmail.compose" in scope


# --- client_secret.json parsing (installed + bare + malformed) ---

def test_load_client_secret_installed_shape(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"installed": {"client_id": "abc.apps", "client_secret": "shh"}}))
    assert gmail.load_client_secret(p) == ("abc.apps", "shh")


def test_load_client_secret_bare_shape(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"client_id": "abc.apps", "client_secret": "shh"}))
    assert gmail.load_client_secret(p) == ("abc.apps", "shh")


def test_load_client_secret_malformed_raises(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text("{ not json")
    with pytest.raises(gmail.GmailError) as exc:
        gmail.load_client_secret(p)
    assert "console.cloud.google.com" in str(exc.value)


def test_load_client_secret_missing_fields_raises(tmp_path):
    p = tmp_path / "cs.json"
    p.write_text(json.dumps({"installed": {"client_id": "abc.apps"}}))  # no client_secret
    with pytest.raises(gmail.GmailError, match="missing client_id/client_secret"):
        gmail.load_client_secret(p)


def test_load_client_secret_missing_file_raises(tmp_path):
    with pytest.raises(gmail.GmailError) as exc:
        gmail.load_client_secret(tmp_path / "nope.json")
    assert "console.cloud.google.com" in str(exc.value)


# --- token exchange / refresh ---

def test_exchange_code_happy():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["form"] = urllib.parse.parse_qs(request.content.decode())
        return httpx.Response(200, json={"refresh_token": "rt-1", "access_token": "at-1"})

    rt = gmail.exchange_code(
        "code-1", "verif", "cid", "sec", "http://127.0.0.1:9", client=_client(handler)
    )
    assert rt == "rt-1"
    assert captured["url"].startswith(gmail.TOKEN_URL)
    form = captured["form"]
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["code-1"]
    assert form["code_verifier"] == ["verif"]
    assert form["redirect_uri"] == ["http://127.0.0.1:9"]


def test_exchange_code_no_refresh_token_raises():
    def handler(request):
        return httpx.Response(200, json={"access_token": "at-1"})  # no refresh_token

    with pytest.raises(gmail.GmailError, match="no refresh_token"):
        gmail.exchange_code("c", "v", "id", "s", "http://127.0.0.1:9", client=_client(handler))


def test_refresh_access_token_happy():
    captured = {}

    def handler(request):
        captured["form"] = urllib.parse.parse_qs(request.content.decode())
        return httpx.Response(200, json={"access_token": "at-9"})

    at = gmail.refresh_access_token("rt", "cid", "sec", client=_client(handler))
    assert at == "at-9"
    assert captured["form"]["grant_type"] == ["refresh_token"]
    assert captured["form"]["refresh_token"] == ["rt"]


def test_refresh_access_token_invalid_grant_explains_7_days():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(gmail.GmailError) as exc:
        gmail.refresh_access_token("rt", "cid", "sec", client=_client(handler))
    msg = str(exc.value)
    assert "7 days" in msg
    assert "gmail-auth" in msg


def test_refresh_access_token_other_error_raises():
    def handler(request):
        return httpx.Response(500, text="upstream boom")

    with pytest.raises(gmail.GmailError) as exc:
        gmail.refresh_access_token("rt", "cid", "sec", client=_client(handler))
    assert "HTTP 500" in str(exc.value)
    assert "upstream boom" in str(exc.value)


# --- loopback redirect capture (real localhost GET, in-thread) ---

def _fire_get(port, query):
    def go():
        try:
            httpx.get(f"http://127.0.0.1:{port}/?{query}", timeout=5)
        except httpx.HTTPError:
            pass  # the assertion under test is on the captured state, not the client
    t = threading.Thread(target=go)
    t.start()
    return t


def test_loopback_captures_code():
    server = gmail.make_callback_server()
    port = server.server_address[1]
    t = _fire_get(port, "code=the-code&state=st-1")
    try:
        code = gmail.capture_authorization_code(server, "st-1", timeout=5)
    finally:
        t.join()
        server.server_close()
    assert code == "the-code"


def test_loopback_state_mismatch_raises():
    server = gmail.make_callback_server()
    port = server.server_address[1]
    t = _fire_get(port, "code=c&state=WRONG")
    try:
        with pytest.raises(gmail.GmailError, match="state mismatch"):
            gmail.capture_authorization_code(server, "expected", timeout=5)
    finally:
        t.join()
        server.server_close()


def test_loopback_error_param_raises():
    server = gmail.make_callback_server()
    port = server.server_address[1]
    t = _fire_get(port, "error=access_denied&state=st")
    try:
        with pytest.raises(gmail.GmailError, match="authorization error"):
            gmail.capture_authorization_code(server, "st", timeout=5)
    finally:
        t.join()
        server.server_close()


# --- token file storage (0600) ---

def test_save_token_writes_0600(tmp_path):
    p = tmp_path / ".gmail-token.json"
    out = gmail._save_token("rt", "cid", "sec", path=p)
    assert out == p.resolve()
    assert json.loads(p.read_text()) == {
        "refresh_token": "rt", "client_id": "cid", "client_secret": "sec"
    }
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# --- bounce extraction (three fixtures: header, DSN, unparseable) + classification ---

def test_extract_bounce_header_path():
    dsn = (
        "Final-Recipient: rfc822;jane@acme.test\n"
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: smtp; 550 5.1.1 User unknown\n"
    )
    msg = _message(
        headers=[{"name": "X-Failed-Recipients", "value": "jane@acme.test"}],
        parts=[_text_part("Your message could not be delivered."), _dsn_part(dsn)],
    )
    bounce = gmail.extract_bounce(msg)
    assert bounce is not None
    assert bounce.recipient == "jane@acme.test"
    assert bounce.domain_failure is False
    assert "5.1.1" in bounce.diagnostic


def test_extract_bounce_dsn_path_prefers_original_recipient():
    dsn = (
        "Reporting-MTA: dns; mail.relay.test\n\n"
        "Final-Recipient: rfc822;forwarded@relay.test\n"
        "Original-Recipient: rfc822;jane@acme.test\n"
        "Action: failed\nStatus: 5.1.1\n"
    )
    bounce = gmail.extract_bounce(_message(parts=[_dsn_part(dsn)]))
    assert bounce is not None
    assert bounce.recipient == "jane@acme.test"  # Original preferred over Final
    assert bounce.domain_failure is False


def test_extract_bounce_unparseable_returns_none():
    msg = _message(parts=[_text_part("Delivery failed but no machine-readable recipient here.")])
    assert gmail.extract_bounce(msg) is None


def test_extract_bounce_domain_failure_classified():
    dsn = (
        "Final-Recipient: rfc822;bob@nodomain.test\n"
        "Action: failed\nStatus: 5.1.2\n"
        "Diagnostic-Code: smtp; 550 5.1.2 Host or domain name not found\n"
    )
    bounce = gmail.extract_bounce(_message(parts=[_dsn_part(dsn)]))
    assert bounce.recipient == "bob@nodomain.test"
    assert bounce.domain_failure is True


def test_extract_bounce_mailbox_failure_is_not_domain_level():
    dsn = (
        "Final-Recipient: rfc822;bob@realco.test\n"
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: smtp; 550 5.1.1 no such user\n"
    )
    bounce = gmail.extract_bounce(_message(parts=[_dsn_part(dsn)]))
    assert bounce.domain_failure is False


# --- sync_bounces orchestration ---

def _write_token(tmp_path):
    (tmp_path / ".gmail-token.json").write_text(
        json.dumps({"refresh_token": "rt", "client_id": "cid", "client_secret": "sec"})
    )


def test_sync_bounces_saves_unparseable_raw(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_token(tmp_path)
    good = "Final-Recipient: rfc822;a@x.test\nAction: failed\nStatus: 5.1.1\n"
    messages = {
        "m1": _message(parts=[_dsn_part(good)]),
        "m2": _message(parts=[_text_part("no recipient")]),
    }

    def handler(request):
        if str(request.url).startswith(gmail.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if request.url.path == "/gmail/v1/users/me/messages":
            return httpx.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        mid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=messages[mid])

    result = gmail.sync_bounces(client=_client(handler))
    assert [b.recipient for b in result.bounces] == ["a@x.test"]
    assert result.scanned == 2
    assert len(result.unparseable) == 1
    assert result.unparseable[0].is_file()
    assert result.unparseable[0].parent == (tmp_path / "out" / "raw").resolve()


def test_sync_bounces_no_token_raises_naming_auth(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(gmail.GmailError, match="gmail-auth"):
        gmail.sync_bounces()
