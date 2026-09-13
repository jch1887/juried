import json
from html.parser import HTMLParser
from pathlib import Path

from juried.config import Config, parse_config
from juried.criteria import Criterion
from juried.judge import Verdict
from juried.report import build_report, render_html, write_reports
from juried.runner import RunRecord, ScenarioResult
from juried.scenarios import Scenario, Turn
from juried.stats import Gate

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
            Gate(3, 0),
            [RunRecord(i, "Open 9am to 5pm.", verdict=verdict(True, "ok")) for i in range(1, 4)],
        ),
        ScenarioResult(
            refunds,
            REFUNDS,
            Gate(3, 1),
            [
                RunRecord(
                    1, "Refunds within 14 days.", verdict=verdict(True, "ok"), response_ms=820.4
                ),
                RunRecord(
                    2,
                    "<script>alert(1)</script> Refunds are possible.",
                    verdict=verdict(False, "does not mention 14 days"),
                    response_ms=1310.0,
                ),
                RunRecord(3, error="HTTP 500 from http://bot/chat: boom"),
            ],
        ),
    ]


def config(tmp_path: Path) -> Config:
    return parse_config('[target]\nurl = "http://bot/chat"\n[judge]\nprovider = "stub"\n', tmp_path)


def test_build_report_structure(tmp_path: Path) -> None:
    report = build_report(config(tmp_path), [HOURS, REFUNDS, UNCOVERED], results())
    assert report["tool"] == "juried"
    assert report["schema_version"] == 1
    assert report["judge"] == {
        "provider": "stub",
        "model": "stub",
        "temperature": None,
        "votes": 1,
        "prices_usd_per_million": [0.0, 0.0],
    }
    assert report["summary"] == {
        "criteria": 3,
        "scenarios": 2,
        "gates_passed": 1,
        "gates_failed": 0,
        "incomplete": 1,
        "transport_errors": 1,
        "responses_from_cache": 0,
        "split_verdicts": 0,
        "judge_errors": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0, "estimated_cost_usd": 0.0},
        "criteria_without_scenarios": ["tone"],
    }
    assert report["criteria"][0]["scenarios"][0]["usage"]["estimated_cost_usd"] == 0.0
    assert [c["id"] for c in report["criteria"]] == ["hours", "refunds", "tone"]
    hours = report["criteria"][0]
    assert hours["gates_passed"] == 1
    scenario = hours["scenarios"][0]
    assert scenario["passes"] == 3
    assert scenario["interval"]["lower"] == 0.4385
    assert scenario["gate_passed"] is True
    assert scenario["required_passes"] == 3
    assert scenario["misses"] == 0
    assert scenario["threshold"] == 0.4385
    assert report["defaults"] == {
        "runs": 20,
        "misses": 1,
        "required_passes": 19,
        "threshold": 0.7639,
    }
    assert scenario["history"][0]["content"] == "hi"
    assert report["criteria"][1]["incomplete"] == 1
    assert report["criteria"][1]["gates_failed"] == 0
    refunds = report["criteria"][1]["scenarios"][0]
    assert refunds["gate_passed"] is False
    assert refunds["status"] == "incomplete"
    assert refunds["quality_met"] is True
    assert refunds["misses"] == 1
    assert refunds["required_passes"] == 2
    assert refunds["judged"] == 2
    assert refunds["pass_rate"] == 0.5
    assert [f["attempt"] for f in refunds["failures"]] == [2, 3]
    assert refunds["failures"][0]["reason"] == "does not mention 14 days"
    assert refunds["failures"][0]["judged_at"] == "2026-09-12T10:00:00+00:00"
    assert refunds["failures"][1]["outcome"] == "transport_error"
    assert refunds["failures"][1]["model"] is None
    assert refunds["failures"][0]["agreement"] == 1.0
    assert refunds["failures"][1]["agreement"] is None
    assert refunds["judge_agreement"] == 1.0
    assert refunds["latency"] == {"measured": 2, "mean_ms": 1065.2, "max_ms": 1310.0}
    assert scenario["latency"]["measured"] == 0


class Checker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.open: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts += 1
        if tag not in {"meta", "br", "link", "img"}:
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
    assert '<img src="data:image/png;base64,iVBOR' in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Asks about a refund &lt;late&gt;" in html
    assert "Opening hours" in html
    assert "does not mention 14 days" in html
    assert "HTTP 500 from http://bot/chat: boom" in html
    assert "Criteria with no scenarios: tone." in html
    assert "1 / 2" in html
    assert "1 transport</span>" in html
    assert "judge errors" in html
    assert "gates upheld" in html
    assert "temperature not set, one verdict per response" in html
    assert "judge spend" in html
    assert "$0.0000" in html
    assert "No list price is known" not in html
    assert "split verdicts" not in html
    assert '<th class="num">Gate needs</th>' in html
    assert '<th class="num">Interval</th>' in html
    assert '<td class="num">44% to 100%</td>' in html
    assert '<td class="num">3 / 3<br><span class="meta">0 misses</span></td>' in html
    assert '<td class="num">2 / 3<br><span class="meta">1 miss</span></td>' in html
    assert "gate needs 19 of 20 to pass (1 miss tolerated)" in html
    assert "threshold" not in html.lower()
    rule = 'A scenario is upheld when at least the number of runs under "Gate needs"'
    assert html.count(rule) == 1
    assert ">upheld</td>" in html
    assert 'style="grid-template-columns: repeat(8, minmax(0, 1fr));"' in html
    assert ">incomplete</td>" in html
    assert ">failed</td>" not in html
    assert '<th class="num">Runs upheld / judged</th>' in html
    assert '<td class="num bound">1 / 2<br>' in html
    assert '<th class="num">Passes</th>' not in html
    assert ">pass<" not in html and ">fail<" not in html
    assert html.count('<span class="meta">hours-happy</span>') == 0
    assert "(hours-happy)" in html
    assert "attempt 3</span>transport error</p>" in html
    assert "attempt 2</span>failed</p>" in html
    assert "1065 ms" in html
    assert "max 1310 ms" in html
    assert html.count('<span class="meta">not measured</span>') == 1
    assert "replayed" not in html
    assert "<details open>" in html
    assert 'href="http' not in html
    assert '<p><span class="label">assistant</span>hello</p>' in html


def test_write_reports(tmp_path: Path) -> None:
    paths = write_reports(config(tmp_path), [HOURS, REFUNDS], results(), tmp_path / "out")
    assert paths.json == tmp_path / "out" / "juried-report.json"
    assert paths.html == tmp_path / "out" / "juried-report.html"
    data = json.loads(paths.json.read_text())
    assert data["summary"]["scenarios"] == 2
    assert paths.html.read_text().startswith("<!doctype html>")


def test_optional_tiles_split_into_two_even_rows(tmp_path: Path) -> None:
    voting = parse_config(
        '[target]\nurl = "http://bot/chat"\n[judge]\nprovider = "stub"\nvotes = 3\n', tmp_path
    )
    html = render_html(build_report(voting, [HOURS, REFUNDS], results()))
    assert "split verdicts" in html
    assert 'style="grid-template-columns: repeat(5, minmax(0, 1fr));"' in html
