"""Render only measured benchmark results. Missing runs are never successes."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "bench" / "results"


def get_latest_bench_report():
    reports = list(RESULTS_DIR.glob("*/report.json"))
    if not reports:
        return None
    latest = max(reports, key=lambda p: p.stat().st_mtime)
    return json.loads(latest.read_text()), latest.parent


def generate_html(output=None):
    found = get_latest_bench_report()
    data, directory = found or ({"results": []}, None)
    esc = lambda value: html.escape(str(value))
    rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{'PASS' if r['passed'] else 'FAIL'}</td>"
        f"<td>{esc(r.get('dur_s', ''))}</td><td>{esc('; '.join(r.get('failures', [])))}</td></tr>"
        for r in data.get("results", [])
    )
    summary = (f"{esc(data['passed'])}/{esc(data['total'])} scenario checks passed · mode: {esc(data.get('mode', 'unknown'))}"
               if found else "No benchmark results available. Run python bench/runner.py first.")
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>MCP-Vision measured checks</title>
<style>body{{font:16px system-ui;max-width:1000px;margin:60px auto;padding:20px}}td,th{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>MCP-Vision measured checks</h1><p>{summary}</p>
<p>Scripted checks measure the harness, not model accuracy. Safe stops are not completed tasks.
No capability scores or golden replay results are inferred.</p>
<p>Source: {esc(directory or 'none')} · Revision: {esc(data.get('revision', 'unknown'))}</p>
<table><thead><tr><th>Scenario</th><th>Result</th><th>Seconds</th><th>Failures</th></tr></thead><tbody>{rows}</tbody></table></html>"""
    path = Path(output) if output else ROOT / "report.html"
    path.write_text(body)
    return path


if __name__ == "__main__":
    print(generate_html())
