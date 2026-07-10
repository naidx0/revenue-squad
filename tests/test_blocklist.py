from revenue_squad.blocklist import Blocklist, append_entries


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


# --- append_entries: preserves existing content, dedupes, atomic, dated header ---

def test_append_entries_creates_file(tmp_path):
    p = tmp_path / "blocklist.txt"
    added = append_entries(["bounce@acme.com", "dead.test"], p)
    assert added == ["bounce@acme.com", "dead.test"]
    bl = Blocklist.load(p)
    assert bl.is_blocked("bounce@acme.com")
    assert bl.is_blocked("anyone@dead.test")
    assert "# added by squad gmail-sync-bounces" in p.read_text()


def test_append_entries_preserves_existing_comments(tmp_path):
    p = tmp_path / "blocklist.txt"
    p.write_text("# hand-maintained\nmanual@old.com\n")
    append_entries(["new@fresh.com"], p)
    text = p.read_text()
    assert "# hand-maintained" in text
    assert "manual@old.com" in text
    assert "new@fresh.com" in text
    # Existing content stays above the new dated header (nothing rewritten).
    assert text.index("manual@old.com") < text.index("# added by squad")


def test_append_entries_dedupes_case_insensitively(tmp_path):
    p = tmp_path / "blocklist.txt"
    p.write_text("Existing@Acme.com\n")
    added = append_entries(["existing@acme.com", "NEW@x.com"], p)
    assert added == ["new@x.com"]


def test_append_entries_dedupes_within_batch(tmp_path):
    p = tmp_path / "blocklist.txt"
    added = append_entries(["dup@x.com", "DUP@x.com", "other@y.com"], p)
    assert added == ["dup@x.com", "other@y.com"]


def test_append_entries_nothing_new_returns_empty_and_no_write(tmp_path):
    p = tmp_path / "blocklist.txt"
    p.write_text("a@b.com\n")
    before = p.read_text()
    assert append_entries(["a@b.com", "A@B.COM"], p) == []
    assert p.read_text() == before
