from __future__ import annotations

from pathlib import Path

import markdown as md
from weasyprint import CSS, HTML


CSS_TEXT = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: DejaVu Sans, sans-serif; font-size: 11pt; line-height: 1.45; }
h1, h2, h3 { line-height: 1.2; }
code, pre { font-family: DejaVu Sans Mono, monospace; font-size: 9.5pt; }
pre { background: #f6f8fa; padding: 10px; border-radius: 6px; overflow-wrap: anywhere; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; }
td, th { border: 1px solid #ddd; padding: 6px; }
"""


def markdown_to_pdf(md_text: str, base_dir: Path, out_pdf: Path, title: str | None = None) -> None:
    base_dir = base_dir.resolve()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    html_body = md.markdown(md_text, extensions=["extra", "toc", "sane_lists"])
    if title:
        html = f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{html_body}</body></html>"
    else:
        html = f"<!doctype html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"

    HTML(string=html, base_url=str(base_dir)).write_pdf(
        str(out_pdf),
        stylesheets=[CSS(string=CSS_TEXT)],
    )
