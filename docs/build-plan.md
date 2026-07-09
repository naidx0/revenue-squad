# revenue-squad — v1 build plan

**Goal:** An open-source, clone-and-run CLI (`revenue-squad`, github.com/naidx0/revenue-squad, MIT) that runs a solo operator's outbound B2B workflow — prospect research → cold-email drafting → proposal generation — by driving the Claude CLI headless with three bundled, readable skills, logging every lead to a local CSV pipeline (Notion CRM adapter included), encoding the exact discipline proven by Sequence's own operation: verified emails only, MX-check every domain, cross-check blocklist before any outreach, Day 1/3/7 follow-up cadence.

## Decisions (settled — do not reopen)

| Decision | Choice | Why |
|---|---|---|
| Language/packaging | Python ≥3.11, `uv`, Typer CLI, hatchling | Mirrors naidx0/localleadfinder, the operator's existing OSS precedent |
| Claude mechanism | `subprocess` → `claude -p <task> --append-system-prompt-file <skill> --allowedTools ... --output-format json` | Verified available in claude CLI 2.1.186 on this machine |
| Skills | `skills/{research,outreach,proposal}/SKILL.md`, markdown with `name`+`description` frontmatter | Double as drop-in Claude Code skills (the operator's chosen packaging). Runner strips frontmatter when composing the headless prompt |
| CRM default | `pipeline.csv`, columns mirroring Sequence's real Notion "Master Lead CRM" **plus an `Email Evidence` URL column** | Zero-dependency, provable today. Evidence column added after live Phase 3 proof: research validated `email_evidence_url` then discarded it, so outreach (correctly) refused every lead — verified-with-evidence must survive the CRM round-trip |
| CRM adapter | Notion REST API (httpx, `NOTION_TOKEN`), incl. `squad notion-init` to create a compatible DB | Deterministic; no LLM in the CRM write path; fails loudly without token |
| Lead seeding | Optional Google Places seed (`GOOGLE_MAPS_API_KEY`), same API pattern as localleadfinder | the operator explicitly wants "attach your Google API"; optional, not required |
| Gmail | NOT in v1. Drafts are files; `squad mark-sent` records sends. Bounce history = operator-maintained `blocklist.txt` | No OAuth scope in v1; README states this honestly as roadmap |
| Email verification | Real MX lookup in the tool (dnspython) + email only accepted with `email_evidence_url` | Encode "never guess an address" as code, not prose |
| Status values | `New, Contacted, Replied, Call Booked, Proposal Sent, Won, Lost, Nurture` | Verbatim from Sequence's Master Lead CRM |
| License | MIT | the operator confirmed |
| No fallbacks | Nonzero claude exit, unparseable JSON, missing token → loud error with stderr tail + saved raw output path | AGENTS.md §5 |

## Interface contracts (builders A and B must both honor these exactly)

### research — claude output contract
Final message must end with one fenced ```json block:
```json
{"leads": [{"company": "", "website": "", "contact_name": null, "title": null,
  "email": null, "email_evidence_url": null, "phone": null, "city": "",
  "vertical": "", "score": 0, "score_rationale": "", "notes": ""}]}
```
Rules: `email` stays null unless found on a real fetched page; `email_evidence_url` is REQUIRED whenever `email` is set. CLI then: filters `blocklist.txt` (domain or exact email), MX-checks each email domain (failure → email demoted to null, noted loudly), appends survivors to pipeline as `Status=New`, writes `out/research-<slug>-<date>.json` + a markdown summary.

### outreach — claude output contract
Input: pipeline rows with `Status=New`, verified email, not blocked. Output per lead, one fenced ```json block:
```json
{"drafts": [{"company": "", "day1": {"subject": "", "body": ""},
  "day3": {"subject": "", "body": ""}, "day7": {"subject": "", "body": ""}}]}
```
CLI writes `out/outreach/<company-slug>.md` (To/Subject/Body per touch). CLI REFUSES (per-lead loud reason) any lead that is Blocked, Lost, blocklisted, or lacks a verified email with evidence URL. Drafting never changes Status — `squad mark-sent <company> [--day 1|3|7]` sets `Day N Sent` (+ `Status=Contacted` on day 1).

### propose — claude output contract
`squad propose <company> --notes <discovery-notes.md>` → claude returns a complete markdown proposal (no JSON wrapper) → CLI writes `out/proposals/<company-slug>.md`. Does not change Status.

## Phases

**Phase 1 — Core CLI (builder A) ∥ Skills (builder B), disjoint ownership.**
Done when: standing gate passes; `uv run squad --help` shows research/outreach/propose/mark-sent; unit tests cover CSV pipeline round-trip, blocklist matching, MX check (mocked DNS), runner JSON extraction (mocked subprocess), refusal paths; all three SKILL.md files exist with contracts matching this doc and zero client-identifying data from Sequence's Notion.

**Phase 2 — Notion REST adapter + Places seed.**
Done when: `--crm notion` writes/reads via REST with exact Master-Lead-CRM property names, `squad notion-init` creates a compatible DB, `--seed places` pulls candidates; both fully covered by mocked-HTTP tests; missing tokens fail loudly with actionable messages; gate passes.

**Phase 3 — Real end-to-end proof (no mocks).**
Run `squad research` for a real vertical+city (5 prospects), `squad outreach` on the survivors, `squad propose` on one. Done when: real command output, real `pipeline.csv`, and real generated files exist under `examples/demo-run/`, with a note stating exactly which paths ran live (Notion/Places live-verified only if tokens are provided).

**Phase 4 — Packaging.**
README to the localleadfinder standard (Why this exists / Features / Install / API-key setup / Usage / Skills / Output schema / Design decisions / What v1 does NOT do / License) with REAL demo output from Phase 3; MIT LICENSE; GitHub Actions CI running the gate; contributing note for pluggable adapters. Done when: a clean clone passes `uv sync && uv run pytest` and README claims match exactly what Phase 3 proved.

**Phase 5 — Adversarial review loop.**
Codex reviews full diff; triage; Opus fix pass with locking regression tests; re-gate; repeat until RELEASABLE with zero findings.

## Standing gate (after every phase)
```
uv sync --all-extras
uv run pytest -q --timeout=60 --timeout-method=thread --durations=10 --durations-min=1
uv run squad --help
```

## Honesty ledger (README must match)
- Proven live in v1: research → verify → CSV pipeline → outreach drafts → proposal, driven through real `claude -p` runs.
- Implemented + mock-tested, live-verified only with user tokens: Notion adapter, Places seed.
- Not built in v1: Gmail send/bounce integration, the other 6 "squad" agents, any hosted version.

## Deviations from the original brief (flagged, not silent)
1. Packaging changed from "SKILL.md pack only" to "runnable CLI with skills inside" — the operator's explicit Step 0 choice.
2. The brief's "Sequence README honesty standard" doesn't exist — Desktop `sequence` README is untouched create-next-app boilerplate (and that repo is a different product: clinic-intake SaaS). The honesty exemplar is localleadfinder's README; the *workflow* reference is Sequence HQ in Notion.
3. The brief's `orchestrator` skill directory is empty; the format standard used instead is `orchestrated-build/SKILL.md` (frontmatter: `name` + `description` with trigger phrases).
