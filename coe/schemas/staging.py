from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class ProposedRecordCreate(BaseModel):
    record_type: str = Field(
        ...,
        pattern="^(proposal|service_area|competency|past_performance|domain_experience)$",
    )
    source_file: str = Field(..., max_length=500)
    payload: Any  # Will be JSON-serialized before DB insert


class ProposedRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_type: str
    source_file: str
    payload: str
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    promoted_id: Optional[int]
    promoted_table: Optional[str]
    created_at: datetime
    updated_at: datetime