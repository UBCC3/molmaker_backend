from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class StructureResponse(BaseModel):
    """Public structure fields included in another API response."""

    structure_id: UUID
    uploaded_at: datetime
    group_id: Optional[UUID] = None
    is_public: bool
    name: str
    formula: str
    location: str
    notes: Optional[str] = None
