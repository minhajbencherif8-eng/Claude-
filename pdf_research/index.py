"""SQLite index of the extracted PDFs, plus company and person search."""

from __future__ import annotations

import difflib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .extractor import CompanyRecord, extract
from .textutil import normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    details TEXT NOT NULL,
    warnings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    position TEXT NOT NULL,
    salary_raw TEXT NOT NULL,
    salary REAL
);
CREATE INDEX IF NOT EXISTS idx_emp_company ON employees(company_id);
"""


@dataclass
class IndexReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    failed: list[tuple[str, str]] = None
    warnings: list[tuple[str, str]] = None


class Index:
    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- building ----------

    def build(self, folder: str | Path, progress=None) -> IndexReport:
        """Index every PDF under `folder`, re-reading only new or changed files."""
        folder = Path(folder)
        report = IndexReport(failed=[], warnings=[])
        pdfs = sorted(p for p in folder.rglob("*") if p.suffix.lower() == ".pdf" and p.is_file())
        known = {r["path"]: (r["mtime"], r["size"]) for r in self.db.execute("SELECT * FROM files")}
        current = set()
        for pdf in pdfs:
            key = str(pdf.resolve())
            current.add(key)
            stat = pdf.stat()
            if known.get(key) == (stat.st_mtime, stat.st_size):
                report.unchanged += 1
                continue
            if progress:
                progress(pdf)
            try:
                record = extract(pdf)
            except Exception as exc:  # a broken PDF must not stop the whole run
                report.failed.append((str(pdf), str(exc)))
                continue
            with self.db:
                self.db.execute("DELETE FROM files WHERE path = ?", (key,))
                self.db.execute("INSERT INTO files VALUES (?, ?, ?)", (key, stat.st_mtime, stat.st_size))
                self._store(key, record)
            if key in known:
                report.updated += 1
            else:
                report.added += 1
            report.warnings.extend((str(pdf), w) for w in record.warnings)
        with self.db:
            for gone in set(known) - current:
                if Path(gone).is_relative_to(folder.resolve()):
                    self.db.execute("DELETE FROM files WHERE path = ?", (gone,))
                    report.removed += 1
        return report

    def _store(self, source: str, rec: CompanyRecord) -> None:
        cur = self.db.execute(
            "INSERT INTO companies (source, name, name_norm, details, warnings) VALUES (?, ?, ?, ?, ?)",
            (source, rec.company, normalize(rec.company), json.dumps(rec.details, ensure_ascii=False),
             json.dumps(rec.warnings)),
        )
        self.db.executemany(
            "INSERT INTO employees (company_id, name, name_norm, position, salary_raw, salary)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(cur.lastrowid, e.name, normalize(e.name), e.position, e.salary_raw, e.salary)
             for e in rec.employees],
        )

    # ---------- searching ----------

    def search_company(self, query: str) -> list[dict]:
        """Companies whose name matches `query`, each with its employees."""
        rows = self.db.execute("SELECT * FROM companies ORDER BY name").fetchall()
        hits = _match(query, rows)
        return [self._company_dict(r) for r in hits]

    def search_person(self, query: str) -> list[dict]:
        """People whose name matches `query`, with their company and salary."""
        rows = self.db.execute(
            "SELECT e.*, c.name AS company, c.source FROM employees e"
            " JOIN companies c ON c.id = e.company_id ORDER BY e.name"
        ).fetchall()
        return [
            {"name": r["name"], "position": r["position"], "salary": r["salary"],
             "salary_raw": r["salary_raw"], "company": r["company"], "source": r["source"]}
            for r in _match(query, rows)
        ]

    def list_companies(self) -> list[dict]:
        return [self._company_dict(r) for r in self.db.execute("SELECT * FROM companies ORDER BY name")]

    def _company_dict(self, row: sqlite3.Row) -> dict:
        employees = self.db.execute(
            "SELECT name, position, salary, salary_raw FROM employees WHERE company_id = ? ORDER BY name",
            (row["id"],),
        ).fetchall()
        return {
            "company": row["name"],
            "source": row["source"],
            "details": json.loads(row["details"]),
            "warnings": json.loads(row["warnings"]),
            "employees": [dict(e) for e in employees],
        }


def _match(query: str, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Accent/case-insensitive match; every query word must appear, in any order.

    Falls back to close (typo-tolerant) matches when nothing matches exactly.
    """
    q = normalize(query)
    if not q:
        return []
    words = q.split()
    exact = [r for r in rows if all(w in r["name_norm"] for w in words)]
    if exact:
        return exact
    def score(r):
        return max(difflib.SequenceMatcher(None, q, r["name_norm"]).ratio(),
                   *(difflib.SequenceMatcher(None, q, t).ratio() for t in r["name_norm"].split()))
    scored = sorted(((score(r), r) for r in rows), key=lambda x: -x[0])
    return [r for s, r in scored if s >= 0.75]
