---
name: research
description: >-
  Runs a fixed 2-minute research drill on each prospect using web search and
  page fetches, scores every prospect on a numeric rubric with a written
  rationale, and emits a strict JSON leads array in which an email is included
  ONLY when it was seen on a real fetched page and is paired with an evidence
  URL. Every field is either grounded in a page you actually read or left null —
  a guessed email is worse than no email. Use when qualifying or enriching
  outbound B2B prospects, building a prospect list for a vertical and city,
  finding and verifying a contact email, or scoring leads before any outreach.
  Do NOT use for writing cold emails or follow-ups (that is the outreach skill)
  or for drafting a proposal from call notes (that is the proposal skill).
---

# Prospect Research

Qualify a prospect in about two minutes, then hand back a clean, honest record.
The one rule that governs everything: **never invent a fact about a prospect.**
Every field you return is either something you saw on a page you actually
fetched, or it is `null`. `null` is always an acceptable answer. A fabricated
name, title, or email does real damage downstream — it burns a send, trips a
bounce, or gets a domain blocklisted. Missing data costs nothing.

You have web search and page fetch available. Use them. Do not rely on memory or
pattern-guessing for any concrete field.

## The 2-minute drill

Run these steps on each prospect, in order:

1. **Fetch their website.** Look for signs of manual, un-automated process:
   plain contact forms, "call us" / "walk in" language, no online booking, a
   dated or template site. These are the pains you can help with — and your
   personalization hook.
2. **Check their public professional presence** (e.g. LinkedIn or an
   equivalent): rough company/team size, how long they have operated, any recent
   posts, any mention of tooling or being busy/overwhelmed.
3. **Search `"<company> reviews"`** to gauge volume and reputation — a proxy for
   how much throughput their manual process is straining under.
4. **Write down one specific, true observation** about this prospect. That single
   line is what makes later outreach non-generic; it goes in `notes`.

Keep it to about two minutes per prospect. Depth beyond that is wasted before a
reply exists.

## Finding the email (the strict part)

An email address is only ever valid if you **saw it published on a real page you
fetched.** Look on, in this order of preference for where to look: a **contact**
page, an **about** or **team** page, the site footer, or a listed staff bio.

- **Prefer a named person's address** (e.g. the owner/principal) over a generic
  `info@` / `office@` inbox — but a generic inbox that is genuinely published on
  the page is still valid and still better than nothing.
- If the only contact path is a web form with no address shown, the email is
  `null`. That is a correct, complete answer.
- **Never pattern-guess** an address from a person's name and the domain
  (`first@domain`, `firstlast@domain`, etc.). A guessed address is a fabrication.
- Whenever you set `email`, you MUST also set `email_evidence_url` to the exact
  URL of the page where the address was visibly published. No evidence URL means
  no email — set both to `null`.

The runner takes it from here: after you return, it MX-verifies each email's
domain and cross-checks a blocklist and bounce history before anything is ever
sent, and it demotes any email whose domain fails verification. Your single job
is to make sure every address you hand over was actually on a page — so those
downstream checks are operating on real data, not on a guess.

## Scoring

Score each prospect by adding the points for every factor that is true. Only
credit a factor you have evidence for; if you cannot tell, do not add its points.

| Factor | Points |
|---|---|
| Website looks outdated / no automation visible | +3 |
| Team size roughly 5–50 | +2 |
| Recently posted about being busy / overwhelmed | +3 |
| In a high-automation-opportunity industry | +2 |
| Local to your target region | +1 |
| Has budget signals (hiring, growing, new office) | +3 |
| Active on their professional network | +1 |

Thresholds:

- **8+** — Priority prospect
- **5–7** — Good prospect
- **Below 5** — Low priority

Put the integer total in `score`. In `score_rationale`, write one or two plain
sentences naming *which* factors you credited and why — e.g. "Dated site with a
form-only contact (+3), ~12 staff (+2), in a high-opportunity vertical (+2);
no visible budget signals." The rationale is not optional: it is what lets a
human trust or overrule the number.

## Field discipline

- `company`, `website`, `city`, `vertical` are normally knowable from the site —
  fill them. If a value genuinely is not present, `null` (or `""` where the
  schema uses a string) beats a guess.
- `vertical` should be one of the operator's configured verticals — a typical
  default set is Law, Real Estate, Healthcare, Home Services, Accounting, Auto,
  Cleaning, Other. Pick the closest; use Other rather than inventing a label.
- `contact_name`, `title`, `email`, `email_evidence_url`, `phone` stay `null`
  unless found on a fetched page.
- `notes` carries your one specific observation (the personalization hook) plus
  anything a human would want before deciding — kept factual.

## Output contract

End your final message with exactly one fenced `json` block, and nothing after
it, in this shape:

```json
{"leads": [{"company": "", "website": "", "contact_name": null, "title": null,
  "email": null, "email_evidence_url": null, "phone": null, "city": "",
  "vertical": "", "score": 0, "score_rationale": "", "notes": ""}]}
```

Rules restated for the machine reader: `email` stays `null` unless found on a
real fetched page; `email_evidence_url` is REQUIRED whenever `email` is set; any
field you cannot ground is `null`.

## Worked example (invented — illustration only)

**Input task:** "Research 1 prospect: dental practices in Rivertown."

**What the drill turns up (all fictional):** A practice called *Brightwell
Family Dental* at `brightwelldental.example`. The site is a dated template with a
"Call us to book" banner and a basic contact form — no online booking. An about
page lists Dr. Dana Brightwell and names roughly eight staff. Their contact page
publishes `frontdesk@brightwelldental.example`. No principal's direct address is
shown, so the generic front-desk inbox is used, with the contact page as
evidence. Search shows ~120 reviews — steady volume straining a manual front
desk.

**Output:**

```json
{"leads": [{"company": "Brightwell Family Dental", "website": "https://brightwelldental.example",
  "contact_name": "Dana Brightwell", "title": "Owner / Dentist",
  "email": "frontdesk@brightwelldental.example",
  "email_evidence_url": "https://brightwelldental.example/contact",
  "phone": null, "city": "Rivertown", "vertical": "Healthcare", "score": 8,
  "score_rationale": "Dated template site with call-to-book and form-only contact, no online booking (+3); ~8 staff (+2); healthcare is high-opportunity (+2); local (+1). No visible hiring/budget signal.",
  "notes": "Hook: 'Call us to book' banner and no online scheduling despite ~120 reviews — front desk likely fielding booking by phone. Only a generic front-desk inbox is published; no principal address found."}]}
```

## When NOT to use this skill

Stop here once the record is built. This skill does not write outreach, does not
send anything, and does not decide contact eligibility — the runner handles
MX/blocklist/bounce filtering, the outreach skill writes the emails, and the
proposal skill handles post-call proposals. If you are asked to draft a message
or a proposal, that is a different skill; do not do it here.
