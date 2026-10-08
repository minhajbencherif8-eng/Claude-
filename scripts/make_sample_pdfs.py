"""Generate fake company PDFs in different layouts, for trying the tool and for tests.

    python scripts/make_sample_pdfs.py samples/
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

STYLES = getSampleStyleSheet()
GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)])


def _doc(path, flow):
    SimpleDocTemplate(str(path), pagesize=A4).build(flow)


def english_table(path):
    _doc(path, [
        Paragraph("Company: Acme Robotics Ltd", STYLES["Title"]),
        Paragraph("Address: 12 Harbour Road, Bristol", STYLES["Normal"]),
        Paragraph("Sector: Industrial automation", STYLES["Normal"]),
        Paragraph("Registration number: 0845123", STYLES["Normal"]),
        Spacer(1, 12),
        Table([["Employee ID", "Name", "Position", "Annual Salary"],
               ["E01", "John Smith", "CEO", "$120,000.00"],
               ["E02", "Maria Garcia", "Engineer", "$85,500.00"],
               ["E03", "Chen Wei", "Technician", "$52,000.00"],
               ["", "Total", "", "$257,500.00"]], style=GRID),
    ])


def french_table(path):
    _doc(path, [
        Paragraph("Raison sociale : Boulangerie Dupré SARL", STYLES["Title"]),
        Paragraph("Adresse : 5 rue des Lilas, 69003 Lyon", STYLES["Normal"]),
        Paragraph("SIRET : 812 345 678 00019", STYLES["Normal"]),
        Paragraph("Capital social : 10 000 €", STYLES["Normal"]),
        Spacer(1, 12),
        Table([["Nom", "Prénom", "Fonction", "Salaire brut mensuel"],
               ["Dupré", "Amélie", "Gérante", "3 800,00 €"],
               ["Benali", "Karim", "Boulanger", "2 450,50 €"],
               ["Smith", "John", "Livreur", "1 900,00 €"]], style=GRID),
    ])


def text_lines(path):
    _doc(path, [
        Paragraph("Nordic Timber AS", STYLES["Title"]),
        Paragraph("Location: Bergen, Norway", STYLES["Normal"]),
        Paragraph("Founded: 1998", STYLES["Normal"]),
        Spacer(1, 12),
        Paragraph("Staff", STYLES["Heading2"]),
        Paragraph("Ingrid Hansen - Director - 95 000 EUR", STYLES["Normal"]),
        Paragraph("Lars Olsen - Forester - 48 200 EUR", STYLES["Normal"]),
        Paragraph("Sofie Berg - Accountant - 56 750 EUR", STYLES["Normal"]),
        Paragraph("Total payroll: 199 950 EUR", STYLES["Normal"]),
    ])


def cnss(path, company, employees, city="CASABLANCA"):
    """Same text layout as a CNSS "Attestation des salariés déclarés" (fake data)."""
    p = lambda t: Paragraph(t, STYLES["Normal"])  # noqa: E731
    months = ["décembre 2023", "janvier 2024", "février 2024"]
    total = sum(s for _, _, _, s in employees)
    money = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")  # noqa: E731
    flow = [
        p("Attestation des salariés déclarés"), p("Attestation n°:"), p("202400009999001"),
        p("atteste que:"),
        p(f"Nom/Raison Sociale: {company}"), p("Activité exercée: TEST ACTIVITE"),
        p("Adresse: 1 RUE DES TESTS"), p(f"Ville: {city}"), p("N° d'affiliation: 1234567"),
        p("Identifiant Commun de l'Entreprise: :"), p("Forme Juridique: Société Anonyme"),
        p("A déclaré à la Caisse Nationale de Sécurité (2) du : 1 2 2 0 2 3"),
        p("au : 0 2 2 0 2 4"),
    ]
    for m in months:
        flow.append(p(f"{m} {len(employees)} {money(total)}" if employees else f"{m} NEANT"))
    flow += [PageBreak(), p("Etat des déclarations de salaires"), p("Période 0 8 2 0 2 3"),
             p("N° d'immatriculation Nom et prénom Nombre de jours Salaire déclaré (en DHs)")]
    for ref, name, days, sal in employees:
        flow.append(p(f"{ref} {name} {days} {money(sal)}"))
    if employees:
        flow.append(p(f"TOTAL {money(total)}"))
    _doc(path, flow)


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    english_table(out / "acme.pdf")
    french_table(out / "dupre.pdf")
    text_lines(out / "nordic.pdf")
    cnss(out / "cnss_atlas.pdf", "ATLAS TEXTILE S A", [
        ("100000001", "EL AMRANI YOUSSEF", 26, 9307.64),
        ("100000002", "BENNANI FATIMA ZAHRA", 15, 12450.00),
        ("100000003", "SMITH JOHN", 8, 3100.50),
    ])
    cnss(out / "cnss_empty.pdf", "SOCIETE VIDE SARL", [], city="RABAT")
    print(f"Wrote 5 sample PDFs to {out}/")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")
