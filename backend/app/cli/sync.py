"""Run a collection cycle by hand, without waiting for Celery Beat.

    python -m app.cli.sync log-sources --instance qradarce2
    python -m app.cli.sync offenses    --instance qradarce2
    python -m app.cli.sync rules       --instance qradarce2
    python -m app.cli.sync all         --instance qradarce2

These call exactly the same orchestration functions the Celery tasks wrap, so a
manual run and a scheduled run cannot drift apart. Omitting --instance runs
against every enabled instance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable

from app.workers.tasks import (
    collect_offenses,
    evaluate_detection_coverage,
    evaluate_rule_health,
    sync_log_sources,
    sync_rule_inventory,
)

Runner = Callable[[str | None], Awaitable[dict]]

#: Ordered so `all` runs dependencies before dependents: inventory before the
#: health classification that reads it, health before the coverage verdicts
#: that read that.
COMMANDS: dict[str, Runner] = {
    "log-sources": sync_log_sources,
    "offenses": collect_offenses,
    "rules": sync_rule_inventory,
    "rule-health": evaluate_rule_health,
    "coverage": evaluate_detection_coverage,
}


def _report(name: str, result: dict) -> None:
    """Print one run's outcome. Does not mutate `result`.

    `skipped_locked` is reported but is not a failure: another worker holds the
    advisory lock and is doing the work.
    """
    if result.get("status") == "no-instance":
        print(f"{name}: no matching enabled instance")
        return

    for entry in result.get("results", []):
        instance = entry.get("instance", "?")
        status = entry.get("status")
        detail = " ".join(
            f"{k}={v}"
            for k, v in entry.items()
            if k not in ("instance", "status") and v not in (None, 0)
        )
        print(f"{name:<12} {instance:<16} {status:<14} {detail}")


async def _run(names: list[str], instance: str | None, as_json: bool) -> int:
    exit_code = 0
    collected: dict[str, dict] = {}

    for name in names:
        result = await COMMANDS[name](instance)
        collected[name] = result

        # Success is decided by the result, never by which formatter ran, so
        # --json and the human output can never disagree about the exit code.
        failed = any(e.get("status") == "failed" for e in result.get("results", []))
        if result.get("status") == "no-instance" or failed:
            exit_code = 1

        if not as_json:
            _report(name, result)

    if as_json:
        print(json.dumps(collected, indent=2, default=str))
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.sync")
    parser.add_argument(
        "target",
        choices=[*COMMANDS, "all"],
        help="which collection to run ('rules' covers building blocks too)",
    )
    parser.add_argument(
        "--instance", default=None, help="instance name; default is every enabled instance"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = list(COMMANDS) if args.target == "all" else [args.target]
    try:
        return asyncio.run(_run(names, args.instance, args.json))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
