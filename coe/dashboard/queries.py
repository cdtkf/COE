"""
queries.py —> Read-only data access for the dashboard.

Every function here opens a read-only SQLite connection, runs one SQL query,
and returns either a scalar or a pandas DataFrame. Keep this file free of
Streamlit-specific imports EXCEPT for @st.cache_data decorators — caching
is where Streamlit and the data layer intersect.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
import sqlite3

import pandas as pd
import streamlit as st
import yaml

from coe.dashboard.naics import NAICS_SECTORS, sector_label, sector_title

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"

CACHE_TTL_SECONDS = 60  # How long query results stay cached


def _load_db_path() -> Path:
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return REPO_ROOT / config["settings"]["database"]


def _connect_readonly() -> sqlite3.Connection:
    db_path = _load_db_path()
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _build_opps_filters(
    active_only: bool,
    date_from: Optional[str],
    date_to: Optional[str],
    table_alias: str = "",
) -> tuple[str, list]:
    """
    Build a SQL WHERE-clause fragment plus its parameter list based on the
    active-only/date-range filter state.

    Args:
        table_alias: Optional alias/prefix for column references
                     (e.g. "o" → "o.active"). Empty string for unqualified.

    Returns:
        (sql_fragment, params). The fragment is either a valid WHERE body
        or "1=1" (a no-op that's safe to AND into other clauses).
    """
    prefix = f"{table_alias}." if table_alias else ""
    clauses: list[str] = []
    params: list = []

    if active_only:
        clauses.append(f"{prefix}active = 'Yes'")
    if date_from:
        clauses.append(f"{prefix}posted_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{prefix}posted_date <= ?")
        params.append(date_to)

    if not clauses:
        return "1=1", []
    return " AND ".join(clauses), params


# ---------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_total_opportunities(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> int:
    """Count of rows in the opportunities table, respecting filters."""
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"SELECT COUNT(*) AS n FROM opportunities WHERE {where_sql}",
            conn,
            params=params,
        )
    return int(df["n"].iloc[0])


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_active_opportunities(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> int:
    """Count of active opportunities, respecting date filters."""
    where_sql, params = _build_opps_filters(
        active_only=True, date_from=date_from, date_to=date_to
    )
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"SELECT COUNT(*) AS n FROM opportunities WHERE {where_sql}",
            conn,
            params=params,
        )
    return int(df["n"].iloc[0])


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_departments_covered(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> int:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"SELECT COUNT(DISTINCT department) AS n FROM opportunities WHERE {where_sql}",
            conn,
            params=params,
        )
    return int(df["n"].iloc[0])


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_latest_pull_timestamp() -> Optional[datetime]:
    """Latest successful pull — unaffected by opps filters."""
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


# ---------------------------------------------------------------------
# Full-table view
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_all_opportunities(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"SELECT * FROM opportunities WHERE {where_sql} ORDER BY posted_date DESC",
            conn,
            params=params,
        )
    return df


# ---------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_department(
    top_n: int = 10,
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    params = list(params) + [top_n]
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT department AS label, COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
                 AND department IS NOT NULL AND department != ''
               GROUP BY department
               ORDER BY count DESC
               LIMIT ?""",
            conn,
            params=params,
        )
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_naics(
    top_n: int = 10,
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    params = list(params) + [top_n]
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT naics_code AS label, COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
                 AND naics_code IS NOT NULL AND naics_code != ''
               GROUP BY naics_code
               ORDER BY count DESC
               LIMIT ?""",
            conn,
            params=params,
        )
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_set_aside(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT COALESCE(NULLIF(set_aside_type, ''), 'Unrestricted') AS label,
                      COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
               GROUP BY label
               ORDER BY count DESC""",
            conn,
            params=params,
        )
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_notice_type(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT COALESCE(NULLIF(base_type, ''), '(unknown)') AS label,
                      COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
               GROUP BY label
               ORDER BY count DESC""",
            conn,
            params=params,
        )
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_posted_by_day(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT DATE(posted_date) AS date, COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
                 AND posted_date IS NOT NULL AND posted_date != ''
               GROUP BY DATE(posted_date)
               ORDER BY date ASC""",
            conn,
            params=params,
        )
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------
# Office coverage
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_office_coverage(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    # Filters apply to the joined opportunities, not the junction.
    where_sql, params = _build_opps_filters(
        active_only, date_from, date_to, table_alias="o"
    )
    sql = f"""
    SELECT
        oo.office_code,
        MAX(o.office) AS office_name,
        COUNT(DISTINCT oo.opportunity_id) AS opps_count,
        SUM(CASE WHEN o.active = 'Yes' THEN 1 ELSE 0 END) AS active_opps,
        MIN(oo.first_seen_via_office_at) AS first_discovered_at,
        MAX(o.last_updated_at)            AS last_activity_at
    FROM opportunity_offices oo
    JOIN opportunities o ON oo.opportunity_id = o.id
    WHERE {where_sql}
    GROUP BY oo.office_code
    ORDER BY opps_count DESC, oo.office_code ASC
    """
    with _connect_readonly() as conn:
        df = pd.read_sql_query(sql, conn, params=params)

    df["first_discovered_at"] = pd.to_datetime(df["first_discovered_at"], errors="coerce")
    df["last_activity_at"]    = pd.to_datetime(df["last_activity_at"],    errors="coerce")
    return df


# ---------------------------------------------------------------------
# NAICS rollup + drill-down
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_naics_sector(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    Roll opportunities up to NAICS sector (first 2 digits of naics_code).

    Returns a DataFrame with columns: prefix, label, count.
        - prefix: '54'                                         (raw 2-digit code)
        - label:  '54 — Professional, Scientific, and ...'     (chart-friendly)
        - count:  number of opportunities in that sector
    """
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT SUBSTR(naics_code, 1, 2) AS prefix, COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
                 AND naics_code IS NOT NULL AND naics_code != ''
               GROUP BY SUBSTR(naics_code, 1, 2)
               ORDER BY count DESC""",
            conn,
            params=params,
        )
    df["label"] = df["prefix"].apply(sector_label)
    return df[["prefix", "label", "count"]]


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_naics_codes_with_counts(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    Every NAICS code present in the filtered set, with its opportunity count
    and sector title. Used to populate the drill-down picker.

    Returns columns: naics_code, sector_title, count, display
        - display: 'NAICS 541512 — Professional, Scientific... (12 opps)'
                   so the selectbox is self-explanatory.
    """
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT naics_code, COUNT(*) AS count
               FROM opportunities
               WHERE {where_sql}
                 AND naics_code IS NOT NULL AND naics_code != ''
               GROUP BY naics_code
               ORDER BY count DESC, naics_code ASC""",
            conn,
            params=params,
        )
    df["sector_title"] = df["naics_code"].apply(sector_title)
    df["display"] = df.apply(
        lambda r: f"NAICS {r['naics_code']} — {r['sector_title']} ({r['count']} opps)",
        axis=1,
    )
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_for_naics_code(
    naics_code: str,
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    All opportunities matching a specific NAICS code. Slim column set —
    this is meant for in-tab drill-down display, not the full table view.
    """
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    params = list(params) + [naics_code]
    with _connect_readonly() as conn:
        df = pd.read_sql_query(
            f"""SELECT title, department, office, set_aside_type,
                      posted_date, response_deadline, active, description_url
               FROM opportunities
               WHERE {where_sql}
                 AND naics_code = ?
               ORDER BY posted_date DESC""",
            conn,
            params=params,
        )
    return df


# ---------------------------------------------------------------------
# Office hierarchy + drill-down
# ---------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_department_office_hierarchy(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    Department → office hierarchy with counts.

    Returns columns: department, office, office_code, opps_count, active_opps.
    Sorted so departments cluster together (alphabetical), then offices
    within a department by descending opp count.

    Note: we group on opportunities.office_code (the office that owns the
    opportunity), NOT the opportunity_offices junction (which tracks which
    *queries* surfaced it). That gives you a cleaner department→office tree.
    """
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    sql = f"""
    SELECT
        COALESCE(NULLIF(department, ''), '(unknown department)') AS department,
        COALESCE(NULLIF(office, ''), '(unknown office)')         AS office,
        COALESCE(NULLIF(office_code, ''), '')                    AS office_code,
        COUNT(*) AS opps_count,
        SUM(CASE WHEN active = 'Yes' THEN 1 ELSE 0 END) AS active_opps
    FROM opportunities
    WHERE {where_sql}
    GROUP BY department, office, office_code
    ORDER BY department ASC, opps_count DESC
    """
    with _connect_readonly() as conn:
        return pd.read_sql_query(sql, conn, params=params)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_by_department_summary(
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    One row per department with its rollup totals. Powers the top-level
    chart in the Offices tab so the user can see scale at a glance.

    Columns: department, opps_count, active_opps, office_count.
    """
    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    sql = f"""
    SELECT
        COALESCE(NULLIF(department, ''), '(unknown department)') AS department,
        COUNT(*) AS opps_count,
        SUM(CASE WHEN active = 'Yes' THEN 1 ELSE 0 END) AS active_opps,
        COUNT(DISTINCT COALESCE(NULLIF(office_code, ''), office)) AS office_count
    FROM opportunities
    WHERE {where_sql}
    GROUP BY department
    ORDER BY opps_count DESC
    """
    with _connect_readonly() as conn:
        return pd.read_sql_query(sql, conn, params=params)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def get_opportunities_for_office(
    office_code: Optional[str] = None,
    office_name: Optional[str] = None,
    active_only: bool = False,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    """
    Opportunities for a specific office. Pass office_code (preferred — unique)
    or office_name (fallback when an office has no code).

    Returns the same slim column set as get_opportunities_for_naics_code so
    both drill-down panels render consistently.
    """
    if not office_code and not office_name:
        return pd.DataFrame()

    where_sql, params = _build_opps_filters(active_only, date_from, date_to)
    params = list(params)
    if office_code:
        where_sql += " AND office_code = ?"
        params.append(office_code)
    else:
        where_sql += " AND office = ?"
        params.append(office_name)

    sql = f"""
    SELECT title, department, office, naics_code, set_aside_type,
           posted_date, response_deadline, active, description_url
    FROM opportunities
    WHERE {where_sql}
    ORDER BY posted_date DESC
    """
    with _connect_readonly() as conn:
        return pd.read_sql_query(sql, conn, params=params)