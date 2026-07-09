import dns.exception
import dns.resolver

from revenue_squad.verify import check_mx


def test_check_mx_ok(monkeypatch):
    monkeypatch.setattr(dns.resolver, "resolve", lambda *a, **k: ["mx1", "mx2"])
    ok, reason = check_mx("example.com")
    assert ok is True
    assert "MX record" in reason


def test_check_mx_nxdomain(monkeypatch):
    def raise_nx(*a, **k):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "resolve", raise_nx)
    ok, reason = check_mx("nope.invalid")
    assert ok is False
    assert "NXDOMAIN" in reason


def test_check_mx_no_answer(monkeypatch):
    def raise_na(*a, **k):
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(dns.resolver, "resolve", raise_na)
    ok, reason = check_mx("example.com")
    assert ok is False
    assert "NoAnswer" in reason


def test_check_mx_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise dns.exception.Timeout()

    monkeypatch.setattr(dns.resolver, "resolve", raise_timeout)
    ok, reason = check_mx("slow.example")
    assert ok is False
    assert reason == "DNS timeout — could not verify"


def test_check_mx_empty_domain():
    ok, reason = check_mx("")
    assert ok is False
    assert "empty" in reason
