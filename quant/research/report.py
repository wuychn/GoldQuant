"""HTML 实验报告生成。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_report_html(
    *,
    experiment: dict[str, Any],
    metrics: dict[str, Any],
    attribution: dict[str, Any] | None = None,
    significance: dict[str, Any] | None = None,
) -> str:
    attr_block = ""
    if attribution:
        attr_block = f"<pre>{json.dumps(attribution, ensure_ascii=False, indent=2)}</pre>"
    sig_block = ""
    if significance:
        sig_block = f"<pre>{json.dumps(significance, ensure_ascii=False, indent=2)}</pre>"

    rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(metrics.items())
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GoldQuant Report {experiment.get('id','')}</title>
<style>body{{font-family:sans-serif;margin:2em}} table{{border-collapse:collapse}} td,th{{border:1px solid #ccc;padding:6px 12px}}</style>
</head><body>
<h1>GoldQuant 实验报告</h1>
<p><b>假设</b>: {experiment.get('hypothesis','')}</p>
<p><b>模式</b>: {experiment.get('mode','')} | <b>区间</b>: {experiment.get('data_from','')} ~ {experiment.get('data_to','')}</p>
<h2>绩效指标</h2>
<table><tr><th>指标</th><th>值</th></tr>{rows}</table>
<h2>归因</h2>{attr_block}
<h2>显著性</h2>{sig_block}
</body></html>"""


def write_report(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
