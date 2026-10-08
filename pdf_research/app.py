"""Desktop app: pick the ZIP of PDFs once, then search companies and employees.

Everything runs on this computer: the ZIP is read in place, and the database
lives in the user's local app-data folder.
"""

from __future__ import annotations

import csv
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from .index import ZIP_SEP, Index

APP_NAME = "Company & Employee Search"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    return Path(base) / "PDFResearch"


def fmt_money(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def open_with_system(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - opening the user's own file
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class App:
    def __init__(self, root: tk.Tk, db_path: Path):
        self.root = root
        self.db_path = db_path
        self.index = Index(db_path)
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.results: list[dict] = []
        self.mode = tk.StringVar(value="company")
        self.query = tk.StringVar()
        self.status = tk.StringVar()

        root.title(APP_NAME)
        root.geometry("1200x780")
        root.minsize(900, 600)
        self._build()
        self._refresh_stats()
        root.after(150, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- layout ----------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        self.select_btn = ttk.Button(top, text="Select ZIP File…", command=self.select_zip)
        self.select_btn.pack(side="left")
        self.stop_btn = ttk.Button(top, text="Stop", command=self.cancel.set, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))
        self.progress = ttk.Progressbar(top, length=260, mode="determinate")
        self.progress.pack(side="left", padx=10)
        self.progress_lbl = ttk.Label(top, text="")
        self.progress_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Import report", command=self.open_report).pack(side="right")

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", **pad)
        ttk.Label(bar, text="Search:").pack(side="left")
        ttk.Radiobutton(bar, text="Company", value="company", variable=self.mode,
                        command=self.search).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(bar, text="Employee", value="person", variable=self.mode,
                        command=self.search).pack(side="left", padx=(8, 8))
        entry = ttk.Entry(bar, textvariable=self.query, width=50)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.search())
        entry.focus_set()
        ttk.Button(bar, text="Search", command=self.search).pack(side="left", padx=6)
        ttk.Button(bar, text="Export to Excel (CSV)", command=self.export_csv).pack(side="left")

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8)

        res = ttk.Frame(panes)
        self.count_lbl = ttk.Label(res, text="")
        self.count_lbl.pack(anchor="w")
        self.tree = self._tree(res, [])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected())
        self.tree.bind("<Double-1>", lambda e: self.open_pdf())
        panes.add(res, weight=3)

        detail = ttk.Frame(panes)
        head = ttk.Frame(detail)
        head.pack(fill="x", pady=(6, 2))
        self.detail_title = ttk.Label(head, text="", font=("TkDefaultFont", 12, "bold"))
        self.detail_title.pack(side="left")
        self.open_btn = ttk.Button(head, text="Open PDF", command=self.open_pdf, state="disabled")
        self.open_btn.pack(side="right")
        split = ttk.PanedWindow(detail, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split)
        self.detail_text = tk.Text(left, wrap="word", width=50, height=12, relief="flat",
                                   font="TkDefaultFont", background=self.root.cget("background"))
        ds = ttk.Scrollbar(left, orient="vertical", command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=ds.set)
        self.detail_text.pack(side="left", fill="both", expand=True)
        ds.pack(side="right", fill="y")
        self.detail_text.configure(state="disabled")
        split.add(left, weight=2)
        right = ttk.Frame(split)
        self.emp_tree = self._tree(right, [("name", "Employee", 220), ("ref", "CNSS n°", 100),
                                           ("period", "Period", 70), ("days", "Days", 50),
                                           ("salary", "Salary (DH)", 100)])
        split.add(right, weight=3)
        panes.add(detail, weight=2)

        ttk.Label(self.root, textvariable=self.status, foreground="#555").pack(fill="x", padx=8, pady=4)

    def _tree(self, parent, columns) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, show="headings", selectmode="browse")
        ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self._set_columns(tree, columns)
        return tree

    @staticmethod
    def _set_columns(tree: ttk.Treeview, columns) -> None:
        tree.delete(*tree.get_children())
        tree["columns"] = [c[0] for c in columns]
        for key, title, width in columns:
            numeric = key in ("salary", "total", "count", "days")
            tree.heading(key, text=title, anchor="e" if numeric else "w")
            tree.column(key, width=width, anchor="e" if numeric else "w", stretch=key in ("name", "company"))

    # ---------- importing ----------

    def select_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="Select the ZIP file with the PDFs",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")])
        if path:
            self.start_import(Path(path))

    def start_import(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as zf:
                n = sum(1 for n in zf.namelist() if n.lower().endswith(".pdf"))
        except (zipfile.BadZipFile, OSError) as exc:
            messagebox.showerror(APP_NAME, f"Could not open this ZIP file:\n{exc}")
            return
        if n == 0:
            messagebox.showwarning(APP_NAME, "This ZIP file contains no PDF files.")
            return
        self.cancel.clear()
        self.select_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(value=0, maximum=1)
        self.progress_lbl.configure(text=f"Checking {n:,} PDFs…")
        self.started = time.monotonic()
        self.worker = threading.Thread(target=self._import, args=(path,), daemon=True)
        self.worker.start()

    def _import(self, path: Path) -> None:
        # The worker thread needs its own database connection.
        idx = Index(self.db_path)
        try:
            report = idx.build(path, progress=lambda d, t, name: self.events.put(("progress", d, t, name)),
                               cancel=self.cancel)
            self.events.put(("done", path, report))
        except Exception as exc:
            self.events.put(("error", exc))
        finally:
            idx.close()

    def _poll(self) -> None:
        last = None
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    last = ev
                elif ev[0] == "done":
                    last = None
                    self._import_done(ev[1], ev[2])
                elif ev[0] == "error":
                    self._import_finished()
                    messagebox.showerror(APP_NAME, f"Import failed:\n{ev[1]}")
        except queue.Empty:
            pass
        if last:
            _, done, total, name = last
            self.progress.configure(value=done, maximum=max(total, 1))
            elapsed = time.monotonic() - self.started
            eta = elapsed / done * (total - done) if done else 0
            self.progress_lbl.configure(
                text=f"Reading PDF {done:,} of {total:,} — about {int(eta // 60)} min {int(eta % 60)} s left"
                     f" — {Path(name).name}")
        self.root.after(150, self._poll)

    def _import_finished(self) -> None:
        self.select_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.worker = None

    def _import_done(self, path: Path, report) -> None:
        self._import_finished()
        self.progress.configure(value=self.progress["maximum"])
        self._write_report(path, report)
        self._refresh_stats()
        read = report.added + report.updated
        msg = (f"{'Stopped' if report.cancelled else 'Finished'}: {read:,} PDFs read"
               f", {report.unchanged:,} already in the database")
        if report.removed:
            msg += f", {report.removed:,} no longer in the ZIP were removed"
        self.progress_lbl.configure(text=msg)
        text = msg + "."
        if report.cancelled:
            text += "\n\nSelect the same ZIP again to continue where it stopped."
        if report.failed or report.warnings:
            text += (f"\n\n{len(report.failed):,} PDFs could not be read and {len(report.warnings):,}"
                     " need a check. Click “Import report” for the list.")
        messagebox.showinfo(APP_NAME, text)

    def _write_report(self, path: Path, report) -> None:
        lines = [f"Import of {path}", time.strftime("%Y-%m-%d %H:%M"), f"Database: {self.db_path}", "",
                 f"Read: {report.added + report.updated}", f"Already in database: {report.unchanged}",
                 f"Removed: {report.removed}", f"Stopped early: {report.cancelled}", ""]
        if report.failed:
            lines += ["PDFs that could not be read:"] + [f"  {k}: {e}" for k, e in report.failed] + [""]
        if report.warnings:
            lines += ["PDFs to check:"] + [f"  {k}: {w}" for k, w in report.warnings]
        self.report_path.write_text("\n".join(lines), encoding="utf-8")

    @property
    def report_path(self) -> Path:
        return self.db_path.parent / "import_report.txt"

    def open_report(self) -> None:
        if self.report_path.exists():
            open_with_system(self.report_path)
        else:
            messagebox.showinfo(APP_NAME, "No import has been run yet.")

    def _refresh_stats(self) -> None:
        s = self.index.stats()
        if s["files"]:
            self.status.set(f"{s['companies']:,} companies and {s['employees']:,} employee entries"
                            " in the database. All data stays on this computer.")
        else:
            self.status.set("The database is empty: click “Select ZIP File…” to import your PDFs"
                            " (only needed once).")

    # ---------- searching ----------

    def search(self) -> None:
        q = self.query.get().strip()
        if not q:
            return
        t = time.perf_counter()
        if self.mode.get() == "company":
            self.results = self.index.search_company(q)
            self._set_columns(self.tree, [("company", "Company", 260), ("city", "City", 120),
                                          ("aff", "Affiliation n°", 100), ("count", "Employees", 80),
                                          ("total", "Total salaries (DH)", 130), ("file", "PDF", 220)])
            for i, c in enumerate(self.results):
                d = c["details"]
                total = sum(e["salary"] or 0 for e in c["employees"])
                self.tree.insert("", "end", iid=str(i), values=(
                    c["company"], d.get("Ville", ""), d.get("N° d'affiliation", ""), len(c["employees"]),
                    fmt_money(total) if c["employees"] else "", self._file_name(c["source"])))
        else:
            self.results = self.index.search_person(q)
            self._set_columns(self.tree, [("name", "Employee", 220), ("ref", "CNSS n°", 100),
                                          ("company", "Company", 240), ("city", "City", 120),
                                          ("period", "Period", 70), ("days", "Days", 50),
                                          ("salary", "Salary (DH)", 110)])
            for i, r in enumerate(self.results):
                self.tree.insert("", "end", iid=str(i), values=(
                    r["name"], r["ref"], r["company"], r["city"], r["period"],
                    "" if r["days"] is None else r["days"], r["salary_raw"] or fmt_money(r["salary"])))
        n = len(self.results)
        more = " (only the first 1,000 are shown, type more of the name to narrow it down)" if n >= 1000 else ""
        self.count_lbl.configure(text=f"{n:,} result{'s' if n != 1 else ''} in"
                                      f" {time.perf_counter() - t:.2f} s{more}" if n else "No results.")
        self._show_company(None)
        if n:
            self.tree.selection_set("0")

    @staticmethod
    def _file_name(source: str) -> str:
        return Path(source.split(ZIP_SEP, 1)[-1]).name

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.results[int(sel[0])] if sel else None

    def _show_selected(self) -> None:
        r = self._selected()
        if r is None:
            return
        if self.mode.get() == "company":
            self._show_company(r)
        else:
            self._show_company(self.index.company(r["company_id"]), highlight=r["ref"] or r["name"])

    def _show_company(self, c: dict | None, highlight: str = "") -> None:
        self.current = c
        self.emp_tree.delete(*self.emp_tree.get_children())
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.open_btn.configure(state="normal" if c else "disabled")
        if c is None:
            self.detail_title.configure(text="")
            self.detail_text.configure(state="disabled")
            return
        self.detail_title.configure(text=c["company"])
        out = [f"{k}: {v}" for k, v in c["details"].items()]
        out.append(f"PDF: {self._file_name(c['source'])}")
        for w in c["warnings"]:
            out.append(f"⚠ {w}")
        if c["payroll"]:
            out += ["", "Monthly declarations (employees · payroll DH):"]
            out += [f"  {p['month']}: " + (f"{p['employees']} · {p['payroll']}" if p["payroll"] else "NEANT")
                    for p in c["payroll"]]
        self.detail_text.insert("1.0", "\n".join(out))
        self.detail_text.configure(state="disabled")
        focus = None
        for i, e in enumerate(c["employees"]):
            iid = self.emp_tree.insert("", "end", values=(
                e["name"], e["ref"], e["period"], "" if e["days"] is None else e["days"],
                e["salary_raw"] or fmt_money(e["salary"])))
            if highlight and highlight in (e["ref"], e["name"]):
                focus = iid
        if not c["employees"]:
            self.emp_tree.insert("", "end", values=("(no employees declared in this PDF)", "", "", "", ""))
        if focus:
            self.emp_tree.selection_set(focus)
            self.emp_tree.see(focus)

    def open_pdf(self) -> None:
        c = getattr(self, "current", None)
        if not c:
            return
        source = c["source"]
        try:
            if ZIP_SEP in source:
                zip_path, member = source.split(ZIP_SEP, 1)
                out = Path(tempfile.gettempdir()) / "PDFResearch" / Path(member).name
                out.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zip_path) as zf:
                    out.write_bytes(zf.read(member))
                open_with_system(out)
            else:
                open_with_system(Path(source))
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            messagebox.showerror(APP_NAME, f"Could not open the PDF (was the ZIP moved?):\n{exc}")

    def export_csv(self) -> None:
        if not self.results:
            messagebox.showinfo(APP_NAME, "Search for something first, then export the results.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV (Excel)", "*.csv")],
                                            initialfile="search_results.csv")
        if not path:
            return
        header = ["Company", "City", "Affiliation n°", "Employee", "CNSS n°", "Period", "Days", "Salary (DH)", "PDF"]
        rows = []
        if self.mode.get() == "company":
            for c in self.results:
                d = c["details"]
                base = [c["company"], d.get("Ville", ""), d.get("N° d'affiliation", "")]
                for e in c["employees"] or [{}]:
                    rows.append(base + [e.get("name", ""), e.get("ref", ""), e.get("period", ""),
                                        e.get("days") or "", fmt_money(e.get("salary")),
                                        self._file_name(c["source"])])
        else:
            for r in self.results:
                rows.append([r["company"], r["city"], "", r["name"], r["ref"], r["period"], r["days"] or "",
                             fmt_money(r["salary"]), self._file_name(r["source"])])
        # utf-8-sig + ";" so Excel (French/Moroccan settings) opens it with accents and columns intact.
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(header)
            w.writerows(rows)
        messagebox.showinfo(APP_NAME, f"Saved {len(rows):,} rows to\n{path}")

    def _on_close(self) -> None:
        if self.worker and not messagebox.askyesno(
                APP_NAME, "An import is running. Stop it and quit?\n"
                          "What was read so far is kept; select the ZIP again later to continue."):
            return
        self.cancel.set()
        self.root.destroy()


def main(db_path: Path | None = None) -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root, db_path or data_dir() / "index.sqlite")
    root.mainloop()
