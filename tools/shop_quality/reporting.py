from __future__ import annotations
import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".quality-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _display(value: Any) -> str:
    return "N/A" if value is None else (f"{value:.6f}" if type(value) is float else str(value))


def markdown(report: dict[str, Any]) -> str:
    # User-controlled IDs are not interpolated into markup headings or tables.
    lines = ["# ShopAgentStack Evaluation", "", f"Scope: **{report['claim_scope']}**", "",
             f"Split: `{report['split']}`; cases: **{report['dataset_cases']}**.", "",
             f"Dataset SHA256: `{report['manifest']['dataset_sha256']}`", "",
             "| Metric | Value | Samples |", "|---|---:|---:|"]
    for name, item in sorted(report["metrics"].items()):
        lines.append(f"| {name} | {_display(item['value'])} | {item['n']} |")
    if report["gates"] is not None:
        lines.extend(["", f"Gate: **{'PASS' if report['gates']['passed'] else 'FAIL'}**", "",
                      "| Gate | Actual | Samples | Result |", "|---|---:|---:|---|"])
        for item in report["gates"]["checks"]:
            lines.append(f"| {item['metric']} | {_display(item['actual'])} | {item['n']} | {'PASS' if item['passed'] else 'FAIL'} |")
    lines.extend(["", "## Interpretation", ""] + [f"- {warning}" for warning in report["warnings"]])
    return "\n".join(lines) + "\n"


def html_report(report: dict[str, Any]) -> str:
    esc = html.escape
    rows = "".join(f"<tr><td>{esc(key)}</td><td>{esc(_display(metric['value']))}</td><td>{metric['n']}</td></tr>"
                   for key, metric in sorted(report["metrics"].items()))
    warnings = "".join(f"<p>{esc(warning)}</p>" for warning in report["warnings"])
    status = "NOT RUN" if report["gates"] is None else ("PASS" if report["gates"]["passed"] else "FAIL")
    cases = "".join(f"<tr><td>{esc(item['case_id'])}</td><td>{esc(item['status'])}</td></tr>" for item in report["per_case"])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>ShopAgentStack · Evaluation Evidence</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#101723;color:#e6eaf0;max-width:1000px;margin:48px auto;padding:0 24px}}
h1{{font-size:32px}}.badge{{display:inline-block;padding:8px 16px;border:1px solid #6ebac9;border-radius:8px}}
.note{{padding:16px 24px;background:#223245;border-radius:12px;margin:24px 0}}table{{border-collapse:collapse;width:100%;margin:24px 0}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid #36465b}}td:nth-child(2){{font-variant-numeric:tabular-nums}}
code{{overflow-wrap:anywhere}}summary{{cursor:pointer}}footer{{color:#aebbd0;margin:36px 0}}</style></head>
<body><p>QUALITY ENGINEERING / M1</p><h1>ShopAgentStack 评测证据</h1>
<div class="badge">{esc(report['claim_scope'])} · GATE {status}</div>
<p>本页是评测报告，不是商城界面或线上 Demo。</p>
<p>{report['dataset_cases']} cases · {report['split']} · run {esc(report['manifest']['run_id'])}</p>
<div class="note">{warnings}</div><h2>指标与样本量</h2><table><thead><tr><th>Metric</th><th>Value</th><th>n</th></tr></thead><tbody>{rows}</tbody></table>
<details><summary>逐案例状态（{len(report['per_case'])}）</summary><table><tr><th>Case</th><th>Status</th></tr>{cases}</table></details>
<footer>Dataset SHA256: <code>{esc(report['manifest']['dataset_sha256'])}</code><br>
配置指纹: <code>{report['system_sha256']}</code><br>不会加载外部脚本、样式、字体或分析服务。</footer></body></html>'''


def write_reports(report: dict[str, Any], output: Path) -> None:
    atomic_write(output / "report.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    atomic_write(output / "report.md", markdown(report))
    atomic_write(output / "report.html", html_report(report))
