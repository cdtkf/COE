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