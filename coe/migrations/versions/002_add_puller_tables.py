"""Add puller tables (opportunities, opportunity_offices, pull_history)

Revision ID: 002
Revises: 001
Create Date: 2026-05-21

Brings the SAM.gov puller's three core tables onto Postgres so the puller
and dashboard can share a single hosted database (Neon) instead of a
local SQLite file. The schema is a faithful port of `SCHEMA_SQL` in
`coe/puller/sqlite_db.py` with two intentional upgrades:

    1. Timestamp columns (first_seen_at, last_updated_at, pulled_at,
       first_seen_via_office_at) become `TIMESTAMP WITH TIME ZONE`
       instead of ISO strings. Lets the dashboard do real date math.
    2. opportunity_offices.opportunity_id has ON DELETE CASCADE so
       deleting an opportunity cleans up its junction rows automatically.

Intentionally NOT migrated: `match_scores` and `schema_meta`. The new
scoring pipeline writes to its own Postgres tables (see migration 001
and coe/models/scoring.py); the SQLite `match_scores` table is legacy
and will be retired separately. `schema_meta` is replaced by Alembic's
`alembic_version` table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- opportunities --
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer, primary_key=True),
        # SAM.gov identifiers
        sa.Column("notice_id", sa.String(200), nullable=False, unique=True),
        sa.Column("solicitation_number", sa.String(200)),
        # Opportunity details
        sa.Column("title", sa.Text),
        sa.Column("notice_type", sa.String(50)),
        sa.Column("base_type", sa.String(100)),
        # Organization hierarchy
        sa.Column("department", sa.String(500)),
        sa.Column("sub_tier", sa.String(500)),
        sa.Column("office", sa.String(500)),
        sa.Column("office_code", sa.String(50)),
        # Classification
        sa.Column("naics_code", sa.String(20)),
        sa.Column("classification_code", sa.String(20)),
        sa.Column("set_aside_type", sa.String(200)),
        sa.Column("set_aside_code", sa.String(50)),
        # Dates (kept as strings — SAM.gov shape is inconsistent;
        # dashboard queries cast at read time)
        sa.Column("posted_date", sa.String(50)),
        sa.Column("response_deadline", sa.String(50)),
        sa.Column("archive_date", sa.String(50)),
        # Award info
        sa.Column("award_number", sa.String(200)),
        sa.Column("award_amount", sa.Float),
        sa.Column("awardee_name", sa.String(500)),
        # Place of performance
        sa.Column("pop_city", sa.String(100)),
        sa.Column("pop_state", sa.String(100)),
        # Links
        sa.Column("description_url", sa.Text),
        # Status
        sa.Column("active", sa.String(10)),
        # Raw API response (json-encoded string)
        sa.Column("raw_json", sa.Text),
        # Pull lifecycle timestamps — set by the puller
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        # Bookkeeping — server-managed
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_opp_naics", "opportunities", ["naics_code"])
    op.create_index("idx_opp_set_aside", "opportunities", ["set_aside_code"])
    op.create_index("idx_opp_notice_type", "opportunities", ["notice_type"])
    op.create_index("idx_opp_posted_date", "opportunities", ["posted_date"])
    op.create_index("idx_opp_active", "opportunities", ["active"])
    op.create_index("idx_opp_office_code", "opportunities", ["office_code"])
    op.create_index(
        "idx_opp_response_deadline", "opportunities", ["response_deadline"]
    )

    # -- opportunity_offices (junction) --
    op.create_table(
        "opportunity_offices",
        sa.Column(
            "opportunity_id",
            sa.Integer,
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("office_code", sa.String(50), primary_key=True),
        sa.Column(
            "first_seen_via_office_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_opp_offices_code", "opportunity_offices", ["office_code"]
    )

    # -- pull_history (audit log of pull runs) --
    op.create_table(
        "pull_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("office_code", sa.String(50), nullable=False),
        sa.Column("office_name", sa.String(500)),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opportunities_found", sa.Integer, server_default="0"),
        sa.Column("new_opportunities", sa.Integer, server_default="0"),
        sa.Column("updated_opportunities", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="success"),
        sa.Column("error_message", sa.Text),
        sa.Column("duration_seconds", sa.Float),
    )
    op.create_index("idx_pull_history_office", "pull_history", ["office_code"])


def downgrade() -> None:
    # Indexes are dropped automatically with the tables in Postgres, but
    # being explicit makes the inverse operation auditable.
    op.drop_index("idx_pull_history_office", table_name="pull_history")
    op.drop_table("pull_history")

    op.drop_index("idx_opp_offices_code", table_name="opportunity_offices")
    op.drop_table("opportunity_offices")

    op.drop_index("idx_opp_response_deadline", table_name="opportunities")
    op.drop_index("idx_opp_office_code", table_name="opportunities")
    op.drop_index("idx_opp_active", table_name="opportunities")
    op.drop_index("idx_opp_posted_date", table_name="opportunities")
    op.drop_index("idx_opp_notice_type", table_name="opportunities")
    op.drop_index("idx_opp_set_aside", table_name="opportunities")
    op.drop_index("idx_opp_naics", table_name="opportunities")
    op.drop_table("opportunities")
