"""Generate fake company PDFs in different layouts, for trying the tool and for tests.

    python scripts/make_sample_pdfs.py samples/
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    english_table(out / "acme.pdf")
    french_table(out / "dupre.pdf")
    text_lines(out / "nordic.pdf")
    print(f"Wrote 3 sample PDFs to {out}/")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples")
