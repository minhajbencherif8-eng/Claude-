# Company & People Search for PDFs

Search a folder (or `.zip`) of company PDFs:

- **by company name** to get the company's details and every person who works there, with their salary
- **by person name** to see where that person works and their salary (across all PDFs)

Searches ignore upper/lower case and accents (`dupre` finds `Dupré`), accept words in any order (`smith john`), and tolerate small typos (`Ingird` finds `Ingrid`).

## Setup

```bash
pip install -r requirements.txt     # Python 3.10+
```

## Use

```bash
# 1. Read the PDFs once (a folder or a .zip). Re-run after adding or changing PDFs;
#    only new or changed files are read again.
python -m pdf_research index my_pdfs.zip

# 2. Search
python -m pdf_research company "Acme"
python -m pdf_research person "John Smith"
python -m pdf_research list                 # every company found, and any PDF that needs a look

# Or use the search page in your browser at http://127.0.0.1:8000
python -m pdf_research serve
```

Add `--json` to `company` / `person` for machine-readable output.

## What the PDFs can look like

The tool reads text-based PDFs (not scans) and recognises, in English or French:

- **Company name** from a labelled line such as `Company: …`, `Entreprise : …`, `Raison sociale : …`, `Société : …`; otherwise the first line of the PDF.
- **Company details**: every other `Label: value` line (address, SIRET, sector, …).
- **Employees** from a table whose header has a name column (`Name`, `Nom`, `Employee`, or separate `Nom` / `Prénom` / `First name` / `Last name`) and a salary column (`Salary`, `Salaire`, `Brut`, `Net`, `Wage`, …); an optional `Position` / `Poste` / `Fonction` column is picked up too.
- **Employees without a table**: lines like `Ingrid Hansen - Director - 95 000 EUR`.

`index` warns about any PDF where it found no employees. That usually means a scanned PDF (it needs OCR first) or a layout the rules above don't cover yet.

## Privacy

Salary data is personal data. The index (`research_index.sqlite`) stays on your computer, the search page only listens on `127.0.0.1`, and `.gitignore` keeps PDFs, zips and the index out of git.

## Development

```bash
pip install -r requirements-dev.txt
python scripts/make_sample_pdfs.py samples/   # fake PDFs in 3 layouts to try the tool
python -m pytest
```
