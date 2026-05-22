#!/usr/bin/env python3
"""
One-shot migration: copy SAM.gov puller data from local SQLite to Postgres.

Run this once, after:
    1. Setting up a Postgres database (e.g. Neon).
    2. Exporting DATABASE_URL pointing at it.
    3. Running `alembic upgrade head` against it (so the puller tables
       exist on the Postgres side).

Then:
    python scripts/migrate_sqlite_to_postgres.py
    # or, to wipe the Postgres tables first:
    python scripts/migrate_sqlite_to_postgres.py --reset
    # or, to read a non-default SQLite path:
    python scripts/migrate_sqlite_to_postgres.py --sqlite-path /path/to/opportunities.db

Behavior:
    - Preserves SQLite IDs so opportunity_offices links survive.
    - Parses ISO-string timestamps into proper datetime objects.
    - ON CONFLICT DO NOTHING for opportunities + opportunity_offices, so
      the script is safe to re-run if it dies partway through.
    - After insert, resets the Postgres sequences so future puller runs
      get correct auto-incremented IDs.

NOT migrated:
    - match_scores (the new scoring pipeline writes to different tables).
    - schema_meta (replaced by Alembic's alembic_version table).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import text

# Path bootstrap: the script lives in scripts/, repo root is its parent.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coe.database import SessionLocal  # noqa: E402

logger = logging.getLogger("migrate_sqlite_to_postgres")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """
    Convert an ISO timestamp string from SQLite into a tz-aware datetime.

    SQLite stored timestamps as `datetime.now().isoformat()` (naive),
    so we attach UTC for rows that don't carry a tz. Empty strings and
    None are returned as None — Postgres NULLs are correct here.
    """
    if value is None or value == "":
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found at {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------
# Per-table migrators
# ---------------------------------------------------------------------
def migrate_opportunities(sqlite_conn: sqlite3.Connection, pg_session) -> int:
    """Copy the opportunities table. Returns the number of rows inserted."""
    rows: Iterable[sqlite3.Row] = sqlite_conn.execute(
        "SELECT * FROM opportunities"
    ).fetchall()

    inserted = 0
    for r in rows:
        pg_session.execute(
            text(
                """INSERT INTO opportunities (
                       id, notice_id, solicitation_number, title, notice_type,
                       base_type, department, sub_tier, office, office_code,
                       naics_code, classification_code, set_aside_type,
                       set_aside_code, posted_date, response_deadline,
                       archive_date, award_number, award_amount, awardee_name,
                       pop_city, pop_state, description_url, active, raw_json,
                       first_seen_at, last_updated_at, created_at, updated_at
                   ) VALUES (
                       :id, :notice_id, :solicitation_number, :title,
                       :notice_type, :base_type, :department, :sub_tier,
                       :office, :office_code, :naics_code, :classification_code,
                       :set_aside_type, :set_aside_code, :posted_date,
                       :response_deadline, :archive_date, :award_number,
                       :award_amount, :awardee_name, :pop_city, :pop_state,
                       :description_url, :active, :raw_json, :first_seen_at,
                       :last_updated_at, :created_at, :updated_at
                   )
                   ON CONFLICT (notice_id) DO NOTHING"""
            ),
            {
                "id": r["id"],
                "notice_id": r["notice_id"],
                "solicitation_number": r["solicitation_number"],
                "title": r["title"],
                "notice_type": r["notice_type"],
                "base_type": r["base_type"],
                "department": r["department"],
                "sub_tier": r["sub_tier"],
                "office": r["office"],
                "office_code": r["office_code"],
                "naics_code": r["naics_code"],
                "classification_code": r["classification_code"],
                "set_aside_type": r["set_aside_type"],
                "set_aside_code": r["set_aside_code"],
                "posted_date": r["posted_date"],
                "response_deadline": r["response_deadline"],
                "archive_date": r["archive_date"],
                "award_number": r["award_number"],
                "award_amount": r["award_amount"],
                "awardee_name": r["awardee_name"],
                "pop_city": r["pop_city"],
                "pop_state": r["pop_state"],
                "description_url": r["description_url"],
                "active": r["active"],
                "raw_json": r["raw_json"],
                "first_seen_at": parse_ts(r["first_seen_at"]),
                "last_updated_at": parse_ts(r["last_updated_at"]),
                "created_at": parse_ts(r["created_at"]) or datetime.now(timezone.utc),
                "updated_at": parse_ts(r["updated_at"]) or datetime.now(timezone.utc),
            },
        )
        inserted += 1

    return inserted


def migrate_opportunity_offices(
    sqlite_conn: sqlite3.Connection, pg_session
) -> int:
    rows = sqlite_conn.execute(
        "SELECT * FROM opportunity_offices"
    ).fetchall()

    inserted = 0
    for r in rows:
        pg_session.execute(
            text(
                """INSERT INTO opportunity_offices
                       (opportunity_id, office_code, first_seen_via_office_at)
                   VALUES (:opp_id, :office_code, :first_seen)
                   ON CONFLICT (opportunity_id, office_code) DO NOTHING"""
            ),
            {
                "opp_id": r["opportunity_id"],
                "office_code": r["office_code"],
                "first_seen": parse_ts(r["first_seen_via_office_at"]),
            },
        )
        inserted += 1

    return inserted


def migrate_pull_history(sqlite_conn: sqlite3.Connection, pg_session) -> int:
    rows = sqlite_conn.execute("SELECT * FROM pull_history").fetchall()

    inserted = 0
    for r in rows:
        pg_session.execute(
            text(
                """INSERT INTO pull_history (
                       id, office_code, office_name, pulled_at,
                       opportunities_found, new_opportunities,
                       updated_opportunities, status, error_message,
                       duration_seconds
                   ) VALUES (
                       :id, :office_code, :office_name, :pulled_at,
                       :found, :new, :updated, :status, :error_message,
                       :duration
                   )"""
            ),
            {
                "id": r["id"],
                "office_code": r["office_code"],
                "office_name": r["office_name"],
                "pulled_at": parse_ts(r["pulled_at"]),
                "found": r["opportunities_found"],
                "new": r["new_opportunities"],
                "updated": r["updated_opportunities"],
                "status": r["status"],
                "error_message": r["error_message"],
                "duration": r["duration_seconds"],
            },
        )
        inserted += 1

    return inserted


def reset_sequences(pg_session) -> None:
    """
    After preserving SQLite IDs, the Postgres SERIAL sequences are
    still pointing at 1 — so the next puller insert would collide. Bump
    each sequence past MAX(id). Safe to run on an empty table (setval
    to COALESCE(MAX(id), 0) becomes setval(seq, 1, false), which is the
    initial state of a fresh sequence).
    """
    for table, col in [("opportunities", "id"), ("pull_history", "id")]:
        pg_session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                f"COALESCE((SELECT MAX({col}) FROM {table}), 1), "
                f"(SELECT MAX({col}) IS NOT NULL FROM {table}))"
            )
        )


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default="data/opportunities.db",
        help="Path to the SQLite database (default: data/opportunities.db)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="TRUNCATE Postgres puller tables before copying. Cascades.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sqlite_path = Path(args.sqlite_path).resolve()
    logger.info(f"Reading from SQLite: {sqlite_path}")

    try:
        sqlite_conn = open_sqlite(sqlite_path)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    pg_session = SessionLocal()

    try:
        if args.reset:
            logger.warning("Truncating Postgres puller tables (--reset)")
            pg_session.execute(
                text(
                    "TRUNCATE TABLE opportunities, opportunity_offices, "
                    "pull_history RESTART IDENTITY CASCADE"
                )
            )
            pg_session.commit()

        logger.info("Copying opportunities ...")
        n_opps = migrate_opportunities(sqlite_conn, pg_session)
        pg_session.commit()
        logger.info(f"  opportunities: {n_opps} rows processed")

        logger.info("Copying opportunity_offices ...")
        n_off = migrate_opportunity_offices(sqlite_conn, pg_session)
        pg_session.commit()
        logger.info(f"  opportunity_offices: {n_off} rows processed")

        logger.info("Copying pull_history ...")
        n_hist = migrate_pull_history(sqlite_conn, pg_session)
        pg_session.commit()
        logger.info(f"  pull_history: {n_hist} rows processed")

        logger.info("Resetting Postgres sequences past migrated IDs ...")
        reset_sequences(pg_session)
        pg_session.commit()

        logger.info("Migration complete.")
        return 0
    except Exception:
        pg_session.rollback()
        logger.exception("Migration failed; rolled back.")
        return 1
    finally:
        sqlite_conn.close()
        pg_session.close()


if __name__ == "__main__":
    sys.exit(main())
