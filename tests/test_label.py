import io
import json
import random
from pathlib import Path

import pytest
import yaml

from juried.calibrate import load_calibration
from juried.checks import CheckOutcome
from juried.cli import main
from juried.criteria import Criterion
from juried.judge import Verdict
from juried.label import (
    HEDGES,
    attempt_reasons,
    build_queue,
    hedged,
    import_marks,
    merge_queue,
    read_marks,
    read_queue,
    render_csv,
    render_html,
    run_session,
    summarise_sources,
    write_queue,
)
from juried.runner import RunRecord, ScenarioResult
from juried.scenarios import Check, Scenario
from juried.stats import Gate

HOURS = Criterion("hours", "Opening hours", "States the hours.")


def verdict(passed: bool, reason: str = "clear", votes: list[bool] | None = None) -> Verdict:
    return Verdict(passed, reason, "m", "t")


def record(
    attempt: int,
    passed: bool,
    reason: str = "clear",
    votes: list[bool] | None = None,
    checks: list[CheckOutcome] | None = None,
    response: str | None = None,
) -> RunRecord:
    rec = RunRecord(attempt, response or f"response {attempt}", verdict=verdict(passed, reason))
    if votes is not None:
        rec.votes = [verdict(v) for v in votes]
    if checks is not None:
        rec.checks = checks
    return rec


def result(records: list[RunRecord], misses: int = 1, name: str = "Asks hours") -> ScenarioResult:
    scenario = Scenario(
        id=f"hours-{name.lower().replace(' ', '-')}",
        criterion="hours",
        name=name,
        message="When are you open?",
        expected="Gives the hours.",
    )
    return ScenarioResult(scenario, HOURS, Gate(len(records), misses), records)


def test_hedges_are_matched_as_whole_words() -> None:
    assert hedged("This probably meets the expectation")
    assert hedged("It is hard to say whether the hours are given")
    assert not hedged("The response is clear and complete")
    assert not hedged("improbably good")  # "probably" inside another word does not count
    assert "borderline" in HEDGES


def test_each_reason_is_detected() -> None:
    passed_check = CheckOutcome(Check(contains="9am"), True, "found")
    split = record(1, True, votes=[True, True, False])
    hedge = record(2, True, reason="This seems to give the hours")
    disagreed = record(3, False, checks=[passed_check])
    plain = record(4, True)
    # Four attempts, one miss tolerated, two fails: one over the gate, so on the boundary.
    res = result([split, hedge, disagreed, record(5, False)], misses=1)
    assert attempt_reasons(res, split) == ["split"]
    assert attempt_reasons(res, hedge) == ["low_confidence"]
    assert attempt_reasons(res, disagreed) == ["boundary", "check_disagreed"]
    assert attempt_reasons(res, res.runs[3]) == ["boundary"]
    assert attempt_reasons(res, plain) == []
    comfortable = result([record(1, True), record(2, True), record(3, True)], misses=1)
    assert attempt_reasons(comfortable, comfortable.runs[0]) == []


def test_build_queue_samples_dedupes_and_orders_newest_first() -> None:
    records = [record(i, True) for i in range(1, 51)]
    res = result(records, misses=1)
    queue = build_queue([res], "run-1", sample_rate=0.1, rng=random.Random(3))
    assert queue and all(entry.reasons == ["sampled"] for entry in queue)
    assert len(queue) < 15
    assert [e.attempt for e in queue] == sorted((e.attempt for e in queue), reverse=True)
    assert build_queue([res], "run-1", sample_rate=0.0) == []
    same = build_queue([res], "run-1", sample_rate=0.1, rng=random.Random(3))
    assert [e.id for e in same] == [e.id for e in queue]
    # Two attempts with the same response collapse into one entry with merged reasons.
    twice = result(
        [
            record(1, True, votes=[True, True, False], response="same words"),
            record(2, True, reason="probably fine", response="same words"),
            record(3, False),
        ],
        misses=0,
    )
    merged = build_queue([twice], "run-2", sample_rate=0.0)
    assert [e.reasons for e in merged] == [["boundary"], ["split", "low_confidence"]]
    assert merged[1].verdict == "pass" and merged[1].case_name.endswith(f"#{merged[1].id[:6]})")
    assert merged[0].run == "run-2"
    # Errored and check decided attempts are not queued.
    errored = RunRecord(1, error="HTTP 500")
    by_check = RunRecord(2, "x", verdict=Verdict(False, "check", "checks", "t"))
    assert build_queue([result([errored, by_check], misses=0)], "r", 1.0) == []


def test_merge_queue_keeps_earlier_entries_and_caps(tmp_path: Path) -> None:
    older = build_queue([result([record(1, True), record(2, False)], misses=0)], "old", 0.0)
    newer = build_queue([result([record(3, False), record(4, True)], misses=0)], "new", 0.0)
    merged = merge_queue(newer, older, cap=50)
    assert [e.run for e in merged] == ["new", "old"]
    assert merge_queue(newer, older, cap=1) == newer[:1]
    assert merge_queue(newer, newer, cap=5) == newer
    path = tmp_path / "queue.jsonl"
    write_queue(path, merged)
    assert read_queue(path) == merged
    assert read_queue(tmp_path / "missing.jsonl") == []


def test_terminal_session_writes_calibration_yaml(tmp_path: Path) -> None:
    res = result(
        [record(1, False, reason="misses 5pm"), record(2, False), record(3, True)], misses=1
    )
    queue = build_queue([res], "2026-09-14T09:00:00+00:00", sample_rate=0.0)
    assert len(queue) == 2
    stdin = io.StringIO("n\nsays nine\np\nx\nf\n")
    stdout = io.StringIO()
    outcome = run_session(queue, tmp_path / "calibration", stdin, stdout)
    text = stdout.getvalue()
    assert text.index("expected:") < text.index("response:") < text.index("judge (m): fail")
    assert "[1/2] hours: Asks hours   why: boundary" in text
    assert "p, f, s, n or q" in text
    assert [v for _, v in outcome.labelled] == ["pass", "fail"]
    assert outcome.remaining == [] and outcome.skipped == []
    path = tmp_path / "calibration" / "from-runs" / "hours.yaml"
    assert outcome.files == [path]
    document = yaml.safe_load(path.read_text())
    assert document["criterion"] == "hours"
    assert [c["verdict"] for c in document["cases"]] == ["pass", "fail"]
    assert document["cases"][0]["note"] == "says nine"
    assert "note" not in document["cases"][1]
    assert document["cases"][0]["source"] == "2026-09-14T09:00:00+00:00"
    assert document["cases"][0]["name"].startswith("Asks hours (2026-09-14 #")
    # Skips are dropped and quitting keeps the rest; the file appends across sessions.
    again = build_queue(
        [
            result(
                [record(1, False, response="a"), record(2, False, response="b"), record(3, True)],
                misses=1,
            )
        ],
        "run-3",
        0.0,
    )
    quit_early = run_session(again, tmp_path / "calibration", io.StringIO("s\nq\n"), io.StringIO())
    assert len(quit_early.skipped) == 1 and len(quit_early.remaining) == 1
    eof = run_session(again, tmp_path / "calibration", io.StringIO("p\n"), io.StringIO())
    assert len(eof.labelled) == 1 and len(eof.remaining) == 1
    cases = load_calibration(tmp_path / "calibration", {"hours": HOURS})
    assert len(cases) == 3
    assert {c.source for c in cases} == {"2026-09-14T09:00:00+00:00", "run-3"}


def test_html_and_csv_round_trip(tmp_path: Path) -> None:
    queue = build_queue(
        [
            result(
                [record(1, False, response="<b>bold</b>"), record(2, False), record(3, True)],
                misses=1,
            )
        ],
        "run-4",
        0.0,
    )
    page = render_html(queue)
    assert "<script" not in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert page.count('class="verdict"') == 2
    # A reviewer types into the two cells in a text editor.
    marked = page.replace(
        f'<tr id="case-{queue[0].id}" data-id="{queue[0].id}">',
        f'<tr id="case-{queue[0].id}" data-id="{queue[0].id}">',
        1,
    )
    first_row_end = marked.index('<td class="verdict"></td><td class="note"></td>')
    marked = (
        marked[:first_row_end]
        + '<td class="verdict"> Fail </td><td class="note">drops the\n closing time</td>'
        + marked[first_row_end + len('<td class="verdict"></td><td class="note"></td>') :]
    )
    (tmp_path / "queue.html").write_text(marked)
    marks = read_marks(tmp_path / "queue.html")
    assert marks == {queue[0].id: ("fail", "drops the closing time")}
    outcome = import_marks(queue, marks, tmp_path / "calibration")
    assert [v for _, v in outcome.labelled] == ["fail"] and len(outcome.remaining) == 1
    sheet = render_csv(queue)
    assert sheet.splitlines()[0] == "id,verdict,note,criterion,scenario,response"
    filled = sheet.replace(f"{queue[1].id},,,", f"{queue[1].id},PASS,fine,")
    (tmp_path / "queue.csv").write_text(filled)
    assert read_marks(tmp_path / "queue.csv") == {queue[1].id: ("pass", "fine")}
    (tmp_path / "blank.csv").write_text(sheet)
    assert read_marks(tmp_path / "blank.csv") == {}


def test_calibrate_merges_hand_and_from_runs(
    tmp_path: Path,
    fake_bot_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "juried.toml").write_text(
        f'[target]\nurl = "{fake_bot_url}/chat"\n[judge]\nprovider = "stub"\n'
    )
    (tmp_path / "acceptance.md").write_text("# C\n\n## Hours\nStates the hours.\n")
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "hours.yaml").write_text(
        'criterion: hours\ncases:\n  - name: Hand\n    message: m\n    expected: Gives "9am".\n'
        "    response: 9am\n    verdict: pass\n"
    )
    (tmp_path / "calibration" / "from-runs").mkdir()
    (tmp_path / "calibration" / "from-runs" / "hours.yaml").write_text(
        'criterion: hours\ncases:\n  - name: Run one\n    message: m\n    expected: Gives "9am".\n'
        "    response: nine\n    verdict: fail\n    source: run-1\n"
        '  - name: Run two\n    message: m\n    expected: Gives "9am".\n'
        "    response: 9am sharp\n    verdict: pass\n    source: run-2\n"
    )
    monkeypatch.chdir(tmp_path)
    assert main(["calibrate"]) == 0
    out = capsys.readouterr().out
    assert (
        "calibrating stub/stub against 3 labelled response(s): 1 hand labelled, 2 from 2 run(s) "
        "via juried label"
    ) in out
    report = json.loads((tmp_path / "reports" / "juried-calibration.json").read_text())
    assert [c["origin"] for c in report["cases"]] == ["run-1", "run-2", "hand"]
    assert report["cases"][0]["source"].endswith("from-runs/hours.yaml")
    assert summarise_sources(["hand", "hand"]) == "2 hand labelled"


def test_label_command_end_to_end(
    tmp_path: Path,
    fake_bot_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "juried.toml").write_text(
        f'[target]\nurl = "{fake_bot_url}/chat"\n[run]\nruns = 4\nmisses = 0\n'
        '[judge]\nprovider = "stub"\n[label]\nsample_rate = 1.0\n'
    )
    (tmp_path / "acceptance.md").write_text("# C\n\n## Opening hours\nStates the hours.\n")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.yaml").write_text(
        "criterion: opening-hours\nscenarios:\n  - name: Asks hours\n    message: hours?\n"
        "    expected: Gives the opening time.\n"
    )
    monkeypatch.chdir(tmp_path)
    assert main(["label"]) == 0
    assert "nothing to label" in capsys.readouterr().out
    assert main(["run", "-q"]) == 0
    out = capsys.readouterr().out
    assert "label queue: 1 response(s) waiting for a human label" in out
    assert "calibration set: none under" in out
    queue = read_queue(tmp_path / ".juried" / "label-queue.jsonl")
    assert len(queue) == 1 and queue[0].reasons == ["sampled"]
    assert main(["label", "--html"]) == 0
    out = capsys.readouterr().out
    assert "label-queue.html" in out and "label-queue.csv" in out
    sheet = tmp_path / "reports" / "label-queue.csv"
    sheet.write_text(sheet.read_text().replace(f"{queue[0].id},,,", f"{queue[0].id},pass,ok,"))
    assert main(["label", "--import", str(sheet)]) == 0
    assert "imported 1 label(s)" in capsys.readouterr().out
    assert read_queue(tmp_path / ".juried" / "label-queue.jsonl") == []
    assert (tmp_path / "calibration" / "from-runs" / "opening-hours.yaml").is_file()
    assert main(["run", "-q"]) == 0
    out = capsys.readouterr().out
    assert (
        "calibration set: 1 labelled case(s) (0 hand labelled, 1 from 1 run(s) via juried "
        "label): opening-hours 1"
    ) in out
    # The terminal session, driven by stdin.
    monkeypatch.setattr("sys.stdin", io.StringIO("f\n"))
    assert main(["label"]) == 0
    out = capsys.readouterr().out
    assert "labelled 1, skipped 0, 0 left in the queue" in out
    assert "run 'juried calibrate'" in out
