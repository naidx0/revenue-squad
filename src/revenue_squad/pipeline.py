"""Post-processing between the Claude skills and the CSV pipeline.

- research: validate contract fields, drop blocklisted leads (loud), MX-check emails
  and demote failures to null (loud), require an evidence URL for any email (loud),
  map survivors to CRM rows (Status=New).
- outreach: decide per-lead eligibility, with a loud refusal reason.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import crm
from .blocklist import Blocklist
from .verify import check_mx

# Keys of the research output contract's per-lead object.
LEAD_KEYS = (
    "company",
    "website",
    "contact_name",
    "title",
    "email",
    "email_evidence_url",
    "phone",
    "city",
    "vertical",
    "score",
    "score_rationale",
    "notes",
)

Reporter = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "untitled"


def domain_of(value: str) -> str:
    """Extract a bare domain from a URL or a plain domain string."""
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "//" + value
    netloc = urlparse(value).netloc
    if not netloc:
        return ""
    host = netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _normalize_lead(lead: object) -> dict:
    if not isinstance(lead, dict):
        raise ValueError(f"lead is not a JSON object: {lead!r}")
    norm = {key: lead.get(key) for key in LEAD_KEYS}
    if not (norm.get("company") or "").strip():
        raise ValueError(f"lead missing required 'company': {lead!r}")
    return norm


def process_research_leads(
    leads: list,
    *,
    vertical: str,
    service_line: str | None,
    batch: str,
    blocklist: Blocklist,
    mx_check: Callable[[str], tuple[bool, str]] = check_mx,
    report: Reporter = _noop,
) -> list[dict[str, str]]:
    """Return CRM rows (Status=New) for the surviving, contract-valid leads."""
    rows: list[dict[str, str]] = []
    for raw in leads:
        lead = _normalize_lead(raw)
        company = lead["company"].strip()
        email = (lead.get("email") or "").strip()
        evidence = (lead.get("email_evidence_url") or "").strip()
        website = (lead.get("website") or "").strip()
        site_domain = domain_of(website)

        # Drop entirely if the email or the site domain is blocklisted.
        if email and blocklist.is_blocked(email):
            report(f"DROP {company}: email {email} is blocklisted")
            continue
        if site_domain and blocklist.is_blocked(site_domain):
            report(f"DROP {company}: domain {site_domain} is blocklisted")
            continue

        demotions: list[str] = []

        # An email is only allowed with evidence it was seen on a real page.
        if email and not (lead.get("email_evidence_url") or "").strip():
            report(f"DEMOTE {company}: email {email} -> null (no evidence URL)")
            demotions.append(f"email {email} dropped: no evidence URL")
            email = ""

        # MX-verify the email's domain; a failure demotes the email to null.
        if email:
            ok, reason = mx_check(email.split("@", 1)[1])
            if not ok:
                report(f"DEMOTE {company}: email {email} -> null (MX: {reason})")
                demotions.append(f"email {email} dropped: MX failed ({reason})")
                email = ""

        row = crm.empty_row()
        row.update(
            {
                "Company": company,
                "Contact": (lead.get("contact_name") or "").strip(),
                "Email": email,
                # Evidence only rides along with an email that survived validation.
                "Email Evidence": evidence if email else "",
                "Phone": (lead.get("phone") or "").strip(),
                "City": (lead.get("city") or "").strip(),
                "Website": website,
                "Status": "New",
                "Vertical": (lead.get("vertical") or vertical or "").strip(),
                "Service Line": (service_line or "").strip(),
                "Batch": batch,
                "Lead Score": _score_str(lead.get("score")),
                "Score Rationale": (lead.get("score_rationale") or "").strip(),
            }
        )
        notes = (lead.get("notes") or "").strip()
        if demotions:
            notes = " | ".join([n for n in [notes, *demotions] if n])
        row["Notes"] = notes
        rows.append(row)
    return rows


def _score_str(score: object) -> str:
    if score is None or score == "":
        return ""
    return str(score)


def write_research_outputs(
    rows: list[dict[str, str]],
    *,
    vertical_slug: str,
    date_str: str | None = None,
    out_dir: Path | str = "out",
) -> tuple[Path, Path]:
    """Write out/research-<slug>-<date>.json + a markdown summary. Returns both paths."""
    date_str = date_str or date.today().isoformat()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"research-{vertical_slug}-{date_str}"

    json_path = out_dir / f"{stem}.json"
    json_path.write_text(json.dumps(rows, indent=2))

    md_path = out_dir / f"{stem}.md"
    md_path.write_text(_markdown_summary(rows, stem))
    return json_path, md_path


def _markdown_summary(rows: list[dict[str, str]], stem: str) -> str:
    lines = [
        f"# {stem}",
        "",
        f"{len(rows)} lead(s) appended as Status=New.",
        "",
        "| Company | City | Email | Score | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        email = r.get("Email") or "_(unverified)_"
        notes = (r.get("Notes") or "").replace("|", "\\|")
        lines.append(
            f"| {r.get('Company', '')} | {r.get('City', '')} | {email} | "
            f"{r.get('Lead Score', '')} | {notes} |"
        )
    return "\n".join(lines) + "\n"


def outreach_eligibility(
    row: dict[str, str], blocklist: Blocklist
) -> tuple[bool, str]:
    """Return (eligible, reason). Refuse Blocked, Lost, blocklisted, or no verified email."""
    if (row.get("Blocked") or "").strip():
        return (False, "row is marked Blocked")
    if (row.get("Status") or "").strip() == "Lost":
        return (False, "status is Lost")
    email = (row.get("Email") or "").strip()
    evidence = (row.get("Email Evidence") or "").strip()
    # A lead is only workable with BOTH a surviving email and the evidence URL
    # proving it was seen on a real page — missing either is the same refusal.
    if not email or not evidence:
        return (False, "no verified email (with evidence)")
    if blocklist.is_blocked(email):
        return (False, "email is blocklisted")
    site_domain = domain_of(row.get("Website", ""))
    if site_domain and blocklist.is_blocked(site_domain):
        return (False, f"domain {site_domain} is blocklisted")
    return (True, "")
