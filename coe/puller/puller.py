#!/usr/bin/env python3
"""
SAM.gov Contract Opportunities Puller

Main script that orchestrates the daily pull:
1. Loads config (office codes, settings)
2. Pulls ALL recent opportunities from SAM.gov (one API call with pagination)
3. Filters client-side to only keep opportunities from tracked offices
4. Upserts matches into Postgres with deduplication
5. Logs a summary of what was pulled

The puller now writes to Postgres via coe.puller.db (was SQLite). The
connection URL is read from DATABASE_URL by coe.database.engine.

Usage:
    python -m coe.puller.puller                      # Normal run using config.yaml
    python -m coe.puller.puller --config other.yaml  # Use a different config file
    python -m coe.puller.puller --force-lookback 14  # Override lookback to 14 days
    python -m coe.puller.puller --reset              # Truncate DB tables and start fresh
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from coe.puller.sam_client import SAMClient, SAMClientError
from coe.puller.db import Database

logger = logging.getLogger("sam_puller")


def setup_logging(log_level: str, log_dir: Path):
    """Configure logging to both console and daily log file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pull_{datetime.now().strftime('%Y-%m-%d')}.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(str(log_file))
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)


def load_config(config_path: str) -> dict:
    """Load and validate the YAML config file."""
    path = Path(config_path)
    if not path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    with open(path) as f:
        config = yaml.safe_load(f)

    # Allow SAM_API_KEY env var to override the config file. Useful for
    # CI (GitHub Actions) and any other environment where we don't want
    # a real API key sitting in a committed file.
    env_key = os.getenv("SAM_API_KEY")
    if env_key:
        config["api_key"] = env_key

    # Validate required fields. Catch all known placeholder strings so
    # `cp config.yaml.example config.yaml` without setting a real key
    # fails loudly instead of pinging SAM.gov with a literal "YOUR-...".
    _PLACEHOLDERS = {"", "YOUR_API_KEY_HERE", "YOUR-SAM-GOV-API-KEY"}
    if not config.get("api_key") or config["api_key"] in _PLACEHOLDERS:
        logger.error(
            "SAM.gov API key not set. Either set the SAM_API_KEY env "
            "var or fill in api_key in config.yaml."
        )
        sys.exit(1)

    if not config.get("offices"):
        logger.error("No offices defined in config.yaml")
        sys.exit(1)

    for office in config["offices"]:
        if not office.get("code"):
            logger.error(f"Office entry missing 'code': {office}")
            sys.exit(1)

    return config


def run(config_path: str, force_lookback: int = None, reset: bool = False):
    """Main execution flow."""
    # Load config
    config = load_config(config_path)
    settings = config.get("settings", {})
    config_dir = Path(config_path).parent

    # Setup logging
    setup_logging(
        log_level=settings.get("log_level", "INFO"),
        log_dir=config_dir / "logs",
    )

    # Build set of tracked office codes + lookup map
    office_codes = set()
    office_names = {}
    for office in config["offices"]:
        code = office["code"]
        office_codes.add(code)
        office_names[code] = office.get("name", code)

    logger.info("=" * 60)
    logger.info("SAM.gov Contract Opportunities Puller — Starting")
    logger.info(f"  Tracking {len(office_codes)} unique offices")
    logger.info("=" * 60)

    # Initialize database. The DB path from config.yaml is now ignored;
    # the puller writes to Postgres at DATABASE_URL (see coe.database).
    # The path is read and logged purely for backward-compatibility with
    # existing config files.
    db_path_setting = settings.get("database", "opportunities.db")
    logger.info(f"  Config db setting (informational only): {db_path_setting}")

    db = Database()

    # Handle --reset flag: TRUNCATE the puller tables on Postgres.
    if reset:
        logger.warning("Resetting database (--reset flag)")
        db.reset()

    # Initialize API client
    client = SAMClient(
        api_key=config["api_key"],
        request_delay=settings.get("request_delay", 1.0),
    )

    # Settings
    initial_lookback = force_lookback or settings.get("initial_lookback_days", 7)
    max_results = settings.get("max_api_results", 10000)

    # Determine how far back to look based on the earliest last-pull across all offices
    last_pull = db.get_last_successful_pull("__global__")
    if last_pull and not force_lookback:
        # Pull from last successful run (with 1hr overlap for safety)
        posted_from = last_pull - timedelta(hours=1)
        logger.info(f"  Last global pull: {last_pull.isoformat()}")
    else:
        posted_from = datetime.now() - timedelta(days=initial_lookback)
        logger.info(f"  Looking back {initial_lookback} days (from {posted_from.strftime('%Y-%m-%d')})")

    # -------------------------------------------------------------------------
    # Phase 1: Fetch ALL recent opportunities from SAM.gov
    # -------------------------------------------------------------------------
    run_start = time.time()

    try:
        all_opportunities, total_available = client.fetch_recent_opportunities(
            posted_from=posted_from,
            limit=max_results,
        )
    except SAMClientError as e:
        logger.error(f"Failed to fetch opportunities: {e}")
        db.close()
        return 1

    fetch_duration = time.time() - run_start
    logger.info(f"  API fetch complete: {len(all_opportunities)} pulled in {fetch_duration:.1f}s")

    if total_available > len(all_opportunities):
        logger.warning(
            f"  Note: {total_available} total available but only pulled {len(all_opportunities)} "
            f"(limit: {max_results}). Consider increasing max_api_results in config."
        )

    # -------------------------------------------------------------------------
    # Phase 2: Filter to tracked offices and upsert into database
    # -------------------------------------------------------------------------
    # Track per-office counts
    per_office = {code: {"found": 0, "new": 0, "updated": 0, "unchanged": 0}
                  for code in office_codes}
    totals = {"fetched": len(all_opportunities), "matched": 0, "new": 0,
              "updated": 0, "unchanged": 0, "unmatched": 0}

    for raw_opp in all_opportunities:
        matched_offices = client.match_office(raw_opp, office_codes)

        if not matched_offices:
            totals["unmatched"] += 1
            continue

        totals["matched"] += 1
        parsed = client.parse_opportunity(raw_opp)

        # Upsert once, but link to all matched offices
        for office_code in matched_offices:
            result = db.upsert_opportunity(parsed, source_office_code=office_code)
            per_office[office_code]["found"] += 1

            # Only count new/updated for the first office to avoid double-counting in totals
            if office_code == matched_offices[0]:
                totals[result] += 1
            per_office[office_code][result] += 1

    db.commit()

    # Record the global pull
    duration = time.time() - run_start
    db.record_pull(
        office_code="__global__",
        office_name="All Offices",
        found=totals["fetched"],
        new=totals["new"],
        updated=totals["updated"],
        duration=duration,
    )

    # -------------------------------------------------------------------------
    # Phase 3: Log results
    # -------------------------------------------------------------------------
    logger.info("")
    logger.info("Per-office breakdown:")
    for code in sorted(office_codes):
        counts = per_office[code]
        if counts["found"] > 0:
            logger.info(
                f"  {office_names[code]} ({code}): "
                f"{counts['found']} matched, {counts['new']} new, "
                f"{counts['updated']} updated"
            )
        else:
            logger.info(f"  {office_names[code]} ({code}): no new opportunities")

    stats = db.get_stats()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Run complete!")
    logger.info(f"  Duration: {duration:.1f}s")
    logger.info(f"  API: {totals['fetched']} fetched, {totals['matched']} matched your offices, "
                f"{totals['unmatched']} from other offices (filtered out)")
    logger.info(f"  Changes: {totals['new']} new, {totals['updated']} updated, "
                f"{totals['unchanged']} unchanged")
    logger.info(f"  Database: {stats['total_opportunities']} total opportunities, "
                f"{stats['active_opportunities']} active")
    logger.info("=" * 60)

    db.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Pull contract opportunities from SAM.gov"
    )
    # Repo root is two levels up from coe/puller/puller.py
    default_config = Path(__file__).resolve().parents[2] / "config.yaml"
    parser.add_argument(
        "--config",
        default=str(default_config),
        help=f"Path to config.yaml (default: {default_config})"
    )
    parser.add_argument(
        "--force-lookback",
        type=int,
        default=None,
        help="Override lookback days (e.g., 30 to pull last 30 days)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing database and start fresh"
    )
    args = parser.parse_args()

    exit_code = run(
        config_path=args.config,
        force_lookback=args.force_lookback,
        reset=args.reset,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
