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
    repeat_everywhere: bool = Field(
        description=(
            "Carry every detection to every place its text stands, whatever its label, and find it there however "
            "the text breaks it (line breaks, hyphenation, letter spacing, accents, ligatures). For a copy nobody "
            "reviews, such as a redacted PDF."
        ),
        default=False,
    )
    drop_scraps: bool = Field(
        description="Drop detections that are only stopwords or have no run of two letters or digits",
        default=False,
    )


class RedactBatchInput(BaseModel):
    texts: list[str] = Field(description="Texts to redact, one output returned per text in the same order")
    entity_types: list[str] | dict[str, str] = Field(
        description="Enity types to redact either a list of labels or a dict with the name of the label as key and the description of the label as value",
        default=[],
    )
    threshold: float = Field(description="Confidence threshold for redaction", default=0.8)
    blacklist: list[str] = Field(description="Blacklist of words to avoid redaction", default=[])
    repeat_everywhere: bool = Field(
        description=(
            "Carry every detection to every place its text stands, whatever its label, and find it there however "
            "the text breaks it (line breaks, hyphenation, letter spacing, accents, ligatures). For a copy nobody "
            "reviews, such as a redacted PDF."
        ),
        default=False,
    )
    drop_scraps: bool = Field(
        description="Drop detections that are only stopwords or have no run of two letters or digits",
        default=False,
    )


class Entity(GlinerEntity):
    id: str = Field(description="ID of the entity")
    text: str = Field(description="Text of the entity")
    label: str = Field(description="Label of the entity")


class RedactOutput(BaseModel):
    text: str = Field(description="Redacted text redacted word are written as [label:id] for example [person:1]")
    entities: dict[str, list[Entity]]


class RedactFileOptions(BaseModel):
    """Everything a document needs redacted besides the file itself."""

    entity_types: list[str] | dict[str, str] = Field(
        description="Entity types to redact, either a list of labels or a dict of label to description",
        default=[],
    )
    threshold: float = Field(description="Confidence threshold for redaction", default=0.8)
    blacklist: list[str] = Field(description="Blacklist of words to avoid redaction", default=[])
    marked_pdf: bool = Field(
        description=(
            "Return the PDF itself with a redaction mark (an ISO 32000 /Redact annotation) on everything "
            "detected, instead of its text, for an editor such as Kofax Power PDF to review and apply. "
            "Only for PDFs."
        ),
        default=False,
    )


class DocumentRedactOutput(BaseModel):
    """A document converted and redacted in one submission."""

    text: str = Field(description="The converted document as markdown text, before redaction")
    page_offsets: list[int] = Field(
        description="Character offset where each page starts in `text`; empty for formats without pages",
        default_factory=list,
    )
    redacted_text: str = Field(description="`text` with every detection written as [label:id]")
    entities: dict[str, list[Entity]]
