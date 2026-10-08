from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field

from anony_mate_api.models.gliner_models import GlinerEntity
from anony_mate_api.models.marked_pdf import PdfAnnotations


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
    pdf_annotations: bool = Field(
        description=(
            "Return only what was detected as marks with their boxes on the page, instead of the marked "
            "PDF: the caller keeps the file and draws the marks over it. Only for PDFs."
        ),
        default=False,
    )


class MarkBox(BaseModel):
    """One marked area of a page, in points from the top left."""

    page: int = Field(description="1-based page the box stands on", ge=1)
    left: float = Field(description="Distance from the page's left edge, in points")
    top: float = Field(description="Distance from the page's top edge, in points")
    right: float = Field(description="Distance of the right edge, in points")
    bottom: float = Field(description="Distance of the bottom edge, in points")

    @classmethod
    def parse(cls, value: "MarkBox | dict | tuple") -> "MarkBox":
        """The box from either of its two shapes: named, or the bare
        (page, left, top, right, bottom) array a caller still sends."""
        if isinstance(value, MarkBox):
            return value
        if isinstance(value, dict):
            return cls.model_validate(value)
        page, left, top, right, bottom = value
        return cls(page=page, left=left, top=top, right=right, bottom=bottom)


#: One box, in either of its two shapes: named, or the bare
#: (page, left, top, right, bottom) array a caller may still send.
AnyBox = Annotated[
    MarkBox | tuple[int, float, float, float, float],
    BeforeValidator(lambda value: MarkBox.parse(value)),
]


class MarkAnnotationInput(BaseModel):
    """One mark a caller reviewed and now wants written onto the PDF.

    The id is carried through to the annotation's ``/NM``, so a mark stays the
    same annotation across passes. An ``overlay`` is written onto the area the
    mark leaves when it is applied; without one the area is filled.
    """

    id: str = Field(description="The mark's id, kept as the annotation's /NM")
    label: str = Field(description="Label of the detection the mark covers")
    confidence: float = Field(description="Confidence of the detection", default=1.0)
    text: str = Field(description="Text the mark covers; empty for a mark on a picture", default="")
    boxes: list[AnyBox] = Field(
        description=(
            "One per line the mention runs over, as an object {page, left, top, right, bottom} "
            "or the bare array of the five; pages 1-based, points from the top left"
        ),
    )
    overlay: str | None = Field(
        description="Text written into the area when the mark is applied, e.g. a placeholder",
        default=None,
    )

    def mark_boxes(self) -> "list[MarkBox]":
        """This mark's boxes as named ones, whatever shape they came in."""
        return [MarkBox.parse(box) for box in self.boxes]


class MarkAnnotationOutput(BaseModel):
    """One mark a caller draws over the PDF it kept."""

    id: str = Field(description="The mark's id, kept as its annotation's /NM on the PDF")
    label: str = Field(description="Label of the detection the mark covers")
    text: str = Field(description="What the mark covers; empty for a mark on a picture")
    confidence: float = Field(description="Confidence of the detection")
    boxes: list[MarkBox] = Field(description="One box per line the mention runs over")


class PdfAnnotationsOutput(BaseModel):
    """What a PDF scanned for redaction answers with, instead of the marked PDF."""

    annotations: list[MarkAnnotationOutput] = Field(description="The marks, in detection order")
    page_sizes: dict[str, list[float]] = Field(
        description="Width and height of each page in points, by 1-based page number",
    )

    @classmethod
    def of(cls, result: "PdfAnnotations") -> "PdfAnnotationsOutput":
        """The output of a marking job's answer, as the contract reads it."""
        return cls(
            annotations=[
                MarkAnnotationOutput(
                    id=annotation.id,
                    label=annotation.label,
                    text=annotation.text,
                    confidence=annotation.confidence,
                    boxes=[
                        MarkBox(page=box[0], left=box[1], top=box[2], right=box[3], bottom=box[4])
                        for box in annotation.boxes
                    ],
                )
                for annotation in result.annotations
            ],
            page_sizes={str(page): [width, height] for page, (width, height) in result.page_sizes.items()},
        )


class RedactPdfAnnotateInput(BaseModel):
    """Marks to write onto a PDF for redaction."""

    annotations: list[MarkAnnotationInput] = Field(description="The reviewed marks, as they are to be applied")


class DocumentRedactOutput(BaseModel):
    """A document converted and redacted in one submission."""

    text: str = Field(description="The converted document as markdown text, before redaction")
    page_offsets: list[int] = Field(
        description="Character offset where each page starts in `text`; empty for formats without pages",
        default_factory=list,
    )
    redacted_text: str = Field(description="`text` with every detection written as [label:id]")
    entities: dict[str, list[Entity]]
