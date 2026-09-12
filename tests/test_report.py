import json
from html.parser import HTMLParser
from pathlib import Path

from vouch.config import Config, parse_config
from vouch.criteria import Criterion
from vouch.judge import Verdict
from vouch.report import build_report, render_html, write_reports
from vouch.runner import RunRecord, ScenarioResult
from vouch.scenarios import Scenario, Turn

HOURS = Criterion("hours", "Opening hours", "States the hours.")
REFUNDS = Criterion("refunds", "Refund policy", "Refunds within 14 days.")
UNCOVERED = Criterion("tone", "Stays polite", "")


def verdict(passed: bool, reason: str) -> Verdict:
    return Verdict(passed, reason, "stub", "2026-09-12T10:00:00+00:00")


def results() -> list[ScenarioResult]:
    hours = Scenario(
        id="hours-happy",
        criterion="hours",
        name="Asks hours",
        kind="happy_path",
        message="When are you open?",
        expected='Mentions "9am".',
        history=[Turn(role="user", content="hi"), Turn(role="assistant", content="hello")],
    )
    refunds = Scenario(
        id="refunds-edge",
        criterion="refunds",
        name="Asks about a refund <late>",
        kind="edge_case",
        message="refund after 3 weeks?",
        expected='Mentions "14 days".',
    )
    return [
        ScenarioResult(
            hours,
            HOURS,
            0.4,
            [RunRecord(i, "Open 9am to 5pm.", verdict=verdict(True, "ok")) for i in range(1, 4)],
        ),
        ScenarioResult(
            refunds,
            REFUNDS,
            0.7,
            [
                RunRecord(1, "Refunds within 14 days.", verdict=verdict(True, "ok")),
                RunRecord(
                    2,
                    "<script>alert(1)</script> Refunds are possible.",
                    verdict=verdict(False, "does not mention 14 days"),
                ),
                RunRecord(3, error="HTTP 500 from http://bot/chat: boom"),
            ],
        ),
    ]


def config(tmp_path: Path) -> Config:
    return parse_config('[target]\nurl = "http://bot/chat"\n[judge]\nprovider = "stub"\n', tmp_path)


def test_build_report_structure(tmp_path: Path) -> None:
    report = build_report(config(tmp_path), [HOURS, REFUNDS, UNCOVERED], results())
    assert report["tool"] == "vouch"
    assert report["judge"] == {"provider": "stub", "model": "stub", "temperature": 0.0}
    assert report["summary"] == {
        "criteria": 3,
        "scenarios": 2,
        "gates_passed": 1,
        "gates_failed": 1,
        "transport_errors": 1,
        "criteria_without_scenarios": ["tone"],
    }
    assert [c["id"] for c in report["criteria"]] == ["hours", "refunds", "tone"]
    hours = report["criteria"][0]
    assert hours["gates_passed"] == 1
    scenario = hours["scenarios"][0]
    assert scenario["passes"] == 3
    assert scenario["interval"]["lower"] == 0.4385
    assert scenario["gate_passed"] is True
    assert scenario["history"][0]["content"] == "hi"
    refunds = report["criteria"][1]["scenarios"][0]
    assert refunds["gate_passed"] is False
    assert [f["attempt"] for f in refunds["failures"]] == [2, 3]
    assert refunds["failures"][0]["reason"] == "does not mention 14 days"
    assert refunds["failures"][0]["judged_at"] == "2026-09-12T10:00:00+00:00"
    assert refunds["failures"][1]["outcome"] == "transport_error"
    assert refunds["failures"][1]["model"] is None


class Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.open: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
        if tag not in {"meta", "br", "link"}:
            self.open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.open and self.open[-1] == tag, f"unbalanced {tag}, open {self.open[-3:]}"
        self.open.pop()


def test_render_html_is_self_contained_and_escaped(tmp_path: Path) -> None:
    report = build_report(config(tmp_path), [HOURS, REFUNDS, UNCOVERED], results())
    html = render_html(report)
    checker = Checker()
    checker.feed(html)
    assert checker.open == []
    assert checker.scripts == 0
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Asks about a refund &lt;late&gt;" in html
    assert "Opening hours" in html
    assert "does not mention 14 days" in html
    assert "HTTP 500 from http://bot/chat: boom" in html
    assert "Criteria with no scenarios: tone." in html
    assert "1 / 3" in html
    assert "44% to 100%" in html
    assert 'href="http' not in html
    assert '<p><span class="label">assistant</span>hello</p>' in html


def test_write_reports(tmp_path: Path) -> None:
    paths = write_reports(config(tmp_path), [HOURS, REFUNDS], results(), tmp_path / "out")
    assert paths.json == tmp_path / "out" / "vouch-report.json"
    assert paths.html == tmp_path / "out" / "vouch-report.html"
    data = json.loads(paths.json.read_text())
    assert data["summary"]["scenarios"] == 2
    assert paths.html.read_text().startswith("<!doctype html>")
