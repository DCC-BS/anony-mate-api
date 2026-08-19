from pydantic import BaseModel, Field


class RedactInput(BaseModel):
    text: str = Field(description="Text to redact")
    entity_types: list[str] | dict[str, str] = Field(
        description="Enity types to redact either a list of labels or a dict with the name of the label as key and the description of the label as value",
        default=[],
    )
    threshold: float = Field(description="Confidence threshold for redaction", default=0.8)
    blacklist: list[str] = Field(description="Blacklist of words to avoid redaction", default=[])


class RedactBatchInput(BaseModel):
    texts: list[str] = Field(description="Texts to redact, one output returned per text in the same order")
    entity_types: list[str] | dict[str, str] = Field(
        description="Enity types to redact either a list of labels or a dict with the name of the label as key and the description of the label as value",
        default=[],
    )
    threshold: float = Field(description="Confidence threshold for redaction", default=0.8)
    blacklist: list[str] = Field(description="Blacklist of words to avoid redaction", default=[])


class Entity(BaseModel):
    id: str = Field(description="ID of the entity")
    text: str = Field(description="Text of the entity")
    label: str = Field(description="Label of the entity")
    start: int = Field(description="Start char index of the entity")
    end: int = Field(description="End char index of the entity")
    confidence: float = Field(description="Confidence of the entity betweeen 0 and 1")


class RedactOutput(BaseModel):
    text: str = Field(description="Redacted text redacted word are written as [label:id] for example [person:1]")
    entities: dict[str, list[Entity]]
