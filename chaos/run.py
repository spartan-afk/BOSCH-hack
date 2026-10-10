"""Chaos Platform Orchestrator CLI.

Commands:
  python -m chaos.run baseline   -- Run steady-state loadgen & observer to generate baseline.json
  python -m chaos.run map        -- Run Agent 1 (Mapper) to produce map.json
  python -m chaos.run day1       -- Run full Day 1 workflow (plan, validate, pre-flight, inject, observe, rollback, analyze)
  python -m chaos.run guard-test -- Run guard abortion test with tight threshold
  python -m chaos.run abort      -- Emergency kill switch (reset toxiproxy, re-enable proxies, restart stopped containers)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import httpx

from chaos.loadgen import LoadGenerator
from chaos.observer import Observer, compute_metrics_summary

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("chaos.cli")

DATA_DIR = Path(__file__).resolve().parent / "data"


async def check_backend_alive(url: str = "http://localhost:8000/api/fleet/status") -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=3.0)
            return resp.status_code == 200
    except Exception:
        return False


async def run_baseline(duration_s: int = 300):
    """Run steady-state loadgen and observer concurrently for baseline.json."""
    logger.info(f"=== Starting Baseline Collection ({duration_s} seconds / {duration_s/60:.1f} mins) ===")
    
    # 1. Health check
    alive = await check_backend_alive()
    if not alive:
        logger.error(
            "Fleet Backend is not reachable at http://localhost:8000/api/fleet/status! "
            "Please ensure the backend is running (e.g. via 'docker compose up -d' or 'uvicorn app.main:app')."
        )
        sys.exit(1)

    loadgen = LoadGenerator()
    observer = Observer(phase="baseline")

    # Run loadgen and observer concurrently
    async def progress_reporter():
        start = time.time()
        while time.time() - start < duration_s:
            await asyncio.sleep(10.0)
            elapsed = int(time.time() - start)
            recent = compute_metrics_summary("baseline")
            logger.info(
                f"[Progress: {elapsed}/{duration_s}s] "
                f"Samples: {recent.get('sample_count', 0)} | "
                f"Error Rate: {recent.get('error_rate_pct', 0)}% | "
                f"p50 Latency: {recent.get('p50_latency_ms', 0)}ms | "
                f"Mean Data Age: {recent.get('mean_data_age_s', 0)}s"
            )

    try:
        await asyncio.gather(
            loadgen.run(duration_s=float(duration_s)),
            observer.run(duration_s=float(duration_s)),
            progress_reporter(),
        )
    except KeyboardInterrupt:
        logger.warning("Baseline collection interrupted by user.")
    finally:
        loadgen.stop()
        observer.stop()

    summary = compute_metrics_summary("baseline")
    logger.info("=== Baseline Collection Complete ===")
    print(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Chaos Engineering CLI for Fleet Resilience")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # baseline
    parser_base = subparsers.add_parser("baseline", help="Run loadgen and observer for baseline")
    parser_base.add_argument("--duration", type=int, default=300, help="Duration in seconds (default: 300)")

    # map
    parser_map = subparsers.add_parser("map", help="Run Agent 1 (Mapper)")
    parser_map.add_argument("--force", action="store_true", help="Force overwrite existing map.json")

    # day1
    parser_day1 = subparsers.add_parser("day1", help="Run Day 1 latency injection workflow")

    # guard-test
    parser_guard = subparsers.add_parser("guard-test", help="Test guard automatic abort on threshold breach")

    # abort
    parser_abort = subparsers.add_parser("abort", help="Manual kill switch to reset all proxies")

    args = parser.parse_args()

    if args.command == "baseline":
        asyncio.run(run_baseline(duration_s=args.duration))
    elif args.command == "map":
        from chaos.agents.mapper import run_mapper
        asyncio.run(run_mapper(force=args.force))
    elif args.command == "day1":
        from chaos.day1_workflow import run_day1_workflow
        asyncio.run(run_day1_workflow())
    elif args.command == "guard-test":
        from chaos.day1_workflow import run_day1_workflow
        asyncio.run(run_day1_workflow(guard_test_mode=True))
    elif args.command == "abort":
        from chaos.executor import ToxiproxyExecutor
        executor = ToxiproxyExecutor()
        executor.kill_switch()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
