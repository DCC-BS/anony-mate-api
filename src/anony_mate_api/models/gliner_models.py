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


class GlinerResponse(BaseModel):
    entities: dict[str, list[GlinerEntity]] = Field(description="The extracted entities grouped by entity type.")
