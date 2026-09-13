from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from juried import __version__
from juried.adversarial import criteria_for
from juried.calibrate import (
    CalibrationError,
    load_calibration,
    run_calibration,
    write_calibration_report,
)
from juried.compare import (
    DEFAULT_ALPHA,
    DEFAULT_MIN_EFFECT,
    NOISE,
    CompareError,
    check_same_schema,
    compare_reports,
    comparison_dict,
    load_report,
    power_of,
)
from juried.config import CONFIG_FILENAME, Config, ConfigError, find_config, load_config
from juried.criteria import CriteriaError
from juried.estimate import describe_plan, load_previous_report, plan_run
from juried.generate import generate_scenarios
from juried.judge import ProviderError, build_provider
from juried.pricing import describe_usage
from juried.report import JSON_NAME
from juried.scenarios import ScenarioError, load_scenarios
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
# For an endpoint that streams its reply: the event format and the dotted path to the
# text delta in each event. The deltas are joined into the response and the report shows
# the time to the first one beside the total latency.
# stream = true
# stream_format = "sse"
# stream_path = "choices.0.delta.content"
# A run costs target calls as well as judge calls. To price the target side, name the
# paths to the token counts in its reply and the prices in US dollars per million tokens,
# or set a flat price per call for a target that reports no tokens. Without either the
# run reports the request count and says the cost is unknown.
# usage_input_path = "usage.prompt_tokens"
# usage_output_path = "usage.completion_tokens"
# input_price = 2.0
# output_price = 10.0
# cost_per_request = 0.002

[criteria]
file = "acceptance.md"
scenarios_dir = "scenarios"
# Human labelled responses for 'juried calibrate', which measures how often the judge
# agrees with your team.
calibration_dir = "calibration"

[run]
# Each scenario runs this many times, and passes when no more than `misses` of those
# attempts fail: 20 runs with 1 miss means the gate needs 19/20 passes. juried prints
# what the gate needs at the start of every run and reports the Wilson 95% interval on
# the pass rate alongside, for reading, not for the gate.
runs = 20
misses = 1
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
# Any OpenAI compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, Azure OpenAI with
# its path) works as the judge with provider = "openai" and its URL here. api_key_env
# names the variable holding the key when it is not the provider's default.
# base_url = "http://127.0.0.1:11434/v1"
# api_key_env = "OLLAMA_API_KEY"
# Set temperature only for a model that accepts it; claude-sonnet-5 rejects the parameter.
# temperature = 0.0
# Each response is judged once. Set an odd number above 1 to judge it that many times and
# take the majority; the report then shows how often the votes split.
votes = 1
# A "quoted phrase" in a scenario's expected text must appear in the response, ignoring
# case, spacing, hyphens and end of word punctuation, before the judge is asked at all.
# Set true to require it word for word.
# strict_quotes = false
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
# `juried generate --adversarial` writes scenarios that try to make the feature violate
# each criterion. With this true it also writes juried's built-in pack (prompt injection,
# system prompt extraction, PII disclosure), whose criteria then count in runs.
# adversarial_pack = false
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
    generate.add_argument(
        "--adversarial",
        action="store_true",
        help="write scenarios that try to make the feature violate each criterion, to "
        "scenarios/generated/<criterion>.adversarial.yaml",
    )

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

    estimate = commands.add_parser(
        "estimate", help="print what a run would send and cost, without sending anything"
    )
    estimate.add_argument("--config", help=f"path to {CONFIG_FILENAME}")

    compare = commands.add_parser(
        "compare", help="compare two JSON reports and flag scenarios that got worse"
    )
    compare.add_argument("old", help="the earlier juried-report.json")
    compare.add_argument("new", help="the later juried-report.json")
    compare.add_argument(
        "--alpha",
        type=float,
        default=DEFAULT_ALPHA,
        metavar="P",
        help=f"a drop is a regression when its one sided p-value is below this (default "
        f"{DEFAULT_ALPHA})",
    )
    compare.add_argument(
        "--min-effect",
        type=float,
        default=None,
        metavar="RATE",
        help=f"and the pass rate fell by at least this much (default {DEFAULT_MIN_EFFECT})",
    )
    compare.add_argument(
        "--tolerance",
        type=float,
        default=None,
        metavar="RATE",
        help="deprecated alias for --min-effect, removed in 0.4",
    )
    compare.add_argument("--json", metavar="PATH", help="also write the comparison as JSON")

    run = commands.add_parser("run", help="run scenarios with pytest and write the report")
    run.add_argument("--config", help=f"path to {CONFIG_FILENAME}")
    run.add_argument("--runs", type=int, help="override run.runs")
    run.add_argument("--misses", type=int, help="override run.misses")
    run.add_argument(
        "--threshold",
        type=float,
        help="override run.threshold (deprecated; misses is derived from it)",
    )
    run.add_argument(
        "--no-cache", action="store_true", help="ignore cached verdicts (and responses)"
    )
    run.add_argument(
        "--cache-responses",
        action="store_true",
        help="replay responses from .juried/cache/responses instead of sampling the feature",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned requests and estimated cost, then exit without sending",
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


def command_generate(
    explicit: str | None, only: list[str] | None, force: bool, adversarial: bool = False
) -> int:
    _, config = locate_config(explicit)
    criteria = criteria_for(config)
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
        config.generate_api_key_env,
    )
    mode = "adversarial scenarios" if adversarial else "scenarios"
    print(f"generating {mode} with {provider.name}/{provider.model}")
    outcome = generate_scenarios(
        config,
        criteria,
        provider,
        force=force,
        only=set(only) if only else None,
        adversarial=adversarial,
    )
    for path in outcome.skipped:
        print(f"kept existing {path} (use --force to regenerate)")
    for path in outcome.written:
        key = path.name.removesuffix(".adversarial.yaml").removesuffix(".yaml")
        print(f"wrote {path} ({outcome.counts[key]} scenarios)")
    if provider.name != "stub":
        print(
            "usage: " + describe_usage(provider.usage_total, provider.model, config.generate_prices)
        )
    if outcome.written:
        print("review and edit the generated files, then commit them and run 'juried run'")
    return 0


def command_calibrate(explicit: str | None, min_accuracy: float | None) -> int:
    _, config = locate_config(explicit)
    criteria = criteria_for(config)
    cases = load_calibration(config.calibration_path, {c.id: c for c in criteria})
    judge = config.judge
    provider = build_provider(
        judge.provider,
        judge.model,
        judge.temperature,
        judge.max_tokens,
        judge.base_url,
        judge.api_key_env,
        judge.strict_quotes,
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


def command_estimate(explicit: str | None, extra: Sequence[str] = ()) -> int:
    _, config = locate_config(explicit)
    known = {criterion.id for criterion in criteria_for(config)}
    scenarios = load_scenarios(config.scenarios_path)
    unknown = sorted({s.criterion for s in scenarios} - known)
    if unknown:
        raise ScenarioError(f"scenarios refer to unknown criteria: {', '.join(unknown)}")
    previous = load_previous_report(config.report_path / JSON_NAME)
    plan = plan_run(config, scenarios, previous)
    for line in describe_plan(plan, config):
        print(line)
    if extra:
        print(
            f"note: a dry run plans every scenario under {config.criteria.scenarios_dir}; "
            f"pytest arguments ({' '.join(extra)}) do not narrow it"
        )
    return 0


def resolve_min_effect(min_effect: float | None, tolerance: float | None) -> float:
    if tolerance is not None:
        if min_effect is not None and min_effect != tolerance:
            raise CompareError(
                f"--tolerance {tolerance} and --min-effect {min_effect} disagree; --tolerance "
                "is a deprecated alias for --min-effect, pass one of them"
            )
        print(
            f"juried: --tolerance is deprecated and is removed in 0.4; use --min-effect "
            f"{tolerance:g}",
            file=sys.stderr,
        )
        return tolerance
    return DEFAULT_MIN_EFFECT if min_effect is None else min_effect


def command_compare(
    old: str,
    new: str,
    alpha: float,
    min_effect: float | None,
    tolerance: float | None,
    json_path: str | None,
) -> int:
    if not 0.0 < alpha < 1.0:
        raise CompareError(f"--alpha must be between 0 and 1 exclusive, not {alpha}")
    effect = resolve_min_effect(min_effect, tolerance)
    if not 0.0 <= effect <= 1.0:
        raise CompareError(f"--min-effect must be between 0 and 1, not {effect}")
    old_path, new_path = Path(old), Path(new)
    old_report, new_report = load_report(old_path), load_report(new_path)
    check_same_schema(old_path, old_report, new_path, new_report)
    changes = compare_reports(old_report, new_report, alpha, effect)
    regressions = [change for change in changes if change.regression]
    noise = [change for change in changes if change.kind in NOISE]
    improvements = [change for change in changes if change.kind in ("gate regained", "improved")]
    neutral = [change for change in changes if change.kind in ("added", "removed")]
    unchanged = sum(1 for change in changes if change.kind == "unchanged")
    print(f"comparing {old_path} -> {new_path} (alpha {alpha:g}, min effect {effect:g})")
    for label, group in (
        ("regressions", regressions),
        ("drops within noise", noise),
        ("improvements", improvements),
        ("other changes", neutral),
    ):
        if group:
            print(f"{label}:")
            for change in group:
                print(f"  {change.kind}: {change.id}: {change.detail}")
    print(
        f"{len(regressions)} regression(s), {len(noise)} drop(s) within noise, "
        f"{len(improvements)} improvement(s), {len(neutral)} added or removed, "
        f"{unchanged} unchanged"
    )
    power = power_of(changes, alpha)
    if power is not None:
        print(power.describe(alpha))
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = comparison_dict(old_path, new_path, changes, alpha, effect)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"comparison: {path}")
    return 1 if regressions else 0


def command_run(
    explicit: str | None,
    runs: int | None,
    misses: int | None,
    threshold: float | None,
    no_cache: bool,
    cache_responses: bool,
    pytest_args: Sequence[str],
) -> int:
    path, config = locate_config(explicit)
    args = [f"--juried-config={path}", f"--rootdir={path.parent}", "-v"]
    if runs is not None:
        args.append(f"--juried-runs={runs}")
    if misses is not None:
        args.append(f"--juried-misses={misses}")
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
    if args.command == "run" and args.dry_run:
        args.command = "estimate"
    try:
        if args.command == "init":
            return command_init(Path(args.dir), args.force)
        if args.command == "generate":
            return command_generate(args.config, args.criterion, args.force, args.adversarial)
        if args.command == "calibrate":
            return command_calibrate(args.config, args.min_accuracy)
        if args.command == "estimate":
            return command_estimate(args.config, extra)
        if args.command == "compare":
            return command_compare(
                args.old, args.new, args.alpha, args.min_effect, args.tolerance, args.json
            )
        return command_run(
            args.config,
            args.runs,
            args.misses,
            args.threshold,
            args.no_cache,
            args.cache_responses,
            extra,
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
