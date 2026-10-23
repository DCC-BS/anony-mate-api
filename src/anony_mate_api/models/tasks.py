"""What the task endpoints answer: acceptance and state of a background job."""

from typing import Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["pending", "running", "finished", "failed"]
"""Where a submitted job can stand while it works its way to a result."""


class TaskAccepted(BaseModel):
    """Handed back the moment work is accepted, before it has run."""

    task_id: str = Field(description="Poll `GET /task/{task_id}` with this")


class TaskState(BaseModel):
    """Where a submitted job stands, cheap enough to poll in a loop."""

    task_id: str
    status: TaskStatus
    progress: float | None = Field(
        default=None,
        description="Fraction of the work done, in [0, 1]; null while unknown",
    )
    queue_position: int | None = Field(
        default=None,
        description="Place in the queue while the work is still waiting; null once it runs",
    )
    resource_id: str | None = Field(
        default=None,
        description="Set once finished: fetch `GET /resource/{resource_id}` exactly once",
    )
    error: str | None = Field(default=None, description="Set when the status is failed")
