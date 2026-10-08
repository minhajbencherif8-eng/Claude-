"""Extract a company, its details and its employees (with salaries) from a PDF.

CNSS "Attestation des salariés déclarés" forms (Morocco) have a dedicated
parser, see `parse_cnss`. Any other layout goes through heuristic extraction
in two passes:

1. Tables: any table whose header has a name column and a salary column
   (English or French labels) yields one employee per row.
2. Text lines: if no table produced employees, each line that looks like
   "<person name> ... <amount>" becomes an employee.

The company name comes from a labelled line ("Company: ...", "Entreprise : ...",
"Raison sociale : ..."), else the first line of the document, else the file name.
Every other "Label: value" line is kept as a company detail.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .textutil import MONEY_RE, clean, normalize, parse_amount, strip_arabic

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
    ref: str = ""  # e.g. CNSS "N° d'immatriculation"
    days: int | None = None
    period: str = ""  # e.g. "08/2023"


@dataclass
class CompanyRecord:
    source: str
    company: str
    details: dict[str, str] = field(default_factory=dict)
    employees: list[Employee] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Monthly totals: {"month": "décembre 2023", "employees": 7, "payroll": "34 863,89"}
    payroll: list[dict] = field(default_factory=list)


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


# ---------- CNSS "Attestation des salariés déclarés" ----------

_CNSS_MARKERS = ("Raison Sociale", "salariés déclarés")
_CNSS_FIELD = re.compile(r"^(?P<label>[A-Za-zÀ-ÿ°'’/ ]{3,45}?)\s*:\s*(?P<value>.*?)[\s:]*$")
_CNSS_FIELDS = (
    "Activité exercée", "Adresse", "Ville", "N° d'affiliation", "Date d'affiliation",
    "Identifiant Commun de l'Entreprise", "N° Registre de commerce", "N° Taxe Professionnelle",
    "Identifiant Fiscal", "Forme Juridique",
)
_DH = r"\d{1,3}(?: \d{3})*,\d{2}"
_CNSS_EMPLOYEE = re.compile(rf"^(?P<ref>\d{{6,12}}) (?P<name>\D+?) (?P<days>\d{{1,3}}) (?P<salary>{_DH})$")
_CNSS_TOTAL = re.compile(rf"^TOTAL (?P<amount>{_DH})$")
_CNSS_MONTH = re.compile(
    rf"^(?P<month>(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)"
    rf" \d{{4}}) (?:NEANT|(?P<count>\d+) (?P<payroll>{_DH}))$",
    re.IGNORECASE,
)
_SPACED_MONTH = r"((?:\d\s?){6})"  # "1 2 2 0 2 3" -> 12/2023


def _month(spaced: str) -> str:
    d = re.sub(r"\D", "", spaced)
    return f"{d[:2]}/{d[2:]}"


def is_cnss(text: str) -> bool:
    return all(m in text for m in _CNSS_MARKERS)


def parse_cnss(text: str, source: str) -> CompanyRecord:
    lines = [clean(strip_arabic(l)) for l in text.splitlines()]
    lines = [l for l in lines if l]
    flat = " ".join(lines)
    rec = CompanyRecord(source=source, company="")

    for line in lines:
        m = _CNSS_FIELD.match(line)
        if not m:
            continue
        label, value = m.group("label").strip(), m.group("value").strip(" :/")
        if label == "Nom/Raison Sociale":
            rec.company = rec.company or value
        elif label in _CNSS_FIELDS and value and label not in rec.details:
            rec.details[label] = value

    if m := re.search(r"Attestation n°\s*:?\s*:?\s*(\d{8,})", flat):
        rec.details["Attestation n°"] = m.group(1)
    start = re.search(r"\bdu\s*:\s*" + _SPACED_MONTH, flat)
    end = re.search(r"\bau\s*:\s*" + _SPACED_MONTH, flat)
    if start and end:
        rec.details["Période déclarée"] = f"{_month(start.group(1))} - {_month(end.group(1))}"

    period, total = "", None
    for line in lines:
        if m := re.match(r"^Période " + _SPACED_MONTH + "$", line):
            period = _month(m.group(1))
        elif m := _CNSS_EMPLOYEE.match(line):
            rec.employees.append(Employee(
                name=m.group("name").strip(), salary_raw=m.group("salary"),
                salary=parse_amount(m.group("salary")), ref=m.group("ref"),
                days=int(m.group("days")), period=period,
            ))
        elif m := _CNSS_TOTAL.match(line):
            total = (total or 0) + parse_amount(m.group("amount"))
        elif m := _CNSS_MONTH.match(line):
            rec.payroll.append({
                "month": m.group("month"),
                "employees": int(m.group("count")) if m.group("count") else 0,
                "payroll": m.group("payroll") or "",
            })
    if period:
        rec.details["Période des salaires (annexe)"] = period

    if not rec.company:
        rec.company = Path(source).stem
        rec.warnings.append("company name not found")
    found = round(sum(e.salary or 0 for e in rec.employees), 2)
    if total is not None and abs(found - total) > 0.01:
        rec.warnings.append(f"employees add up to {found:,.2f} but the PDF's TOTAL is {total:,.2f}"
                            " (some employee lines were not read)")
    if total is None and rec.employees:
        rec.warnings.append("no TOTAL line found to check the employee list")
    return rec


# ---------- entry point ----------

def extract(path: str | Path, data: bytes | None = None) -> CompanyRecord:
    """Read a PDF from `path`, or from `data` (bytes) labelled with `path`."""
    path = Path(path)
    source = io.BytesIO(data) if data is not None else path
    with pdfplumber.open(source) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
        full = "\n".join(texts)
        if is_cnss(full):
            return parse_cnss(full, str(path))
        return _extract_generic(pdf, texts, path)


def _extract_generic(pdf, texts: list[str], path: Path) -> CompanyRecord:
    lines: list[str] = []
    employees: list[Employee] = []
    for page, text in zip(pdf.pages, texts):
        lines.extend(clean(l) for l in text.splitlines())
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
