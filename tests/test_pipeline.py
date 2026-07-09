import pytest

from revenue_squad import pipeline
from revenue_squad.blocklist import Blocklist


def _empty_blocklist():
    return Blocklist(emails=set(), domains=set())


def _lead(**over):
    base = {
        "company": "Acme",
        "website": "https://acme.com",
        "contact_name": "Jane",
        "title": "COO",
        "email": "jane@acme.com",
        "email_evidence_url": "https://acme.com/team",
        "phone": "555-1000",
        "city": "Denver",
        "vertical": "dentists",
        "score": 8,
        "score_rationale": "great fit",
        "notes": "warm",
    }
    base.update(over)
    return base


def _mx_ok(_domain):
    return (True, "ok")


def _mx_fail(_domain):
    return (False, "NXDOMAIN — gone")


def _process(leads, blocklist=None, mx=_mx_ok, **kw):
    reports = []
    rows = pipeline.process_research_leads(
        leads,
        vertical="dentists",
        service_line="SEO",
        batch="dentists-2026-07-09",
        blocklist=blocklist or _empty_blocklist(),
        mx_check=mx,
        report=reports.append,
        **kw,
    )
    return rows, reports


def test_slugify():
    assert pipeline.slugify("Med Spas & Clinics!") == "med-spas-clinics"
    assert pipeline.slugify("") == "untitled"


def test_domain_of():
    assert pipeline.domain_of("https://www.acme.com/team") == "acme.com"
    assert pipeline.domain_of("acme.com") == "acme.com"
    assert pipeline.domain_of("") == ""


def test_happy_path_maps_fields():
    rows, reports = _process([_lead()])
    assert reports == []
    row = rows[0]
    assert row["Company"] == "Acme"
    assert row["Email"] == "jane@acme.com"
    assert row["Email Evidence"] == "https://acme.com/team"
    assert row["Status"] == "New"
    assert row["Service Line"] == "SEO"
    assert row["Batch"] == "dentists-2026-07-09"
    assert row["Lead Score"] == "8"
    assert row["Contact"] == "Jane"


def test_evidence_dropped_when_email_demoted_for_mx():
    # A surviving email carries its evidence; a demoted email leaves the column blank.
    rows, _ = _process([_lead()], mx=_mx_fail)
    assert rows[0]["Email"] == ""
    assert rows[0]["Email Evidence"] == ""


def test_evidence_blank_when_no_email():
    rows, _ = _process([_lead(email=None, email_evidence_url=None)])
    assert rows[0]["Email Evidence"] == ""


def test_blocklisted_email_drops_lead():
    bl = Blocklist(emails={"jane@acme.com"}, domains=set())
    rows, reports = _process([_lead()], blocklist=bl)
    assert rows == []
    assert any("blocklisted" in r for r in reports)


def test_blocklisted_domain_drops_lead():
    bl = Blocklist(emails=set(), domains={"acme.com"})
    rows, reports = _process([_lead(email="", email_evidence_url=None)], blocklist=bl)
    assert rows == []
    assert any("domain" in r for r in reports)


def test_missing_evidence_demotes_email_to_null():
    rows, reports = _process([_lead(email_evidence_url=None)])
    assert rows[0]["Email"] == ""
    assert any("no evidence URL" in r for r in reports)
    assert "no evidence URL" in rows[0]["Notes"]


def test_mx_failure_demotes_email_to_null():
    rows, reports = _process([_lead()], mx=_mx_fail)
    assert rows[0]["Email"] == ""
    assert any("MX" in r for r in reports)
    assert "MX failed" in rows[0]["Notes"]
    # lead survives with a null email
    assert rows[0]["Company"] == "Acme"


def test_lead_without_email_is_kept():
    rows, reports = _process([_lead(email=None, email_evidence_url=None)])
    assert rows[0]["Email"] == ""
    assert reports == []


def test_missing_company_raises():
    with pytest.raises(ValueError, match="missing required 'company'"):
        _process([_lead(company="")])


def test_non_object_lead_raises():
    with pytest.raises(ValueError, match="not a JSON object"):
        _process(["just a string"])


def test_write_research_outputs(tmp_path):
    rows, _ = _process([_lead()])
    json_path, md_path = pipeline.write_research_outputs(
        rows, vertical_slug="dentists", date_str="2026-07-09", out_dir=tmp_path / "out"
    )
    assert json_path.exists() and md_path.exists()
    assert "Acme" in md_path.read_text()
    assert json_path.name == "research-dentists-2026-07-09.json"


# --- outreach eligibility ---

def _row(**over):
    from revenue_squad import crm

    row = crm.empty_row()
    row.update({
        "Company": "Acme",
        "Email": "jane@acme.com",
        "Email Evidence": "https://acme.com/team",
        "Website": "https://acme.com",
        "Status": "New",
    })
    row.update(over)
    return row


def test_eligible_row_passes():
    ok, reason = pipeline.outreach_eligibility(_row(), _empty_blocklist())
    assert ok is True and reason == ""


def test_refuse_blocked():
    ok, reason = pipeline.outreach_eligibility(_row(Blocked="yes"), _empty_blocklist())
    assert ok is False and "Blocked" in reason


def test_refuse_lost():
    ok, reason = pipeline.outreach_eligibility(_row(Status="Lost"), _empty_blocklist())
    assert ok is False and "Lost" in reason


def test_refuse_no_email():
    ok, reason = pipeline.outreach_eligibility(_row(Email=""), _empty_blocklist())
    assert ok is False and "no verified email" in reason


def test_refuse_no_evidence():
    # Email present but Email Evidence blank -> same refusal (both are required).
    ok, reason = pipeline.outreach_eligibility(
        _row(**{"Email Evidence": ""}), _empty_blocklist()
    )
    assert ok is False and "no verified email (with evidence)" in reason


def test_refuse_blocklisted_email():
    bl = Blocklist(emails={"jane@acme.com"}, domains=set())
    ok, reason = pipeline.outreach_eligibility(_row(), bl)
    assert ok is False and "blocklisted" in reason


def test_refuse_blocklisted_domain():
    bl = Blocklist(emails=set(), domains={"acme.com"})
    ok, reason = pipeline.outreach_eligibility(_row(), bl)
    assert ok is False and "blocklisted" in reason
