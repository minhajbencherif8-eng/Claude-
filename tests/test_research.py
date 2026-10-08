import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_sample_pdfs  # noqa: E402
from pdf_research.cli import main as cli  # noqa: E402
from pdf_research.extractor import _employee_from_line, extract, parse_cnss  # noqa: E402
from pdf_research.index import Index  # noqa: E402
from pdf_research.textutil import parse_amount, strip_arabic  # noqa: E402


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("pdfs")
    make_sample_pdfs.main(out)
    return out


@pytest.fixture(scope="module")
def sample_zip(samples, tmp_path_factory):
    archive = tmp_path_factory.mktemp("zip") / "pdfs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for pdf in sorted(samples.glob("*.pdf")):
            zf.write(pdf, f"Entreprises/{pdf.name}")
        zf.writestr("Entreprises/readme.txt", "not a pdf")
        zf.writestr("__MACOSX/Entreprises/._acme.pdf", b"junk")
    return archive


@pytest.fixture
def index(sample_zip, tmp_path):
    idx = Index(tmp_path / "db.sqlite")
    rep = idx.build(sample_zip, workers=2)
    assert rep.added == 5 and not rep.failed
    yield idx
    idx.close()


@pytest.mark.parametrize("raw, value", [
    ("3 800,00 €", 3800.0), ("$120,000.00", 120000.0), ("3.500,50", 3500.5),
    ("95 000 EUR", 95000.0), ("45000", 45000.0), ("1,5", 1.5), ("9 307,64", 9307.64),
    ("", None), ("abc", None),
])
def test_parse_amount(raw, value):
    assert parse_amount(raw) == value


# ---------- CNSS attestations ----------

def test_cnss_pdf(samples):
    rec = extract(samples / "cnss_atlas.pdf")
    assert rec.company == "ATLAS TEXTILE S A"
    assert rec.details["Ville"] == "CASABLANCA"
    assert rec.details["N° d'affiliation"] == "1234567"
    assert rec.details["Période déclarée"] == "12/2023 - 02/2024"
    assert "Identifiant Commun de l'Entreprise" not in rec.details  # empty on the form
    assert [(e.ref, e.name, e.days, e.salary, e.period) for e in rec.employees] == [
        ("100000001", "EL AMRANI YOUSSEF", 26, 9307.64, "08/2023"),
        ("100000002", "BENNANI FATIMA ZAHRA", 15, 12450.0, "08/2023"),
        ("100000003", "SMITH JOHN", 8, 3100.5, "08/2023"),
    ]
    assert rec.payroll[0] == {"month": "décembre 2023", "employees": 3, "payroll": "24 858,14"}
    assert rec.warnings == []


def test_cnss_without_employees(samples):
    rec = extract(samples / "cnss_empty.pdf")
    assert (rec.company, rec.employees, rec.warnings) == ("SOCIETE VIDE SARL", [], [])
    assert rec.payroll[0] == {"month": "décembre 2023", "employees": 0, "payroll": ""}


# Text as pdfplumber returns it for a real attestation (fake names), Arabic labels included.
REAL_LAYOUT = """\
Attestation des salariés déclarés ﻦﻴﻃﺮﺨﻨﻤﻟﺍ ﺔﻳﺮﻳﺪﻣ
Attestation n°: :ﻢﻗﺭ ﺓﺩﺎﻬﺷ
202400002525312
Nom/Raison Sociale: EXEMPLE S A :ﻱﺭﺎﺠﺘﻟﺍ ﻢﺳﻹﺍ / ﻢﺳﻹﺍ
Ville: SIDI KACEM :ﺔﻨﻳﺪﻤﻟﺍ
Identifiant Fiscal: :ﻲﺒﻳﺮﻀﻟﺍ ﻒﻳﺮﻌﺘﻟﺍ
A déclaré à la Caisse Nationale de Sécurité (2) du : 1 2 2 0 2 3 : ﻦﻣ (2) ﻲﻋﺎﻤﺘﺟﻹﺍ
au : 1 1 2 0 2 4 :ﻰﻟﺇ
décembre 2023 ﺮﺒﻤﺴﻳﺩ 7 34 863,89
novembre 2024 ﺮﺒﻤﻓﻮﻧ NEANT
Période 0 8 2 0 2 3 ﺓﺮﺘﻔﻟﺍ
102233930 ALAOUI AHMED 26 9 307,64
145751585 SOBAI EL IDRISSI KARIM 15 1 207 200,13
TOTAL 1 216 507,77
"""


def test_cnss_real_text_layout():
    rec = parse_cnss(REAL_LAYOUT, "x.pdf")
    assert rec.company == "EXEMPLE S A"
    assert rec.details == {"Ville": "SIDI KACEM", "Attestation n°": "202400002525312",
                           "Période déclarée": "12/2023 - 11/2024",
                           "Période des salaires (annexe)": "08/2023"}
    assert [(e.name, e.salary) for e in rec.employees] == [
        ("ALAOUI AHMED", 9307.64), ("SOBAI EL IDRISSI KARIM", 1207200.13)]
    assert [p["employees"] for p in rec.payroll] == [7, 0]
    assert rec.warnings == []


def test_cnss_total_mismatch_is_flagged():
    text = REAL_LAYOUT.replace("102233930 ALAOUI AHMED 26 9 307,64", "garbled line")
    assert "not read" in parse_cnss(text, "x.pdf").warnings[0]


def test_strip_arabic():
    assert strip_arabic("Ville: CASABLANCA :ﺔﻨﻳﺪﻤﻟﺍ").strip() == "Ville: CASABLANCA :"


# ---------- other layouts ----------

def test_english_table(samples):
    rec = extract(samples / "acme.pdf")
    assert rec.company == "Acme Robotics Ltd"
    assert {e.name: e.salary for e in rec.employees} == {
        "John Smith": 120000.0, "Maria Garcia": 85500.0, "Chen Wei": 52000.0}


def test_french_table_with_split_names(samples):
    rec = extract(samples / "dupre.pdf")
    karim = next(e for e in rec.employees if e.name == "Karim Benali")
    assert (karim.position, karim.salary) == ("Boulanger", 2450.5)
    assert rec.details["Capital social"] == "10 000 €"


def test_text_lines(samples):
    rec = extract(samples / "nordic.pdf")
    assert [(e.name, e.salary) for e in rec.employees] == [
        ("Ingrid Hansen", 95000.0), ("Lars Olsen", 48200.0), ("Sofie Berg", 56750.0)]


@pytest.mark.parametrize("line", ["Total payroll: 199 950 EUR", "Founded in 1998 by the family",
                                  "Capital: 10 000 €", "Hansen 2021 report"])
def test_non_person_lines_ignored(line):
    assert _employee_from_line(line) is None


# ---------- index and search ----------

def test_zip_is_read_in_place(index, sample_zip):
    assert not (sample_zip.parent / "pdfs").exists()  # nothing extracted to disk
    assert index.stats() == {"files": 5, "companies": 5, "employees": 12, "warnings": 0}


def test_search_company(index):
    [c] = index.search_company("atlas textile")
    assert c["details"]["Ville"] == "CASABLANCA"
    assert len(c["employees"]) == 3


def test_search_company_by_affiliation_number(index):
    assert [c["company"] for c in index.search_company("1234567")] == ["ATLAS TEXTILE S A"]


def test_search_person_any_order_and_case(index):
    hits = index.search_person("john SMITH")
    assert sorted(h["company"] for h in hits) == ["ATLAS TEXTILE S A", "Acme Robotics Ltd", "Boulangerie Dupré SARL"]
    atlas = next(h for h in hits if h["company"] == "ATLAS TEXTILE S A")
    assert (atlas["ref"], atlas["salary"], atlas["city"], atlas["days"]) == ("100000003", 3100.5, "CASABLANCA", 8)


def test_search_person_by_cnss_number(index):
    assert [h["name"] for h in index.search_person("100000002")] == ["BENNANI FATIMA ZAHRA"]


def test_search_accents_and_typos(index):
    assert index.search_company("dupre")[0]["company"] == "Boulangerie Dupré SARL"
    assert [h["name"] for h in index.search_person("Ingird")] == ["Ingrid Hansen"]
    assert [h["name"] for h in index.search_person("benani fatima")] == ["BENNANI FATIMA ZAHRA"]
    assert index.search_person("nobody here") == []


def test_reimport_skips_unchanged_and_drops_removed(samples, tmp_path):
    archive = tmp_path / "pdfs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in ("acme.pdf", "cnss_atlas.pdf"):
            zf.write(samples / name, name)
    idx = Index(tmp_path / "db.sqlite")
    assert idx.build(archive, workers=1).added == 2
    rep = idx.build(archive, workers=1)
    assert (rep.added, rep.unchanged) == (0, 2)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(samples / "acme.pdf", "acme.pdf")
    rep = idx.build(archive, workers=1)
    assert (rep.removed, rep.unchanged) == (1, 1)
    assert idx.search_person("YOUSSEF") == []
    idx.close()


def test_cancel_keeps_what_was_read_and_resumes(sample_zip, tmp_path):
    import threading
    stop = threading.Event()
    idx = Index(tmp_path / "db.sqlite")
    rep = idx.build(sample_zip, workers=1, progress=lambda *a: stop.set(), cancel=stop)
    assert rep.cancelled and 1 <= rep.added < 5
    rep2 = idx.build(sample_zip, workers=1)
    assert (rep2.unchanged, rep2.added) == (rep.added, 5 - rep.added)
    idx.close()


def test_broken_pdf_is_reported_not_fatal(samples, tmp_path):
    archive = tmp_path / "pdfs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(samples / "acme.pdf", "acme.pdf")
        zf.writestr("broken.pdf", b"%PDF-1.4 this is not really a pdf")
    idx = Index(tmp_path / "db.sqlite")
    rep = idx.build(archive, workers=1)
    assert rep.added == 1 and [k.split("::")[1] for k, _ in rep.failed] == ["broken.pdf"]
    idx.close()


def test_cli(sample_zip, tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    assert cli(["--db", db, "index", str(sample_zip)]) == 0
    assert cli(["--db", db, "person", "el amrani"]) == 0
    assert "ATLAS TEXTILE S A" in capsys.readouterr().out
    assert cli(["--db", db, "company", "nothing like this"]) == 1


# ---------- rankings ----------

def _ranking_db(tmp_path, samples):
    """Index the samples, with ATLAS present twice (two PDFs of the same company)."""
    archive = tmp_path / "pdfs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for pdf in sorted(samples.glob("*.pdf")):
            zf.write(pdf, pdf.name)
        zf.write(samples / "cnss_atlas.pdf", "copy/cnss_atlas_again.pdf")
    idx = Index(tmp_path / "db.sqlite")
    idx.build(archive, workers=1)
    return idx


def test_top_companies_by_average_salary(samples, tmp_path):
    idx = _ranking_db(tmp_path, samples)
    rows = idx.top_companies(min_employees=1)
    assert [r["company"] for r in rows] == [
        "Acme Robotics Ltd", "Nordic Timber AS", "ATLAS TEXTILE S A", "Boulangerie Dupré SARL"]
    atlas = rows[2]
    # The duplicate PDF is counted once: 3 people, not 6.
    assert (atlas["people"], atlas["pdfs"]) == (3, 2)
    assert atlas["avg_salary"] == pytest.approx((9307.64 + 12450.0 + 3100.5) / 3)
    assert atlas["avg_day"] == pytest.approx((9307.64 / 26 + 12450.0 / 15 + 3100.5 / 8) / 3)
    # Companies with no employees are never ranked.
    assert "SOCIETE VIDE SARL" not in {r["company"] for r in rows}
    idx.close()


def test_top_companies_filters(samples, tmp_path):
    idx = _ranking_db(tmp_path, samples)
    assert [r["company"] for r in idx.top_companies(city="casa")] == ["ATLAS TEXTILE S A"]
    assert idx.top_companies(min_employees=4) == []
    # Per-day ranking only covers companies that report days worked.
    assert [r["company"] for r in idx.top_companies(by="per_day")] == ["ATLAS TEXTILE S A"]
    assert len(idx.top_companies(limit=2)) == 2
    idx.close()


def test_top_earners(samples, tmp_path):
    idx = _ranking_db(tmp_path, samples)
    rows = idx.top_earners(limit=3)
    assert [(r["name"], r["salary"]) for r in rows] == [
        ("John Smith", 120000.0), ("Ingrid Hansen", 95000.0), ("Maria Garcia", 85500.0)]
    cnss = idx.top_earners(city="casablanca")
    # Each person once, even though ATLAS is in two PDFs.
    assert [r["name"] for r in cnss] == ["BENNANI FATIMA ZAHRA", "EL AMRANI YOUSSEF", "SMITH JOHN"]
    per_day = idx.top_earners(by="per_day")
    assert per_day[0]["name"] == "BENNANI FATIMA ZAHRA"
    assert per_day[0]["per_day"] == pytest.approx(12450.0 / 15)
    idx.close()


def test_cli_rankings(sample_zip, tmp_path, capsys):
    db = str(tmp_path / "db.sqlite")
    cli(["--db", db, "index", str(sample_zip)])
    capsys.readouterr()
    assert cli(["--db", db, "top-companies", "--min-employees", "1", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "Acme Robotics Ltd" in out and "Boulangerie" not in out
    assert cli(["--db", db, "top-earners", "--by", "per_day"]) == 0
    assert "BENNANI FATIMA ZAHRA" in capsys.readouterr().out


def test_rankings_min_days(samples, tmp_path):
    idx = _ranking_db(tmp_path, samples)
    # Only EL AMRANI worked 26 days; PDFs without days worked drop out.
    assert [r["name"] for r in idx.top_earners(min_days=26)] == ["EL AMRANI YOUSSEF"]
    [atlas] = idx.top_companies(min_days=15)
    assert (atlas["company"], atlas["people"]) == ("ATLAS TEXTILE S A", 2)
    assert atlas["avg_salary"] == pytest.approx((9307.64 + 12450.0) / 2)
    idx.close()
