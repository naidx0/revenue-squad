# Demo run — real output, redacted

These are the **actual files** produced by a live end-to-end run on **2026-07-09**:

```
squad research "Union County, NJ" "Elder Law" -n 5
squad outreach
squad propose "<firm A>" --notes discovery-notes-elder-law-firm-a.md
```

The research and outreach commands drove real `claude -p` calls against live
websites. `research` returned 5 leads; one — scored 9/10 with a verified email —
was dropped by the CLI because its domain was on the operator's `blocklist.txt`
(a company-level blackout, working as designed), leaving the 4 leads you see
here: two with verified emails + on-page evidence, two with honest `null` emails
(no address published anywhere on the site — form only). `outreach` drafted a
3-touch sequence for each of the two verified-email leads and refused the two
null-email leads loudly.

## What was redacted, and why

These are **live prospects of a real one-person consulting operation.** Publishing
their names and contact details would burn the pipeline, so this copy is redacted.
The **structure, scores, score rationales, personalization hooks, subjects, and
email/proposal bodies are otherwise intact** — that is what the tool actually
produces.

Redactions applied consistently across every file:

| Original | Redacted to |
|---|---|
| Each firm's name | `Elder Law Firm A / B / C / D (real firm, Union County NJ)` |
| Verified email addresses | `[verified address — redacted]` |
| Prospect first names in salutations | `[First name]` |
| Website / evidence URLs | `[firm site — redacted]` |
| Contact full names (CSV `Contact`) | `[contact — redacted]` |
| Phone numbers (CSV `Phone`) | `[phone — redacted]` |

`Contact` and `Phone` are redacted beyond the base rule because a full name or a
phone number reverse-looks-up to the firm just as fast as its name would.

The sender signature (`— [Your name], [Your business]`) is the operator's own identity,
not a prospect's, so it is left as-is. It also shows exactly the ambient-identity
behavior the `--sender` flag now controls (see the main README's design-decision
note) — these drafts predate that flag.

The **unredacted equivalents exist only on the operator's own machine** and are
gitignored (`out/`, `pipeline.csv`, `blocklist.txt` never leave it).

## Files

- `research-elder-law-2026-07-09.md` — the research summary table (4 survivors).
- `pipeline.csv` — the CRM snapshot those 4 leads were appended to.
- `discovery-notes-elder-law-firm-a.md` — **SAMPLE INPUT** for the proposal demo.
  The firm is real; the call did not happen (see the disclaimer inside the file).
- `outreach/elder-law-firm-a.md`, `outreach/elder-law-firm-b.md` — the two
  3-touch sequences drafted for the verified-email leads. The two null-email
  leads (C, D) got no drafts — that refusal is the point.
- `proposals/elder-law-firm-a.md` — the proposal generated from the discovery
  notes, quoting the prospect's exact words and leaving `[PRICE — confirm after
  scope]` rather than inventing a number.
