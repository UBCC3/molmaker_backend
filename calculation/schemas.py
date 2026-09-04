from pydantic import BaseModel


class JobResourceRange(BaseModel):
    default: int
    minimum: int
    maximum: int


class JobResourceSettingsResponse(BaseModel):
    can_customize: bool
    time_limit_minutes: JobResourceRange
    memory_mb: JobResourceRange
