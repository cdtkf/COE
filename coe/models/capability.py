from datetime import datetime
from typing import Optional, List

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    String, Text, Integer, Float, Boolean, ForeignKey,
    UniqueConstraint, Index, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coe.models.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# Junction tables (many-to-many)
# Simple, two foreign keys as a composite
# primary key. No extra columns, no timestamps needed.
# ---------------------------------------------------------------------------

class ProposalServiceArea(Base):
    __tablename__ = "proposal_service_areas"

    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True
    )
    service_area_id: Mapped[int] = mapped_column(
        ForeignKey("service_areas.id", ondelete="CASCADE"), primary_key=True
    )


class ProposalCompetency(Base):
    __tablename__ = "proposal_competencies"

    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True
    )
    competency_id: Mapped[int] = mapped_column(
        ForeignKey("technical_competencies.id", ondelete="CASCADE"), primary_key=True
    )



# ---------------------------------------------------------------------------
# Core tables
# ---------------------------------------------------------------------------

class Proposal(TimestampMixin, Base):
    """One row per proposal PDF. Everything traces back to this."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_file: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    agency: Mapped[Optional[str]] = mapped_column(String(200))
    contract_vehicle: Mapped[Optional[str]] = mapped_column(String(200))
    naics_codes: Mapped[Optional[str]] = mapped_column(Text)          # JSON array
    set_aside_qualifications: Mapped[Optional[str]] = mapped_column(Text)  # JSON array
    summary: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships — these don't create columns, they tell SQLAlchemy
    # how to load related objects when you access proposal.service_areas, etc.
    service_areas: Mapped[List["ServiceArea"]] = relationship(
        secondary="proposal_service_areas", back_populates="proposals"
    )
    competencies: Mapped[List["TechnicalCompetency"]] = relationship(
        secondary="proposal_competencies", back_populates="proposals"
    )
    past_performances: Mapped[List["PastPerformance"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )
    domain_experiences: Mapped[List["DomainExperience"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class ServiceArea(TimestampMixin, Base):
    """Normalized service categories shared across proposals."""

    __tablename__ = "service_areas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    proposals: Mapped[List["Proposal"]] = relationship(
        secondary="proposal_service_areas", back_populates="service_areas"
    )


class TechnicalCompetency(TimestampMixin, Base):
    """Individual skills/tools with embedding vectors for semantic search."""

    __tablename__ = "technical_competencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(100))

    # 768 dimensions = nomic-embed-text output size
    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))

    proposals: Mapped[List["Proposal"]] = relationship(
        secondary="proposal_competencies", back_populates="competencies"
    )

    # HNSW index for fast approximate nearest neighbor search
    __table_args__ = (
        Index(
            "ix_competency_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class PastPerformance(TimestampMixin, Base):
    """Past performance citations — belongs to one proposal."""

    __tablename__ = "past_performances"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    project_name: Mapped[str] = mapped_column(String(500), nullable=False)
    agency: Mapped[Optional[str]] = mapped_column(String(200))
    contract_number: Mapped[Optional[str]] = mapped_column(String(100))
    contract_value: Mapped[Optional[float]] = mapped_column(Float)
    period_start: Mapped[Optional[str]] = mapped_column(String(20))
    period_end: Mapped[Optional[str]] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(Text)
    relevance_keywords: Mapped[Optional[str]] = mapped_column(Text)  # JSON array

    embedding: Mapped[Optional[list]] = mapped_column(Vector(768))

    proposal: Mapped["Proposal"] = relationship(back_populates="past_performances")

    __table_args__ = (
        Index(
            "ix_past_perf_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DomainExperience(TimestampMixin, Base):
    """Agency/domain experience depth — belongs to one proposal."""

    __tablename__ = "domain_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(200), nullable=False)
    sub_domain: Mapped[Optional[str]] = mapped_column(String(200))
    depth: Mapped[str] = mapped_column(String(20), default="moderate")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    proposal: Mapped["Proposal"] = relationship(back_populates="domain_experiences")

    __table_args__ = (
        UniqueConstraint("proposal_id", "domain", "sub_domain", name="uq_domain_per_proposal"),
    )