# revenue-squad

A clone-and-run CLI that runs a solo operator's outbound B2B workflow — prospect
**research → cold-email drafting → proposal generation** — by driving the Claude
CLI headless through three bundled, readable skills, and logging every lead to a
local CSV pipeline (with an optional Notion CRM adapter). It encodes one rule the
whole way through: **never invent a fact about a prospect** — verified emails only,
paired with the URL that proves it, MX-checked in code, cross-checked against a
blocklist before a single word of outreach is written.

```console
$ squad research "Union County, NJ" "Elder Law" -n 5
! DROP <firm — redacted>: domain <redacted> is blocklisted   # scored 9/10, verified email — dropped anyway
                                   Research results
┌──────────────────────────────┬──────────────┬───────────────────────────┬────────────┐
│ Company                      │ City         │ Email                     │ Lead Score │
├──────────────────────────────┼──────────────┼───────────────────────────┼────────────┤
│ Elder Law Firm A (redacted)  │ Summit       │ [verified — redacted]     │ 8          │
│ Elder Law Firm B (redacted)  │ Cranford     │ [verified — redacted]     │ 7          │
│ Elder Law Firm C (redacted)  │ Cranford     │ —                         │ 6          │
│ Elder Law Firm D (redacted)  │ Scotch Plains│ —                         │ 6          │
└──────────────────────────────┴──────────────┴───────────────────────────┴────────────┘

Appended 4 new lead(s) to pipeline.csv (0 duplicate(s) skipped).
Wrote out/research-elder-law-2026-07-09.json and out/research-elder-law-2026-07-09.md.
```

That is a real run (2026-07-09), identifiers redacted. The model returned 5 leads;
the highest-scoring one had a verified email but its domain was on the operator's
`blocklist.txt`, so the CLI dropped it — a company-level blackout, working as
designed. Two of the four survivors had emails published on a real page (kept, with
evidence URLs); two had none (kept honestly as `null`). Full redacted artifacts are
in [`examples/demo-run/`](examples/demo-run/).

## Why this exists

Outbound that works is boring and disciplined: research a prospect, find a *real*
contact address (never a guessed one), write a short personalized email, follow up
twice, and write a tight proposal when a call goes well. Doing it by hand is slow;
handing it to an LLM naively is worse, because a model that guesses
`firstname@theirdomain.com` will burn a send, trip a bounce, and eventually get a
domain blocklisted.

revenue-squad is the workflow of a real one-person consulting operation, encoded so
the discipline lives in **code**, not in a prompt you hope the model obeys: emails
are only accepted with on-page evidence, every domain is MX-checked before it can be
worked, the blocklist is enforced at the tool boundary, and every failure is loud.
The model does the reading and the writing; the CLI keeps it honest.

## What it does

Three skills, run in sequence, each a plain-Markdown file in [`skills/`](skills/):

- **research** — runs a ~2-minute drill per prospect (fetch the site, check their
  public presence, gauge review volume), scores each on a numeric rubric with a
  written rationale, and returns an email **only** when it was seen on a fetched page
  and paired with an evidence URL. `null` is always an acceptable answer.
- **outreach** — turns one verified, unblocked lead into a **Day 1 / Day 3 / Day 7**
  cold-email sequence, each touch with a distinct job, personalized from the research
  notes. Refuses any lead that is blocked, lost, or lacks a verified email.
- **proposal** — converts discovery-call notes into a one-page proposal grounded
  *only* in the notes: it quotes the prospect's own words, invents no capabilities or
  timelines, and leaves a `[PRICE — confirm after scope]` placeholder rather than
  making up a number.

## Installation

```console
git clone https://github.com/naidx0/revenue-squad
cd revenue-squad
uv sync
```

**Hard prerequisite: the Claude CLI.** `research`, `outreach`, and `propose` shell
out to `claude -p` — the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code)
must be installed, on your `PATH`, and logged in. Without it those three commands
fail loudly (they do not fall back to anything). Verify with:

```console
claude --version     # built and proven against 2.1.186
```

`mark-sent` and `notion-init` are the only commands that don't need it.
Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/) are required.

## Configuration

Everything below is **optional** — the default path (CSV pipeline, no seeding) needs
no configuration at all.

| Variable | Unlocks |
|---|---|
| `SQUAD_SENDER` | The identity that signs outreach emails and proposals, as `"Name \| Business"`. Falls back from the `--sender` flag. **When neither is set, drafts are signed with the literal placeholders `[Your name], [Your business]`** and any identity implied by your environment is explicitly ignored — so your own name never leaks into someone else's cold emails. |
| `NOTION_TOKEN` + `NOTION_DATA_SOURCE_ID` | The Notion CRM backend (`--crm notion`). The token is an internal-integration secret from <https://www.notion.so/my-integrations>; the data source id comes from `squad notion-init` (or an existing DB). Both must be set, or `--crm notion` fails loudly with an actionable message. |
| `GOOGLE_MAPS_API_KEY` | Google Places lead seeding (`--seed places`). Enable the Places API (New) in Google Cloud and create a key. Missing key → loud error. |

### `blocklist.txt`

A local, gitignored file in the repo root, one entry per line. Two kinds of entry:

- **Domains** — `example.com` blocks any email at that domain *and* any lead whose
  website is that domain.
- **Exact emails** — `someone@gmail.com` blocks only that address (this is how you
  exclude a single freemail address without blocking all of `gmail.com`).

Blank lines and `#` comments are ignored. This is the operator-maintained substitute
for bounce/opt-out history in v1 — a lead matching the blocklist is dropped at
research time and refused at outreach time.

## Usage

```console
# 1. Research prospects: LOCATION then VERTICAL. Survivors land in pipeline.csv as Status=New.
squad research "Union County, NJ" "Elder Law" -n 5
squad research "Union County, NJ" "Elder Law" -n 10 --service-line "AI Consulting" --seed places

# 2. Draft Day 1/3/7 outreach for every eligible Status=New lead (or name specific companies).
squad outreach
squad outreach "Some Firm LLC" --sender "Jane Doe | Acme Consulting"

# 3. Write a proposal from discovery notes (grounded only in the notes).
squad propose "Some Firm LLC" --notes out/discovery-notes.md --sender "Jane Doe | Acme Consulting"

# 4. Record that a send actually went out (Day 1 also moves Status New -> Contacted).
squad mark-sent "Some Firm LLC" --day 1

# 5. (optional) Create a Notion CRM database matching the pipeline schema.
squad notion-init --parent-page-id <notion-page-id>
```

`research`, `outreach`, and `mark-sent` all take `--crm csv` (default) or
`--crm notion`. `outreach` writes each sequence to `out/outreach/<slug>.md` and
`propose` writes to `out/proposals/<slug>.md` — they are **files to review and send
yourself**, never auto-sent.

## The skills

The three skills in [`skills/`](skills/) are the interesting part. Each is a readable
Markdown file with `name` + `description` frontmatter — the same format Claude Code
uses — so they **double as drop-in Claude Code skills**: copy a skill directory into
your own `.claude/skills/` and it works there too. The CLI strips the frontmatter and
passes the body to `claude -p` via `--append-system-prompt-file`, so what you read is
exactly what steers the model. No hidden prompt.

## Output schema

`pipeline.csv` (and the Notion DB) carry these columns, in this order — mirroring the
operation's real "Master Lead CRM", **plus an `Email Evidence` column**:

| Column | Meaning |
|---|---|
| `Company` | Business name (part of the dedupe key). |
| `Contact` | Named contact, if one was found on a page. |
| `Email` | Verified address, or blank. Never a guess. |
| `Email Evidence` | URL of the page where the address was published. Rides along only with a surviving email. |
| `Phone` | Phone, if published. |
| `City` | City / area. |
| `Website` | Prospect's site. |
| `Status` | One of `New, Contacted, Replied, Call Booked, Proposal Sent, Won, Lost, Nurture`. |
| `Vertical` | Law, Real Estate, Healthcare, Home Services, Accounting, Auto, Cleaning, Other. |
| `Service Line` | The service you're pitching (from `--service-line`). |
| `Batch` | `<vertical>-<date>` of the research run. |
| `Lead Score` | Integer rubric total. |
| `Score Rationale` | Plain-sentence justification for the score. |
| `Deal Value` | Filled in by you if it closes. |
| `Day 1 Sent` / `Day 3 Sent` / `Day 7 Sent` | Dates set by `mark-sent`. |
| `Follow Up Due` | Cleared when a follow-up is sent. |
| `Replied` / `Reply Date` | Reply tracking. |
| `Call Booked` / `Call Date` | Call tracking. |
| `Notes` | The personalization hook plus anything a human should know (incl. loud demotion notes). |
| `Blocked` | Set to exclude a row from outreach. |

## Design decisions

**Verified emails only, with an evidence URL.** An email is accepted only when the
model saw it on a page it actually fetched *and* returns the URL of that page. No
evidence URL, no email — both fields go `null`. A guessed address is treated as a
fabrication, because downstream it is indistinguishable from one until it bounces.

**MX checks live in code, not in the prompt.** After the model returns, the CLI does
a real DNS MX lookup (`dnspython`) on each email's domain; a failure demotes the email
to `null` with a loud note. "Never send to a dead domain" is a property the tool
guarantees, not an instruction the model is trusted to follow.

**The blocklist is enforced at the tool boundary.** A domain or exact address in
`blocklist.txt` is dropped at research time and refused at outreach time — even for a
9/10 lead with a perfect verified email (see the terminal example above). The model's
enthusiasm can't override an operator blackout.

**Drafts are files, not sends.** `outreach` and `propose` write Markdown you review
and send yourself. The tool has no email-send scope in v1, on purpose.

**`mark-sent` is explicit** because the tool genuinely cannot know you sent an email —
it wrote a file, that's all. Recording the send is a deliberate, separate step; Day 1
also advances `Status` to `Contacted`.

**Fail loud, everywhere.** A nonzero `claude` exit, a timeout, unparseable JSON, or a
missing token raises with the stderr tail and the path to the saved raw output. There
are no silent fallbacks, no retries-that-mask, no `except: pass`. If the model returns
zero or partial drafts, the CLI writes what came back, surfaces the model's own
explanation, names the companies it skipped, and exits nonzero.

**The evidence-column bug (a worked example of the above).** The `Email Evidence`
column exists because of a bug the first live outreach run exposed. Research validated
the evidence URL and then *dropped* it before the CRM write, so by outreach time no
lead had evidence — and the outreach skill, correctly, refused every single one rather
than draft to an address it couldn't prove was real. But the CLI exited `0` and looked
successful. The fix was two-part: the evidence URL now survives the round-trip into the
pipeline, and zero/partial-draft runs now exit nonzero with the model's explanation.
The discipline caught the bug; the bug taught us the CLI's success signal was lying.
Both are fixed, and both now have regression tests.

## What v1 does NOT do

- **No Gmail (or any) send/bounce sync.** Drafts are files; you send them. Bounce and
  opt-out history is the operator-maintained `blocklist.txt`, not a live feed.
- **No hosted anything.** It runs on your machine, against your Claude login.
- **Only three of the "squad."** The other six imagined agents don't exist yet.
- **Notion adapter: live-verified (2026-07-09).** `notion-init` created a real
  database, `research --crm notion` logged real leads into it (verified email +
  evidence URL landing in the right properties), and `mark-sent --crm notion` flipped
  Status and set the Day 1 date — all against the live Notion API, on top of the 18
  mocked tests in `test_notion.py`. To set it up yourself:

  ```console
  export NOTION_TOKEN=secret_...
  squad notion-init --parent-page-id <page-id>
  export NOTION_DATA_SOURCE_ID=<printed id>
  squad research "Your City" "Your Vertical" -n 5 --crm notion
  ```

- **Places seed (`--seed places`): implemented and mock-tested (5 tests in
  `test_places.py`), but NOT yet live-verified** — no run has hit the real Google
  Places API. The request shape follows the same API pattern as
  [localleadfinder](https://github.com/naidx0/localleadfinder). If you run it live
  with your own `GOOGLE_MAPS_API_KEY`, this line should be upgraded to match.

## Costs

Costs are billed to **the operator's own Claude account** — the CLI drives your
logged-in `claude`, so a run costs whatever those turns cost you. Real figures from
the 2026-07-09 run:

- `squad research ... -n 5` — **186s, 18 turns, ~$1.32** in API cost, 5 leads returned.
- `squad outreach` — **~$0.35 per run.**
- `squad propose` — comparable to a single short generation.

Seeding with `--seed places` and using `--crm notion` add Google/Notion API calls on
top, billed by those providers.

## Development

```console
uv sync --all-extras
uv run pytest -q --timeout=60 --timeout-method=thread --durations=10 --durations-min=1
uv run squad --help
```

That three-line gate is the CI workflow and the bar for any change. The suite mocks
`subprocess` and all HTTP, so it never calls the real `claude`, Notion, or Google.

## License

MIT — see [`LICENSE`](LICENSE).

---

Built by dogfooding the outbound workflow of **Sequence**, a real one-person
consulting operation. No client names ship in this repo.
