# Company & Employee Search

A Windows desktop app for a ZIP of company PDFs (CNSS *Attestation des salariés déclarés* and similar):

- **Search by company** to see its details (address, city, affiliation n°, ICE, RC, legal form, monthly declarations) and every declared employee with CNSS n°, days worked and salary.
- **Search by employee** to see every company where that person appears, with their salary.

**Everything stays on your computer.** The app reads the PDFs straight from your ZIP (it doesn't even extract them to disk), saves what it finds in a local database, and never connects to the internet.

## Install (Windows)

1. Open this repository's **Releases** page on GitHub and download the latest `CompanyEmployeeSearch.exe`.
2. Double-click it. The `.exe` isn't code-signed, so Windows may show "Windows protected your PC". Click **More info → Run anyway**.

No Python needed. (If you'd rather run it with Python: install Python 3.10+ from python.org, download this repository, and double-click `run_app.bat`.)

## Use

1. Click **Select ZIP File…** and choose your ZIP. This is needed **only once**.
   - The app reads all the PDFs using every CPU core and shows its progress and the time left. Expect roughly 10–20 PDFs per second, depending on your PC.
   - You can click **Stop** or close the app at any time. Select the same ZIP again later and it continues where it stopped.
   - If you later get an updated ZIP, select it again: only new or changed PDFs are read, and PDFs no longer in the ZIP are removed.
2. Choose **Company** or **Employee**, type a name, and press Enter.
   - Upper/lower case and accents don't matter, words can be in any order (`samir chafai`), and small typos are tolerated (`chafay`).
   - You can also type a number: an affiliation n°, ICE or RC for companies, or a CNSS n° for employees.
3. Click a result to see the company's details and its full employee list (the person you searched for is highlighted).
   - **Open PDF** shows the original document.
   - **Export to Excel (CSV)** saves the current results.
   - **Import report** lists PDFs that could not be read or need a check. For example, a PDF where the employees don't add up to the PDF's TOTAL line, or a scanned PDF with no text.

4. **Rankings** (third row):
   - **Top companies by avg salary**: companies ranked by the average declared salary of their employees. Also shows the average per day worked, the highest salary, and how many PDFs the company appears in.
   - **Top earners**: the highest-paid people, each listed once with their highest declaration.
   - **Rank by**: *Salary* (as declared) or *Salary per day worked*. The per-day option evens out people who only worked part of the month.
   - **Min. staff** leaves out companies with very few employees (default 3), since one or two people would make an average misleading.
   - **Min. days** only counts declarations with at least that many days worked. Set it to 26 to compare full-month salaries only.
   - **City** narrows the ranking to one city, and **Top** sets how many rows to show.
   - A company found in several PDFs is counted once (grouped by affiliation n°), and so is a person (grouped by CNSS n°).
   - Click a row to see the company's details, and use **Export to Excel (CSV)** to save the ranking.

The database is stored in `%LOCALAPPDATA%\PDFResearch\index.sqlite`. Delete that folder to start over.

## Command line (optional)

```bash
pip install -r requirements.txt
python -m pdf_research index my_pdfs.zip
python -m pdf_research company "SONACOS"
python -m pdf_research person "CHAFAI SAMIR"
python -m pdf_research top-companies --min-employees 5 --min-days 26
python -m pdf_research top-earners --city casablanca --by per_day
python -m pdf_research stats
```

It uses the same database as the app.

## How the PDFs are read

- **CNSS attestations**: company fields come from the labelled lines (`Nom/Raison Sociale`, `Adresse`, `Ville`, `N° d'affiliation`, …), the monthly table from page 1, and the employees from the annex (`N° d'immatriculation, Nom et prénom, Nombre de jours, Salaire déclaré`). Employee totals are checked against the PDF's `TOTAL` line.
- **Other layouts**: a `Company:`/`Entreprise :` line, and employee tables with name and salary columns (English or French), or lines like `Name - Position - 3 000 €`.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
python scripts/make_sample_pdfs.py samples/   # fake PDFs (incl. CNSS layout) to try the app
```

Each push builds the Windows `.exe` with GitHub Actions (`.github/workflows/windows-app.yml`). The build runs the tests on Windows, checks that the `.exe` can import a ZIP, and publishes the `.exe` on the Releases page.
