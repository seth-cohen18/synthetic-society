"""Shared HTML report helpers — self-contained, inline-styled, no dependencies.

Every report opens with the same disclaimer banner so the "directional signal, not
demand evidence" framing travels with the artifact even when it's shared.
"""

import html as _html

DISCLAIMER_HTML = """
<div class="disclaimer">
  <strong>⚠ Flight simulator, not the flight.</strong>
  These results come from AI personas, not real people. Read them as
  <em>directional / ordinal signal</em> for finding blind spots and sharpening your
  questions — <strong>never</strong> as evidence of real demand or as numbers you can
  quote. Simulate first; then talk to real humans.
</div>
"""

_CSS = """
:root { --bg:#0f1115; --card:#171a21; --ink:#e8eaed; --muted:#9aa3af; --line:#262b35;
        --accent:#5b9dff; --warn:#ffcc66; --flag:#ff7b72; --good:#6fcf97; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
.wrap { max-width:980px; margin:0 auto; padding:32px 22px 80px; }
h1 { font-size:28px; margin:0 0 4px; }
h2 { font-size:20px; margin:34px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }
h3 { font-size:16px; margin:18px 0 6px; }
.sub { color:var(--muted); margin:0 0 18px; }
.disclaimer { background:#2a2410; border:1px solid #5a4a17; color:#ffe7ad;
              padding:14px 16px; border-radius:10px; margin:18px 0 8px; font-size:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px 18px; margin:12px 0; }
.flagcard { border-color:#5a2b29; background:#1f1413; }
.tag { display:inline-block; font-size:12px; color:var(--muted); border:1px solid var(--line);
       border-radius:999px; padding:1px 9px; margin-right:6px; }
.score { font-weight:700; }
.muted { color:var(--muted); }
.kv { color:var(--muted); font-size:13px; }
.bar { height:10px; border-radius:6px; background:linear-gradient(90deg,var(--flag),var(--warn),var(--good)); }
ul { margin:6px 0 6px 18px; padding:0; } li { margin:3px 0; }
blockquote { margin:8px 0; padding:8px 12px; border-left:3px solid var(--accent);
             background:#10131a; color:#cdd3db; border-radius:0 8px 8px 0; }
.flagsec { border:1px solid #5a2b29; border-radius:12px; padding:4px 18px 14px; background:#190f0e; }
.flagsec h2 { color:var(--flag); border-color:#3a201f; }
table { border-collapse:collapse; width:100%; margin:8px 0; }
td,th { border:1px solid var(--line); padding:6px 9px; text-align:left; font-size:14px; }
th { color:var(--muted); font-weight:600; }
"""


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def page(title: str, subtitle: str, body_html: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{_CSS}</style></head><body><div class='wrap'>"
        f"<h1>{esc(title)}</h1><p class='sub'>{esc(subtitle)}</p>"
        f"{DISCLAIMER_HTML}{body_html}"
        "</div></body></html>"
    )


def write_report(path: str, title: str, subtitle: str, body_html: str) -> str:
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page(title, subtitle, body_html))
    print(f"  Report -> {path}")
    return path
