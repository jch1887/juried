from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from vouch.config import Config
from vouch.criteria import Criterion
from vouch.report.html import render_html
from vouch.report.json import build_report, write_json
from vouch.runner import ScenarioResult

JSON_NAME = "vouch-report.json"
HTML_NAME = "vouch-report.html"


@dataclass(frozen=True)
class ReportPaths:
    json: Path
    html: Path


def write_reports(
    config: Config,
    criteria: Sequence[Criterion],
    results: Sequence[ScenarioResult],
    output_dir: Path,
) -> ReportPaths:
    report = build_report(config, criteria, results)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = ReportPaths(output_dir / JSON_NAME, output_dir / HTML_NAME)
    write_json(report, paths.json)
    paths.html.write_text(render_html(report), encoding="utf-8")
    return paths


__all__ = ["ReportPaths", "build_report", "render_html", "write_json", "write_reports"]
