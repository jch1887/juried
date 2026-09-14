from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from juried.config import Config
from juried.correction import Calibration
from juried.criteria import Criterion
from juried.report.html import render_html
from juried.report.json import build_report, write_json
from juried.runner import ScenarioResult

JSON_NAME = "juried-report.json"
HTML_NAME = "juried-report.html"


@dataclass(frozen=True)
class ReportPaths:
    json: Path
    html: Path


def write_reports(
    config: Config,
    criteria: Sequence[Criterion],
    results: Sequence[ScenarioResult],
    output_dir: Path,
    calibration: Calibration | None = None,
) -> ReportPaths:
    report = build_report(config, criteria, results, calibration)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = ReportPaths(output_dir / JSON_NAME, output_dir / HTML_NAME)
    write_json(report, paths.json)
    paths.html.write_text(render_html(report), encoding="utf-8")
    return paths


__all__ = ["ReportPaths", "build_report", "render_html", "write_json", "write_reports"]
