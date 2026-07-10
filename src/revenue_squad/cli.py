"""revenue-squad CLI: research -> outreach -> proposal, driven through the Claude CLI."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import gmail, pipeline, places, supabase
from .backend import CrmChoice, get_backend
from .blocklist import Blocklist, append_entries
from .notion import NotionBackend
from .runner import RunnerError, run_skill

# Chunk sizes for the batched claude runs — past these, one `claude -p` per command
# blows the context/timeout budget and quality collapses.
RESEARCH_CHUNK = 10
OUTREACH_CHUNK = 8
DAILY_CONFIG_PATH = Path("squad.toml")
DAILY_DEFAULT_COUNT = 10

app = typer.Typer(
    help="Solo-operator outbound B2B workflow driven by the Claude CLI.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err = Console(stderr=True)


def _warn(msg: str) -> None:
    err.print(f"[yellow]![/yellow] {msg}")


def _resolve_sender(sender: Optional[str]) -> str:
    """Explicit --sender wins; else $SQUAD_SENDER; else "" (unconfigured)."""
    if sender is not None and sender.strip():
        return sender.strip()
    return (os.environ.get("SQUAD_SENDER") or "").strip()


def _sender_instruction(sender: Optional[str]) -> str:
    """Identity block appended to the outreach/propose task prompts.

    A headless `claude -p` run would otherwise sign with whatever identity its
    ambient config implies (e.g. the operator's own name pulled from a global
    CLAUDE.md) — which for another user would silently leak their identity into a
    cold email or proposal. So: sign exactly with the configured identity, or, when
    none is set, sign with literal placeholders and explicitly ignore the environment.
    """
    identity = _resolve_sender(sender)
    if identity:
        return (
            "\n\nSender identity — sign exactly with this identity and nothing else: "
            f"{identity}. It is given as \"Name | Business\": where a signature line "
            "uses the name only, use the part before the '|'; where it uses both, sign "
            "'Name, Business'. Do not use any other name or business, and ignore any "
            "identity implied by your environment or configuration."
        )
    return (
        "\n\nSender identity — none configured. Sign with the literal placeholders "
        '"[Your name], [Your business]" (touches that sign with the name only use '
        '"[Your name]"). Do NOT infer or substitute any real name, business, or '
        "identity implied by your environment or configuration — leave the "
        "placeholders verbatim for the operator to fill."
    )


def _split_count(total: int, size: int) -> list[int]:
    """Split `total` into chunk sizes of at most `size` (e.g. 11,10 -> [10, 1]; 10,10 -> [10])."""
    if total <= 0:
        return []
    sizes: list[int] = []
    remaining = total
    while remaining > 0:
        take = min(size, remaining)
        sizes.append(take)
        remaining -= take
    return sizes


def _chunk(items: list, size: int) -> list[list]:
    """Split a list into consecutive groups of at most `size`."""
    return [items[i : i + size] for i in range(0, len(items), size)]


class _ResearchReporter:
    """Wrap the loud _warn reporter and tally blocklist drops / MX demotions for the aggregate."""

    def __init__(self, report) -> None:
        self._report = report
        self.dropped_blocklist = 0
        self.demoted_mx = 0

    def __call__(self, msg: str) -> None:
        self._report(msg)
        if msg.startswith("DROP "):
            self.dropped_blocklist += 1
        elif msg.startswith("DEMOTE ") and "(MX:" in msg:
            self.demoted_mx += 1


@dataclass
class ResearchOutcome:
    requested: int = 0
    returned: int = 0
    rows_produced: int = 0
    appended: int = 0
    dropped_blocklist: int = 0
    demoted_mx: int = 0
    failed_chunks: list[int] = field(default_factory=list)
    n_chunks: int = 0
    all_rows: list[dict[str, str]] = field(default_factory=list)
    json_path: Optional[Path] = None
    md_path: Optional[Path] = None


def _run_research(
    location: str,
    vertical: str,
    count: int,
    service_line: Optional[str],
    *,
    backend,
    blocklist: Blocklist,
    seed: Optional[places.SeedSource],
) -> ResearchOutcome:
    """Research `count` prospects in ceil(count/RESEARCH_CHUNK) sequential claude runs.

    Cross-chunk repeats are absorbed by the existing (Company, Email) dedupe in
    backend.append (each chunk sees the prior chunk's rows). A chunk RunnerError is
    reported loudly and recorded, the remaining chunks still run, and the caller
    exits nonzero naming the failed chunks. Single-chunk runs produce byte-identical
    output to the pre-chunking path.
    """
    sizes = _split_count(count, RESEARCH_CHUNK)
    n_chunks = len(sizes)
    outcome = ResearchOutcome(requested=count, n_chunks=n_chunks)
    reporter = _ResearchReporter(_warn)

    candidates = (
        places.search_places(vertical, location, count)
        if seed == places.SeedSource.places
        else []
    )
    date_str = date.today().isoformat()
    vertical_slug = pipeline.slugify(vertical)
    batch = f"{vertical_slug}-{date_str}"

    offset = 0
    for idx, size in enumerate(sizes, 1):
        if n_chunks > 1:
            console.print(f"[research {idx}/{n_chunks}] researching {size} prospect(s)…")
        task = (
            f"Research {size} prospective B2B clients in {location} for the '{vertical}' vertical"
            + (f", to pitch our '{service_line}' service line" if service_line else "")
            + ". Follow the research skill's output contract exactly and end with the JSON block."
        )
        chunk_candidates = candidates[offset : offset + size]
        offset += size
        if seed == places.SeedSource.places:
            task += "\n\n" + places.format_candidates(chunk_candidates)

        try:
            data = run_skill(task, "research", allowed_tools=["WebSearch", "WebFetch"])
        except RunnerError as exc:
            outcome.failed_chunks.append(idx)
            _warn(f"research chunk {idx}/{n_chunks} failed (continuing): {exc}")
            continue

        leads = data.get("leads") if isinstance(data, dict) else None
        if not isinstance(leads, list):
            raise typer.BadParameter("research result JSON had no 'leads' array")
        outcome.returned += len(leads)
        rows = pipeline.process_research_leads(
            leads,
            vertical=vertical,
            service_line=service_line,
            batch=batch,
            blocklist=blocklist,
            report=reporter,
        )
        outcome.rows_produced += len(rows)
        added = backend.append(rows)
        outcome.appended += len(added)
        outcome.all_rows.extend(rows)

    outcome.dropped_blocklist = reporter.dropped_blocklist
    outcome.demoted_mx = reporter.demoted_mx
    # Write the aggregated research outputs once. Skip only when every chunk failed and
    # nothing came back — don't fabricate an empty summary over a fully-failed run.
    if outcome.all_rows or not outcome.failed_chunks:
        outcome.json_path, outcome.md_path = pipeline.write_research_outputs(
            outcome.all_rows, vertical_slug=vertical_slug, date_str=date_str
        )
    return outcome


@app.command("research")
def research(
    location: str = typer.Argument(..., help="City / area to prospect in."),
    vertical: str = typer.Argument(..., help="Target industry vertical."),
    count: int = typer.Option(5, "-n", "--count", help="Number of prospects to research."),
    service_line: Optional[str] = typer.Option(
        None, "--service-line", help="Service line you're pitching (stored on each lead)."
    ),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm",
        help="CRM backend to append leads to: csv (default), notion, or supabase.",
    ),
    seed: Optional[places.SeedSource] = typer.Option(
        None, "--seed", help="Optional lead seed source (places) — needs GOOGLE_MAPS_API_KEY."
    ),
) -> None:
    """Research prospects, verify emails, and append survivors to the pipeline."""
    backend = get_backend(crm_backend.value)
    blocklist = Blocklist.load()
    outcome = _run_research(
        location, vertical, count, service_line,
        backend=backend, blocklist=blocklist, seed=seed,
    )

    _preview_research(outcome.all_rows)
    console.print(
        f"\nAppended [bold]{outcome.appended}[/bold] new lead(s) to {backend.describe()} "
        f"({outcome.rows_produced - outcome.appended} duplicate(s) skipped)."
    )
    if outcome.json_path is not None:
        console.print(f"Wrote {outcome.json_path} and {outcome.md_path}.")
    if outcome.n_chunks > 1:
        console.print(
            f"Chunked research across {outcome.n_chunks} runs — requested {outcome.requested}, "
            f"returned {outcome.returned}, deduped {outcome.rows_produced - outcome.appended}, "
            f"dropped-blocklist {outcome.dropped_blocklist}, demoted-MX {outcome.demoted_mx}, "
            f"appended {outcome.appended}."
        )
    if outcome.failed_chunks:
        _warn(
            "research chunk(s) failed: "
            + ", ".join(str(i) for i in outcome.failed_chunks)
            + " — raw output saved under out/raw/. Partial results applied above; exiting nonzero."
        )
        raise typer.Exit(code=1)


@app.command("outreach")
def outreach(
    companies: Optional[List[str]] = typer.Argument(
        None, help="Companies to draft for. Default: all eligible Status=New rows."
    ),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm",
        help="CRM backend to read leads from: csv (default), notion, or supabase.",
    ),
    sender: Optional[str] = typer.Option(
        None, "--sender", help='Sign drafts as "Name | Business" (falls back to $SQUAD_SENDER).'
    ),
    gmail_drafts: bool = typer.Option(
        False, "--gmail-drafts",
        help="Also create a Gmail draft per lead (Day 1 touch). Requires `squad gmail-auth`.",
    ),
) -> None:
    """Draft Day 1/3/7 cold outreach for eligible leads. Never changes Status."""
    backend = get_backend(crm_backend.value)
    blocklist = Blocklist.load()
    rows = backend.load()
    if not rows:
        raise typer.BadParameter(f"{backend.describe()} is empty — run `squad research` first.")

    explicit = bool(companies)
    if explicit:
        wanted = {c.strip().lower() for c in companies}
        targets = [r for r in rows if r.get("Company", "").strip().lower() in wanted]
        missing_from_pipeline = wanted - {r.get("Company", "").strip().lower() for r in targets}
        for name in sorted(missing_from_pipeline):
            _warn(f"REFUSE {name}: not found in pipeline")
    else:
        targets = [r for r in rows if r.get("Status") == "New"]

    eligible = _filter_eligible(targets, blocklist)

    prose = ""
    drafted_keys: set[str] = set()
    gmail_failed = False
    if not eligible:
        console.print("No eligible leads to draft.")
        # Explicit targets that produced nothing is unfulfilled work, not "nothing to
        # do": fall through to the loud nonzero exit below. No-args with nothing
        # eligible is genuinely nothing to do, so exit 0.
        if not explicit:
            raise typer.Exit(code=0)
    else:
        outcome = _draft_eligible(eligible, sender, gmail_drafts=gmail_drafts)
        prose = outcome.prose
        drafted_keys = outcome.drafted_keys
        gmail_failed = outcome.gmail_failed

    if explicit:
        # Every named company must have produced a written draft; anything missing
        # (absent from pipeline, refused, or no draft returned) is unfulfilled work.
        seen: set[str] = set()
        unfulfilled: list[str] = []
        for name in companies:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            if key not in drafted_keys:
                unfulfilled.append(name.strip())
        if unfulfilled:
            if prose.strip():
                err.print(f"[yellow]Model explanation:[/yellow] {prose.strip()}")
            _warn("Unfulfilled — no outreach draft written for: " + ", ".join(unfulfilled))
            raise typer.Exit(code=1)
        # Draft FILES all landed; a Gmail draft failure is still a nonzero outcome.
        if gmail_failed:
            raise typer.Exit(code=1)
        return

    # No-args mode: eligible leads went in but some/all came back with no draft.
    missing = [
        r.get("Company", "")
        for r in eligible
        if r.get("Company", "").strip().lower() not in drafted_keys
    ]
    if missing:
        if prose.strip():
            err.print(f"[yellow]Model explanation:[/yellow] {prose.strip()}")
        _warn("No outreach draft returned for: " + ", ".join(missing))
        raise typer.Exit(code=1)
    if gmail_failed:
        raise typer.Exit(code=1)


def _filter_eligible(
    targets: list[dict[str, str]], blocklist: Blocklist
) -> list[dict[str, str]]:
    """Return the outreach-eligible rows, warning REFUSE with a reason for each ineligible one."""
    eligible: list[dict[str, str]] = []
    for row in targets:
        ok, reason = pipeline.outreach_eligibility(row, blocklist)
        if ok:
            eligible.append(row)
        else:
            _warn(f"REFUSE {row.get('Company', '')}: {reason}")
    return eligible


@dataclass
class DraftOutcome:
    prose: str = ""
    drafted_keys: set[str] = field(default_factory=set)
    gmail_failed: bool = False
    written: list[tuple[str, Path]] = field(default_factory=list)
    failed_chunks: list[int] = field(default_factory=list)
    n_chunks: int = 0


def _draft_eligible(
    eligible: list[dict[str, str]], sender: Optional[str], *, gmail_drafts: bool = False
) -> DraftOutcome:
    """Run the outreach skill for eligible leads in groups of at most OUTREACH_CHUNK, writing
    each returned draft to disk. A chunk RunnerError is reported loudly and recorded; its
    leads simply never enter drafted_keys, so the caller's existing fulfilled/unfulfilled
    machinery flags them and exits nonzero. Single-chunk runs are byte-identical to the
    pre-chunking path. With gmail_drafts, every written draft is also pushed to Gmail (once,
    after all chunks) as a Day 1 draft."""
    groups = _chunk(eligible, OUTREACH_CHUNK)
    n_chunks = len(groups)
    outcome = DraftOutcome(n_chunks=n_chunks)
    out_dir = Path("out") / "outreach"
    out_dir.mkdir(parents=True, exist_ok=True)
    proses: list[str] = []
    for_gmail: list[tuple[str, dict[str, str], dict]] = []  # (company, row, draft)

    for idx, group in enumerate(groups, 1):
        if n_chunks > 1:
            console.print(f"[outreach {idx}/{n_chunks}] drafting {len(group)} lead(s)…")
        lead_payload = [
            {
                "company": r.get("Company", ""),
                "contact": r.get("Contact", ""),
                "email": r.get("Email", ""),
                "email_evidence": r.get("Email Evidence", ""),
                "website": r.get("Website", ""),
                "city": r.get("City", ""),
                "vertical": r.get("Vertical", ""),
                "service_line": r.get("Service Line", ""),
                "lead_score": r.get("Lead Score", ""),
                "score_rationale": r.get("Score Rationale", ""),
                "notes": r.get("Notes", ""),
            }
            for r in group
        ]
        task = (
            "Draft Day 1 / Day 3 / Day 7 cold outreach for these leads. Follow the outreach "
            "skill's output contract exactly and end with the JSON block."
            + _sender_instruction(sender)
            + "\n\nLeads to draft (JSON array):\n"
            + json.dumps(lead_payload, indent=2)
        )
        try:
            data, prose = run_skill(task, "outreach", return_prose=True)  # no tools: drafting only
        except RunnerError as exc:
            outcome.failed_chunks.append(idx)
            _warn(f"outreach chunk {idx}/{n_chunks} failed (continuing): {exc}")
            continue
        if prose.strip():
            proses.append(prose.strip())
        drafts = data.get("drafts") if isinstance(data, dict) else None
        if not isinstance(drafts, list):
            raise typer.BadParameter("outreach result JSON had no 'drafts' array")

        by_company = {r.get("Company", "").strip().lower(): r for r in group}
        for draft in drafts:
            company = (draft.get("company") or "").strip()
            row = by_company.get(company.lower())
            if row is None:
                _warn(f"SKIP draft for {company!r}: no matching eligible lead")
                continue
            path = out_dir / f"{pipeline.slugify(company)}.md"
            path.write_text(_render_draft(company, row.get("Email", ""), draft))
            outcome.written.append((company, path))
            outcome.drafted_keys.add(row.get("Company", "").strip().lower())
            for_gmail.append((company, row, draft))

    outcome.prose = "\n".join(proses)
    _preview_outreach(outcome.written)
    console.print(f"\nWrote {len(outcome.written)} outreach file(s) under {out_dir}.")

    if outcome.n_chunks > 1:
        console.print(
            f"Chunked outreach across {outcome.n_chunks} runs — {len(eligible)} eligible, "
            f"{len(outcome.written)} draft(s) written."
        )
    if gmail_drafts and for_gmail:
        outcome.gmail_failed = _create_gmail_drafts(for_gmail, sender)
    return outcome


def _create_gmail_drafts(
    entries: list[tuple[str, dict[str, str], dict]], sender: Optional[str]
) -> bool:
    """Create one Gmail draft per lead (Day 1 touch only). Returns True if any failed.

    The draft FILES are already on disk before this runs, so any Gmail failure never
    removes them — the message says so. Reports every created draft id and every
    per-lead failure loudly. A missing token short-circuits with one loud pointer to
    `squad gmail-auth` rather than one identical error per lead.
    """
    if not Path(gmail.TOKEN_PATH).is_file():
        _warn(
            f"No Gmail token at {gmail.TOKEN_PATH} — run `squad gmail-auth --client-secret "
            "<path>` first. Draft files are on disk; no Gmail drafts were created."
        )
        return True

    from_identity = _resolve_sender(sender)
    any_failed = False
    for company, row, draft in entries:
        touch = draft.get("day1") or {}
        to = row.get("Email", "")
        try:
            draft_id = gmail.create_draft(
                to, touch.get("subject", ""), touch.get("body", ""), from_identity
            )
        except gmail.GmailError as exc:
            any_failed = True
            _warn(f"GMAIL DRAFT FAILED for {company} (file kept on disk): {exc}")
            continue
        console.print(
            f"Created Gmail draft [bold]{draft_id}[/bold] for {company} (To: {to})."
        )
    return any_failed


@app.command("propose")
def propose(
    company: str = typer.Argument(..., help="Company to write a proposal for."),
    notes: Path = typer.Option(..., "--notes", help="Path to discovery notes (markdown)."),
    sender: Optional[str] = typer.Option(
        None, "--sender", help='Sign the proposal as "Name | Business" (falls back to $SQUAD_SENDER).'
    ),
) -> None:
    """Generate a markdown proposal from discovery notes. Does not change Status."""
    if not notes.is_file():
        raise typer.BadParameter(f"notes file not found: {notes}")
    task = (
        f"Write a complete client proposal for {company}. Return markdown only (no JSON)."
        + _sender_instruction(sender)
        + f"\n\nDiscovery notes follow:\n\n{notes.read_text()}"
    )
    proposal_md = run_skill(task, "proposal", extract_json=False)

    out_dir = Path("out") / "proposals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pipeline.slugify(company)}.md"
    path.write_text(proposal_md)
    console.print(f"Wrote proposal for [bold]{company}[/bold] to {path}.")


def _day_transition_eligible(row: dict[str, str], day: int) -> bool:
    """True when `row` is in the right state for a Day-`day` send to be recorded.

    Day 1: not yet sent. Day 3: Day 1 sent, Day 3 not. Day 7: Day 3 sent, Day 7 not.
    """
    d1 = (row.get("Day 1 Sent") or "").strip()
    d3 = (row.get("Day 3 Sent") or "").strip()
    d7 = (row.get("Day 7 Sent") or "").strip()
    if day == 1:
        return not d1
    if day == 3:
        return bool(d1) and not d3
    return bool(d3) and not d7


def _mark_one(backend, company: str, day: int) -> bool:
    """Mark a single company's Day-`day` send. Returns True on success, False (loud) if absent."""
    try:
        row = backend.mark_sent(company, day=day)
    except ValueError as exc:
        _warn(f"NOT MARKED {company.strip()}: {exc}")
        return False
    console.print(
        f"Marked Day {day} sent for [bold]{row.get('Company', '')}[/bold] "
        f"(Status={row.get('Status', '')})."
    )
    return True


@app.command("mark-sent")
def mark_sent(
    companies: Optional[List[str]] = typer.Argument(
        None, help="One or more companies whose send you're recording."
    ),
    day: int = typer.Option(1, "--day", help="Which touch was sent: 1, 3, or 7."),
    batch: Optional[str] = typer.Option(
        None, "--batch",
        help="Mark every eligible row whose Batch matches this label (mutually exclusive with COMPANIES).",
    ),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm",
        help="CRM backend to update: csv (default), notion, or supabase.",
    ),
) -> None:
    """Record that a Day 1/3/7 email went out. Day 1 also moves Status New->Contacted.

    Accepts multiple companies, or `--batch <label>` to mark every Batch-matching row that is
    eligible for the transition. Any not-found (companies) or ineligible (batch) row is
    reported and forces a nonzero exit; the rest are still processed.
    """
    if day not in (1, 3, 7):
        raise typer.BadParameter("--day must be 1, 3, or 7")
    if batch and companies:
        raise typer.BadParameter("pass either company name(s) OR --batch, not both.")
    if not batch and not companies:
        raise typer.BadParameter("name at least one company, or pass --batch <label>.")

    backend = get_backend(crm_backend.value)
    any_failed = False

    if batch:
        rows = backend.load()
        target = batch.strip().lower()
        matched = [r for r in rows if (r.get("Batch") or "").strip().lower() == target]
        if not matched:
            _warn(f"no pipeline rows in batch {batch!r}.")
            raise typer.Exit(code=1)
        for r in matched:
            company = (r.get("Company") or "").strip()
            if not _day_transition_eligible(r, day):
                any_failed = True
                _warn(
                    f"SKIP {company}: not eligible for Day {day} "
                    "(already recorded, or a prior touch is missing)."
                )
                continue
            if not _mark_one(backend, company, day):
                any_failed = True
    else:
        seen: set[str] = set()
        for name in companies:
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            if not _mark_one(backend, name, day):
                any_failed = True

    if any_failed:
        raise typer.Exit(code=1)


@app.command("notion-init")
def notion_init(
    parent_page_id: str = typer.Option(
        ..., "--parent-page-id", help="Notion page id to create the CRM database under."
    ),
) -> None:
    """Create a Notion CRM database matching the pipeline schema; print its data source id."""
    result = NotionBackend.create_database(parent_page_id)
    console.print(f"Created database [bold]{result['database_id']}[/bold].")
    console.print(f"Data source id: [bold]{result['data_source_id']}[/bold]")
    console.print(
        "\nExport this to use `--crm notion`:\n"
        f"  export NOTION_DATA_SOURCE_ID={result['data_source_id']}"
    )


def _supabase_schema_path() -> Path:
    """Locate supabase_schema.sql (repo root of a source checkout); bare name otherwise."""
    candidate = Path(__file__).resolve().parents[2] / "supabase_schema.sql"
    return candidate if candidate.is_file() else Path("supabase_schema.sql")


@app.command("supabase-init")
def supabase_init() -> None:
    """Print the Supabase pipeline-table setup steps, then verify the table responds."""
    schema_path = _supabase_schema_path()
    console.print(
        "To use `--crm supabase`, create the pipeline table once:\n"
        "  1. Open your Supabase project -> SQL Editor -> New query.\n"
        f"  2. Paste the contents of {schema_path} and click Run.\n"
        "  3. Export your credentials (Project Settings -> API):\n"
        "       export SUPABASE_URL=https://<ref>.supabase.co\n"
        "       export SUPABASE_SERVICE_ROLE_KEY=<service_role secret>\n"
    )
    try:
        supabase.verify_table()
    except supabase.SupabaseError as exc:
        _warn(str(exc))
        raise typer.Exit(code=1)
    console.print("[green]table ready[/green] — Supabase pipeline table is reachable.")


@app.command("gmail-auth")
def gmail_auth(
    client_secret: Path = typer.Option(
        ..., "--client-secret", help="Path to your Google Desktop-app OAuth client_secret.json."
    ),
) -> None:
    """Authorize Gmail via OAuth (PKCE + loopback). Stores a refresh token at .gmail-token.json."""
    try:
        token_path = gmail.authorize(client_secret)
    except gmail.GmailError as exc:
        _warn(str(exc))
        raise typer.Exit(code=1)
    console.print(f"\nSaved Gmail token to [bold]{token_path}[/bold] (0600 perms).")
    console.print(
        "[yellow]7-day caveat:[/yellow] while your Google OAuth app is in 'Testing' publishing "
        "status, this refresh token expires after 7 days. Re-run `squad gmail-auth` weekly."
    )


def _apply_and_report_bounces(result: gmail.BounceSyncResult) -> bool:
    """Append failed recipients (and dead domains) to blocklist.txt and report loudly.

    Returns True if any bounce message was unparseable — the caller turns that into a
    nonzero exit. Shared by `gmail-sync-bounces` and the `daily` Gmail step.
    """
    reasons: dict[str, str] = {}
    ordered: list[str] = []
    for bounce in result.bounces:
        rcpt = bounce.recipient.strip().lower()
        if rcpt and rcpt not in reasons:
            reasons[rcpt] = f"bounced ({bounce.diagnostic})" if bounce.diagnostic else "bounced"
            ordered.append(rcpt)
        if bounce.domain_failure and "@" in rcpt:
            domain = rcpt.split("@", 1)[1]
            if domain and domain not in reasons:
                reasons[domain] = (
                    f"domain-level failure ({bounce.diagnostic})"
                    if bounce.diagnostic
                    else "domain-level failure"
                )
                ordered.append(domain)

    added = append_entries(ordered)
    if added:
        console.print(f"Added [bold]{len(added)}[/bold] entr(ies) to blocklist.txt:")
        for entry in added:
            console.print(f"  + {entry}  — {reasons.get(entry, '')}", markup=False)
    elif result.bounces:
        console.print("Bounces found, but every failed recipient/domain was already blocklisted.")
    else:
        console.print("No new bounces.")

    if result.unparseable:
        for path in result.unparseable:
            _warn(f"UNPARSEABLE bounce saved to {path} — inspect it and blocklist manually.")
        _warn(
            f"{len(result.unparseable)} bounce message(s) could not be parsed. "
            "Partial results applied above; exiting nonzero."
        )
        return True
    return False


@app.command("gmail-sync-bounces")
def gmail_sync_bounces() -> None:
    """Scan Gmail for bounces and add failed recipients (and dead domains) to blocklist.txt.

    Appends the exact failed email always, plus its domain when the diagnostic indicates a
    domain-level failure. Dedupes against existing entries. Sends nothing.
    """
    try:
        result = gmail.sync_bounces()
    except gmail.GmailError as exc:
        _warn(str(exc))
        raise typer.Exit(code=1)

    if _apply_and_report_bounces(result):
        raise typer.Exit(code=1)


# --- squad daily: the bounded, propose-don't-execute campaign loop ---


class DailyConfigError(RuntimeError):
    """Raised when squad.toml is malformed or a required daily setting is absent."""


@dataclass
class DailyConfig:
    location: str
    vertical: str
    count: int
    service_line: Optional[str]


def _read_daily_toml(config_path: Path) -> dict:
    """Return the [daily] table from squad.toml, or {} if the file is absent. Loud on bad TOML."""
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise DailyConfigError(f"{path} is not valid TOML: {exc}")
    daily = data.get("daily", {})
    if not isinstance(daily, dict):
        raise DailyConfigError(f"[daily] in {path} must be a table of key = value pairs.")
    return daily


def _resolve_daily_config(
    *,
    location: Optional[str],
    vertical: Optional[str],
    count: Optional[int],
    service_line: Optional[str],
    config_path: Path,
) -> DailyConfig:
    """Merge flags over squad.toml [daily] (flags win). Missing required keys raise loudly."""
    table = _read_daily_toml(config_path)
    r_location = location or table.get("location")
    r_vertical = vertical or table.get("vertical")
    r_service = service_line or table.get("service_line")
    if count is not None:
        r_count = count
    elif "count" in table:
        r_count = table.get("count")
    else:
        r_count = DAILY_DEFAULT_COUNT

    missing: list[tuple[str, str]] = []
    if not (isinstance(r_location, str) and r_location.strip()):
        missing.append(("--location", "location"))
    if not (isinstance(r_vertical, str) and r_vertical.strip()):
        missing.append(("--vertical", "vertical"))
    if missing:
        details = "; ".join(
            f"{flag} (or [daily] {key} in {config_path})" for flag, key in missing
        )
        raise DailyConfigError(f"missing required daily config: {details}.")
    if isinstance(r_count, bool) or not isinstance(r_count, int) or r_count <= 0:
        raise DailyConfigError(
            f"daily count must be a positive integer (got {r_count!r}) — "
            f"pass -n/--count or set [daily] count in {config_path}."
        )
    return DailyConfig(
        location=r_location.strip(),
        vertical=r_vertical.strip(),
        count=r_count,
        service_line=(r_service.strip() if isinstance(r_service, str) and r_service.strip() else None),
    )


@app.command("daily")
def daily(
    location: Optional[str] = typer.Option(
        None, "--location", help="City / area to prospect in (overrides squad.toml [daily] location)."
    ),
    vertical: Optional[str] = typer.Option(
        None, "--vertical", help="Target vertical (overrides squad.toml [daily] vertical)."
    ),
    count: Optional[int] = typer.Option(
        None, "-n", "--count", help="Prospects to research (default 10, or [daily] count)."
    ),
    service_line: Optional[str] = typer.Option(
        None, "--service-line", help="Service line (overrides squad.toml [daily] service_line)."
    ),
    sender: Optional[str] = typer.Option(
        None, "--sender", help='Sign staged drafts as "Name | Business" (falls back to $SQUAD_SENDER).'
    ),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm", help="CRM backend: csv (default), notion, or supabase."
    ),
    gmail_sync: bool = typer.Option(
        False, "--gmail", help="Run Gmail bounce sync first (needs a token via `squad gmail-auth`)."
    ),
    gmail_drafts: bool = typer.Option(
        False, "--gmail-drafts", help="Also stage a Gmail draft per new lead (Day 1 touch)."
    ),
    config_path: Path = typer.Option(
        DAILY_CONFIG_PATH, "--config", help="Path to squad.toml."
    ),
) -> None:
    """Run the bounded daily loop: bounce sync (opt), research, follow-ups due, stage drafts.

    Proposes work only — NOTHING is ever sent and no Status is auto-advanced. `squad mark-sent`
    stays the single state-advancing human act.
    """
    try:
        cfg = _resolve_daily_config(
            location=location, vertical=vertical, count=count,
            service_line=service_line, config_path=config_path,
        )
    except DailyConfigError as exc:
        _warn(str(exc))
        raise typer.Exit(code=1)

    backend = get_backend(crm_backend.value)
    blocklist = Blocklist.load()
    problems = False

    # 1. Gmail bounce sync — only with --gmail; never folded in silently.
    console.rule("[bold]1. Gmail bounce sync[/bold]")
    if gmail_sync:
        try:
            result = gmail.sync_bounces()
        except gmail.GmailError as exc:
            _warn(str(exc))
            raise typer.Exit(code=1)
        if _apply_and_report_bounces(result):
            problems = True
        blocklist = Blocklist.load()  # pick up anything freshly blocklisted
    else:
        console.print("Gmail bounce sync skipped (--gmail not set).")

    # 2. Research new prospects (chunked path).
    console.rule("[bold]2. Research new prospects[/bold]")
    outcome = _run_research(
        cfg.location, cfg.vertical, cfg.count, cfg.service_line,
        backend=backend, blocklist=blocklist, seed=None,
    )
    _preview_research(outcome.all_rows)
    console.print(
        f"Appended {outcome.appended} new lead(s) to {backend.describe()} "
        f"({outcome.rows_produced - outcome.appended} duplicate(s) skipped)."
    )
    if outcome.failed_chunks:
        problems = True
        _warn(
            "research chunk(s) failed: "
            + ", ".join(str(i) for i in outcome.failed_chunks)
            + " — raw output saved under out/raw/."
        )

    # 3. Follow-ups due — surfaced, never sent.
    console.rule("[bold]3. Follow-ups due[/bold]")
    rows = backend.load()
    due = pipeline.followups_due(rows, date.today())
    if not due:
        console.print("No follow-ups due.")
    else:
        table = Table(title="Follow-ups due")
        for col in ("Company", "Touch", "Due since", "Outreach file"):
            table.add_column(col, overflow="fold")
        for company, touch, since in due:
            path = Path("out") / "outreach" / f"{pipeline.slugify(company)}.md"
            if path.is_file():
                table.add_row(company, touch, since, str(path))
            else:
                table.add_row(company, touch, since, f"{path} (MISSING)")
                _warn(
                    f"outreach file missing for {company} ({touch}): {path} — "
                    "draft it before sending."
                )
        console.print(table)

    # 4. Stage outreach drafts for eligible new leads (chunked path).
    console.rule("[bold]4. Stage outreach drafts[/bold]")
    eligible = _filter_eligible([r for r in rows if r.get("Status") == "New"], blocklist)
    staged = 0
    if not eligible:
        console.print("No eligible new leads to draft.")
    else:
        draft_outcome = _draft_eligible(eligible, sender, gmail_drafts=gmail_drafts)
        staged = len(draft_outcome.written)
        if draft_outcome.failed_chunks or draft_outcome.gmail_failed:
            problems = True

    # 5. Review summary — the whole point is that nothing left the building.
    console.rule("[bold]5. Summary — review, nothing sent[/bold]")
    console.print(f"Researched: {outcome.returned} returned, {outcome.appended} appended.")
    console.print(f"Follow-ups due: {len(due)}.")
    console.print(f"Outreach drafts staged: {staged}.")
    console.print(
        "[bold]Nothing was sent[/bold] and no Status changed — "
        "run `squad mark-sent` to record real sends."
    )

    if problems:
        _warn("daily completed with problems (see the loud lines above); exiting nonzero.")
        raise typer.Exit(code=1)


def _preview_research(rows: list[dict[str, str]]) -> None:
    table = Table(title="Research results")
    for col in ("Company", "City", "Email", "Lead Score", "Notes"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(
            r.get("Company", ""),
            r.get("City", ""),
            r.get("Email", "") or "—",
            r.get("Lead Score", ""),
            r.get("Notes", ""),
        )
    console.print(table)


def _preview_outreach(written: list[tuple[str, Path]]) -> None:
    table = Table(title="Outreach drafts")
    table.add_column("Company")
    table.add_column("File", overflow="fold")
    for company, path in written:
        table.add_row(company, str(path))
    console.print(table)


def _render_draft(company: str, email: str, draft: dict) -> str:
    parts = [f"# Outreach — {company}", ""]
    for day in ("day1", "day3", "day7"):
        touch = draft.get(day) or {}
        parts += [
            f"## {day.replace('day', 'Day ')}",
            f"To: {email}",
            f"Subject: {touch.get('subject', '')}",
            "",
            touch.get("body", ""),
            "",
        ]
    return "\n".join(parts)
