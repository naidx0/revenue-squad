"""MX verification via dnspython. Unverified is never silently treated as OK."""

from __future__ import annotations

import dns.exception
import dns.resolver

MX_TIMEOUT = 5.0


def check_mx(domain: str, timeout: float = MX_TIMEOUT) -> tuple[bool, str]:
    """Return (ok, reason). ok is True only when MX records were positively found.

    NoAnswer / NXDOMAIN -> (False, reason). DNS timeout -> (False, "DNS timeout ...").
    Callers must treat every (False, ...) as NOT verified.
    """
    domain = (domain or "").strip()
    if not domain:
        return (False, "empty domain")
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
    except dns.resolver.NXDOMAIN:
        return (False, f"NXDOMAIN — {domain} does not exist")
    except dns.resolver.NoAnswer:
        return (False, f"NoAnswer — no MX records for {domain}")
    except dns.resolver.NoNameservers:
        return (False, f"NoNameservers — could not resolve {domain}")
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
        return (False, "DNS timeout — could not verify")
    if len(answers) == 0:
        return (False, f"no MX records for {domain}")
    return (True, f"{len(answers)} MX record(s) for {domain}")
