"""
Database layer for SAM.gov Contract Opportunities Puller.

SQLite database with tables designed for:
1. Storing opportunity data with deduplication
2. Tracking which offices surfaced which opportunities
3. Recording pull history so we only fetch what's new
4. Supporting a future capability-matching system
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- Opportunities: the core table storing every unique contract opportunity
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- SAM.gov identifiers
    notice_id TEXT UNIQUE NOT NULL,
    solicitation_number TEXT,

    -- Opportunity details
    title TEXT,
    notice_type TEXT,          -- o, p, r, k, i, s, g
    base_type TEXT,            -- e.g., "Presolicitation", "Combined Synopsis/Solicitation"

    -- Organization hierarchy
    department TEXT,           -- Top-level department (e.g., "Department of Veterans Affairs")
    sub_tier TEXT,             -- Sub-tier agency
    office TEXT,               -- Office name
    office_code TEXT,          -- Office code used to query (e.g., "36C10B")

    -- Classification
    naics_code TEXT,
    classification_code TEXT,  -- Product/Service Code (PSC)
    set_aside_type TEXT,       -- Human-readable set-aside description
    set_aside_code TEXT,       -- Set-aside code (e.g., "SBA", "8A")

    -- Dates
    posted_date TEXT,
    response_deadline TEXT,
    archive_date TEXT,

    -- Award info (populated for award notices)
    award_number TEXT,
    award_amount REAL,
    awardee_name TEXT,

    -- Place of performance
    pop_city TEXT,
    pop_state TEXT,

    -- Links
    description_url TEXT,      -- Link to full opportunity on SAM.gov

    -- Status
    active TEXT,               -- "Yes" / "No" from API

    -- Raw API response (for future matching system)
    raw_json TEXT,

    -- Metadata
    first_seen_at TEXT NOT NULL,    -- When we first pulled this opportunity
    last_updated_at TEXT NOT NULL,  -- When we last saw/updated it
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Junction table: which offices surfaced which opportunities
-- (one opportunity can appear from multiple office queries)
CREATE TABLE IF NOT EXISTS opportunity_offices (
    opportunity_id INTEGER NOT NULL,
    office_code TEXT NOT NULL,
    first_seen_via_office_at TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, office_code),
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
);

-- Pull history: tracks when each office was last successfully pulled
CREATE TABLE IF NOT EXISTS pull_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    office_code TEXT NOT NULL,
    office_name TEXT,
    pulled_at TEXT NOT NULL,
    opportunities_found INTEGER DEFAULT 0,
    new_opportunities INTEGER DEFAULT 0,
    updated_opportunities INTEGER DEFAULT 0,
    status TEXT DEFAULT 'success',   -- 'success' or 'error'
    error_message TEXT,
    duration_seconds REAL
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for common queries the matching system will use
CREATE INDEX IF NOT EXISTS idx_opp_naics ON opportunities(naics_code);
CREATE INDEX IF NOT EXISTS idx_opp_set_aside ON opportunities(set_aside_code);
CREATE INDEX IF NOT EXISTS idx_opp_notice_type ON opportunities(notice_type);
CREATE INDEX IF NOT EXISTS idx_opp_posted_date ON opportunities(posted_date);
CREATE INDEX IF NOT EXISTS idx_opp_active ON opportunities(active);
CREATE INDEX IF NOT EXISTS idx_opp_office_code ON opportunities(office_code);
CREATE INDEX IF NOT EXISTS idx_opp_response_deadline ON opportunities(response_deadline);
CREATE INDEX IF NOT EXISTS idx_pull_history_office ON pull_history(office_code);
CREATE INDEX IF NOT EXISTS idx_opp_offices_code ON opportunity_offices(office_code);
"""

# Phase 2: Match scores table for AI-powered capability matching
MATCH_SCORES_SQL = """
-- Match scores: AI-generated opportunity-to-capability match scores
CREATE TABLE IF NOT EXISTS match_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Links to the opportunity
    opportunity_id INTEGER NOT NULL,

    -- Composite and sub-scores (0-100)
    overall_score INTEGER NOT NULL,
    domain_score INTEGER DEFAULT 0,
    capability_score INTEGER DEFAULT 0,
    naics_score INTEGER DEFAULT 0,
    set_aside_fit INTEGER DEFAULT 0,

    -- AI-generated analysis
    rationale TEXT,                -- 2-3 sentence explanation
    matched_profiles TEXT,         -- JSON array of proposal names that matched
    key_alignment_factors TEXT,    -- JSON array of alignment reasons
    risk_factors TEXT,             -- JSON array of concerns

    -- Metadata
    model_used TEXT,               -- Which LLM model scored this
    scored_at TEXT NOT NULL,        -- When scoring occurred
    raw_response TEXT,             -- Full LLM response for debugging

    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id),
    UNIQUE (opportunity_id)        -- One score per opportunity
);

-- Indexes for efficient retrieval
CREATE INDEX IF NOT EXISTS idx_match_overall_score ON match_scores(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_match_scored_at ON match_scores(scored_at);
"""


class Database:
    """SQLite database manager for contract opportunities."""

    def __init__(self, db_path: str):
        """
        Args:
            db_path: Path to the SQLite database file. Created if it doesn't exist.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent read perf
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        """Create tables and indexes if they don't exist."""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.executescript(MATCH_SCORES_SQL)
        # Track schema version
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION))
        )
        self.conn.commit()
        logger.info(f"Database initialized at {self.db_path} (schema v{SCHEMA_VERSION})")

    def upsert_opportunity(self, opp: dict, source_office_code: str) -> str:
        """
        Insert or update a single opportunity. Returns 'new', 'updated', or 'unchanged'.

        Deduplicates on notice_id. If the opportunity already exists, updates
        fields that may have changed (dates, status, award info).

        Args:
            opp: Parsed opportunity dict from SAMClient.parse_opportunity().
            source_office_code: The office code query that found this opportunity.

        Returns:
            'new' if inserted, 'updated' if modified, 'unchanged' if identical.
        """
        now = datetime.now().isoformat()
        raw_json_str = json.dumps(opp.get("raw_json", {}))
        notice_id = opp["notice_id"]

        # Check if this opportunity already exists
        existing = self.conn.execute(
            "SELECT id, raw_json FROM opportunities WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()

        if existing is None:
            # New opportunity — insert it
            self.conn.execute(
                """INSERT INTO opportunities (
                    notice_id, solicitation_number, title, notice_type, base_type,
                    department, sub_tier, office, office_code,
                    naics_code, classification_code, set_aside_type, set_aside_code,
                    posted_date, response_deadline, archive_date,
                    award_number, award_amount, awardee_name,
                    pop_city, pop_state, description_url, active,
                    raw_json, first_seen_at, last_updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?
                )""",
                (
                    notice_id, opp["solicitation_number"], opp["title"],
                    opp["notice_type"], opp["base_type"],
                    opp["department"], opp["sub_tier"], opp["office"],
                    source_office_code,
                    opp["naics_code"], opp["classification_code"],
                    opp["set_aside_type"], opp["set_aside_code"],
                    opp["posted_date"], opp["response_deadline"], opp["archive_date"],
                    opp["award_number"], opp["award_amount"], opp["awardee_name"],
                    opp["place_of_performance_city"], opp["place_of_performance_state"],
                    opp["description_url"], opp["active"],
                    raw_json_str, now, now,
                )
            )
            opp_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            result = "new"
        else:
            opp_id = existing["id"]
            # Check if anything actually changed
            if existing["raw_json"] == raw_json_str:
                result = "unchanged"
            else:
                # Update the opportunity with fresh data
                self.conn.execute(
                    """UPDATE opportunities SET
                        solicitation_number = ?, title = ?, notice_type = ?, base_type = ?,
                        department = ?, sub_tier = ?, office = ?,
                        naics_code = ?, classification_code = ?,
                        set_aside_type = ?, set_aside_code = ?,
                        posted_date = ?, response_deadline = ?, archive_date = ?,
                        award_number = ?, award_amount = ?, awardee_name = ?,
                        pop_city = ?, pop_state = ?, description_url = ?, active = ?,
                        raw_json = ?, last_updated_at = ?, updated_at = datetime('now')
                    WHERE id = ?""",
                    (
                        opp["solicitation_number"], opp["title"],
                        opp["notice_type"], opp["base_type"],
                        opp["department"], opp["sub_tier"], opp["office"],
                        opp["naics_code"], opp["classification_code"],
                        opp["set_aside_type"], opp["set_aside_code"],
                        opp["posted_date"], opp["response_deadline"], opp["archive_date"],
                        opp["award_number"], opp["award_amount"], opp["awardee_name"],
                        opp["place_of_performance_city"], opp["place_of_performance_state"],
                        opp["description_url"], opp["active"],
                        raw_json_str, now,
                        opp_id,
                    )
                )
                result = "updated"

        # Record which office surfaced this opportunity
        self.conn.execute(
            """INSERT OR IGNORE INTO opportunity_offices
               (opportunity_id, office_code, first_seen_via_office_at)
               VALUES (?, ?, ?)""",
            (opp_id, source_office_code, now)
        )

        return result

    def record_pull(
        self,
        office_code: str,
        office_name: str,
        found: int,
        new: int,
        updated: int,
        status: str = "success",
        error_message: Optional[str] = None,
        duration: Optional[float] = None,
    ):
        """Record a pull attempt in the history table."""
        self.conn.execute(
            """INSERT INTO pull_history
               (office_code, office_name, pulled_at, opportunities_found,
                new_opportunities, updated_opportunities, status, error_message,
                duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                office_code, office_name, datetime.now().isoformat(),
                found, new, updated, status, error_message, duration,
            )
        )
        self.conn.commit()

    def get_last_successful_pull(self, office_code: str) -> Optional[datetime]:
        """
        Get the timestamp of the last successful pull for an office.

        Returns:
            datetime of last pull, or None if never pulled.
        """
        row = self.conn.execute(
            """SELECT pulled_at FROM pull_history
               WHERE office_code = ? AND status = 'success'
               ORDER BY pulled_at DESC LIMIT 1""",
            (office_code,)
        ).fetchone()

        if row:
            return datetime.fromisoformat(row["pulled_at"])
        return None

    def get_stats(self) -> dict:
        """Get summary statistics for the database."""
        total = self.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
        active = self.conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE active = 'Yes'"
        ).fetchone()[0]
        offices = self.conn.execute(
            "SELECT COUNT(DISTINCT office_code) FROM opportunity_offices"
        ).fetchone()[0]
        latest_pull = self.conn.execute(
            "SELECT MAX(pulled_at) FROM pull_history WHERE status = 'success'"
        ).fetchone()[0]

        return {
            "total_opportunities": total,
            "active_opportunities": active,
            "tracked_offices": offices,
            "latest_pull": latest_pull,
        }

    # ------------------------------------------------------------------
    # Phase 2: Match scoring methods
    # ------------------------------------------------------------------

    def get_unscored_opportunities(self, limit: int = 50) -> list:
        """Get active opportunities that haven't been scored yet.

        Returns list of Row objects with all opportunity fields.
        """
        rows = self.conn.execute(
            """SELECT o.* FROM opportunities o
               LEFT JOIN match_scores ms ON o.id = ms.opportunity_id
               WHERE ms.id IS NULL AND o.active = 'Yes'
               ORDER BY o.posted_date DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return rows

    def get_all_unscored_opportunities(self) -> list:
        """Get ALL opportunities that haven't been scored (no limit)."""
        rows = self.conn.execute(
            """SELECT o.* FROM opportunities o
               LEFT JOIN match_scores ms ON o.id = ms.opportunity_id
               WHERE ms.id IS NULL
               ORDER BY o.posted_date DESC"""
        ).fetchall()
        return rows

    def insert_match_score(self, score: dict):
        """Insert a match score for an opportunity.

        Args:
            score: dict with keys matching match_scores columns:
                opportunity_id, overall_score, domain_score, capability_score,
                naics_score, set_aside_fit, rationale, matched_profiles,
                key_alignment_factors, risk_factors, model_used, scored_at,
                raw_response
        """
        self.conn.execute(
            """INSERT OR REPLACE INTO match_scores (
                opportunity_id, overall_score, domain_score, capability_score,
                naics_score, set_aside_fit, work_summary, rationale, matched_profiles,
                key_alignment_factors, risk_factors, model_used, scored_at,
                raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score["opportunity_id"],
                score["overall_score"],
                score.get("domain_score", 0),
                score.get("capability_score", 0),
                score.get("naics_score", 0),
                score.get("set_aside_fit", 0),
                score.get("work_summary", ""),
                score.get("rationale", ""),
                json.dumps(score.get("matched_profiles", [])),
                json.dumps(score.get("key_alignment_factors", [])),
                json.dumps(score.get("risk_factors", [])),
                score.get("model_used", ""),
                score["scored_at"],
                score.get("raw_response", ""),
            )
        )

    def get_scored_opportunities(
        self,
        min_score: int = 0,
        office_code: str = None,
        days: int = None,
        active_only: bool = False,
        limit: int = 500,
    ) -> list:
        """Get scored opportunities with their match scores, ordered by score.

        Returns joined rows with opportunity + score fields.
        """
        query = """
            SELECT o.*, ms.overall_score, ms.domain_score, ms.capability_score,
                   ms.naics_score, ms.set_aside_fit, ms.work_summary, ms.rationale,
                   ms.matched_profiles, ms.key_alignment_factors, ms.risk_factors,
                   ms.model_used, ms.scored_at
            FROM opportunities o
            INNER JOIN match_scores ms ON o.id = ms.opportunity_id
            WHERE ms.overall_score >= ?
        """
        params = [min_score]

        if office_code:
            query += " AND o.office_code = ?"
            params.append(office_code)

        if days:
            query += " AND o.posted_date >= date('now', ?)"
            params.append(f"-{days} days")

        if active_only:
            query += " AND o.active = 'Yes'"

        query += " ORDER BY ms.overall_score DESC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchall()

    def get_scoring_stats(self) -> dict:
        """Get summary statistics about scoring progress."""
        total_opps = self.conn.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]
        scored = self.conn.execute(
            "SELECT COUNT(*) FROM match_scores"
        ).fetchone()[0]
        avg_score = self.conn.execute(
            "SELECT AVG(overall_score) FROM match_scores"
        ).fetchone()[0]
        high_matches = self.conn.execute(
            "SELECT COUNT(*) FROM match_scores WHERE overall_score >= 70"
        ).fetchone()[0]
        medium_matches = self.conn.execute(
            "SELECT COUNT(*) FROM match_scores WHERE overall_score >= 40 AND overall_score < 70"
        ).fetchone()[0]

        return {
            "total_opportunities": total_opps,
            "scored": scored,
            "unscored": total_opps - scored,
            "avg_score": round(avg_score, 1) if avg_score else 0,
            "high_matches_70plus": high_matches,
            "medium_matches_40_69": medium_matches,
        }

    def commit(self):
        """Commit pending changes."""
        self.conn.commit()

    def close(self):
        """Close the database connection."""
        self.conn.close()
