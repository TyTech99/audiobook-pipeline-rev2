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


class JobSubmitIn(BaseModel):
    type: str
    input_format: str
    input_url: str
    metadata: dict = Field(default_factory=dict)
    page_range: list[int] | None = Field(default=None, description="Optional 1-based inclusive page range [start, end] for PDF processing")


class JobProgressIn(BaseModel):
    stage: str
    percent: int | None = None
    current_page: int | None = None
    total_pages: int | None = None
    current_chapter: int | None = None
    total_chapters: int | None = None
    current_segment: int | None = None
    total_segments: int | None = None
    estimated_minutes_remaining: int | None = None
    message: str | None = None


class JobStartIn(BaseModel):
    worker_id: str
    stage: str = "prep"


class JobCompleteIn(BaseModel):
    package_url: str | None = None
    output_url: str | None = None
    total_pages: int | None = None
    kept_pages: int | None = None
    skipped_pages: int | None = None
    voice_recipe: dict | None = None
    performance_style: str | None = None
    error: str | None = None
