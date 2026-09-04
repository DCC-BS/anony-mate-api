from pydantic import BaseModel, Field


class GlinerEntity(BaseModel):
    text: str = Field(description="The extracted entity text as it appears in the input.")
    confidence: float = Field(description="The model's confidence score for this entity, between 0 and 1.")
    start: int = Field(description="The character offset of the entity's start within the input text.")
    end: int = Field(description="The character offset of the entity's end within the input text.")


class GlinerInput(BaseModel):
    threshold: float = Field(description="Minimum confidence score for an entity to be included in the results.")
    include_confidence: bool = Field(description="Whether to include the confidence score for each entity.")
    include_spans: bool = Field(description="Whether to include the character spans (start/end) for each entity.")
    text: str = Field(description="The input text to run entity extraction on.")
    entity_types: list[str] | dict[str, str] = Field(
        description="The entity types to extract, either as a list of labels or a mapping of label to description."
    )


class GlinerProgress(BaseModel):
    current: int
    length: int
    progress: float


class GlinerResponse(BaseModel):
    entities: dict[str, list[GlinerEntity]] = Field(description="The extracted entities grouped by entity type.")
    # Only the streaming batch endpoint reports this; a task's progress is read
    # from the task itself while it runs.
    progress: GlinerProgress | None = None


class GlinerTaskState(BaseModel):
    """The state Gliner reports for a submitted extraction task while it runs."""

    status: str = Field(description="Where the task stands: pending, running, finished or failed.")
    progress: float | None = Field(
        default=None,
        description="Fraction of the work done, in [0, 1]; null while unknown",
    )
    error: str | None = Field(default=None, description="Set when the status is failed")
    resource_id: str | None = Field(
        default=None,
        description="Set once finished: fetch `GET /resource/{resource_id}` exactly once",
    )
