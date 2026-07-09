---
name: proposal
description: >-
  Converts discovery-call notes into a one-page, tightly scoped proposal — a
  problem recap in the prospect's own words, a phased roadmap, options or
  pricing, and a single clear next step — returned as a complete Markdown
  document with no JSON wrapper. It is grounded ONLY in the notes provided: it
  quotes the prospect's stated pains, invents no capabilities, timelines, or
  prices, and leaves a bracketed placeholder wherever a fact is missing. Use when
  writing or drafting a proposal or scope-of-work from discovery-call notes. Do
  NOT use to find or score prospects (that is the research skill) or to write
  cold outreach emails (that is the outreach skill).
---

# Proposal

Turn what you heard on a discovery call into a short proposal the prospect can
actually say yes to. Two principles govern it: **stay grounded only in the notes
you were given, and keep the scope small.** The proposal is the first buyable
step, not a showcase of everything that is possible. A proposal that is smaller
and more specific than the full vision closes; a sprawling one stalls.

You work only from the discovery notes handed to you in the prompt. You do not
search, fetch, or use any tool. If a fact is not in the notes, you do not assert
it.

## Grounding rule

- **Recap the pain in the prospect's own words.** Quote or closely paraphrase
  what they actually said on the call. This is what shows them you listened — it
  is the single most persuasive part of the document.
- **Invent nothing.** No capability the notes do not support, no timeline the
  notes do not state, no metric you cannot back.
- **Missing price → `[PRICE]`.** If the notes do not contain a price or an agreed
  band, write the bracketed placeholder `[PRICE]` (or `[PRICE — confirm after
  scope]`). Never make up a number.
- **Missing timeline → bracket it too** (e.g. `[TIMELINE]`) rather than guessing.
- When something genuinely needs confirming before the price or scope is final,
  list it as an open question instead of assuming an answer.

## Structure

Write these sections, in this order. Keep the whole thing to roughly one page.

1. **What we heard** — 2–3 sentences recapping their situation and the core pain,
   in their words. No solution yet; just proof you understood.
2. **Proposed roadmap** — the work, phased:
   - **Phase 1 (core):** the 2–3 concrete things you will build to solve the
     stated pain. Concrete automations, not a feature dump. Give a delivery
     window if the notes support one, otherwise `[TIMELINE]`.
   - **Phase 2 (optional, later):** one short paragraph on the natural next step
     *if* it follows from the notes — explicitly optional, "discuss after Phase 1
     is running." Do not pad this to look bigger.
3. **Investment** — the price or options. Use the figure/band from the notes; if
   none, `[PRICE]`. State plainly that the final number depends on scope, and
   point to the open questions if any remain.
4. **Next step** — exactly one clear call to action (e.g. "Reply to this email to
   move forward" or "book the 30-minute walkthrough"). One step, not a menu.

Optionally close with a short **Open questions to confirm scope** list when the
notes leave real unknowns — the honest way to keep a price bracketed.

## Scope discipline

- The proposal is always **smaller and more specific** than the full capability
  picture. Resist listing everything the system could ever do; name the first
  real step toward it.
- Two or three automations in Phase 1 is the target, not eight.
- Plain-English outcomes ("hours back each week," "fewer leads lost to slow
  replies," "fewer no-shows") land better than a technical spec — but only claim
  outcomes the notes support.

## Output contract

Return a **complete Markdown document** — the proposal itself, ready to send.
**No JSON, no wrapper, no preamble** like "Here is the proposal." Just the
Markdown, starting at the proposal's title.

## Worked example (invented — illustration only)

**Input notes (fictional):** Discovery call with Brightwell Family Dental. Pain
in their words: "the front desk spends half the day on the phone booking and
reminding people, and we still get a chunk of no-shows." Uses a basic scheduling
tool plus paper reminders. Wants it handled without ripping out what they have.
No price discussed on the call.

**Output:**

```markdown
# Proposal — Front-Desk Automation for Brightwell Family Dental

## What we heard
Your front desk "spends half the day on the phone booking and reminding people,"
and you still lose a chunk of the schedule to no-shows. You want this handled
without ripping out the scheduling tool you already use.

## Proposed roadmap

**Phase 1 (core) — [TIMELINE, typically 2–3 weeks]**
- Automated appointment reminders (text + email) that run off your existing
  schedule — no manual calls.
- Instant waitlist fill: when a slot cancels, the next patient is offered it
  automatically so the gap doesn't sit empty.
- A simple confirm/reschedule link in every reminder, so patients self-serve
  instead of phoning the desk.

**Phase 2 (optional, discuss after Phase 1 is running)**
- Reactivation sequence for patients who haven't been in for a while — only if
  you want to pursue it once the core is live.

## Investment
`[PRICE — confirm after scope]`. The final number depends on reminder volume and
whether Phase 2 is included; the open questions below settle it.

## Next step
Reply to this email to move forward, and I'll send a short scope confirmation.

## Open questions to confirm scope
1. Roughly how many appointments per week need reminders?
2. Which scheduling tool are you on today, so reminders plug into it directly?
```

## When NOT to use this skill

This skill writes a post-call proposal from notes — nothing else. It does not
research prospects, does not write cold emails, and does not invent facts to fill
gaps. If you have no discovery notes, you are not ready to use it. For finding
prospects use the research skill; for cold outreach use the outreach skill.
