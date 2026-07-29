from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from enum_types import CalculationType, JobFailureReason
from structures.schemas import StructureResponse


class JobResponseStatus(str, Enum):
    submitting = "submitting"
    submitted = "submitted"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobResponse(BaseModel):
    """Job response fields shared by every job endpoint."""

    job_id: UUID
    submitted_at: datetime
    group_id: Optional[UUID] = None
    user_sub: Optional[str] = None
    is_public: bool
    job_name: Optional[str] = None
    job_notes: Optional[str] = None
    filename: str
    status: JobResponseStatus
    calculation_type: CalculationType
    method: str
    basis_set: str
    charge: int
    multiplicity: int
    optimization_type: Optional[str] = None
    completed_at: Optional[datetime] = None
    runtime_seconds: Optional[int] = Field(default=None, ge=0)
    cancel_requested: bool
    failure_reason: Optional[JobFailureReason] = None
    failure_message: Optional[str] = None
    tags: List[str]
    structures: List[StructureResponse]


class AdminJobResponse(JobResponse):
    """Job response with administrator-only ownership details."""

    user_email: Optional[str] = None
    group_name: Optional[str] = None
