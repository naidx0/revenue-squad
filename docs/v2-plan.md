# revenue-squad — v2 plan (integrations + bounded autonomy)

**Goal:** v2 ships four things — Gmail bounce-sync into the blocklist, optional Gmail draft creation, a Supabase CRM adapter proving the pluggable seam, and a `squad daily` command that runs the whole daily loop with everything staged for human review — all in v1's exact conventions (raw httpx, env-var config, fail-loud, mocked tests, adapters behind `CrmBackend`).

## Decisions (settled by research — sources in the v2 research report)

| Decision | Choice | Why |
|---|---|---|
| Gmail auth | OAuth installed-app flow, PKCE + loopback redirect, implemented RAW: stdlib `http.server` + `secrets`/`hashlib` + two httpx POSTs to `oauth2.googleapis.com/token`. No google-auth libs | Matches v1's no-SDK rule; device flow is blocked for Gmail by Google; ~60–80 lines, mockable |
| Gmail credential reality | Scopes `gmail.readonly` + `gmail.compose` are RESTRICTED; unverified Testing apps: refresh token expires every 7 days | Therefore bounce sync is a manual `squad gmail-sync-bounces`, re-authed weekly via `squad gmail-auth`; NEVER folded silently into `daily`. README states the 7-day reality plainly |
| Bounce extraction | Try `X-Failed-Recipients` header; else parse RFC 3464 `message/delivery-status` part with stdlib `email` (`Original-Recipient` preferred over `Final-Recipient`); unparseable → save raw to `out/raw/` + loud per-message error, never guess | Deterministic, no fabrication |
| Bounce query | `from:mailer-daemon OR subject:"Delivery Status Notification" OR subject:"Undelivered Mail Returned to Sender" OR subject:"Mail delivery failed"` | Single-sender match misses relayed bounces |
| Blocklist policy | Always append the exact failed email; also append its domain when the DSN diagnostic indicates a domain-level failure (domain not found / no MX). Dedupe against existing entries; report every addition | Mirrors the operator's real "company-level blackout on hard bounce" rule without over-blocking freemail |
| Gmail drafts | `squad outreach --gmail-drafts`: POST `users/me/drafts` with base64url RFC 2822 built via stdlib `email.message` | One httpx call once a token exists |
| Token storage | `.gmail-token.json` at repo root, gitignored, 0600 perms | Same locality as blocklist/pipeline |
| Supabase adapter | `--crm supabase` via straight PostgREST: `{SUPABASE_URL}/rest/v1/pipeline`, headers `apikey` + `Authorization: Bearer` with the **service_role** key (env `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`), `Prefer: return=representation`; dedupe normalized in Python like csv/notion | Structurally identical to notion.py; anon key is wrong for a trusted local CLI |
| Supabase init | Ship static `supabase_schema.sql` (24 columns + unique index for on_conflict) + `squad supabase-init` that prints instructions and then VERIFIES the table responds (GET limit 1 → actionable error naming the SQL file if missing). No Management API dependency | DDL isn't a PostgREST call; keep one credential type |
| `squad daily` | Bounded, propose-don't-execute: (1) optional `--gmail` bounce sync (loud error if unauthorized, loud skip-notice if flag absent), (2) research N new prospects (default 10 — the operator's real daily quota) for configured location/vertical, (3) surface follow-ups due (Day 3/7 from pipeline dates), (4) stage outreach drafts for eligible leads. Ends with a review summary. NOTHING is ever sent; `mark-sent` stays the only state-advancing human act | The approval-queue pattern v1 already embodies |
| Daily config | `squad daily` reads location/vertical/count from flags or a small `squad.toml` (`[daily] location=… vertical=… count=10`) — the first config file in the project; env-var-only doesn't fit multi-field recurring config | Explicit, versionable, still optional |

## Phases

**P1 — Gmail auth + bounce sync** (gmail.py: auth dance, token refresh, sync command; tests: mocked token endpoints, header-path + DSN-path + unparseable-path fixtures from real RFC 3464 samples). *Done when:* gate passes; `squad gmail-auth`/`gmail-sync-bounces` exist; blocklist additions logged loudly; 0-bounce run says so plainly.

**P2 — Gmail drafts + Supabase adapter** (parallelizable, disjoint: gmail drafts in gmail.py+cli.py marker section; supabase.py + schema file + backend wiring). *Done when:* gate passes; `--gmail-drafts` builds valid RFC 2822 (test decodes and asserts headers/body); `--crm supabase` passes the same backend test matrix notion has; `supabase-init` verifies-or-instructs.

**P3 — `squad daily`** + squad.toml parsing + follow-up-due computation (pure function over pipeline dates, heavily unit-tested). *Done when:* gate passes; a full staged `daily` run against a fixture pipeline produces the review summary with zero sends.

**P4 — Docs + live verification + codex loop.** README v2 sections (Gmail 7-day honesty, Supabase setup, daily loop philosophy); live-verify Supabase via a real project; live-verify Gmail with the operator's one-time GCP setup (his action — OAuth client creation); codex adversarial rounds to zero; version bump; push.

## Standing gate
```
uv sync --all-extras
uv run pytest -q --timeout=60 --timeout-method=thread --durations=10 --durations-min=1
uv run squad --help
```

## Explicitly NOT in v2 (roadmap only)
Apollo, GoHighLevel, Outlook/SMTP sending, any auto-send, any hosted version, Google app verification (CASA) — each listed in README roadmap with one honest line on why.
