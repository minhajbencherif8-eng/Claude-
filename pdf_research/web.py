"""A small local search page (standard library only).

Salary data is personal data: the server listens on 127.0.0.1 by default so
only this computer can reach it.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .index import Index

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Company &amp; People Search</title>
<style>
  :root { --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e3e3de; --accent:#2f5bd3; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161615; --fg:#ececea; --muted:#9a9a94; --card:#1f1f1d; --line:#33332f; --accent:#7d9cff; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:960px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:22px; margin:0 0 16px; }
  form { display:flex; gap:8px; flex-wrap:wrap; }
  select, input, button { font:inherit; padding:9px 12px; border:1px solid var(--line);
    border-radius:8px; background:var(--card); color:var(--fg); }
  input { flex:1; min-width:200px; }
  button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:16px; }
  .card h2 { margin:0 0 4px; font-size:18px; }
  .muted { color:var(--muted); font-size:13px; }
  .details { margin:8px 0; font-size:14px; }
  .scroll { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; margin-top:8px; font-size:14px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); white-space:nowrap; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .warn { color:#b5651d; font-size:13px; }
</style></head>
<body><main>
<h1>Company &amp; People Search</h1>
<form id="f">
  <select id="type">
    <option value="company">Company</option>
    <option value="person">Person</option>
  </select>
  <input id="q" placeholder="Type a company or person name" autofocus>
  <button>Search</button>
</form>
<div id="out"></div>
</main>
<script>
const $ = (tag, props = {}, kids = []) => {
  const el = Object.assign(document.createElement(tag), props);
  kids.forEach(k => el.append(k));
  return el;
};
const money = r => r.salary_raw || (r.salary == null ? "" : r.salary.toLocaleString());
const table = (heads, rows, numCol) => $("div", {className: "scroll"}, [$("table", {}, [
  $("tr", {}, heads.map((h, i) => $("th", {textContent: h, className: i === numCol ? "num" : ""}))),
  ...rows.map(r => $("tr", {}, r.map((c, i) => $("td", {textContent: c, className: i === numCol ? "num" : ""}))))
])]);
document.getElementById("f").onsubmit = async e => {
  e.preventDefault();
  const type = document.getElementById("type").value, q = document.getElementById("q").value.trim();
  const out = document.getElementById("out");
  if (!q) return;
  const res = await fetch(`/api/search?type=${type}&q=${encodeURIComponent(q)}`).then(r => r.json());
  out.replaceChildren();
  if (!res.length) { out.append($("div", {className: "card", textContent: `No ${type} matches "${q}".`})); return; }
  if (type === "person") {
    out.append($("div", {className: "card"}, [table(["Name", "Company", "Position", "Salary"],
      res.map(r => [r.name, r.company, r.position, money(r)]), 3)]));
    return;
  }
  for (const c of res) {
    const total = c.employees.reduce((s, e) => s + (e.salary || 0), 0);
    out.append($("div", {className: "card"}, [
      $("h2", {textContent: c.company}),
      $("div", {className: "muted", textContent: c.source}),
      $("div", {className: "details"}, Object.entries(c.details).map(([k, v]) =>
        $("div", {}, [$("b", {textContent: k + ": "}), v]))),
      ...c.warnings.map(w => $("div", {className: "warn", textContent: "⚠ " + w})),
      c.employees.length
        ? table(["Name", "Position", "Salary"], c.employees.map(e => [e.name, e.position, money(e)]), 2)
        : $("div", {className: "muted", textContent: "No employees found in this PDF."}),
      $("div", {className: "muted", textContent:
        `${c.employees.length} people · total salaries ${total.toLocaleString()}`}),
    ]));
  }
};
</script>
</body></html>
"""


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if url.path == "/api/search":
                qs = parse_qs(url.query)
                q, kind = qs.get("q", [""])[0], qs.get("type", ["company"])[0]
                idx = Index(db_path)
                try:
                    res = idx.search_person(q) if kind == "person" else idx.search_company(q)
                finally:
                    idx.close()
                return self._send(200, json.dumps(res, ensure_ascii=False).encode(),
                                  "application/json; charset=utf-8")
            self._send(404, b"not found", "text/plain")

        def log_message(self, *args):
            pass

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"Search page: http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
