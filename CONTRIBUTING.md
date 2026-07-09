# Contributing

The most useful contributions are **new pluggable adapters** — this repo is built
around two seams that are meant to be extended.

## New CRM backends

The CRM is selected by `--crm` and lives behind the `CrmBackend` protocol in
[`src/revenue_squad/backend.py`](src/revenue_squad/backend.py):

```python
class CrmBackend(Protocol):
    def load(self) -> list[dict[str, str]]: ...
    def append(self, rows: list[dict[str, str]]) -> list[dict[str, str]]: ...
    def mark_sent(self, company: str, day: int = 1) -> dict[str, str]: ...
    def describe(self) -> str: ...
```

`CsvBackend` and `NotionBackend` implement it. To add one (Airtable, a Google Sheet,
a Postgres table, …): implement the four methods, use `crm.COLUMNS` as the schema of
record, dedupe by `(Company, Email)` exactly as the existing backends do, add a value
to the `CrmChoice` enum, and wire it into `get_backend`.

## New lead-seed sources

`--seed` sources follow [`src/revenue_squad/places.py`](src/revenue_squad/places.py):
pull untrusted candidate businesses, sanitize them, and render them as a clearly
delimited, injection-safe block for the research prompt (candidates are *data to
verify*, never instructions). Add a value to `SeedSource` and hand its output to the
research task the same way `format_candidates` does.

## Rules for any adapter

- **Mock-test it.** Every code path — success, empty result, and each failure — must
  be covered by tests that mock the network/subprocess. The suite never touches a real
  API. See `tests/test_notion.py` and `tests/test_places.py` for the pattern.
- **Fail loudly.** A missing token or a non-2xx response raises with an actionable
  message and a body/stderr tail. No silent fallbacks, no retries-that-mask, no
  `except: pass` (see `AGENTS.md §5`, mirrored throughout the codebase).
- **Don't claim live verification you didn't get.** If an adapter has only ever been
  exercised against mocks, say exactly that in its docs and in the README honesty
  ledger — the way the Notion/Places status is written today. An adapter that ships
  claiming a live run it never had is the one thing this project won't accept.

## Before you open a PR

Run the gate — it's the same three lines CI runs:

```console
uv sync --all-extras
uv run pytest -q --timeout=60 --timeout-method=thread --durations=10 --durations-min=1
uv run squad --help
```

PR expectations: the gate is green, new behavior is locked by a test, changed docs
ship in the same PR as the code, and every changed line traces to the change you're
making (don't refactor unrelated code in passing).
