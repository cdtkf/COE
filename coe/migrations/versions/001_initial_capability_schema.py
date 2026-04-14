"""Initial capability corpus schema

Revision ID: 001
Revises: None
Create Date: 2026-04-14

Creates all structured capability tables for Phase 1.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -- Proposals --
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("source_file", sa.String(500), nullable=False, unique=True),
        sa.Column("agency", sa.String(200)),
        sa.Column("contract_vehicle", sa.String(200)),
        sa.Column("naics_codes", sa.Text),
        sa.Column("set_aside_qualifications", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # -- Service Areas --
    op.create_table(
        "service_areas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # -- Technical Competencies --
    op.create_table(
        "technical_competencies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(300), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Vector column and HNSW index via raw SQL (Alembic doesn't handle pgvector natively)
    op.execute("ALTER TABLE technical_competencies ADD COLUMN embedding vector(768)")
    op.execute("""
        CREATE INDEX ix_competency_embedding ON technical_competencies
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
    """)

    # -- Past Performances --
    op.create_table(
        "past_performances",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_name", sa.String(500), nullable=False),
        sa.Column("agency", sa.String(200)),
        sa.Column("contract_number", sa.String(100)),
        sa.Column("contract_value", sa.Float),
        sa.Column("period_start", sa.String(20)),
        sa.Column("period_end", sa.String(20)),
        sa.Column("description", sa.Text),
        sa.Column("relevance_keywords", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("ALTER TABLE past_performances ADD COLUMN embedding vector(768)")
    op.execute("""
        CREATE INDEX ix_past_perf_embedding ON past_performances
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
    """)

    # -- Domain Experiences --
    op.create_table(
        "domain_experiences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(200), nullable=False),
        sa.Column("sub_domain", sa.String(200)),
        sa.Column("depth", sa.String(20), default="moderate"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("proposal_id", "domain", "sub_domain", name="uq_domain_per_proposal"),
    )

    # -- Junction: Proposal <-> Service Area --
    op.create_table(
        "proposal_service_areas",
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("service_area_id", sa.Integer, sa.ForeignKey("service_areas.id", ondelete="CASCADE"), primary_key=True),
    )

    # -- Junction: Proposal <-> Competency --
    op.create_table(
        "proposal_competencies",
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("competency_id", sa.Integer, sa.ForeignKey("technical_competencies.id", ondelete="CASCADE"), primary_key=True),
    )

    # -- Proposed Records (staging) --
    op.create_table(
        "proposed_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("source_file", sa.String(500), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), default="pending", nullable=False),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_notes", sa.Text),
        sa.Column("promoted_id", sa.Integer),
        sa.Column("promoted_table", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # -- Scoring Runs --
    op.create_table(
        "scoring_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_notice_id", sa.String(100), nullable=False),
        sa.Column("opportunity_title", sa.String(1000)),
        sa.Column("overall_score", sa.Integer, nullable=False),
        sa.Column("capability_score", sa.Integer, default=0),
        sa.Column("naics_score", sa.Integer, default=0),
        sa.Column("domain_score", sa.Integer, default=0),
        sa.Column("set_aside_score", sa.Integer, default=0),
        sa.Column("work_summary", sa.Text),
        sa.Column("rationale", sa.Text),
        sa.Column("risk_factors", sa.Text),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("scoring_duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scoring_notice_id", "scoring_runs", ["opportunity_notice_id"])
    op.create_index("ix_scoring_overall", "scoring_runs", ["overall_score"])

    # -- Scoring Retrieved Records --
    op.create_table(
        "scoring_retrieved_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scoring_run_id", sa.Integer, sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_type", sa.String(50), nullable=False),
        sa.Column("record_id", sa.Integer, nullable=False),
        sa.Column("record_name", sa.String(500), nullable=False),
        sa.Column("retrieval_method", sa.String(50), nullable=False),
        sa.Column("similarity_score", sa.Float),
        sa.Column("rank", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("scoring_retrieved_records")
    op.drop_table("scoring_runs")
    op.drop_table("proposed_records")
    op.drop_table("proposal_competencies")
    op.drop_table("proposal_service_areas")
    op.drop_table("domain_experiences")
    op.drop_table("past_performances")
    op.drop_table("technical_competencies")
    op.drop_table("service_areas")
    op.drop_table("proposals")
    op.execute("DROP EXTENSION IF EXISTS vector")