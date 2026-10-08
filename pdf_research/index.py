"""SQLite index of the extracted PDFs, plus company and person search.

PDFs are read straight out of a .zip (nothing is extracted to disk) or from a
folder, on several CPU cores. Results are committed as they come in, so an
interrupted run resumes where it stopped, and a re-run only reads PDFs that
are new or changed.
"""

from __future__ import annotations

import difflib
import json
import os
import sqlite3
import threading
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .extractor import CompanyRecord, extract
from .textutil import normalize

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,      -- file path, or "<zip path>::<member name>"
    stamp TEXT NOT NULL         -- mtime/size or CRC/size, to spot changed files
);
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    search TEXT NOT NULL,       -- normalized name + identifiers
    details TEXT NOT NULL,
    payroll TEXT NOT NULL,
    warnings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    search TEXT NOT NULL,       -- normalized name + reference number
    ref TEXT NOT NULL,
    position TEXT NOT NULL,
    days INTEGER,
    period TEXT NOT NULL,
    salary_raw TEXT NOT NULL,
    salary REAL
);
CREATE INDEX IF NOT EXISTS idx_emp_company ON employees(company_id);
CREATE INDEX IF NOT EXISTS idx_company_source ON companies(source);
"""
COMPANY_ID_FIELDS = ("N° d'affiliation", "Identifiant Commun de l'Entreprise",
                     "N° Registre de commerce", "Attestation n°")
MAX_RESULTS = 1000
ZIP_SEP = "::"


@dataclass
class IndexReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    cancelled: bool = False
    failed: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Item:
    key: str        # stored path
    stamp: str
    path: str       # file on disk (the zip, for zip members)
    member: str | None = None


# Each worker process keeps its zip open between PDFs.
_open_zips: dict[str, zipfile.ZipFile] = {}


def _work(item: _Item) -> CompanyRecord:
    if item.member is None:
        return extract(item.path)
    zf = _open_zips.get(item.path)
    if zf is None:
        zf = _open_zips[item.path] = zipfile.ZipFile(item.path)
    return extract(item.key, zf.read(item.member))


def _items(source: Path) -> list[_Item]:
    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            return [
                _Item(f"{source}{ZIP_SEP}{i.filename}", f"{i.CRC}:{i.file_size}", str(source), i.filename)
                for i in zf.infolist()
                if not i.is_dir() and i.filename.lower().endswith(".pdf")
                and not Path(i.filename).name.startswith("._")  # macOS metadata files
            ]
    out = []
    for p in sorted(source.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".pdf":
            st = p.stat()
            out.append(_Item(str(p), f"{st.st_mtime}:{st.st_size}", str(p)))
    return out


class Index:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        if self.db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            self.db.executescript("DROP TABLE IF EXISTS employees; DROP TABLE IF EXISTS companies;"
                                  " DROP TABLE IF EXISTS files;")
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.db.executescript(SCHEMA)
        self._vocab: list[str] | None = None

    def close(self) -> None:
        self.db.close()

    # ---------- building ----------

    def build(
        self,
        source: str | Path,
        progress: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None,
        workers: int | None = None,
    ) -> IndexReport:
        """Index every PDF in `source` (a folder or a .zip).

        `progress(done, total, name)` is called after each PDF that is read.
        Setting `cancel` stops early; what was read so far is kept.
        """
        source = Path(source).resolve()
        report = IndexReport()
        items = _items(source)
        prefix = f"{source}{ZIP_SEP}" if source.is_file() else f"{source}{os.sep}"
        known = {r["path"]: r["stamp"] for r in self.db.execute(
            "SELECT * FROM files WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))}
        todo = []
        for it in items:
            if known.get(it.key) == it.stamp:
                report.unchanged += 1
            else:
                todo.append(it)

        with self.db:
            for gone in set(known) - {it.key for it in items}:
                self.db.execute("DELETE FROM files WHERE path = ?", (gone,))
                report.removed += 1

        total = len(todo)
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        done = since_commit = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending: dict = {}
            queue = iter(todo)
            while True:
                # Keep a bounded number of PDFs in flight so memory stays flat.
                while len(pending) < workers * 4 and not (cancel and cancel.is_set()):
                    it = next(queue, None)
                    if it is None:
                        break
                    pending[pool.submit(_work, it)] = it
                if not pending:
                    break
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in finished:
                    it = pending.pop(fut)
                    try:
                        record = fut.result()
                    except Exception as exc:  # a broken PDF must not stop the run
                        report.failed.append((it.key, str(exc) or type(exc).__name__))
                    else:
                        self._save(it, record)
                        if it.key in known:
                            report.updated += 1
                        else:
                            report.added += 1
                        report.warnings.extend((it.key, w) for w in record.warnings)
                    done += 1
                    since_commit += 1
                    if progress:
                        progress(done, total, it.member or it.key)
                if since_commit >= 200:
                    self.db.commit()
                    since_commit = 0
            if cancel and cancel.is_set():
                report.cancelled = True
        self.db.commit()
        self._vocab = None
        return report

    def _save(self, it: _Item, rec: CompanyRecord) -> None:
        self.db.execute("DELETE FROM files WHERE path = ?", (it.key,))
        self.db.execute("INSERT INTO files VALUES (?, ?)", (it.key, it.stamp))
        ids = " ".join(rec.details.get(f, "") for f in COMPANY_ID_FIELDS)
        cur = self.db.execute(
            "INSERT INTO companies (source, name, search, details, payroll, warnings)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (it.key, rec.company, normalize(f"{rec.company} {ids}"),
             json.dumps(rec.details, ensure_ascii=False), json.dumps(rec.payroll, ensure_ascii=False),
             json.dumps(rec.warnings, ensure_ascii=False)),
        )
        self.db.executemany(
            "INSERT INTO employees (company_id, name, search, ref, position, days, period, salary_raw, salary)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(cur.lastrowid, e.name, normalize(f"{e.name} {e.ref}"), e.ref, e.position, e.days,
              e.period, e.salary_raw, e.salary) for e in rec.employees],
        )

    # ---------- searching ----------

    def stats(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {"files": q("SELECT COUNT(*) FROM files"),
                "companies": q("SELECT COUNT(*) FROM companies"),
                "employees": q("SELECT COUNT(*) FROM employees"),
                "warnings": q("SELECT COUNT(*) FROM companies WHERE warnings != '[]'")}

    def search_company(self, query: str, limit: int = MAX_RESULTS) -> list[dict]:
        """Companies whose name (or affiliation/ICE/RC number) matches, with their employees."""
        rows = self._search("SELECT * FROM companies WHERE {} ORDER BY name LIMIT ?", query, limit)
        return [self._company_dict(r) for r in rows]

    def search_person(self, query: str, limit: int = MAX_RESULTS) -> list[dict]:
        """People whose name (or CNSS number) matches, with their company and salary."""
        rows = self._search(
            "SELECT e.*, c.id AS cid, c.name AS company, c.source, c.details FROM employees e"
            " JOIN companies c ON c.id = e.company_id WHERE {} ORDER BY e.name, c.name LIMIT ?",
            query, limit, column="e.search")
        return [
            {"name": r["name"], "ref": r["ref"], "position": r["position"], "days": r["days"],
             "period": r["period"], "salary": r["salary"], "salary_raw": r["salary_raw"],
             "company": r["company"], "company_id": r["cid"], "source": r["source"],
             "city": json.loads(r["details"]).get("Ville", "")}
            for r in rows
        ]

    def company(self, company_id: int) -> dict:
        return self._company_dict(self.db.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone())

    def companies_with_warnings(self) -> list[dict]:
        return [self._company_dict(r) for r in self.db.execute(
            "SELECT * FROM companies WHERE warnings != '[]' ORDER BY name")]

    def _search(self, sql: str, query: str, limit: int, column: str = "search") -> list[sqlite3.Row]:
        """Case/accent-insensitive; every query word must appear, in any order.

        Words that match nothing are swapped for the closest known word, so small
        typos still find results.
        """
        words = normalize(query).split()
        if not words:
            return []

        def run(ws):
            where = " AND ".join(f"{column} LIKE ?" for _ in ws)
            return self.db.execute(sql.format(where), [*(f"%{w}%" for w in ws), limit]).fetchall()

        rows = run(words)
        if rows:
            return rows
        fixed = []
        for w in words:
            close = difflib.get_close_matches(w, self._vocabulary(), n=1, cutoff=0.75)
            fixed.append(close[0] if close else w)
        return run(fixed) if fixed != words else []

    def _vocabulary(self) -> list[str]:
        if self._vocab is None:
            words = set()
            for table in ("companies", "employees"):
                for (s,) in self.db.execute(f"SELECT search FROM {table}"):
                    words.update(w for w in s.split() if not w.isdigit())
            self._vocab = sorted(words)
        return self._vocab

    def _company_dict(self, row: sqlite3.Row) -> dict:
        employees = self.db.execute(
            "SELECT name, ref, position, days, period, salary, salary_raw FROM employees"
            " WHERE company_id = ? ORDER BY period, name", (row["id"],)).fetchall()
        return {
            "id": row["id"],
            "company": row["name"],
            "source": row["source"],
            "details": json.loads(row["details"]),
            "payroll": json.loads(row["payroll"]),
            "warnings": json.loads(row["warnings"]),
            "employees": [dict(e) for e in employees],
        }
