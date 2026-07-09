---
name: outreach
description: >-
  Turns one verified, unblocked lead into a three-touch cold-email sequence —
  Day 1, Day 3, Day 7 — where each touch has a distinct job, the copy is short
  and specifically personalized from the lead's research notes, and the result is
  a strict JSON drafts array. Follow-ups are the point: most replies come on the
  second or third touch, so all three are written up front. Use when drafting
  cold outbound emails or their follow-ups for a lead that already has a verified
  email and evidence URL. Do NOT use to find, score, or verify prospects (that is
  the research skill) or to write a proposal from call notes (that is the proposal
  skill). Refuse any lead that is blocked, bounced, lost, or unverified.
---

# Cold Outreach

Write a three-email sequence that earns a reply from a stranger who is busy and
skeptical. The discipline that makes it work: **brevity, one specific detail, one
ask — and relentless, well-mannered follow-up.** Most deals close on the second
or third touch, not the first, so you draft all three touches now.

You work only from the lead data and research notes handed to you in the prompt.
You do not search the web, fetch pages, or use any tool — if a fact is not in
the lead record, do not assert it.

## Voice rules

- **Short.** A cold email is a few sentences, not a pitch deck. If it needs
  scrolling, it is too long.
- **One specific, true observation up front.** Pull the personalization hook
  from the lead's research notes and open with it. "I noticed <specific thing>"
  beats any generic compliment. No hook, no send — a mail merge that could go to
  anyone gets deleted.
- **Say who you are and what you do in one line,** with one or two concrete
  examples of the repetitive work you take off their plate — not a feature list.
- **Exactly one ask,** and make it low-friction: a short call, or a plain yes/no.
  Never stack two asks.
- **Sound like a person, not a bot.** Plain language, a real signature, no
  buzzword soup, no fake urgency.
- **Personalize per vertical.** The pain you name for a law firm (intake,
  chasing documents, unsigned agreements going cold) is not the pain you name for
  a property manager (leads sitting overnight, maintenance/renewals by hand) or a
  clinic (no-shows, dormant patients). Speak to their actual work.

Treat the templates below as *structure*, not copy to paste. Adapt every line to
the specific lead. Bracketed `[...]` items are placeholders the operator or the
lead record fills.

## The three-touch structure

Each touch does a different job. Do not just resend the same message.

**Day 1 — the value-first opener.** Earn the reply. Lead with the specific
observation, say what you help with in one line plus a concrete example or two,
and make one low-friction ask.

> Subject: [specific, honest, ties to their work]
>
> Hi [Name],
>
> I noticed [specific observation from the research notes].
>
> I help [their vertical] businesses take [specific repetitive task] off their
> team's plate — things like [1–2 concrete examples].
>
> Worth a quick 15-minute call to see if there's a fit?
>
> — [Your name], [Your business]

**Day 3 — the short bump.** For non-responders only. Acknowledge that inboxes
bury things, add no new pitch, and re-offer the same easy ask with even less
friction. Two or three sentences, maximum.

> Subject: [re: the Day 1 subject, or a short nudge]
>
> Hi [Name], just floating this back up — I know things get buried.
>
> Still happy to do a quick 15 minutes this week. No pitch, just a look at where
> the quick wins are. Worth it?
>
> — [Your name]

**Day 7 — the graceful close.** The last touch. Remove all pressure, name the
one pain once more, and leave the door open so a "not now" can become a "yes"
later. This is a breakup note, not a guilt trip.

> Subject: [short, final]
>
> [Name] — last note from me.
>
> If automating [specific pain point] isn't a priority right now, no worries at
> all. If it ever becomes one, I'm an easy call away.
>
> — [Your name]

## Refuse ineligible leads

Only draft for a lead that has a **verified email with an evidence URL** and is
**not** Blocked, Lost, or on the blocklist/bounce history. If you are handed —
or directly asked to write for — a lead that is blocked, bounced, lost, or has no
verified email, **do not produce outreach copy for it.** Return an empty
`drafts` array and state the per-lead reason in a plain line before the JSON
(e.g. `Skipped Acme Co: no verified email with evidence URL`). The runner
enforces this refusal too and will not send such a lead, but you must not write
the copy even if asked directly — a drafted email for an ineligible address is a
liability, not a convenience.

Drafting never changes a lead's status. Marking a send as sent is the runner's
job, not yours.

## Legality (short, non-negotiable)

- **Truthful subject lines.** The subject must reflect what the email actually
  says — no bait, no fake "re:" on a first contact.
- **Real sender identity.** A genuine name, business, and reachable return
  address. No spoofing.
- **Honor opt-outs.** If someone asks to stop, they stop — permanently, and go on
  the blocklist. Never re-contact a hard bounce or an opt-out.

This is baseline honest-sender conduct, not legal advice.

## Output contract

End your final message with exactly one fenced `json` block, and nothing after
it, in this shape:

```json
{"drafts": [{"company": "", "day1": {"subject": "", "body": ""},
  "day3": {"subject": "", "body": ""}, "day7": {"subject": "", "body": ""}}]}
```

One object per eligible company. If a lead is ineligible, omit it from `drafts`
(and note why, above the block).

## Worked example (invented — illustration only)

**Input lead (fictional):** Brightwell Family Dental, Rivertown. Verified email
`frontdesk@brightwelldental.example` (evidence on file). Status New. Research
note: "Call us to book" banner, no online scheduling despite steady volume.

**Output:**

```json
{"drafts": [{"company": "Brightwell Family Dental",
  "day1": {"subject": "booking by phone at Brightwell?",
    "body": "Hi Dr. Brightwell,\n\nI noticed your site still routes new patients through a 'call us to book' banner rather than online scheduling.\n\nI help dental practices take that off the front desk — things like instant reminders and automatic waitlist fill so cancelled slots don't sit empty.\n\nWorth a quick 15-minute call to see if there's a fit?\n\n— Alex Rivera, Northlight Automation"},
  "day3": {"subject": "re: booking by phone at Brightwell?",
    "body": "Hi Dr. Brightwell, just floating this back up — I know things get buried.\n\nStill happy to do a quick 15 minutes this week. No pitch, just a look at where the quick wins are. Worth it?\n\n— Alex"},
  "day7": {"subject": "last note",
    "body": "Dr. Brightwell — last note from me.\n\nIf cutting down phone-tag on booking isn't a priority right now, no worries at all. If it ever becomes one, I'm an easy call away.\n\n— Alex"}}]}
```

## When NOT to use this skill

This skill drafts cold emails and their follow-ups — nothing else. It does not
research or score prospects, does not verify or find emails, does not send, and
does not write proposals. Hand an ineligible lead here and it refuses. For
finding prospects use the research skill; for a post-call proposal use the
proposal skill.
