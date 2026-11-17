from uuid import UUID

from pydantic import BaseModel, Field


class WorkingHoursIn(BaseModel):
    enabled: bool = True
    start: str
    end: str
    timezone: str


class InviteIn(BaseModel):
    node_id: str
    display_name: str = ""
    allowed_queues: list[str]
    working_hours: WorkingHoursIn


class RegisterIn(BaseModel):
    queues: list[str]
    hardware: dict
    working_hours: WorkingHoursIn


class PatchIn(BaseModel):
    display_name: str | None = None
    allowed_queues: list[str] | None = None
    working_hours: WorkingHoursIn | None = None


class ProbeIn(BaseModel):
    worker_type: str


class ProbeFailIn(BaseModel):
    result: str
    detail: str = ""


class WorkerRegisterIn(BaseModel):
    worker_id: UUID
    node_id: str
    type: str
    capabilities: dict = Field(default_factory=dict)
    queues: list[str]
    working_hours: dict | None = None
    probe_id: UUID | None = None


class WorkerHeartbeatIn(BaseModel):
    status: str
    current_job: str | None = None
    queue_depth: int | None = None
