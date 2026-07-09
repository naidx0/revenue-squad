from revenue_squad.blocklist import Blocklist


def _write(tmp_path, text):
    p = tmp_path / "blocklist.txt"
    p.write_text(text)
    return p


def test_missing_file_is_empty(tmp_path):
    bl = Blocklist.load(tmp_path / "nope.txt")
    assert bl.is_blocked("anyone@example.com") is False


def test_exact_email_match(tmp_path):
    bl = Blocklist.load(_write(tmp_path, "bounce@acme.com\n"))
    assert bl.is_blocked("bounce@acme.com") is True
    assert bl.is_blocked("BOUNCE@ACME.COM") is True
    assert bl.is_blocked("other@acme.com") is False


def test_domain_match_blocks_all_addresses(tmp_path):
    bl = Blocklist.load(_write(tmp_path, "spamco.com\n"))
    assert bl.is_blocked("anyone@spamco.com") is True
    assert bl.is_blocked("spamco.com") is True
    assert bl.is_blocked("anyone@other.com") is False


def test_comments_and_blank_lines_ignored(tmp_path):
    text = "# a comment\n\nspamco.com  # trailing comment\n   \nbounce@acme.com\n"
    bl = Blocklist.load(_write(tmp_path, text))
    assert bl.is_blocked("x@spamco.com") is True
    assert bl.is_blocked("bounce@acme.com") is True
    assert bl.is_blocked("# a comment") is False


def test_empty_value_not_blocked(tmp_path):
    bl = Blocklist.load(_write(tmp_path, "spamco.com\n"))
    assert bl.is_blocked("") is False
