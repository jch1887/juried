from __future__ import annotations

from importlib import resources
from typing import Any

from jinja2 import Environment, select_autoescape


def percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def load_template() -> str:
    return resources.files("vouch.report").joinpath("template.html").read_text(encoding="utf-8")


def render_html(report: dict[str, Any]) -> str:
    environment = Environment(autoescape=select_autoescape(default=True), trim_blocks=True)
    environment.filters["percent"] = percent
    return environment.from_string(load_template()).render(report=report)
