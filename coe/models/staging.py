from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from coe.models.base import Base, TimestampMixin


class ProposedRecord(TimestampMixin, Base):
    """
    Records extracted by Claude, pending human review.
    """
    __tablename__ = "proposed_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_file: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False) #JSON blob

    #Review Tracking
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)

    #Where record went after approval
    promoted_id: Mapped[Optional[int]] = mapped_column()
    promoted_table: Mapped[Optional[str]] = mapped_column(String(50))

