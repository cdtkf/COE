"""
queries.py -> read-only data access for the dashboard

Every function opens a read only SQLite connection, runs one SQL query, 
and returns either a scalar or pandas DF. Keep file free of 
Streamlit imports so its testable in a plain Python REPL.
"""

from pathlib import Path
import sqlite3

import pandas as pd
import yaml

from datetime import datetime
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


def _load_db_path() -> Path:
    """Read the DB path from config.yaml — same source the puller uses."""
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    # config value is relative to the config file
    return REPO_ROOT / config["settings"]["database"]


def _connect_readonly() -> sqlite3.Connection:
    """Open SQLite in read-only mode. Dashboards must never write."""
    db_path = _load_db_path()
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_total_opportunities() -> int:
    """Count of rows in the opportunities table."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query("SELECT COUNT(*) AS n FROM opportunities", conn)
    return int(df["n"].iloc[0])

def get_active_opportunities() -> int:
    """Count of rows in the opportunities table where active = Yes"""
    with _connect_readonly() as conn:
        df = pd.read_sql_query("SELECT COUNT(*) AS n FROM opportunities WHERE active = 'Yes'", conn)
    return int(df["n"].iloc[0])

def get_departments_covered() -> int:
    """Count of distinct top-level departments represented in opportunities."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            "SELECT COUNT(DISTINCT department) AS n FROM opportunities",
            conn,
        )
    return int(df["n"].iloc[0])

def get_latest_pull_timestamp() -> Optional[datetime]:
    """
    Timestamp of the most recent successful pull run across all offices.
    Returns None if the puller has never run successfully.
    """
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            "SELECT MAX(pulled_at) AS latest FROM pull_history "
            "WHERE status = 'success'",
            conn,
        )
    value = df["latest"].iloc[0]
    if value is None or pd.isna(value):
        return None
    return datetime.fromisoformat(value)

# --- Full table access -----------------------------------------------------------

def get_all_opportunities() -> pd.DataFrame:
    """
    Every opportunity row with every column, newest first by posted_date.

    Note: this could be a very large dataframe. Use with caution.
    """
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            "SELECT * FROM opportunities ORDER BY posted_date DESC",
            conn,
        )
    return df

# --- Breakdowns ---------------------------------------------------------------
def get_opportunities_by_department(top_n: int = 10) -> pd.DataFrame:
    """Top N departments by count of active opportunities."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            """SELECT department AS label, COUNT(*) AS count
                FROM opportunities
                WHERE department IS NOT NULL AND department != '' 
                GROUP BY department
                ORDER BY count DESC
                LIMIT ?""",
            conn,
            params=(top_n,),
        )
    return df

def get_opportunities_by_naics(top_n: int = 10) -> pd.DataFrame:
    """Top N NAICS codes by opportunity count."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            """SELECT naics_code AS label, COUNT(*) AS count
               FROM opportunities
               WHERE naics_code IS NOT NULL AND naics_code != ''
               GROUP BY naics_code
               ORDER BY count DESC
               LIMIT ?""",
            conn,
            params=(top_n,),
        )
    return df

def get_opportunities_by_set_aside() -> pd.DataFrame:
    """Top set-aside types by count of opportunities."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            """SELECT COALESCE(NULLIF(set_aside_type, ''), 'Unrestricted') AS label,
                    COUNT(*) AS count
                FROM opportunities
                GROUP BY label
                ORDER BY count DESC""",
            conn,
        )
    return df


def get_opportunities_by_notice_type() -> pd.DataFrame:
    """Opportunity counts by human-readable notice type (base_type)."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            """SELECT COALESCE(NULLIF(base_type, ''), '(unknown)') AS label,
                      COUNT(*) AS count
               FROM opportunities
               GROUP BY label
               ORDER BY count DESC""",
            conn,
        )
    return df


def get_opportunities_posted_by_day() -> pd.DataFrame:
    """Count of opportunities posted each calendar day."""
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            """SELECT DATE(posted_date) AS date, COUNT(*) AS count
               FROM opportunities
               WHERE posted_date IS NOT NULL AND posted_date != ''
               GROUP BY DATE(posted_date)
               ORDER BY date ASC""",
            conn,
        )
    # Convert date string → datetime so the line chart's x-axis
    # understands it as time and spaces values accordingly.
    df["date"] = pd.to_datetime(df["date"])
    return df

# --- Office Coverage -----------------------------------------------------------

def get_office_coverage() -> pd.DataFrame:
    """
    One row per office that has surfaced at least one opportunity.

    Source of truth: the opportunity_offices junction, which the puller
    populates with real office codes (e.g., '36C10B') via match_office().
    Office *names* come from opportunities.office (best-effort, since
    different opps matched against the same code can carry slightly
    different office strings from the API).
    """
    sql = """
    SELECT
        oo.office_code,
        MAX(o.office) AS office_name,
        COUNT(DISTINCT oo.opportunity_id) AS opps_count,
        SUM(CASE WHEN o.active = 'Yes' THEN 1 ELSE 0 END) AS active_opps,
        MIN(oo.first_seen_via_office_at) AS first_discovered_at,
        MAX(o.last_updated_at)            AS last_activity_at
    FROM opportunity_offices oo
    JOIN opportunities o ON oo.opportunity_id = o.id
    GROUP BY oo.office_code
    ORDER BY opps_count DESC, oo.office_code ASC
    """
    with _connect_readonly() as conn:
        df = pd.read_sql_query(sql, conn)

    # Make timestamps proper datetimes so Streamlit renders them nicely.
    df["first_discovered_at"] = pd.to_datetime(df["first_discovered_at"], errors="coerce")
    df["last_activity_at"]    = pd.to_datetime(df["last_activity_at"],    errors="coerce")

    return df