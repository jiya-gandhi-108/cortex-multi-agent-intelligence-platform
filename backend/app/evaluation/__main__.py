"""Evaluation CLI.

python -m app.evaluation list
python -m app.evaluation run --mode live [--only b01-india-healthcare-saas,b03-vector-databases]
                             [--limit 5] [--depth quick] [--resume <run_id>]
python -m app.evaluation run --mode offline          # harness check with scripted agents
python -m app.evaluation report <run_id> [--out eval_results]
python -m app.evaluation import-review <run_id> eval_results/<run_id>/human_review.csv
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import dispose_engine, init_engine
from app.evaluation.benchmarks import BENCHMARKS, BY_ID, Benchmark
from app.evaluation.report import import_review, write_report
from app.evaluation.runner import EvaluationRunner, live_factories, offline_factories
from app.monitoring.tracing import configure_tracing
from app.services.runner import provider_preflight


def _select(only: str | None, limit: int | None) -> list[Benchmark]:
    chosen = list(BENCHMARKS)
    if only:
        ids = [i.strip() for i in only.split(",") if i.strip()]
        unknown = [i for i in ids if i not in BY_ID]
        if unknown:
            raise SystemExit(f"unknown benchmark ids: {', '.join(unknown)}")
        chosen = [BY_ID[i] for i in ids]
    return chosen[:limit] if limit else chosen


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging("INFO" if args.verbose else "WARNING")
    configure_tracing(settings)
    init_engine(settings)
    try:
        if args.command == "list":
            for b in BENCHMARKS:
                print(f"{b.id:<30} {b.domain:<24} {len(b.topics)} topics")
            return 0
        if args.command == "run":
            if args.mode == "live":
                missing = provider_preflight(settings, None)
                if missing:
                    raise SystemExit(f"live mode needs: {', '.join(missing)}")
                llm, collector = live_factories(settings)
            else:
                llm, collector = offline_factories()
            benchmarks = _select(args.only, args.limit)
            print(
                f"Running {len(benchmarks)} benchmarks in {args.mode} mode (depth {args.depth})..."
            )
            runner = EvaluationRunner(
                settings,
                mode=args.mode,
                depth=args.depth,
                llm_factory=llm,
                collector_factory=collector,
            )
            run_id = await runner.run(benchmarks, uuid.UUID(args.resume) if args.resume else None)
            out = await write_report(run_id, Path(args.out))
            print(f"Run {run_id} complete. Report: {out / 'report.md'}")
            return 0
        if args.command == "report":
            out = await write_report(uuid.UUID(args.run_id), Path(args.out))
            print(f"Report written to {out / 'report.md'}")
            return 0
        if args.command == "import-review":
            count = await import_review(uuid.UUID(args.run_id), Path(args.csv))
            out = await write_report(uuid.UUID(args.run_id), Path(args.out))
            print(f"Imported {count} reviews. Report updated: {out / 'report.md'}")
            return 0
        return 1
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list benchmarks")
    run = sub.add_parser("run", help="run benchmarks and write a report")
    run.add_argument("--mode", choices=["live", "offline"], required=True)
    run.add_argument("--depth", choices=["quick", "standard", "deep"], default="quick")
    run.add_argument("--only", help="comma-separated benchmark ids")
    run.add_argument("--limit", type=int)
    run.add_argument("--resume", help="continue an earlier run id, skipping finished benchmarks")
    run.add_argument("--out", default="eval_results")
    rep = sub.add_parser("report", help="re-generate the report for a run")
    rep.add_argument("run_id")
    rep.add_argument("--out", default="eval_results")
    imp = sub.add_parser("import-review", help="import human review scores from CSV")
    imp.add_argument("run_id")
    imp.add_argument("csv")
    imp.add_argument("--out", default="eval_results")
    raise SystemExit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
