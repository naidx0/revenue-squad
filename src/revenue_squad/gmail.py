"""Gmail OAuth (installed-app PKCE + loopback) and bounce sync. Fails loudly (AGENTS.md §5).

No SDK — the OAuth dance is stdlib (`http.server`, `secrets`, `hashlib`, `base64`,
`webbrowser`) plus two httpx POSTs to oauth2.googleapis.com/token, mirroring
notion.py/places.py conventions (typed error class, injectable client, non-2xx
raises with a body tail). The refresh token lands in .gmail-token.json at repo root
(0600). Google expires refresh tokens for apps in 'Testing' publishing status after
7 days, so `squad gmail-sync-bounces` is a manual, weekly-re-authed step — never
folded into anything automatic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from email.parser import Parser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

# gmail.readonly is needed now (search + read bounces); gmail.compose is requested
# too so one consent screen also covers Phase 2 draft creation. Full scope URLs.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)

# The plan's exact multi-clause query — a single-sender match misses relayed bounces.
BOUNCE_QUERY = (
    'from:mailer-daemon OR subject:"Delivery Status Notification" OR '
    'subject:"Undelivered Mail Returned to Sender" OR subject:"Mail delivery failed"'
)

TOKEN_PATH = Path(".gmail-token.json")
GMAIL_TIMEOUT = 30.0
CALLBACK_TIMEOUT = 180  # seconds to wait for the browser redirect
_BODY_TAIL = 2000  # chars of the response body surfaced in errors

# Where to create/download the Desktop-app OAuth client — surfaced in every
# credential error so the operator knows exactly what to fix.
CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"

# RFC 3463 status codes that mean the *domain*, not just the mailbox, is dead.
_DOMAIN_FAILURE_STATUSES = ("5.1.2", "5.1.10", "5.4.4")
_DOMAIN_FAILURE_PHRASES = (
    "domain not found",
    "domain does not exist",
    "no mx",
    "mx record",
    "no such domain",
    "host unknown",
    "host or domain name not found",
    "unrouteable address",
    "unrouteable mail domain",
    "name service error",
    "nxdomain",
)


class GmailError(RuntimeError):
    """Raised on any Gmail credential/OAuth/API failure (never a silent fallback)."""


# --- PKCE ---

def make_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256.

    verifier: 43-char URL-safe string (RFC 7636 requires 43-128, unreserved chars).
    challenge: BASE64URL(SHA256(verifier)) with padding stripped.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, code_challenge: str, state: str) -> str:
    """Build the Google authorization URL. access_type=offline + prompt=consent so the
    exchange reliably returns a refresh_token even on a repeat authorization."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


# --- client_secret.json ---

def load_client_secret(path: Path | str) -> tuple[str, str]:
    """Return (client_id, client_secret) from a Google Desktop-app client_secret.json.

    Accepts both the wrapped `{"installed": {...}}` shape and a bare `{...}` object.
    Missing file / bad JSON / absent fields all raise loudly with the console URL.
    """
    p = Path(path)
    if not p.is_file():
        raise GmailError(
            f"client_secret.json not found at {p} — create a Desktop-app OAuth client "
            f"at {CONSOLE_URL} (Credentials -> Create credentials -> OAuth client ID -> "
            "Desktop app) and download its JSON."
        )
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise GmailError(
            f"client_secret.json at {p} is not valid JSON: {exc}. Re-download it from "
            f"{CONSOLE_URL}."
        ) from exc
    section = data.get("installed", data) if isinstance(data, dict) else {}
    client_id = str((section or {}).get("client_id", "")).strip()
    client_secret = str((section or {}).get("client_secret", "")).strip()
    if not client_id or not client_secret:
        raise GmailError(
            f"client_secret.json at {p} is missing client_id/client_secret — download a "
            f"Desktop-app OAuth client JSON from {CONSOLE_URL} (not an API key or a Web-app "
            "client)."
        )
    return client_id, client_secret


# --- loopback redirect capture ---

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        self.server.auth_code = (params.get("code") or [None])[0]  # type: ignore[attr-defined]
        self.server.auth_state = (params.get("state") or [None])[0]  # type: ignore[attr-defined]
        self.server.auth_error = (params.get("error") or [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body>revenue-squad: authorization received. "
            b"You can close this tab and return to the terminal.</body></html>"
        )

    def log_message(self, *args) -> None:  # silence stdlib request logging
        pass


def make_callback_server() -> HTTPServer:
    """One-shot loopback server on 127.0.0.1:<ephemeral port> for the OAuth redirect."""
    return HTTPServer(("127.0.0.1", 0), _CallbackHandler)


def capture_authorization_code(
    server: HTTPServer, expected_state: str, *, timeout: int = CALLBACK_TIMEOUT
) -> str:
    """Block for the single redirect, validate state/error, return the authorization code."""
    server.timeout = timeout
    server.auth_code = None  # type: ignore[attr-defined]
    server.auth_state = None  # type: ignore[attr-defined]
    server.auth_error = None  # type: ignore[attr-defined]
    server.handle_request()  # blocks until one request or `timeout`
    error = getattr(server, "auth_error", None)
    code = getattr(server, "auth_code", None)
    state = getattr(server, "auth_state", None)
    if error:
        raise GmailError(f"Google returned an authorization error: {error!r}. Re-run `squad gmail-auth`.")
    if code is None:
        raise GmailError(
            f"no authorization code received within {timeout}s. Re-run `squad gmail-auth` "
            "and complete the consent screen in the browser."
        )
    if state != expected_state:
        raise GmailError(
            "OAuth state mismatch (possible CSRF) — aborting. Re-run `squad gmail-auth`."
        )
    return code


# --- token exchange / refresh ---

def exchange_code(
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    *,
    client: httpx.Client,
) -> str:
    """Exchange an authorization code for a refresh token (PKCE). Returns the refresh_token."""
    resp = client.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    if resp.status_code >= 400:
        raise GmailError(
            f"Gmail authorization-code exchange failed: HTTP {resp.status_code}. "
            f"body tail: {resp.text[-_BODY_TAIL:]}"
        )
    refresh_token = resp.json().get("refresh_token")
    if not refresh_token:
        raise GmailError(
            "token exchange returned no refresh_token. Revoke the app at "
            "https://myaccount.google.com/permissions and re-run `squad gmail-auth` "
            "(the auth URL already requests offline access + a fresh consent)."
        )
    return refresh_token


def refresh_access_token(
    refresh_token: str, client_id: str, client_secret: str, *, client: httpx.Client
) -> str:
    """Mint a short-lived access token from the stored refresh token."""
    resp = client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if resp.status_code >= 400:
        body = resp.text
        if "invalid_grant" in body:
            raise GmailError(
                "Gmail refresh token is invalid or expired (invalid_grant). Google expires "
                "refresh tokens for OAuth apps in 'Testing' publishing status after 7 days. "
                "Re-authorize with:\n  squad gmail-auth --client-secret <path>\n"
                f"body tail: {body[-_BODY_TAIL:]}"
            )
        raise GmailError(
            f"Gmail token refresh failed: HTTP {resp.status_code}. body tail: {body[-_BODY_TAIL:]}"
        )
    access_token = resp.json().get("access_token")
    if not access_token:
        raise GmailError(
            f"Gmail token refresh returned no access_token. body tail: {resp.text[-_BODY_TAIL:]}"
        )
    return access_token


# --- token file storage (0600) ---

def _save_token(
    refresh_token: str, client_id: str, client_secret: str, path: Path | str = TOKEN_PATH
) -> Path:
    """Atomically write the token JSON (temp + rename), then enforce 0600."""
    path = Path(path)
    payload = json.dumps(
        {"refresh_token": refresh_token, "client_id": client_id, "client_secret": client_secret},
        indent=2,
    )
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".gmail-token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.chmod(path, 0o600)
    return path.resolve()


def _load_token(path: Path | str = TOKEN_PATH) -> dict[str, str]:
    p = Path(path)
    if not p.is_file():
        raise GmailError(
            f"no Gmail token at {p} — run `squad gmail-auth --client-secret <path>` first. "
            "(Apps in Google 'Testing' status expire the refresh token every 7 days, so you "
            "re-run gmail-auth weekly.)"
        )
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise GmailError(
            f"Gmail token at {p} is corrupt JSON: {exc}. Re-run `squad gmail-auth`."
        ) from exc
    for key in ("refresh_token", "client_id", "client_secret"):
        if not data.get(key):
            raise GmailError(
                f"Gmail token at {p} is missing {key!r}. Re-run `squad gmail-auth`."
            )
    return data


# --- authorization dance (orchestration) ---

def authorize(
    client_secret_path: Path | str,
    *,
    client: httpx.Client | None = None,
    open_browser: bool = True,
    echo=print,
) -> Path:
    """Run the full installed-app PKCE + loopback flow. Returns the saved token path.

    Raises GmailError on any failure (bad credentials, no code, exchange failure).
    """
    client_id, client_secret = load_client_secret(client_secret_path)
    verifier, challenge = make_pkce_pair()
    server = make_callback_server()
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}"
    state = secrets.token_urlsafe(24)
    auth_url = build_auth_url(client_id, redirect_uri, challenge, state)

    echo("Authorize Gmail access in your browser. If it doesn't open, paste this URL:\n" + auth_url)
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception as exc:  # not fatal: the URL was already printed for manual use
            echo(f"(could not launch a browser automatically: {exc}; use the URL above)")

    try:
        code = capture_authorization_code(server, state)
    finally:
        server.server_close()

    http = client or httpx.Client(timeout=GMAIL_TIMEOUT)
    refresh_token = exchange_code(
        code, verifier, client_id, client_secret, redirect_uri, client=http
    )
    return _save_token(refresh_token, client_id, client_secret)


# --- bounce extraction (RFC 3464) ---

@dataclass
class Bounce:
    recipient: str          # the failed recipient email, lowercased
    domain_failure: bool    # True when the diagnostic points at a dead domain, not a mailbox
    diagnostic: str = ""    # Status / Diagnostic-Code hint, for the blocklist reason


def _iter_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _iter_parts(part)


def _first_header(payload: dict, name: str) -> str | None:
    name_l = name.lower()
    for part in _iter_parts(payload):
        for header in part.get("headers", []) or []:
            if (header.get("name") or "").lower() == name_l:
                return header.get("value", "")
    return None


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _find_delivery_status(payload: dict) -> str | None:
    """Return the decoded body of the message/delivery-status part, if present."""
    for part in _iter_parts(payload):
        if (part.get("mimeType") or "").lower() == "message/delivery-status":
            data = (part.get("body") or {}).get("data")
            if data:
                return _b64url_decode(data)
    return None


def _parse_dsn_fields(text: str) -> dict[str, str]:
    """Parse an RFC 3464 delivery-status body into a lowercase field map.

    The body is header-like blocks (per-message, then per-recipient) separated by
    blank lines. Later blocks override earlier ones, so the per-recipient
    Final/Original-Recipient + Diagnostic-Code win over the per-message header.
    """
    fields: dict[str, str] = {}
    for block in re.split(r"\r?\n[ \t]*\r?\n", text):
        block = block.strip("\r\n")
        if not block:
            continue
        msg = Parser().parsestr(block, headersonly=True)
        for key, value in msg.items():
            fields[key.lower()] = " ".join(value.split()).strip()
    return fields


def _clean_email(value: str) -> str:
    match = re.search(r"[^\s<>]+@[^\s<>]+", value or "")
    if not match:
        return ""
    return match.group(0).strip("<>.,;").lower()


def _address_from_dsn_field(value: str) -> str:
    # "rfc822; user@example.com" or bare "user@example.com".
    if ";" in value:
        value = value.split(";", 1)[1]
    return _clean_email(value)


def _classify_domain_failure(fields: dict[str, str]) -> bool:
    status = (fields.get("status") or "").strip()
    if status in _DOMAIN_FAILURE_STATUSES:
        return True
    blob = " ".join(
        (fields.get(k) or "") for k in ("diagnostic-code", "status", "action")
    ).lower()
    return any(phrase in blob for phrase in _DOMAIN_FAILURE_PHRASES)


def _diagnostic_text(fields: dict[str, str]) -> str:
    parts = []
    if fields.get("status"):
        parts.append(fields["status"])
    if fields.get("diagnostic-code"):
        parts.append(fields["diagnostic-code"])
    return " | ".join(parts)


def extract_bounce(message: dict) -> Bounce | None:
    """Extract the failed recipient from a Gmail message JSON, or None if unparseable.

    Order: (1) X-Failed-Recipients header; (2) RFC 3464 message/delivery-status part,
    preferring Original-Recipient over Final-Recipient. Diagnostic-Code/Status classify
    a domain-level failure. Never guesses — an unfound recipient returns None.
    """
    payload = message.get("payload", {}) or {}
    dsn = _find_delivery_status(payload)
    fields = _parse_dsn_fields(dsn) if dsn else {}

    header_value = _first_header(payload, "X-Failed-Recipients")
    if header_value:
        recipient = _clean_email(header_value.split(",")[0])
        if recipient:
            return Bounce(recipient, _classify_domain_failure(fields), _diagnostic_text(fields))

    raw = fields.get("original-recipient") or fields.get("final-recipient")
    if raw:
        recipient = _address_from_dsn_field(raw)
        if recipient:
            return Bounce(recipient, _classify_domain_failure(fields), _diagnostic_text(fields))

    return None


# --- bounce sync (Gmail API) ---

@dataclass
class BounceSyncResult:
    bounces: list[Bounce] = field(default_factory=list)
    unparseable: list[Path] = field(default_factory=list)
    scanned: int = 0


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _list_message_ids(access_token: str, *, client: httpx.Client) -> list[str]:
    headers = _auth_headers(access_token)
    ids: list[str] = []
    params = {"q": BOUNCE_QUERY, "maxResults": 100}
    while True:
        resp = client.get(MESSAGES_URL, headers=headers, params=params)
        if resp.status_code >= 400:
            raise GmailError(
                f"Gmail messages.list failed: HTTP {resp.status_code}. "
                f"body tail: {resp.text[-_BODY_TAIL:]}"
            )
        data = resp.json()
        ids.extend(m["id"] for m in data.get("messages", []) if m.get("id"))
        next_token = data.get("nextPageToken")
        if not next_token:
            return ids
        params = {"q": BOUNCE_QUERY, "maxResults": 100, "pageToken": next_token}


def _get_message(access_token: str, message_id: str, *, client: httpx.Client) -> dict:
    resp = client.get(
        f"{MESSAGES_URL}/{message_id}",
        headers=_auth_headers(access_token),
        params={"format": "full"},
    )
    if resp.status_code >= 400:
        raise GmailError(
            f"Gmail messages.get({message_id}) failed: HTTP {resp.status_code}. "
            f"body tail: {resp.text[-_BODY_TAIL:]}"
        )
    return resp.json()


def _save_raw_message(message: dict) -> Path:
    """Save an unparseable bounce's full JSON under out/raw/ (mirrors runner._save_raw)."""
    raw_dir = Path("out") / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    raw_path = (raw_dir / f"gmail-bounce-{ts}.json").resolve()
    raw_path.write_text(json.dumps(message, indent=2))
    return raw_path


def sync_bounces(*, client: httpx.Client | None = None, token_path: Path | str = TOKEN_PATH) -> BounceSyncResult:
    """Refresh the access token, scan for bounces, and extract failed recipients.

    Unparseable bounces are saved to out/raw/ (path recorded) and reported by the
    caller; the run still returns partial results so good bounces are never lost.
    """
    token = _load_token(token_path)
    http = client or httpx.Client(timeout=GMAIL_TIMEOUT)
    access_token = refresh_access_token(
        token["refresh_token"], token["client_id"], token["client_secret"], client=http
    )
    ids = _list_message_ids(access_token, client=http)
    result = BounceSyncResult(scanned=len(ids))
    for message_id in ids:
        message = _get_message(access_token, message_id, client=http)
        bounce = extract_bounce(message)
        if bounce is None:
            result.unparseable.append(_save_raw_message(message))
        else:
            result.bounces.append(bounce)
    return result
