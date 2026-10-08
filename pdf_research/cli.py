"""Command line: index a folder of PDFs, then search by company or person.

    python -m pdf_research index ./pdfs        (or ./pdfs.zip)
    python -m pdf_research company "Acme"
    python -m pdf_research person "John Smith"
    python -m pdf_research serve          # web page on http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from .index import Index

DEFAULT_DB = Path("research_index.sqlite")


def _salary(e: dict) -> str:
    return e["salary_raw"] or ("" if e["salary"] is None else f"{e['salary']:,.2f}")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), "  ".join("-" * w for w in widths)]
    lines += [fmt.format(*map(str, r)) for r in rows]
    return "\n".join(lines)


def unzip(archive: Path) -> Path:
    """Extract a .zip next to itself (e.g. pdfs.zip -> pdfs/) and return that folder."""
    dest = archive.with_suffix("")
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            # Refuse entries that would land outside the destination ("../" or absolute paths).
            if not (root / member.filename).resolve().is_relative_to(root):
                raise ValueError(f"unsafe path in zip: {member.filename}")
        zf.extractall(dest)
    return dest


def cmd_index(args) -> int:
    folder = Path(args.folder)
    if folder.is_file() and folder.suffix.lower() == ".zip":
        folder = unzip(folder)
        print(f"Extracted to {folder}/")
    if not folder.is_dir():
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 2
    idx = Index(args.db)
    rep = idx.build(folder, progress=lambda p: print(f"  reading {p}"))
    idx.close()
    print(f"\nAdded {rep.added}, updated {rep.updated}, removed {rep.removed}, unchanged {rep.unchanged}.")
    for path, err in rep.failed:
        print(f"  FAILED  {path}: {err}")
    for path, warn in rep.warnings:
        print(f"  WARNING {path}: {warn}")
    return 0


def cmd_company(args) -> int:
    idx = Index(args.db)
    results = idx.search_company(args.query)
    idx.close()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if results else 1
    if not results:
        print(f'No company matches "{args.query}".')
        return 1
    for c in results:
        print(f"\n=== {c['company']} ===")
        print(f"File: {c['source']}")
        for k, v in c["details"].items():
            print(f"{k}: {v}")
        print()
        if c["employees"]:
            print(_table(["Name", "Position", "Salary"],
                         [[e["name"], e["position"], _salary(e)] for e in c["employees"]]))
            total = sum(e["salary"] or 0 for e in c["employees"])
            print(f"\n{len(c['employees'])} people, total salaries {total:,.2f}")
        else:
            print("(no employees found in this PDF)")
    return 0


def cmd_person(args) -> int:
    idx = Index(args.db)
    results = idx.search_person(args.query)
    idx.close()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if results else 1
    if not results:
        print(f'No person matches "{args.query}".')
        return 1
    print(_table(["Name", "Company", "Position", "Salary", "File"],
                 [[r["name"], r["company"], r["position"], _salary(r), Path(r["source"]).name]
                  for r in results]))
    return 0


def cmd_list(args) -> int:
    idx = Index(args.db)
    companies = idx.list_companies()
    idx.close()
    print(_table(["Company", "People", "File", "Warnings"],
                 [[c["company"], len(c["employees"]), Path(c["source"]).name, "; ".join(c["warnings"])]
                  for c in companies]))
    return 0


def cmd_serve(args) -> int:
    from .web import serve
    serve(args.db, args.host, args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdf_research", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"index file (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="read all PDFs in a folder or .zip (re-run after adding PDFs)")
    s.add_argument("folder", help="folder of PDFs, or a .zip of them")
    s.set_defaults(func=cmd_index)

    for name, func, help_ in (("company", cmd_company, "find a company and its people"),
                              ("person", cmd_person, "find a person, their company and salary")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("query", help="full or partial name; case and accents are ignored")
        s.add_argument("--json", action="store_true", help="print results as JSON")
        s.set_defaults(func=func)

    s = sub.add_parser("list", help="list every indexed company")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("serve", help="open a search page in the browser")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
