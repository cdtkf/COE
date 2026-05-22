"""
Postgres-backed data access layer for the SAM.gov puller.

This is the Postgres counterpart to `coe/puller/sqlite_db.py`. It exposes
the same method surface that `coe/puller/puller.py` uses
(upsert_opportunity, record_pull, get_last_successful_pull, get_stats,
commit, close) so swapping the puller's import is a one-line change.

Why not just modify sqlite_db.py in place? Other modules (coe/scoring/,
coe/reporting/) still import the old SQLite Database class. Leaving the
SQLite layer untouched lets us migrate one consumer at a time. This
module is the puller's slice; the dashboard's slice is
`coe/dashboard/queries.py`. The scoring/reporting slices come later.

Connection: we use the shared SQLAlchemy engine from `coe.database`,
which reads DATABASE_URL from the environment. The puller never owns a
connection string of its own.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from coe.database import SessionLocal, engine
from coe.models.opportunity import Opportunity, OpportunityOffice, PullHistory

logger = logging.getLogger(__name__)


class Database:
    """
    Postgres database manager for the SAM.gov puller.

    Public API mirrors the SQLite Database class so callers don't need
    to learn a new interface. Holds a single long-lived Session for the
    duration of a puller run; commit() flushes pending writes.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: Ignored. Kept in the signature so callers that pass
                     a SQLite path don't break. The real connection URL
                     comes from DATABASE_URL via coe.database.engine.
        """
        if db_path:
            logger.debug(
                "db_path=%r passed to Postgres Database; ignored. "
                "Using DATABASE_URL from coe.database.",
                db_path,
            )
        self.session: Session = SessionLocal()
        logger.info(f"Database initialized: {engine.url}")

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------
    def upsert_opportunity(self, opp: dict, source_office_code: str) -> str:
        """
        Insert or update a single opportunity. Returns 'new', 'updated',
        or 'unchanged'.

        Deduplicates on notice_id. If the row already exists, compares
        the new raw_json against the stored raw_json — if they're equal,
        nothing is written (status 'unchanged').

        Always records the source_office_code in opportunity_offices
        (idempotently — duplicate (opp_id, office_code) pairs are
        silently ignored).
        """
        now = datetime.now(timezone.utc)
        raw_json_str = json.dumps(opp.get("raw_json", {}))
        notice_id = opp["notice_id"]

        existing: Optional[Opportunity] = self.session.scalar(
            select(Opportunity).where(Opportunity.notice_id == notice_id)
        )

        if existing is None:
            # New opportunity — insert.
            row = Opportunity(
                notice_id=notice_id,
                solicitation_number=opp["solicitation_number"],
                title=opp["title"],
                notice_type=opp["notice_type"],
                base_type=opp["base_type"],
                department=opp["department"],
                sub_tier=opp["sub_tier"],
                office=opp["office"],
                office_code=source_office_code,
                naics_code=opp["naics_code"],
                classification_code=opp["classification_code"],
                set_aside_type=opp["set_aside_type"],
                set_aside_code=opp["set_aside_code"],
                posted_date=opp["posted_date"],
                response_deadline=opp["response_deadline"],
                archive_date=opp["archive_date"],
                award_number=opp["award_number"],
                award_amount=opp["award_amount"],
                awardee_name=opp["awardee_name"],
                pop_city=opp["place_of_performance_city"],
                pop_state=opp["place_of_performance_state"],
                description_url=opp["description_url"],
                active=opp["active"],
                raw_json=raw_json_str,
                first_seen_at=now,
                last_updated_at=now,
            )
            self.session.add(row)
            self.session.flush()  # populate row.id
            opp_id = row.id
            result = "new"
        else:
            opp_id = existing.id
            if existing.raw_json == raw_json_str:
                result = "unchanged"
            else:
                existing.solicitation_number = opp["solicitation_number"]
                existing.title = opp["title"]
                existing.notice_type = opp["notice_type"]
                existing.base_type = opp["base_type"]
                existing.department = opp["department"]
                existing.sub_tier = opp["sub_tier"]
                existing.office = opp["office"]
                # NOTE: office_code on opportunities is the office that
                # originally created the notice — leave it alone on
                # update. The source_office_code parameter is recorded
                # separately in opportunity_offices below.
                existing.naics_code = opp["naics_code"]
                existing.classification_code = opp["classification_code"]
                existing.set_aside_type = opp["set_aside_type"]
                existing.set_aside_code = opp["set_aside_code"]
                existing.posted_date = opp["posted_date"]
                existing.response_deadline = opp["response_deadline"]
                existing.archive_date = opp["archive_date"]
                existing.award_number = opp["award_number"]
                existing.award_amount = opp["award_amount"]
                existing.awardee_name = opp["awardee_name"]
                existing.pop_city = opp["place_of_performance_city"]
                existing.pop_state = opp["place_of_performance_state"]
                existing.description_url = opp["description_url"]
                existing.active = opp["active"]
                existing.raw_json = raw_json_str
                existing.last_updated_at = now
                result = "updated"

        # Record which office surfaced this opportunity. ON CONFLICT
        # DO NOTHING via SQL because OpportunityOffice has a composite
        # PK and we want the insert to be a no-op on duplicate.
        self.session.execute(
            text(
                """INSERT INTO opportunity_offices
                       (opportunity_id, office_code, first_seen_via_office_at)
                   VALUES (:opp_id, :office_code, :first_seen)
                   ON CONFLICT (opportunity_id, office_code) DO NOTHING"""
            ),
            {
                "opp_id": opp_id,
                "office_code": source_office_code,
                "first_seen": now,
            },
        )

        return result

    # ------------------------------------------------------------------
    # Pull history
    # ------------------------------------------------------------------
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
    ) -> None:
        """Record a pull attempt in the history table."""
        self.session.add(
            PullHistory(
                office_code=office_code,
                office_name=office_name,
                pulled_at=datetime.now(timezone.utc),
                opportunities_found=found,
                new_opportunities=new,
                updated_opportunities=updated,
                status=status,
                error_message=error_message,
                duration_seconds=duration,
            )
        )
        self.session.commit()

    def get_last_successful_pull(self, office_code: str) -> Optional[datetime]:
        """
        Get the timestamp of the last successful pull for an office.
        Returns None if the office has never been pulled successfully.
        """
        ts: Optional[datetime] = self.session.scalar(
            select(PullHistory.pulled_at)
            .where(PullHistory.office_code == office_code)
            .where(PullHistory.status == "success")
            .order_by(PullHistory.pulled_at.desc())
            .limit(1)
        )
        return ts

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> dict:
        """Summary statistics, used by the puller's run summary log line."""
        total = self.session.scalar(select(func.count()).select_from(Opportunity))
        active = self.session.scalar(
            select(func.count())
            .select_from(Opportunity)
            .where(Opportunity.active == "Yes")
        )
        offices = self.session.scalar(
            select(func.count(func.distinct(OpportunityOffice.office_code)))
        )
        latest_pull = self.session.scalar(
            select(func.max(PullHistory.pulled_at))
            .where(PullHistory.status == "success")
        )
        return {
            "total_opportunities": total or 0,
            "active_opportunities": active or 0,
            "tracked_offices": offices or 0,
            "latest_pull": latest_pull.isoformat() if latest_pull else None,
        }

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def commit(self) -> None:
        """Flush pending writes to Postgres."""
        self.session.commit()

    def close(self) -> None:
        """Release the session."""
        self.session.close()

    # ------------------------------------------------------------------
    # Reset (used by puller's --reset flag)
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """
        Truncate all puller tables. Equivalent to deleting the SQLite
        file on the old code path. Cascades to opportunity_offices via
        the FK ON DELETE CASCADE.
        """
        logger.warning("Truncating opportunities, opportunity_offices, pull_history")
        # RESTART IDENTITY resets sequence counters so new IDs start
        # at 1 again — matches the "fresh DB" behavior of the old reset.
        self.session.execute(
            text(
                "TRUNCATE TABLE opportunities, opportunity_offices, "
                "pull_history RESTART IDENTITY CASCADE"
            )
        )
        self.session.commit()
