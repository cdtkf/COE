from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Text, Integer, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coe.models.base import Base, TimestampMixin

class ScoringRun(TimestampMixin, Base):
    """
    One scoring run = one opportunity scored by the LLM.
    """

    __tablename__ = "scoring_runs"

    id: Mapped[int] = mapped_column(primary_key=True)

    #links to opportunity (by notice_id since opps are still in SQLite)
    opportunity_notice_id: Mapped[str] = mapped_column(String(100), nullable=False)
    opportunity_title: Mapped[str] = mapped_column(String(1000))

    #Scores (0-100)
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_score: Mapped[int]  = mapped_column(Integer, default=0)
    naics_score: Mapped[int] = mapped_column(Integer, default=0)
    domain_score: Mapped[int] = mapped_column(Integer, default=0)
    set_aside_score: Mapped[int] = mapped_column(Integer, default=0)

    #LLM output
    work_summary: Mapped[Optional[str]] = mapped_column(Text)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    risk_factors: Mapped[Optional[str]] = mapped_column(Text) #JSON array

    #Metadata
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    scoring_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    #Which capability records were used
    retrieved_records: Mapped[List["ScoringRetrievedRecord"]] = relationship(
        back_populates="scoring_run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_scoring_notice_id", "opportunity_notice_id"), 
        Index("ix_scoring_overall", "overall_score"), 
    )

class ScoringRetrievedRecord(Base): 
    """
    Which capability records were fed to the LLM for a scoring run.
    """

    __tablename__ = "scoring_retrieved_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    scoring_run_id: Mapped[int] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False
    )

    # What was retrieved
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_name: Mapped[str] = mapped_column(String(500), nullable=False)

    # How it was retrieved
    retrieval_method: Mapped[str] = mapped_column(String(50), nullable=False)
    similarity_score: Mapped[Optional[float]] = mapped_column(Float)
    rank: Mapped[Optional[int]] = mapped_column(Integer)

    # Relationship back to parent
    scoring_run: Mapped["ScoringRun"] = relationship(back_populates="retrieved_records")
