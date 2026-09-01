from pydantic import BaseModel, Field

from anony_mate_api.models.gliner_models import GlinerEntity


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


class Entity(GlinerEntity):
    id: str = Field(description="ID of the entity")
    text: str = Field(description="Text of the entity")
    label: str = Field(description="Label of the entity")


class RedactOutput(BaseModel):
    text: str = Field(description="Redacted text redacted word are written as [label:id] for example [person:1]")
    entities: dict[str, list[Entity]]
