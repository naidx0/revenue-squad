"""CRM backend seam: pick where the lead pipeline is stored.

CsvBackend wraps crm.py (the local ./pipeline.csv) unchanged; NotionBackend
(notion.py) talks to the Notion REST API; SupabaseBackend (supabase.py) talks to
PostgREST. `--crm csv|notion|supabase` selects one.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from . import crm
from .notion import NotionBackend
from .supabase import SupabaseBackend


class CrmChoice(str, Enum):
    csv = "csv"
    notion = "notion"
    supabase = "supabase"


class CrmBackend(Protocol):
    def load(self) -> list[dict[str, str]]: ...
    def append(self, rows: list[dict[str, str]]) -> list[dict[str, str]]: ...
    def mark_sent(self, company: str, day: int = 1) -> dict[str, str]: ...
    def describe(self) -> str: ...


class CsvBackend:
    """The default local ./pipeline.csv, delegating to crm.py unchanged."""

    def __init__(self, path=crm.DEFAULT_PATH) -> None:
        self._path = path

    def load(self) -> list[dict[str, str]]:
        return crm.load(self._path)

    def append(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return crm.append(rows, self._path)

    def mark_sent(self, company: str, day: int = 1) -> dict[str, str]:
        return crm.mark_sent(company, day=day, path=self._path)

    def describe(self) -> str:
        return str(self._path)


def get_backend(choice: str) -> CrmBackend:
    if choice == CrmChoice.csv.value:
        return CsvBackend()
    if choice == CrmChoice.notion.value:
        return NotionBackend.from_env()
    if choice == CrmChoice.supabase.value:
        return SupabaseBackend.from_env()
    raise ValueError(f"unknown crm backend: {choice!r}")
