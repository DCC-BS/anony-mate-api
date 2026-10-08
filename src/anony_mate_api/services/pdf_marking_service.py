"""Marking a PDF for redaction instead of turning it into text.

The same question as the text path — what in this document is sensitive — is
asked of the same model over the same kind of text, but each word of that text
knows where it stands on the page. So each detection becomes a redaction mark
on the original PDF, for a person to review and apply in an editor such as
Kofax Power PDF.

Two services take part: docling-serve for the layout, the pictures and every
word with its box, and GLiNER through ``RedactService`` for what is
sensitive. This module puts the answers where they belong.
"""

import asyncio
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import status

from anony_mate_api.models.error_codes import UNREADABLE_PDF
from anony_mate_api.models.marked_pdf import MarkAnnotation, MarkedPdf, PdfAnnotations
from anony_mate_api.models.redact_models import (
    MarkAnnotationInput,
    RedactFileOptions,
    RedactInput,
)
from anony_mate_api.services.document_conversion_service import DocumentConversionService
from anony_mate_api.services.pdf_marks.box import Box
from anony_mate_api.services.pdf_marks.marks import Mark, annotate
from anony_mate_api.services.pdf_marks.pdf import UnreadableDocument, page_count
from anony_mate_api.services.pdf_marks.words import Layout, Picture, TextUnit, read_layout
from anony_mate_api.services.redact_service import RedactService

logger = get_logger("pdf_marking_service")

#: Between two units, so the model never reads the end of one and the start of
#: the next as one name.
UNIT_SEPARATOR = "\n\n"

#: Picture classes marked whole: a signature is no text to detect, so its
#: class is all there is to go on.
PICTURE_CLASSES = frozenset({"signature"})

#: How much of the smaller of two marks the larger has to cover for the two to
#: be one. A name read twice — once in a page's own invisible text layer, once
#: from the pixels — is marked twice, in boxes of different height around the
#: same words. Distinct names stand apart and are never this close.
SAME_MARK = 0.6

#: Lowest classifier score at which a picture counts as one of those classes.
#: Any guess above it counts, not only the first: a signature the classifier
#: half takes for a logo is still a signature.
PICTURE_CONFIDENCE = 0.2


@dataclass(frozen=True)
class Mention:
    """One detection: a mention of something, cut back into spans per unit."""

    label: str
    text: str
    confidence: float
    #: Where the mention starts in the text the model read, for ordering.
    start: int
    #: (unit, start, end) per unit the mention touches, within the unit's text.
    spans: list[tuple[int, int, int]]


class PdfMarkingService:
    def __init__(
        self,
        document_conversion_service: DocumentConversionService,
        redact_service: RedactService,
        author: str,
    ) -> None:
        self.document_conversion_service = document_conversion_service
        self.redact_service = redact_service
        self.author = author

    async def mark(
        self,
        pdf: bytes,
        filename: str,
        settings: RedactFileOptions,
        on_status: Callable[[str, int | None], None] | None = None,
        on_progress: Callable[[float | None], None] | None = None,
    ) -> MarkedPdf | PdfAnnotations:
        """The original PDF with a redaction mark on everything detected in it.

        Args:
            pdf: The uploaded PDF, as it came.
            filename: Its name, kept for the result.
            settings: Entity types, threshold and blacklist, as for the text path.
                With ``pdf_annotations`` set, the answer is the marks alone, for
                an interface that keeps the file and draws the marks over it.
            on_status: Docling's status and queue position while it reads the layout.
            on_progress: The detection's progress, once it runs.

        Raises:
            ApiErrorException: The PDF cannot be read completely, or a service failed.
        """
        pages = _pages(pdf)
        read = await self.document_conversion_service.read_layout(pdf, filename, on_status)
        if not read.pages:
            raise _unreadable(
                "docling-serve returned no words: it does not answer include_word_boxes, which marking a PDF needs"
            )
        unread = [number for number in range(1, pages + 1) if number not in read.pages]
        if unread:
            raise _unreadable(f"Docling read no words on pages {unread}")

        layout = read_layout(read.document, read.pages)
        mentions = await self._detect(layout, settings, on_progress)
        marks = merge_same(_picture_marks(layout.pictures) + _span_marks(mentions, layout))
        logger.info("Marked PDF", filename=filename, pages=len(layout.page_sizes), marks=len(marks))

        if settings.pdf_annotations:
            return PdfAnnotations(annotations=_annotations_of(marks), page_sizes=layout.page_sizes)

        marked = await asyncio.to_thread(annotate, pdf, marks, self.author)
        return MarkedPdf(filename=_marked_name(filename), content=marked, marks=len(marks))

    async def write_annotations(
        self,
        pdf: bytes,
        filename: str,
        annotations: list[MarkAnnotationInput],
    ) -> MarkedPdf:
        """The PDF with the marks of a finished review written on it.

        The marks are the caller's to name: an id becomes the annotation's
        ``/NM``, so a later pass recognises what it wrote, and an overlay is
        written where the caller chose a placeholder for the area. One
        annotation per box: a mention over two lines is two areas to redact,
        named after the one mark they belong to.

        Raises:
            ValueError: A box names a page the PDF does not have, or carries
                it upside down.
        """
        marks = [
            Mark(
                Box(box.page, box.left, box.top, box.right, box.bottom),
                annotation.label,
                annotation.confidence,
                annotation.text,
                id=annotation.id,
                overlay=annotation.overlay,
            )
            for annotation in annotations
            for box in annotation.mark_boxes()
        ]
        pages = _pages(pdf)
        past = sorted({mark.box.page_no for mark in marks if mark.box.page_no > pages})
        if past:
            raise ValueError(f"box names page {past[0]}, past the {pages} the PDF has")

        marked = await asyncio.to_thread(annotate, pdf, marks, self.author)
        return MarkedPdf(filename=_marked_name(filename), content=marked, marks=len(marks))

    async def _detect(
        self,
        layout: Layout,
        settings: RedactFileOptions,
        on_progress: Callable[[float | None], None] | None,
    ) -> list[Mention]:
        """What anonymate finds in the layout's text, with the boxes it covers.

        One call over all of it, so a name found on page 3 is carried to its
        repeats on page 30. Nobody reviews the text in between, so every
        detection is carried everywhere and scraps are dropped.

        The mentions are in the text's order — GLiNER reports per label, so
        they are sorted back across the labels by their offset.
        """
        units = layout.units
        text, offsets = join_units(units)
        if not text.strip():
            return []
        output = await self.redact_service.redact(
            RedactInput(
                text=text,
                entity_types=settings.entity_types,
                threshold=settings.threshold,
                blacklist=settings.blacklist,
                repeat_everywhere=True,
                drop_scraps=True,
            ),
            on_progress,
        )
        mentions: list[Mention] = [
            Mention(
                label=label,
                text=entity.text,
                confidence=entity.confidence,
                start=entity.start,
                spans=split(entity.start, entity.end, offsets, units),
            )
            for label, entities in output.entities.items()
            for entity in entities
        ]
        return sorted(mentions, key=lambda mention: mention.start)


def _annotations_of(marks: list[Mark]) -> list[MarkAnnotation]:
    """The marks grouped into one annotation per mention.

    Marks of the same id belong to one mention — a name over two lines, a
    scan read twice — and are drawn as one mark over all of them; a picture
    mark stands in an id of its own. The first-seen order is kept, which is
    the order the mentions were detected in.
    """
    grouped: dict[str, MarkAnnotation] = {}
    for mark in marks:
        key = mark.id or f"picture:{mark.box.page_no}:{mark.box.t}:{mark.box.l}"
        found = grouped.get(key)
        if found is None:
            grouped[key] = MarkAnnotation(
                id=key,
                label=mark.label,
                confidence=mark.confidence,
                text=mark.text,
                boxes=[(mark.box.page_no, mark.box.l, mark.box.t, mark.box.r, mark.box.b)],
            )
        else:
            found.boxes.append((mark.box.page_no, mark.box.l, mark.box.t, mark.box.r, mark.box.b))
    return list(grouped.values())


def join_units(units: list[TextUnit]) -> tuple[str, list[int]]:
    """The text the model reads, and where each unit starts in it."""
    offsets: list[int] = []
    text = ""
    for unit in units:
        if text:
            text += UNIT_SEPARATOR
        offsets.append(len(text))
        text += unit.text
    return text, offsets


def split(start: int, end: int, offsets: list[int], units: list[TextUnit]) -> list[tuple[int, int, int]]:
    """Cut a detection at unit boundaries, as (unit, start, end) per unit.

    The offsets are character spans within their unit's text; empty ones are
    left out, so a boundary the mention only grazes contributes nothing.
    """
    spans = []
    index = max(0, bisect_right(offsets, start) - 1)
    while index < len(offsets) and offsets[index] < end:
        offset = offsets[index]
        lo, hi = max(start, offset), min(end, offset + len(units[index].text))
        if lo < hi:
            spans.append((index, lo - offset, hi - offset))
        index += 1
    return spans


def _span_marks(mentions: list[Mention], layout: Layout) -> list[Mark]:
    """Every mention's boxes, as one mark per box, all carrying the mention.

    The mention is what the review decides about, so its boxes stay one to a
    caller that draws them: the id names the group, and the annotations it
    becomes keep it. A picture mark carries none and stands alone.
    """
    marks: list[Mark] = []
    for number, mention in enumerate(mentions):
        mention_id = f"d{number + 1}"
        for unit, start, end in mention.spans:
            for box in layout.boxes(unit, start, end):
                marks.append(Mark(box, mention.label, mention.confidence, mention.text, id=mention_id))
    return marks


def merge_same(marks: list[Mark]) -> list[Mark]:
    """One mark where two cover the same words.

    A page whose own text layer is invisible — a scan, a publication copy —
    holds every name twice: once as text nobody sees, once in the picture OCR
    read. Both have to go, and one box over both says so once. The box is the
    union, so nothing either of them covered is left out. The mark that
    noticed them first stays the owner of the id, so the group it names keeps
    one member more rather than losing its name.
    """
    merged: list[Mark] = []
    for mark in marks:
        for index, other in enumerate(merged):
            if other.label != mark.label:
                continue
            if max(mark.box.covered_by(other.box), other.box.covered_by(mark.box)) < SAME_MARK:
                continue
            keep = other if other.confidence >= mark.confidence else mark
            merged[index] = Mark(
                other.box.union(mark.box),
                keep.label,
                keep.confidence,
                keep.text,
                id=other.id or mark.id,
            )
            break
        else:
            merged.append(mark)
    return merged


def _picture_marks(pictures: list[Picture]) -> list[Mark]:
    marks = []
    for picture in pictures:
        found = [(picture.classes.get(name, 0.0), name) for name in PICTURE_CLASSES]
        confidence, name = max(found)
        if confidence >= PICTURE_CONFIDENCE:
            marks.append(Mark(picture.box, name, confidence, text=""))
    return marks


def _pages(pdf: bytes) -> int:
    try:
        return page_count(pdf)
    except UnreadableDocument as error:
        raise _unreadable(str(error)) from error


def _unreadable(message: str) -> ApiErrorException:
    logger.warning("PDF cannot be marked", reason=message)
    return ApiErrorException({
        "errorId": UNREADABLE_PDF,
        "status": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "debugMessage": message,
    })


def _marked_name(filename: str) -> str:
    """``bericht.pdf`` becomes ``bericht.markiert.pdf``."""
    path = Path(filename)
    return f"{path.stem}.markiert{path.suffix or '.pdf'}"
