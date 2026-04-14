from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


# --- Proposals ---

class ProposalCreate(BaseModel):
    name: str = Field(..., max_length=500)
    source_file: str = Field(..., max_length=500)
    agency: Optional[str] = Field(None, max_length=200)
    contract_vehicle: Optional[str] = Field(None, max_length=200)
    naics_codes: Optional[List[str]] = None
    set_aside_qualifications: Optional[List[str]] = None
    summary: Optional[str] = None
    is_active: bool = True

class ProposalRead(ProposalCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# --- Service Areas ---

class ServiceAreaCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None

class ServiceAreaRead(ServiceAreaCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int 
    created_at: datetime
    updated_at: datetime

# --- Technical Compentencies ---
class TechnicalCompetencyCreate(BaseModel):
    name: str = Field(..., max_length=300)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)

class TechnicalCompetencyRead(TechnicalCompetencyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int 
    created_at: datetime
    updated_at: datetime

# --- Past Performances ---

class PastPerformanceCreate(BaseModel):
    proposal_id: int
    project_name: str = Field(..., max_length=500)
    agency: Optional[str] = Field(None, max_length=200)
    contract_number: Optional[str] = Field(None, max_length=100)
    contract_value: Optional[float] = None
    period_start: Optional[str] = Field(None, max_length=20)
    period_end: Optional[str] = Field(None, max_length=20)
    description: Optional[str] = None
    relevance_keywords: Optional[List[str]] = None


class PastPerformanceRead(PastPerformanceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


# --- Domain Experiences ---

class DomainExperienceCreate(BaseModel):
    proposal_id: int
    domain: str = Field(..., max_length=200)
    sub_domain: Optional[str] = Field(None, max_length=200)
    depth: str = Field("moderate", pattern="^(deep|moderate|light)$")
    notes: Optional[str] = None


class DomainExperienceRead(DomainExperienceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime