import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_sample_pdfs  # noqa: E402
from pdf_research.extractor import _employee_from_line, extract  # noqa: E402
from pdf_research.index import Index  # noqa: E402
from pdf_research.textutil import parse_amount  # noqa: E402
from pdf_research.web import make_handler  # noqa: E402


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("pdfs")
    make_sample_pdfs.main(out)
    return out


@pytest.fixture
def index(samples, tmp_path):
    idx = Index(tmp_path / "db.sqlite")
    idx.build(samples)
    yield idx
    idx.close()


@pytest.mark.parametrize("raw, value", [
    ("3 800,00 €", 3800.0), ("$120,000.00", 120000.0), ("3.500,50", 3500.5),
    ("95 000 EUR", 95000.0), ("45000", 45000.0), ("1,5", 1.5), ("", None), ("abc", None),
])
def test_parse_amount(raw, value):
    assert parse_amount(raw) == value


def test_english_table(samples):
    rec = extract(samples / "acme.pdf")
    assert rec.company == "Acme Robotics Ltd"
    assert {e.name: e.salary for e in rec.employees} == {
        "John Smith": 120000.0, "Maria Garcia": 85500.0, "Chen Wei": 52000.0}
    assert rec.details["Sector"] == "Industrial automation"


def test_french_table_with_split_names(samples):
    rec = extract(samples / "dupre.pdf")
    assert rec.company == "Boulangerie Dupré SARL"
    karim = next(e for e in rec.employees if e.name == "Karim Benali")
    assert (karim.position, karim.salary) == ("Boulanger", 2450.5)
    assert rec.details["Capital social"] == "10 000 €"


def test_text_lines(samples):
    rec = extract(samples / "nordic.pdf")
    assert rec.company == "Nordic Timber AS"
    assert [(e.name, e.position, e.salary) for e in rec.employees] == [
        ("Ingrid Hansen", "Director", 95000.0),
        ("Lars Olsen", "Forester", 48200.0),
        ("Sofie Berg", "Accountant", 56750.0),
    ]


@pytest.mark.parametrize("line", ["Total payroll: 199 950 EUR", "Founded in 1998 by the family",
                                  "Capital: 10 000 €", "Hansen 2021 report"])
def test_non_person_lines_ignored(line):
    assert _employee_from_line(line) is None


def test_search_company_ignores_case_and_accents(index):
    [c] = index.search_company("boulangerie dupre")
    assert len(c["employees"]) == 3


def test_search_person_across_companies(index):
    hits = index.search_person("SMITH john")
    assert sorted(h["company"] for h in hits) == ["Acme Robotics Ltd", "Boulangerie Dupré SARL"]


def test_search_tolerates_typos(index):
    assert [h["name"] for h in index.search_person("Ingird")] == ["Ingrid Hansen"]
    assert index.search_person("nobody here") == []


def test_reindex_skips_unchanged_and_drops_deleted(samples, tmp_path):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    for name in ("acme.pdf", "nordic.pdf"):
        (folder / name).write_bytes((samples / name).read_bytes())
    idx = Index(tmp_path / "db.sqlite")
    assert idx.build(folder).added == 2
    rep = idx.build(folder)
    assert (rep.added, rep.unchanged) == (0, 2)
    (folder / "nordic.pdf").unlink()
    assert idx.build(folder).removed == 1
    assert idx.search_person("Lars") == []
    assert [c["company"] for c in idx.list_companies()] == ["Acme Robotics Ltd"]
    idx.close()


def test_web_api(index, tmp_path, samples):
    db = tmp_path / "db.sqlite"
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(db)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(f"{base}/api/search?type=person&q=karim") as r:
            [hit] = json.load(r)
        assert hit["company"] == "Boulangerie Dupré SARL"
        with urllib.request.urlopen(base + "/") as r:
            assert b"Company &amp; People Search" in r.read()
    finally:
        server.shutdown()


def test_index_from_zip(samples, tmp_path):
    import zipfile
    from pdf_research.cli import main
    archive = tmp_path / "pdfs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for pdf in samples.glob("*.pdf"):
            zf.write(pdf, f"pdfs/{pdf.name}")
    db = str(tmp_path / "db.sqlite")
    assert main(["--db", db, "index", str(archive)]) == 0
    assert main(["--db", db, "person", "Sofie"]) == 0


def test_zip_with_unsafe_path_is_refused(tmp_path):
    import zipfile
    from pdf_research.cli import unzip
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.pdf", b"x")
    with pytest.raises(ValueError):
        unzip(archive)
