"""Extract a company, its details and its employees (with salaries) from a PDF.

PDF layouts vary, so extraction is heuristic and works in two passes:

1. Tables: any table whose header has a name column and a salary column
   (English or French labels) yields one employee per row.
2. Text lines: if no table produced employees, each line that looks like
   "<person name> ... <amount>" becomes an employee.

The company name comes from a labelled line ("Company: ...", "Entreprise : ...",
"Raison sociale : ..."), else the first line of the document, else the file name.
Every other "Label: value" line is kept as a company detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .textutil import MONEY_RE, clean, normalize, parse_amount

COMPANY_LABELS = (
    "company name", "company", "enterprise", "entreprise", "nom de l entreprise",
    "raison sociale", "societe", "denomination", "employer", "employeur",
    "organisation", "organization", "firm", "business name",
)
NAME_HEADERS = (
    "full name", "employee name", "employee", "name", "nom complet", "nom et prenom",
    "nom prenom", "nom", "salarie", "employe", "personne", "staff", "worker",
)
FIRST_NAME_HEADERS = ("first name", "firstname", "given name", "prenom", "prenoms")
LAST_NAME_HEADERS = ("last name", "lastname", "surname", "family name", "nom de famille")
SALARY_HEADERS = (
    "salary", "salaire", "wage", "pay", "remuneration", "gross", "brut", "net",
    "montant", "amount", "annual", "monthly", "mensuel", "annuel", "compensation",
)
POSITION_HEADERS = (
    "position", "poste", "job title", "title", "job", "role", "fonction", "function",
    "occupation", "department", "departement", "service",
)
# Labels of "Label: amount" lines that are company figures, not people.
NON_PERSON_LABELS = {
    "total", "sous total", "subtotal", "capital", "capital social", "revenue",
    "chiffre d affaires", "turnover", "ca", "budget", "masse salariale", "payroll",
    "total salaries", "total des salaires", "moyenne", "average", "siret", "siren",
    "phone", "telephone", "tel", "fax", "nif", "rc", "tva", "vat", "iban", "date",
    "year", "annee", "effectif", "employees", "headcount", "zip", "code postal",
}

_LABEL_LINE = re.compile(r"^\s*(?P<label>[^:]{2,40}?)\s*:\s*(?P<value>.+?)\s*$")
_NAME_WORD = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-.]*"
_PERSON_NAME = re.compile(rf"^(?:{_NAME_WORD}\s+){{1,3}}{_NAME_WORD}$")


@dataclass
class Employee:
    name: str
    salary_raw: str = ""
    salary: float | None = None
    position: str = ""


@dataclass
class CompanyRecord:
    source: str
    company: str
    details: dict[str, str] = field(default_factory=dict)
    employees: list[Employee] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _find_col(header: list[str], candidates: tuple[str, ...], exclude: set[int]) -> int | None:
    cells = [(i, normalize(c)) for i, c in enumerate(header) if i not in exclude and c]
    # Exact header first ("Name" beats "Employee ID"), then a header containing the word.
    for i, n in cells:
        if n in candidates:
            return i
    for i, n in cells:
        if any(f" {c} " in f" {n} " for c in candidates):
            return i
    return None


def _employees_from_table(table: list[list[str | None]]) -> list[Employee]:
    rows = [[clean(c) for c in row] for row in table if row and any(row)]
    # The header is the first row that has both a name-ish and salary-ish column.
    for h, header in enumerate(rows[:3]):
        used: set[int] = set()
        first = _find_col(header, FIRST_NAME_HEADERS, used)
        if first is not None:
            used.add(first)
        last = _find_col(header, LAST_NAME_HEADERS, used)
        if last is not None:
            used.add(last)
        name = None
        if first is None or last is None:
            name = _find_col(header, NAME_HEADERS, used)
            if name is not None:
                used.add(name)
        salary = _find_col(header, SALARY_HEADERS, used)
        if salary is not None:
            used.add(salary)
        position = _find_col(header, POSITION_HEADERS, used)
        has_name = name is not None or first is not None or last is not None
        if has_name and salary is not None:
            break
    else:
        return []

    out = []
    for row in rows[h + 1:]:
        def cell(i: int | None) -> str:
            return row[i] if i is not None and i < len(row) else ""

        if name is not None:
            full = cell(name)
            # "Nom" + "Prénom" layout: Nom is the family name.
            if first is not None:
                full = f"{cell(first)} {full}"
            elif last is not None:
                full = f"{full} {cell(last)}"
        else:
            full = f"{cell(first)} {cell(last)}"
        full = clean(full)
        raw_salary = cell(salary)
        if not full or normalize(full) in NON_PERSON_LABELS or normalize(full).startswith("total"):
            continue
        out.append(Employee(full, raw_salary, parse_amount(raw_salary), cell(position)))
    return out


def _employee_from_line(line: str) -> Employee | None:
    matches = list(MONEY_RE.finditer(line))
    if not matches:
        return None
    m = matches[-1]
    amount = parse_amount(m.group("amount"))
    if amount is None or line[m.end():].strip(" .;"):
        return None  # amount must end the line
    before = line[: m.start()].rstrip(" :-–—|,\t")
    label = _LABEL_LINE.match(before + ":x")
    if label and normalize(label.group("label")) in NON_PERSON_LABELS:
        return None
    # Split "Name  Position" on separators or runs of spaces.
    parts = [p for p in re.split(r"\s*[|;\t–—]\s*|\s+-\s+|\s*,\s*|\s{2,}|\s*:\s*", before) if p]
    if not parts:
        return None
    name = clean(parts[0])
    if not _PERSON_NAME.match(name) or normalize(name) in NON_PERSON_LABELS:
        return None
    position = clean(" ".join(parts[1:]))
    return Employee(name, clean(m.group(0)), amount, position)


def _company_from_details(details: dict[str, str]) -> str | None:
    for label in COMPANY_LABELS:
        for key, value in details.items():
            if normalize(key) == label:
                return value
    return None


def extract(path: str | Path) -> CompanyRecord:
    path = Path(path)
    lines: list[str] = []
    employees: list[Employee] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            lines.extend(clean(l) for l in (page.extract_text() or "").splitlines())
            for table in page.extract_tables():
                employees.extend(_employees_from_table(table))
    lines = [l for l in lines if l]

    record = CompanyRecord(source=str(path), company="")
    if not lines:
        record.company = path.stem
        record.warnings.append("no text found (scanned PDF? it needs OCR first)")
        return record

    for line in lines:
        m = _LABEL_LINE.match(line)
        if not m:
            continue
        key, value = clean(m.group("label")), clean(m.group("value"))
        # "Jane Doe: 3 000 €" is an employee line, not a company detail.
        if MONEY_RE.fullmatch(value) and _PERSON_NAME.match(key):
            continue
        record.details.setdefault(key, value)

    record.company = _company_from_details(record.details) or lines[0] or path.stem

    if not employees:
        employees = [e for e in map(_employee_from_line, lines) if e]
    # Same person can be picked up twice when a table spans pages.
    seen = set()
    for e in employees:
        key = (normalize(e.name), e.salary_raw)
        if key not in seen:
            seen.add(key)
            record.employees.append(e)
    if not record.employees:
        record.warnings.append("no employees with salaries found")
    return record
