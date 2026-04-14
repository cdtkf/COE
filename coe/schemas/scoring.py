from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class RetrievedRecordInfo(BaseModel):
    record_type: str
    record_id: int
    record_name: str
    retrieval_method: str
    similarity_score: Optional[float] = None
    rank: Optional[int] = None


class ScoringRunCreate(BaseModel):
    opportunity_notice_id: str = Field(..., max_length=100)
    opportunity_title: Optional[str] = Field(None, max_length=1000)
    overall_score: int = Field(..., ge=0, le=100)
    capability_score: int = Field(0, ge=0, le=100)
    naics_score: int = Field(0, ge=0, le=100)
    domain_score: int = Field(0, ge=0, le=100)
    set_aside_score: int = Field(0, ge=0, le=100)
    work_summary: Optional[str] = None
    rationale: Optional[str] = None
    risk_factors: Optional[List[str]] = None
    model_used: str = Field(..., max_length=100)
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    scoring_duration_ms: Optional[int] = None
    retrieved_records: List[RetrievedRecordInfo] = Field(default_factory=list)


class ScoringRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    opportunity_notice_id: str
    opportunity_title: Optional[str]
    overall_score: int
    capability_score: int
    naics_score: int
    domain_score: int
    set_aside_score: int
    work_summary: Optional[str]
    rationale: Optional[str]
    risk_factors: Optional[str]
    model_used: str
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    scoring_duration_ms: Optional[int]
    created_at: datetime
    updated_at: datetime