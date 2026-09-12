from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADING = re.compile(r"^##\s+(?P<title>.*?)(?:\s*\{#(?P<id>[A-Za-z0-9_-]+)\})?\s*$")
NON_SLUG = re.compile(r"[^a-z0-9]+")


class CriteriaError(Exception):
    pass


@dataclass(frozen=True)
class Criterion:
    id: str
    title: str
    description: str


def slugify(text: str) -> str:
    return NON_SLUG.sub("-", text.lower()).strip("-")


def parse_criteria(text: str) -> list[Criterion]:
    criteria: list[Criterion] = []
    seen: set[str] = set()
    title: str | None = None
    identifier = ""
    body: list[str] = []

    def flush() -> None:
        if title is None:
            return
        if identifier in seen:
            raise CriteriaError(f"duplicate criterion id: {identifier}")
        seen.add(identifier)
        criteria.append(Criterion(identifier, title, "\n".join(body).strip()))

    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            flush()
            title = match.group("title").strip()
            identifier = match.group("id") or slugify(title)
            if not identifier:
                raise CriteriaError(f"criterion heading has no usable id: {line!r}")
            body = []
        elif title is not None:
            body.append(line.rstrip())
    flush()
    return criteria


def load_criteria(path: Path) -> list[Criterion]:
    if not path.is_file():
        raise CriteriaError(f"criteria file not found: {path}")
    criteria = parse_criteria(path.read_text(encoding="utf-8"))
    if not criteria:
        raise CriteriaError(f"no criteria found in {path}: add '## ' headings, one per criterion")
    return criteria
