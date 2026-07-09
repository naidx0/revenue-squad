"""revenue-squad CLI: research -> outreach -> proposal, driven through the Claude CLI."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import pipeline, places
from .backend import CrmChoice, get_backend
from .blocklist import Blocklist
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


@app.command("research")
def research(
    location: str = typer.Argument(..., help="City / area to prospect in."),
    vertical: str = typer.Argument(..., help="Target industry vertical."),
    count: int = typer.Option(5, "-n", "--count", help="Number of prospects to research."),
    service_line: Optional[str] = typer.Option(
        None, "--service-line", help="Service line you're pitching (stored on each lead)."
    ),
    crm_backend: CrmChoice = typer.Option(
        CrmChoice.csv, "--crm", help="CRM backend to append leads to: csv (default) or notion."
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
        CrmChoice.csv, "--crm", help="CRM backend to read leads from: csv (default) or notion."
    ),
) -> None:
    """Draft Day 1/3/7 cold outreach for eligible leads. Never changes Status."""
    backend = get_backend(crm_backend.value)
    blocklist = Blocklist.load()
    rows = backend.load()
    if not rows:
        raise typer.BadParameter(f"{backend.describe()} is empty — run `squad research` first.")

    if companies:
        wanted = {c.strip().lower() for c in companies}
        targets = [r for r in rows if r.get("Company", "").strip().lower() in wanted]
        missing = wanted - {r.get("Company", "").strip().lower() for r in targets}
        for name in sorted(missing):
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

    if not eligible:
        console.print("No eligible leads to draft.")
        raise typer.Exit(code=0)

    lead_payload = [
        {
            "company": r.get("Company", ""),
            "contact": r.get("Contact", ""),
            "email": r.get("Email", ""),
            "city": r.get("City", ""),
            "website": r.get("Website", ""),
            "vertical": r.get("Vertical", ""),
            "service_line": r.get("Service Line", ""),
        }
        for r in eligible
    ]
    task = (
        "Draft Day 1 / Day 3 / Day 7 cold outreach for these leads. Follow the outreach "
        "skill's output contract exactly and end with the JSON block.\n"
        + json.dumps(lead_payload, indent=2)
    )
    data = run_skill(task, "outreach")  # no tools: drafting only
    drafts = data.get("drafts") if isinstance(data, dict) else None
    if not isinstance(drafts, list):
        raise typer.BadParameter("outreach result JSON had no 'drafts' array")

    out_dir = Path("out") / "outreach"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_company = {r.get("Company", "").strip().lower(): r for r in eligible}
    written: list[tuple[str, Path]] = []
    for draft in drafts:
        company = (draft.get("company") or "").strip()
        row = by_company.get(company.lower())
        if row is None:
            _warn(f"SKIP draft for {company!r}: no matching eligible lead")
            continue
        path = out_dir / f"{pipeline.slugify(company)}.md"
        path.write_text(_render_draft(company, row.get("Email", ""), draft))
        written.append((company, path))

    _preview_outreach(written)
    console.print(f"\nWrote {len(written)} outreach file(s) under {out_dir}.")


@app.command("propose")
def propose(
    company: str = typer.Argument(..., help="Company to write a proposal for."),
    notes: Path = typer.Option(..., "--notes", help="Path to discovery notes (markdown)."),
) -> None:
    """Generate a markdown proposal from discovery notes. Does not change Status."""
    if not notes.is_file():
        raise typer.BadParameter(f"notes file not found: {notes}")
    task = (
        f"Write a complete client proposal for {company}. Return markdown only (no JSON). "
        f"Discovery notes follow:\n\n{notes.read_text()}"
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
        CrmChoice.csv, "--crm", help="CRM backend to update: csv (default) or notion."
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
