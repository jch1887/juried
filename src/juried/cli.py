from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from juried import __version__
from juried.calibrate import (
    CalibrationError,
    load_calibration,
    run_calibration,
    write_calibration_report,
)
from juried.compare import CompareError, compare_reports, comparison_dict, load_report
from juried.config import CONFIG_FILENAME, Config, ConfigError, find_config, load_config
from juried.criteria import CriteriaError, load_criteria
from juried.generate import generate_scenarios
from juried.judge import ProviderError, build_provider
from juried.pricing import describe_usage
from juried.scenarios import ScenarioError
from juried.targets.http import TargetConfigError
from juried.transport import TransportFailure

INIT_CONFIG = """# juried configuration. Any value can be overridden with an environment variable
# named JURIED_<SECTION>_<KEY>, for example JURIED_RUN_RUNS=20.

[target]
# A value that is exactly "{{message}}" or "{{history}}" becomes the scenario message
# or the earlier turns as a list of {role, content}; inside longer text {{message}} is
# replaced with the message and {{history}} with the turns as JSON. url, headers and
# body may reference environment variables as ${NAME}.
url = "http://127.0.0.1:8765/chat"
method = "POST"
headers = {}
body = { message = "{{message}}", history = "{{history}}" }
# Dotted path to the reply text in the JSON response, e.g. "choices.0.message.content".
response_path = "reply"
timeout_seconds = 30

[criteria]
file = "acceptance.md"
scenarios_dir = "scenarios"
# Human labelled responses for 'juried calibrate', which measures how often the judge
# agrees with your team.
calibration_dir = "calibration"

[run]
# Each scenario runs this many times. Its gate passes when the lower bound of the
# Wilson 95% interval on the pass rate meets the threshold. The threshold is not a
# pass rate: with runs = 10 and threshold = 0.7 all 10 runs must pass, since 9/10
# has a lower bound of 0.60. 20 runs tolerate 1 miss, 50 runs tolerate 8. With 10
# runs the best possible lower bound is 0.72, so a higher threshold needs more runs.
# juried prints what the gate needs at the start of every run.
runs = 10
threshold = 0.7
# All scenarios run together; this caps requests in flight to the target across them.
concurrency = 4
cache_dir = ".juried"
# Verdicts are cached by content so an unchanged response is not judged twice. Responses
# are sampled fresh on every run unless this is true, which replays saved responses and
# so stops the run from detecting flakiness. Keep it false in CI.
cache_responses = false
report_dir = "reports"

[judge]
# "anthropic" reads ANTHROPIC_API_KEY, "openai" reads OPENAI_API_KEY, "stub" needs
# no key and passes when the response contains every "quoted phrase" in expected.
provider = "anthropic"
model = "claude-sonnet-5"
# Set temperature only for a model that accepts it; claude-sonnet-5 rejects the parameter.
# temperature = 0.0
# Each response is judged once. Set an odd number above 1 to judge it that many times and
# take the majority; the report then shows how often the votes split.
votes = 1
# Every run reports the judge's token usage and an estimated spend from a dated table of
# list prices. Set both to override the table, in US dollars per million tokens.
# input_price = 2.0
# output_price = 10.0
# Judge requests in flight across all scenarios, independent of the target cap above.
concurrency = 4

[generate]
# provider and model default to the judge settings. temperature does not: generation
# wants variety, so leave it unset for the model's default or set one here.
# temperature = 1.0
scenarios_per_criterion = 4
"""

INIT_CRITERIA = """# Acceptance criteria

One `##` heading per criterion. The heading becomes the criterion id
(`opening-hours` below). Add `{#custom-id}` after a heading to fix the id.

## Opening hours
The assistant tells customers the opening hours: Monday to Friday, 9am to 5pm,
and says that it is closed at weekends.

## Unknown questions
When the assistant does not know the answer it says so and points the customer
to help@example.com rather than guessing.
"""

INIT_CALIBRATION = """# Responses your team has judged by hand, for `juried calibrate`.
#
# Do not trust the judge until it has been checked here. Collect real responses from your
# feature, decide pass or fail yourselves, and record them below; then run
# `juried calibrate` and read every disagreement. Label at least a handful of cases per
# criterion, including borderline responses and ones that use the right words for the
# wrong reason. Rerun calibrate whenever the judge model, temperature or prompt changes.
#
# The two cases below only show the shape. Replace them with your own.
criterion: opening-hours
cases:
  - name: Full answer
    message: When are you open?
    expected: Gives the weekday hours "9am" to "5pm" and says it is closed at weekends.
    response: We are open Monday to Friday, 9am to 5pm, and closed at weekends.
    verdict: pass

  - name: Right words, wrong answer
    message: When are you open?
    expected: Gives the weekday hours "9am" to "5pm" and says it is closed at weekends.
    response: We are never open at 9am or 5pm on weekdays, only at weekends.
    verdict: fail
    note: contains both phrases while contradicting the criterion
"""

INIT_GITIGNORE = ".juried/\nreports/\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="juried", description="Acceptance testing for LLM features, built for QA teams."
    )
    parser.add_argument("--version", action="version", version=f"juried {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="write a starter juried.toml and criteria file")
    init.add_argument("--dir", default=".", help="directory to initialise (default: current)")
    init.add_argument("--force", action="store_true", help="overwrite existing files")

    generate = commands.add_parser("generate", help="turn acceptance criteria into scenarios")
    generate.add_argument("--config", help=f"path to {CONFIG_FILENAME}")
    generate.add_argument(
        "--criterion", action="append", metavar="ID", help="only this criterion (repeatable)"
    )
    generate.add_argument("--force", action="store_true", help="overwrite generated files")

    calibrate = commands.add_parser(
        "calibrate", help="judge human labelled responses and report how often the judge agrees"
    )
    calibrate.add_argument("--config", help=f"path to {CONFIG_FILENAME}")
    calibrate.add_argument(
        "--min-accuracy",
        type=float,
        metavar="RATE",
        help="exit with status 1 when the judge agrees with fewer labels than this fraction",
    )

    compare = commands.add_parser(
        "compare", help="compare two JSON reports and flag scenarios that got worse"
    )
    compare.add_argument("old", help="the earlier juried-report.json")
    compare.add_argument("new", help="the later juried-report.json")
    compare.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        metavar="RATE",
        help="ignore pass rate or lower bound drops up to this much (default 0)",
    )
    compare.add_argument("--json", metavar="PATH", help="also write the comparison as JSON")

    run = commands.add_parser("run", help="run scenarios with pytest and write the report")
    run.add_argument("--config", help=f"path to {CONFIG_FILENAME}")
    run.add_argument("--runs", type=int, help="override run.runs")
    run.add_argument("--threshold", type=float, help="override run.threshold")
    run.add_argument(
        "--no-cache", action="store_true", help="ignore cached verdicts (and responses)"
    )
    run.add_argument(
        "--cache-responses",
        action="store_true",
        help="replay responses from .juried/cache/responses instead of sampling the feature",
    )
    run.epilog = (
        "Unrecognised arguments are passed to pytest, e.g. -k refunds -x --junitxml=out.xml"
    )
    return parser


def locate_config(explicit: str | None) -> tuple[Path, Config]:
    path = Path(explicit) if explicit else find_config()
    if path is None:
        raise ConfigError(f"no {CONFIG_FILENAME} found here or in a parent directory")
    return path, load_config(path)


def command_init(directory: Path, force: bool) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        directory / CONFIG_FILENAME: INIT_CONFIG,
        directory / "acceptance.md": INIT_CRITERIA,
        directory / "calibration" / "example.yaml": INIT_CALIBRATION,
    }
    for path, content in files.items():
        if path.exists() and not force:
            print(f"kept existing {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")
    (directory / "scenarios").mkdir(exist_ok=True)
    gitignore = directory / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    missing = [line for line in INIT_GITIGNORE.splitlines() if line not in existing.splitlines()]
    if missing:
        with gitignore.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(missing) + "\n")
        print(f"updated {gitignore}")
    print(
        "next: edit acceptance.md, set [target] in juried.toml, run 'juried generate', and "
        "label real responses in calibration/ before trusting the judge ('juried calibrate')"
    )
    return 0


def command_generate(explicit: str | None, only: list[str] | None, force: bool) -> int:
    _, config = locate_config(explicit)
    criteria = load_criteria(config.criteria_path)
    if only:
        unknown = set(only) - {criterion.id for criterion in criteria}
        if unknown:
            raise CriteriaError(f"unknown criteria: {', '.join(sorted(unknown))}")
    provider = build_provider(
        config.generate_provider,
        config.generate_model,
        config.generate_temperature,
        config.generate.max_tokens,
        config.generate_base_url,
    )
    print(f"generating scenarios with {provider.name}/{provider.model}")
    outcome = generate_scenarios(
        config, criteria, provider, force=force, only=set(only) if only else None
    )
    for path in outcome.skipped:
        print(f"kept existing {path} (use --force to regenerate)")
    for path in outcome.written:
        criterion_id = path.stem
        print(f"wrote {path} ({outcome.counts[criterion_id]} scenarios)")
    print("usage: " + describe_usage(provider.usage_total, provider.model, config.generate_prices))
    if outcome.written:
        print("review and edit the generated files, then commit them and run 'juried run'")
    return 0


def command_calibrate(explicit: str | None, min_accuracy: float | None) -> int:
    _, config = locate_config(explicit)
    criteria = load_criteria(config.criteria_path)
    cases = load_calibration(config.calibration_path, {c.id: c for c in criteria})
    judge = config.judge
    provider = build_provider(
        judge.provider, judge.model, judge.temperature, judge.max_tokens, judge.base_url
    )
    votes = f", {judge.votes} votes each" if judge.votes > 1 else ""
    print(
        f"calibrating {provider.name}/{provider.model} against {len(cases)} labelled "
        f"response(s){votes}"
    )
    result = run_calibration(config, criteria, provider, cases)
    for outcome in result.disagreements:
        note = f" [{outcome.case.note}]" if outcome.case.note else ""
        print(
            f"  {outcome.kind.replace('_', ' ')}: {outcome.case.id}: human says "
            f"{outcome.case.verdict}, judge says {outcome.to_dict()['judge']}: "
            f"{outcome.verdict.reason}{note}"
        )
    print(
        f"judge agreed with the human label on {result.agreed}/{result.total} "
        f"({result.accuracy:.2f}): {result.false_passes} false pass(es), "
        f"{result.false_fails} false fail(s)"
    )
    if judge.votes > 1:
        print(f"judge votes were unanimous on {result.unanimous}/{result.total}")
    print(f"usage: {describe_usage(provider.usage_total, provider.model, judge.prices)}")
    path = write_calibration_report(result, config.report_path)
    print(f"calibration: {path}")
    if min_accuracy is not None and result.accuracy < min_accuracy:
        print(f"juried: judge accuracy {result.accuracy:.2f} is below {min_accuracy:.2f}")
        return 1
    return 0


def command_compare(old: str, new: str, tolerance: float, json_path: str | None) -> int:
    old_path, new_path = Path(old), Path(new)
    changes = compare_reports(load_report(old_path), load_report(new_path), tolerance)
    regressions = [change for change in changes if change.regression]
    improvements = [change for change in changes if change.kind in ("gate regained", "improved")]
    neutral = [change for change in changes if change.kind in ("added", "removed")]
    unchanged = sum(1 for change in changes if change.kind == "unchanged")
    print(f"comparing {old_path} -> {new_path}")
    for label, group in (
        ("regressions", regressions),
        ("improvements", improvements),
        ("other changes", neutral),
    ):
        if group:
            print(f"{label}:")
            for change in group:
                print(f"  {change.kind}: {change.id}: {change.detail}")
    print(
        f"{len(regressions)} regression(s), {len(improvements)} improvement(s), "
        f"{len(neutral)} added or removed, {unchanged} unchanged"
    )
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = comparison_dict(old_path, new_path, changes, tolerance)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"comparison: {path}")
    return 1 if regressions else 0


def command_run(
    explicit: str | None,
    runs: int | None,
    threshold: float | None,
    no_cache: bool,
    cache_responses: bool,
    pytest_args: Sequence[str],
) -> int:
    path, config = locate_config(explicit)
    args = [f"--juried-config={path}", f"--rootdir={path.parent}", "-v"]
    if runs is not None:
        args.append(f"--juried-runs={runs}")
    if threshold is not None:
        args.append(f"--juried-threshold={threshold}")
    if no_cache:
        args.append("--juried-no-cache")
    if cache_responses:
        args.append("--juried-cache-responses")
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        args.extend(["-p", "juried.pytest_plugin"])
    extra = [arg for arg in pytest_args if arg != "--"]
    if not any(not arg.startswith("-") and Path(arg).exists() for arg in extra):
        args.append(str(config.scenarios_path))
    return int(pytest.main([*args, *extra]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "run":
        parser.error(f"unrecognised arguments: {' '.join(extra)}")
    try:
        if args.command == "init":
            return command_init(Path(args.dir), args.force)
        if args.command == "generate":
            return command_generate(args.config, args.criterion, args.force)
        if args.command == "calibrate":
            return command_calibrate(args.config, args.min_accuracy)
        if args.command == "compare":
            return command_compare(args.old, args.new, args.tolerance, args.json)
        return command_run(
            args.config, args.runs, args.threshold, args.no_cache, args.cache_responses, extra
        )
    except (
        ConfigError,
        CriteriaError,
        ScenarioError,
        TargetConfigError,
        CalibrationError,
        CompareError,
        ProviderError,
        TransportFailure,
    ) as exc:
        print(f"juried: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
