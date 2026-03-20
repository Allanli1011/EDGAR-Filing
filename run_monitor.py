#!/usr/bin/env python3
"""
Entry point for the EDGAR Filing Monitor.

Usage:
    python run_monitor.py
    python run_monitor.py --date 2024-12-01
    python run_monitor.py --dry-run
    python run_monitor.py --forms 8-K 4 SC 13D
    python run_monitor.py --max-filings 5
    python run_monitor.py --days-back 3
"""

import argparse
import logging
import sys
from datetime import date

from dotenv import load_dotenv

# Load .env before importing config (so env vars are available)
load_dotenv()

from src.config import MonitorConfig
from src.monitor import DailyMonitor


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Suppress noisy urllib3 logs unless in verbose mode
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EDGAR Filing Monitor — daily SEC filing analysis with Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Target date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch filings but skip Claude analysis",
    )
    parser.add_argument(
        "--forms",
        nargs="+",
        metavar="FORM",
        default=None,
        help="Space-separated list of form types to monitor (overrides config)",
    )
    parser.add_argument(
        "--max-filings",
        type=int,
        default=None,
        help="Cap the number of filings analyzed per run (useful for testing)",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Number of calendar days to look back (default: 1)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)

    config = MonitorConfig()
    if args.days_back is not None:
        config.days_back = args.days_back

    try:
        config.validate()
    except ValueError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    monitor = DailyMonitor(config)
    summary = monitor.run(
        target_date=args.date,
        dry_run=args.dry_run,
        form_types=args.forms,
        max_filings=args.max_filings,
    )
    monitor.print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
