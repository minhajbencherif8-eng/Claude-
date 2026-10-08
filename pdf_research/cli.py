"""Command line: index PDFs from a .zip or folder, then search by company or person.

    python -m pdf_research index my_pdfs.zip
    python -m pdf_research company "SONACOS"
    python -m pdf_research person "CHAFAI SAMIR"
    python -m pdf_research top-companies --min-employees 5
    python -m pdf_research top-earners --city casablanca
    python -m pdf_research app                 # desktop app with a "Select ZIP File" button
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .index import ZIP_SEP, Index


def _default_db() -> Path:
    from .app import data_dir  # same database as the desktop app
    return data_dir() / "index.sqlite"


def _salary(e: dict) -> str:
    return e["salary_raw"] or ("" if e["salary"] is None else f"{e['salary']:,.2f}")


def _file(source: str) -> str:
    return Path(source.split(ZIP_SEP, 1)[-1]).name


def _table(headers: list[str], rows: list[list]) -> str:
    rows = [["" if x is None else str(x) for x in r] for r in rows]
    widths = [max(len(x) for x in col) for col in zip(headers, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join([fmt.format(*headers), "  ".join("-" * w for w in widths)] + [fmt.format(*r) for r in rows])


def cmd_index(args) -> int:
    source = Path(args.source)
    if not source.exists():
        print(f"Not found: {source}", file=sys.stderr)
        return 2
    idx = Index(args.db)

    def progress(done, total, name):
        print(f"\r  {done}/{total}  {Path(name).name[:60]:<60}", end="", flush=True)

    rep = idx.build(source, progress=progress)
    idx.close()
    print(f"\nRead {rep.added + rep.updated}, removed {rep.removed}, already indexed {rep.unchanged}.")
    for path, err in rep.failed:
        print(f"  FAILED  {_file(path)}: {err}")
    for path, warn in rep.warnings:
        print(f"  CHECK   {_file(path)}: {warn}")
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
        for k, v in c["details"].items():
            print(f"{k}: {v}")
        print(f"PDF: {_file(c['source'])}")
        for w in c["warnings"]:
            print(f"WARNING: {w}")
        print()
        if c["employees"]:
            print(_table(["Employee", "CNSS n°", "Period", "Days", "Salary"],
                         [[e["name"], e["ref"], e["period"], e["days"], _salary(e)] for e in c["employees"]]))
            total = sum(e["salary"] or 0 for e in c["employees"])
            print(f"\n{len(c['employees'])} employees, total salaries {total:,.2f}")
        else:
            print("(no employees declared in this PDF)")
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
    print(_table(["Employee", "CNSS n°", "Company", "City", "Period", "Days", "Salary"],
                 [[r["name"], r["ref"], r["company"], r["city"], r["period"], r["days"], _salary(r)]
                  for r in results]))
    return 0


def _money(v) -> str:
    return "" if v is None else f"{v:,.2f}"


def cmd_top_companies(args) -> int:
    idx = Index(args.db)
    rows = idx.top_companies(by=args.by, min_employees=args.min_employees, city=args.city, limit=args.limit,
                             min_days=args.min_days)
    idx.close()
    print(_table(["#", "Company", "City", "Employees", "Avg salary", "Avg per day", "Highest", "PDFs"],
                 [[i, r["company"], r["city"], r["people"], _money(r["avg_salary"]), _money(r["avg_day"]),
                   _money(r["max_salary"]), r["pdfs"]] for i, r in enumerate(rows, 1)]))
    return 0 if rows else 1


def cmd_top_earners(args) -> int:
    idx = Index(args.db)
    rows = idx.top_earners(by=args.by, city=args.city, limit=args.limit, min_days=args.min_days)
    idx.close()
    print(_table(["#", "Employee", "CNSS n°", "Company", "City", "Period", "Days", "Salary", "Per day"],
                 [[i, r["name"], r["ref"], r["company"], r["city"], r["period"], r["days"],
                   _money(r["salary"]), _money(r["per_day"])] for i, r in enumerate(rows, 1)]))
    return 0 if rows else 1


def cmd_stats(args) -> int:
    idx = Index(args.db)
    s = idx.stats()
    idx.close()
    print(f"Database: {args.db}")
    print(f"{s['files']} PDFs, {s['companies']} companies, {s['employees']} employee entries,"
          f" {s['warnings']} PDFs with warnings")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdf_research", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="database file (default: the desktop app's database)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="read all PDFs in a .zip or folder (re-run to pick up changes)")
    s.add_argument("source", help=".zip of PDFs, or a folder of PDFs")
    s.set_defaults(func=cmd_index)

    for name, func, help_ in (("company", cmd_company, "find a company and its employees"),
                              ("person", cmd_person, "find a person, their company and salary")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("query", help="full or partial name, or a CNSS/affiliation number")
        s.add_argument("--json", action="store_true", help="print results as JSON")
        s.set_defaults(func=func)

    for name, func, help_ in (("top-companies", cmd_top_companies, "companies with the highest average salary"),
                              ("top-earners", cmd_top_earners, "highest-paid employees")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--by", choices=["salary", "per_day"], default="salary",
                       help="rank by declared salary (default) or salary per day worked")
        s.add_argument("--city", default="", help="only this city (partial name is fine)")
        s.add_argument("--limit", type=int, default=50, help="how many to show (default 50)")
        s.add_argument("--min-days", type=int, default=1,
                       help="only count declarations with at least this many days worked (26 = full months)")
        if name == "top-companies":
            s.add_argument("--min-employees", type=int, default=3,
                           help="ignore companies with fewer employees (default 3)")
        s.set_defaults(func=func)

    sub.add_parser("stats", help="what is in the database").set_defaults(func=cmd_stats)
    sub.add_parser("app", help="open the desktop app").set_defaults(func=None)

    args = p.parse_args(argv)
    if args.cmd == "app":
        from .app import main as app_main
        app_main(Path(args.db) if args.db else None)
        return 0
    args.db = args.db or str(_default_db())
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
