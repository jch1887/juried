"""The label queue: responses a human should look at, and `juried label`, which turns them
into calibration cases."""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import IO, Any

import yaml

from juried.runner import RunRecord, ScenarioResult

QUEUE_FILE = "label-queue.jsonl"
FROM_RUNS_DIR = "from-runs"
HTML_NAME = "label-queue.html"
CSV_NAME = "label-queue.csv"
DEFAULT_SAMPLE_RATE = 0.02
REASON_ORDER = ("split", "boundary", "check_disagreed", "low_confidence", "sampled")

# Words in a judge's reason that mark a verdict it was not sure of. A heuristic: a fixed
# list, matched as whole words, case insensitively. Extend it here, nowhere else.
HEDGES = (
    "probably",
    "likely",
    "unclear",
    "hard to say",
    "seems",
    "appears",
    "might",
    "possibly",
    "not sure",
    "uncertain",
    "ambiguous",
    "borderline",
    "arguably",
    "partially",
    "somewhat",
    "debatable",
)
HEDGE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in HEDGES) + r")\b", re.IGNORECASE
)


def hedged(reason: str) -> bool:
    return HEDGE_PATTERN.search(reason) is not None


def content_hash(
    criterion: str, message: str, history: list[dict[str, str]], expected: str, response: str
) -> str:
    payload = json.dumps([criterion, message, history, expected, response], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class QueueEntry:
    id: str
    run: str
    scenario: str
    name: str
    criterion: str
    message: str
    history: list[dict[str, str]]
    transcript: list[dict[str, str]]
    expected: str
    response: str
    verdict: str | None
    reason: str
    model: str | None
    reasons: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    attempt: int = 0
    early_stopped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueEntry:
        return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})

    # The name a calibration case gets: the scenario's, plus the run and a short hash so
    # two labels of the same scenario never collide.
    @property
    def case_name(self) -> str:
        return f"{self.name} ({self.run[:10]} #{self.id[:6]})"


def attempt_reasons(result: ScenarioResult, record: RunRecord) -> list[str]:
    reasons: list[str] = []
    if record.agreement is not None and record.agreement < 1.0:
        reasons.append("split")
    on_boundary = result.complete and result.fails >= 1 and abs(result.fails - result.misses) <= 1
    if on_boundary and not record.passed:
        reasons.append("boundary")
    if record.checks and all(outcome.passed for outcome in record.checks) and not record.passed:
        reasons.append("check_disagreed")
    if record.verdict is not None and hedged(record.verdict.reason):
        reasons.append("low_confidence")
    return reasons


def build_queue(
    results: Sequence[ScenarioResult],
    run_id: str,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    rng: random.Random | None = None,
    early_stopped: bool = False,
) -> list[QueueEntry]:
    rng = rng or random.Random(run_id)
    entries: dict[str, QueueEntry] = {}
    for result in results:
        for record in result.runs:
            # Only judged responses: a check decided attempt tells you about the check,
            # not the judge, and an errored one has nothing to label.
            if record.response is None or record.verdict is None or record.failed_by_checks:
                continue
            reasons = attempt_reasons(result, record)
            if not reasons and rng.random() < sample_rate:
                reasons = ["sampled"]
            if not reasons:
                continue
            history = [turn.model_dump() for turn in result.scenario.history]
            key = content_hash(
                result.criterion.id,
                result.scenario.message,
                history,
                result.scenario.expected,
                record.response,
            )
            if key in entries:
                merged = entries[key]
                merged.reasons = sorted(set(merged.reasons) | set(reasons), key=REASON_ORDER.index)
                continue
            entries[key] = QueueEntry(
                key,
                run_id,
                result.scenario.id,
                result.scenario.name,
                result.criterion.id,
                result.scenario.message,
                history,
                [turn.model_dump() for turn in record.transcript],
                result.scenario.expected,
                record.response,
                "pass" if record.verdict.passed else "fail",
                record.verdict.reason,
                record.verdict.model,
                sorted(set(reasons), key=REASON_ORDER.index),
                [outcome.to_dict() for outcome in record.checks],
                record.attempt,
                early_stopped,
            )
    return list(reversed(entries.values()))


# This run's entries first, then whatever earlier runs left unlabelled, capped.
def merge_queue(
    fresh: Sequence[QueueEntry], existing: Sequence[QueueEntry], cap: int
) -> list[QueueEntry]:
    seen = {entry.id for entry in fresh}
    merged = list(fresh)
    for entry in existing:
        if entry.id not in seen:
            merged.append(entry)
            seen.add(entry.id)
    return merged[:cap]


def read_queue(path: Path) -> list[QueueEntry]:
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(QueueEntry.from_dict(json.loads(line)))
    return entries


def write_queue(path: Path, entries: Sequence[QueueEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n" for entry in entries)
    path.write_text(text, encoding="utf-8")


def case_dict(entry: QueueEntry, verdict: str, note: str) -> dict[str, Any]:
    case: dict[str, Any] = {
        "name": entry.case_name,
        "message": entry.message,
    }
    if entry.history:
        case["history"] = entry.history
    case.update({"expected": entry.expected, "response": entry.response, "verdict": verdict})
    if note:
        case["note"] = note
    case["source"] = entry.run
    return case


def append_case(calibration_dir: Path, entry: QueueEntry, verdict: str, note: str = "") -> Path:
    path = calibration_dir / FROM_RUNS_DIR / f"{entry.criterion}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {"criterion": entry.criterion, "cases": []}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            document = loaded
            document.setdefault("cases", [])
    document["cases"].append(case_dict(entry, verdict, note))
    header = (
        "# Labelled with `juried label` from real runs. Each case names the run it came from.\n"
    )
    path.write_text(
        header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=88),
        encoding="utf-8",
    )
    return path


def describe_entry(entry: QueueEntry, index: int, total: int) -> str:
    lines = [
        f"[{index}/{total}] {entry.criterion}: {entry.name}   why: {', '.join(entry.reasons)}"
        + ("   (early stopped run)" if entry.early_stopped else "")
    ]
    lines.extend(f"  {turn['role']}: {turn['content']}" for turn in entry.history)
    lines.extend(f"  {turn['role']} (live): {turn['content']}" for turn in entry.transcript)
    lines.append(f"  user: {entry.message}")
    lines.append(f"  expected: {entry.expected}")
    lines.append(f"  response: {entry.response}")
    if entry.checks:
        lines.append(
            "  checks: "
            + "; ".join(
                f"{c['kind']} {c['value']!r}: {'passed' if c['passed'] else c['reason']}"
                for c in entry.checks
            )
        )
    # The judge's view comes last so the labeller decides before seeing it.
    lines.append(f"  judge ({entry.model}): {entry.verdict}: {entry.reason}")
    return "\n".join(lines)


@dataclass
class SessionOutcome:
    labelled: list[tuple[QueueEntry, str]] = field(default_factory=list)
    skipped: list[QueueEntry] = field(default_factory=list)
    remaining: list[QueueEntry] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


def run_session(
    entries: Sequence[QueueEntry],
    calibration_dir: Path,
    stdin: IO[str],
    stdout: IO[str],
) -> SessionOutcome:
    outcome = SessionOutcome()
    pending = list(entries)
    total = len(pending)
    index = 0
    while pending:
        entry = pending[0]
        index += 1
        stdout.write(describe_entry(entry, index, total) + "\n")
        note = ""
        while True:
            stdout.write("  [p]ass  [f]ail  [s]kip  [n]ote  [q]uit > ")
            stdout.flush()
            key = stdin.readline()
            if not key:
                key = "q"
            key = key.strip().lower()[:1]
            if key == "n":
                stdout.write("  note: ")
                stdout.flush()
                note = stdin.readline().strip()
                continue
            if key in ("p", "f"):
                verdict = "pass" if key == "p" else "fail"
                path = append_case(calibration_dir, entry, verdict, note)
                if path not in outcome.files:
                    outcome.files.append(path)
                outcome.labelled.append((entry, verdict))
                stdout.write(f"  labelled {verdict}, written to {path}\n\n")
                pending.pop(0)
                break
            if key == "s":
                outcome.skipped.append(pending.pop(0))
                stdout.write("  skipped\n\n")
                break
            if key == "q":
                outcome.remaining = pending
                return outcome
            stdout.write("  p, f, s, n or q\n")
    return outcome


def render_html(entries: Sequence[QueueEntry]) -> str:
    rows = []
    for entry in entries:
        conversation = "".join(
            f"<p><b>{html.escape(t['role'])}:</b> {html.escape(t['content'])}</p>"
            for t in [*entry.history, *entry.transcript]
        )
        checks = "; ".join(
            f"{c['kind']} {c['value']}: {'passed' if c['passed'] else c['reason']}"
            for c in entry.checks
        )
        rows.append(
            f'<tr id="case-{entry.id}" data-id="{entry.id}">'
            f"<td>{html.escape(entry.id)}</td>"
            f"<td>{html.escape(entry.criterion)}<br><small>{html.escape(entry.name)}</small>"
            f"<br><small>{html.escape(', '.join(entry.reasons))}</small></td>"
            f"<td>{conversation}<p><b>user:</b> {html.escape(entry.message)}</p></td>"
            f"<td>{html.escape(entry.expected)}</td>"
            f"<td>{html.escape(entry.response)}"
            + (f"<br><small>checks: {html.escape(checks)}</small>" if checks else "")
            + "</td>"
            f"<td><small>{html.escape(entry.verdict or '')}: "
            f"{html.escape(entry.reason)}</small></td>"
            '<td class="verdict"></td><td class="note"></td></tr>'
        )
    return (
        '<!doctype html>\n<html lang="en-GB"><head><meta charset="utf-8">'
        "<title>juried label queue</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;color:#1f2933}"
        "table{border-collapse:collapse;width:100%;font-size:.9rem}"
        "th,td{border:1px solid #d9dee3;padding:.4rem .6rem;vertical-align:top;text-align:left}"
        "th{background:#f5f7fa}td.verdict,td.note{background:#fffbe6;min-width:6rem}"
        "p{margin:.2rem 0}</style></head><body>"
        "<h1>juried label queue</h1>"
        "<p>Decide pass or fail for each response before reading the judge's column. Then "
        "either type <code>pass</code> or <code>fail</code> and a note into the two shaded "
        "cells of each row in a text editor, or fill in <code>label-queue.csv</code>, and run "
        "<code>juried label --import</code> on the file. Rows left blank stay in the queue.</p>"
        f"<p>{len(entries)} response(s).</p>"
        "<table><thead><tr><th>id</th><th>criterion</th><th>conversation</th><th>expected</th>"
        "<th>response</th><th>judge (read last)</th><th>verdict</th><th>note</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></body></html>\n"
    )


def render_csv(entries: Sequence[QueueEntry]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "verdict", "note", "criterion", "scenario", "response"])
    for entry in entries:
        writer.writerow([entry.id, "", "", entry.criterion, entry.name, entry.response])
    return buffer.getvalue()


class _MarkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.marks: dict[str, dict[str, str]] = {}
        self._row: str | None = None
        self._cell: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr" and attributes.get("data-id"):
            self._row = str(attributes["data-id"])
            self.marks[self._row] = {"verdict": "", "note": ""}
        elif tag == "td" and self._row and attributes.get("class") in ("verdict", "note"):
            self._cell = str(attributes["class"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._cell = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._row and self._cell:
            self.marks[self._row][self._cell] += data


def read_marks(path: Path) -> dict[str, tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    raw: dict[str, dict[str, str]]
    if path.suffix.lower() == ".csv":
        raw = {
            str(row.get("id", "")).strip(): {
                "verdict": str(row.get("verdict", "")),
                "note": str(row.get("note", "")),
            }
            for row in csv.DictReader(io.StringIO(text))
        }
    else:
        parser = _MarkParser()
        parser.feed(text)
        raw = parser.marks
    marks: dict[str, tuple[str, str]] = {}
    for case_id, cells in raw.items():
        verdict = cells["verdict"].strip().lower()
        if verdict in ("pass", "fail"):
            marks[case_id] = (verdict, " ".join(cells["note"].split()))
    return marks


def import_marks(
    entries: Sequence[QueueEntry], marks: dict[str, tuple[str, str]], calibration_dir: Path
) -> SessionOutcome:
    outcome = SessionOutcome()
    for entry in entries:
        if entry.id in marks:
            verdict, note = marks[entry.id]
            path = append_case(calibration_dir, entry, verdict, note)
            if path not in outcome.files:
                outcome.files.append(path)
            outcome.labelled.append((entry, verdict))
        else:
            outcome.remaining.append(entry)
    return outcome


def summarise_sources(sources: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for source in sources:
        counts[source] = counts.get(source, 0) + 1
    hand = counts.pop("hand", 0)
    parts = [f"{hand} hand labelled"]
    from_runs = sum(counts.values())
    if from_runs:
        parts.append(f"{from_runs} from {len(counts)} run(s) via juried label")
    return ", ".join(parts)
