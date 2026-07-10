"""revenue-squad CLI: research -> outreach -> proposal, driven through the Claude CLI."""

from __future__ import annotations

import json
import os
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
from .runner import run_skill

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
    task = (
        f"Research {count} prospective B2B clients in {location} for the '{vertical}' vertical"
        + (f", to pitch our '{service_line}' service line" if service_line else "")
        + ". Follow the research skill's output contract exactly and end with the JSON block."
    )
    if seed == places.SeedSource.places:
        task += "\n\n" + places.format_candidates(
            places.search_places(vertical, location, count)
        )
    data = run_skill(task, "research", allowed_tools=["WebSearch", "WebFetch"])
    leads = data.get("leads") if isinstance(data, dict) else None
    if not isinstance(leads, list):
        raise typer.BadParameter("research result JSON had no 'leads' array")

    date_str = date.today().isoformat()
    vertical_slug = pipeline.slugify(vertical)
    rows = pipeline.process_research_leads(
        leads,
        vertical=vertical,
        service_line=service_line,
        batch=f"{vertical_slug}-{date_str}",
        blocklist=blocklist,
        report=_warn,
    )
    added = backend.append(rows)
    json_path, md_path = pipeline.write_research_outputs(
        rows, vertical_slug=vertical_slug, date_str=date_str
    )

    _preview_research(rows)
    console.print(
        f"\nAppended [bold]{len(added)}[/bold] new lead(s) to {backend.describe()} "
        f"({len(rows) - len(added)} duplicate(s) skipped)."
    )
    console.print(f"Wrote {json_path} and {md_path}.")


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

    eligible: list[dict[str, str]] = []
    for row in targets:
        ok, reason = pipeline.outreach_eligibility(row, blocklist)
        if ok:
            eligible.append(row)
        else:
            _warn(f"REFUSE {row.get('Company', '')}: {reason}")

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
        prose, drafted_keys, gmail_failed = _draft_eligible(
            eligible, sender, gmail_drafts=gmail_drafts
        )

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


def _draft_eligible(
    eligible: list[dict[str, str]], sender: Optional[str], *, gmail_drafts: bool = False
) -> tuple[str, set[str], bool]:
    """Run the outreach skill for eligible leads, write each returned draft, and return
    (model prose, set of drafted Company keys, gmail_failed). Any draft that comes back is
    written to disk; with gmail_drafts, each is also pushed to Gmail as a Day 1 draft."""
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
        for r in eligible
    ]
    task = (
        "Draft Day 1 / Day 3 / Day 7 cold outreach for these leads. Follow the outreach "
        "skill's output contract exactly and end with the JSON block."
        + _sender_instruction(sender)
        + "\n\nLeads to draft (JSON array):\n"
        + json.dumps(lead_payload, indent=2)
    )
    data, prose = run_skill(task, "outreach", return_prose=True)  # no tools: drafting only
    drafts = data.get("drafts") if isinstance(data, dict) else None
    if not isinstance(drafts, list):
        raise typer.BadParameter("outreach result JSON had no 'drafts' array")

    out_dir = Path("out") / "outreach"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_company = {r.get("Company", "").strip().lower(): r for r in eligible}
    written: list[tuple[str, Path]] = []
    drafted_keys: set[str] = set()
    for_gmail: list[tuple[str, dict[str, str], dict]] = []  # (company, row, draft)
    for draft in drafts:
        company = (draft.get("company") or "").strip()
        row = by_company.get(company.lower())
        if row is None:
            _warn(f"SKIP draft for {company!r}: no matching eligible lead")
            continue
        path = out_dir / f"{pipeline.slugify(company)}.md"
        path.write_text(_render_draft(company, row.get("Email", ""), draft))
        written.append((company, path))
        drafted_keys.add(row.get("Company", "").strip().lower())
        for_gmail.append((company, row, draft))

    _preview_outreach(written)
    console.print(f"\nWrote {len(written)} outreach file(s) under {out_dir}.")

    gmail_failed = False
    if gmail_drafts and for_gmail:
        gmail_failed = _create_gmail_drafts(for_gmail, sender)
    return prose, drafted_keys, gmail_failed


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


@app.command("mark-sent")
def mark_sent(
    company: str = typer.Argument(..., help="Company whose send you're recording."),
    day: int = typer.Option(1, "--day", help="Which touch was sent: 1, 3, or 7."),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm",
        help="CRM backend to update: csv (default), notion, or supabase.",
    ),
) -> None:
    """Record that a Day 1/3/7 email went out. Day 1 also moves Status New->Contacted."""
    if day not in (1, 3, 7):
        raise typer.BadParameter("--day must be 1, 3, or 7")
    row = get_backend(crm_backend.value).mark_sent(company, day=day)
    console.print(
        f"Marked Day {day} sent for [bold]{row.get('Company', '')}[/bold] "
        f"(Status={row.get('Status', '')})."
    )


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
