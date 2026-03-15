#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Ultimate Stem Lab - Audition Report

Creates a lightweight HTML audition sheet for a project folder produced by
run_stem_lab.py / score_stems.py.

Usage examples:
    python audition_report.py --project ".\ultimate_stem_lab\projects\downloaded_track"
    python audition_report.py --project ".\ultimate_stem_lab\projects\downloaded_track" --open

This script:
- reads final/stem_scores_v2.json if present, else final/stem_scores.json
- reads final/stem_selection_report_v2.txt if present, else final/stem_selection_report.txt
- discovers available winning stems in the project's final folder
- writes an HTML report with embedded audio players
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any


def read_text_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
    return None


def pick_first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def rel_uri(path: Path, base: Path) -> str:
    try:
        rel = path.resolve().relative_to(base.resolve())
        return rel.as_posix()
    except Exception:
        return path.resolve().as_uri()


def discover_final_stems(final_dir: Path) -> list[Path]:
    preferred = sorted(final_dir.glob("*_best.wav")) + sorted(final_dir.glob("*_best.mp3")) + sorted(final_dir.glob("*_best.flac"))
    if preferred:
        return preferred

    fallback = []
    for ext in ("*.wav", "*.mp3", "*.flac"):
        fallback.extend(sorted(final_dir.glob(ext)))
    return fallback


def metric_table_rows(data: dict[str, Any] | None) -> str:
    if not data:
        return "<tr><td colspan='6'>No JSON score file found.</td></tr>"

    winners = data.get("winners", {})
    rows = []
    for stem_name in sorted(winners.keys()):
        info = winners.get(stem_name, {}) or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(stem_name)}</td>"
            f"<td>{html.escape(str(info.get('model', '')))}</td>"
            f"<td>{html.escape(str(info.get('path', '')))}</td>"
            f"<td>{html.escape(str(info.get('score', '')))}</td>"
            f"<td>{html.escape(str(info.get('confidence', info.get('margin', ''))))}</td>"
            f"<td>{html.escape(str(info.get('notes', '')))}</td>"
            "</tr>"
        )

    if not rows:
        return "<tr><td colspan='6'>Score file loaded, but no winners were found.</td></tr>"

    return "\n".join(rows)


def build_audio_cards(final_stems: list[Path], project_dir: Path) -> str:
    if not final_stems:
        return "<p>No final stems found in the project's final folder.</p>"

    cards = []
    for stem_path in final_stems:
        stem_label = stem_path.stem
        src = rel_uri(stem_path, project_dir)
        cards.append(
            "<div class='card'>"
            f"<h3>{html.escape(stem_label)}</h3>"
            f"<div class='path'>{html.escape(str(stem_path))}</div>"
            f"<audio controls preload='none' src='{html.escape(src)}'></audio>"
            "</div>"
        )
    return "\n".join(cards)


def render_html(project_dir: Path, score_json: dict[str, Any] | None, report_text: str, stems: list[Path]) -> str:
    title = f"Audition Report - {project_dir.name}"
    rows = metric_table_rows(score_json)
    cards = build_audio_cards(stems, project_dir)

    escaped_report = html.escape(report_text.strip()) if report_text.strip() else "No text report found."

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
    body {{
        font-family: Arial, Helvetica, sans-serif;
        margin: 24px;
        background: #111;
        color: #eee;
    }}
    h1, h2, h3 {{
        margin-top: 0;
    }}
    .muted {{
        color: #bbb;
    }}
    .section {{
        margin-bottom: 28px;
        padding: 18px;
        border: 1px solid #333;
        border-radius: 12px;
        background: #1a1a1a;
    }}
    .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 16px;
    }}
    .card {{
        border: 1px solid #333;
        border-radius: 10px;
        padding: 14px;
        background: #202020;
    }}
    .path {{
        font-size: 12px;
        color: #aaa;
        margin-bottom: 10px;
        word-break: break-all;
    }}
    audio {{
        width: 100%;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
    }}
    th, td {{
        border: 1px solid #333;
        padding: 8px;
        text-align: left;
        vertical-align: top;
        font-size: 14px;
    }}
    th {{
        background: #252525;
    }}
    pre {{
        white-space: pre-wrap;
        word-wrap: break-word;
        background: #202020;
        padding: 14px;
        border-radius: 10px;
        border: 1px solid #333;
    }}
    a {{
        color: #9ecbff;
    }}
</style>
</head>
<body>
    <h1>{html.escape(title)}</h1>
    <p class="muted">Project directory: {html.escape(str(project_dir))}</p>

    <div class="section">
        <h2>Winning stems</h2>
        <div class="grid">
            {cards}
        </div>
    </div>

    <div class="section">
        <h2>Score summary</h2>
        <table>
            <thead>
                <tr>
                    <th>Stem</th>
                    <th>Winning model</th>
                    <th>Path</th>
                    <th>Score</th>
                    <th>Confidence / Margin</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>

    <div class="section">
        <h2>Text report</h2>
        <pre>{escaped_report}</pre>
    </div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an HTML audition report for a stem-separation project.")
    parser.add_argument("--project", required=True, help="Path to the project folder.")
    parser.add_argument("--open", action="store_true", dest="open_report", help="Open the generated HTML report in the default browser.")
    args = parser.parse_args()

    project_dir = Path(args.project).expanduser().resolve()
    if not project_dir.exists():
        print(f"[!] Project folder not found: {project_dir}", file=sys.stderr)
        return 1

    final_dir = project_dir / "final"
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    score_path = pick_first_existing([
        final_dir / "stem_scores_v2.json",
        final_dir / "stem_scores.json",
    ])
    report_path = pick_first_existing([
        final_dir / "stem_selection_report_v2.txt",
        final_dir / "stem_selection_report.txt",
    ])

    score_json = read_json_if_exists(score_path) if score_path else None
    report_text = read_text_if_exists(report_path) if report_path else ""
    stems = discover_final_stems(final_dir)

    out_path = reports_dir / "audition_report.html"
    out_path.write_text(
        render_html(project_dir=project_dir, score_json=score_json, report_text=report_text, stems=stems),
        encoding="utf-8",
        errors="replace",
    )

    print(f"[+] Audition report written: {out_path}")
    if args.open_report:
        webbrowser.open(out_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
