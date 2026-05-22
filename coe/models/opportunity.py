"""
SQLAlchemy models for the SAM.gov opportunity data.

These mirror the SQLite schema that lives in `coe/puller/sqlite_db.py`
(SCHEMA_SQL) so that the puller and dashboard can run on Postgres
instead of a local file. Keep this module the single source of truth
for the Postgres-side puller tables.

Three tables:
    - opportunities          one row per unique notice_id
    - opportunity_offices    junction: which office query surfaced which opp
    - pull_history           audit log of puller runs

Note: `match_scores` from the SQLite layer is intentionally NOT modeled
here. The new scoring pipeline (coe/scoring/) writes to its own Postgres
tables via coe/models/scoring.py.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Text,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from coe.models.base import Base, TimestampMixin


class Opportunity(Base, TimestampMixin):
    """
    A single SAM.gov contract opportunity.

    Deduplicated on `notice_id` — the puller upserts on this column.
    Fields here are kept as TEXT/String to match the shape coming out of
    SAMClient.parse_opportunity(); we don't try to parse posted_date or
    response_deadline into Date columns here because SAM.gov occasionally
    returns blanks or odd formats. Casting happens at read time
    (dashboard queries do `CAST(NULLIF(posted_date, '') AS DATE)`).
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # SAM.gov identifiers
    notice_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    solicitation_number: Mapped[Optional[str]] = mapped_column(String(200))

    # Opportunity details
    title: Mapped[Optional[str]] = mapped_column(Text)
    notice_type: Mapped[Optional[str]] = mapped_column(String(50))
    base_type: Mapped[Optional[str]] = mapped_column(String(100))

    # Organization hierarchy
    department: Mapped[Optional[str]] = mapped_column(String(500))
    sub_tier: Mapped[Optional[str]] = mapped_column(String(500))
    office: Mapped[Optional[str]] = mapped_column(String(500))
    office_code: Mapped[Optional[str]] = mapped_column(String(50))

    # Classification
    naics_code: Mapped[Optional[str]] = mapped_column(String(20))
    classification_code: Mapped[Optional[str]] = mapped_column(String(20))
    set_aside_type: Mapped[Optional[str]] = mapped_column(String(200))
    set_aside_code: Mapped[Optional[str]] = mapped_column(String(50))

    # Dates (kept as strings — see class docstring)
    posted_date: Mapped[Optional[str]] = mapped_column(String(50))
    response_deadline: Mapped[Optional[str]] = mapped_column(String(50))
    archive_date: Mapped[Optional[str]] = mapped_column(String(50))

    # Award info (only populated on award notices)
    award_number: Mapped[Optional[str]] = mapped_column(String(200))
    award_amount: Mapped[Optional[float]] = mapped_column(Float)
    awardee_name: Mapped[Optional[str]] = mapped_column(String(500))

    # Place of performance
    pop_city: Mapped[Optional[str]] = mapped_column(String(100))
    pop_state: Mapped[Optional[str]] = mapped_column(String(100))

    # Links
    description_url: Mapped[Optional[str]] = mapped_column(Text)

    # Status — "Yes" / "No" string from SAM.gov (kept as-is for compatibility
    # with existing dashboard queries that filter `active = 'Yes'`)
    active: Mapped[Optional[str]] = mapped_column(String(10))

    # Raw API response, json-encoded. Used to detect changes on re-pull
    # and as a debugging breadcrumb. Could become JSONB later.
    raw_json: Mapped[Optional[str]] = mapped_column(Text)

    # Pull lifecycle timestamps (set by the puller, not Postgres defaults)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # created_at / updated_at come from TimestampMixin (server defaults)

    __table_args__ = (
        Index("idx_opp_naics", "naics_code"),
        Index("idx_opp_set_aside", "set_aside_code"),
        Index("idx_opp_notice_type", "notice_type"),
        Index("idx_opp_posted_date", "posted_date"),
        Index("idx_opp_active", "active"),
        Index("idx_opp_office_code", "office_code"),
        Index("idx_opp_response_deadline", "response_deadline"),
    )


class OpportunityOffice(Base):
    """
    Junction table: which office query surfaced which opportunity.

    A single opportunity can show up under multiple offices because we
    query SAM.gov per office_code. This table lets us answer "which
    of my tracked offices surfaced this opp first?".
    """

    __tablename__ = "opportunity_offices"

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    office_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    first_seen_via_office_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_opp_offices_code", "office_code"),
    )


class PullHistory(Base):
    """
    Audit log of puller runs, one row per office per attempt.

    Used by the puller to find the last successful pull timestamp per
    office (so incremental pulls only fetch new data) and by the
    dashboard to show "latest pull" KPIs.
    """

    __tablename__ = "pull_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    office_code: Mapped[str] = mapped_column(String(50), nullable=False)
    office_name: Mapped[Optional[str]] = mapped_column(String(500))
    pulled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    new_opportunities: Mapped[int] = mapped_column(Integer, default=0)
    updated_opportunities: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (
        Index("idx_pull_history_office", "office_code"),
    )
