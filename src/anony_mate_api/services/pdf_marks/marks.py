"""Proposals as redaction marks on the original PDF, for a person to review and apply.

A mark is the PDF standard's redaction annotation (``/Redact``, ISO 32000-1
§12.5.6.23): an area marked for redaction and not yet removed. Any editor
with a redaction function lists the marks, lets a reviewer drop or add some,
and removes what lies under them when told to apply them. Nothing is removed
here; the marked file holds everything the original does, and a list of
what in it is sensitive.

Every field set is a standard one: the author (``/T``), a subject naming the
kind of mark (``/Subj``), the category, the model's confidence and the text
the mark covers as the comment (``/Contents``), when it was marked, and a
colour per category, so a reviewer tells them apart at a glance.

The covered text is in the comment because a mark cannot always be read off
the page: text the page does not paint — a scanner's layer, white on white,
a name under a black bar — is marked where it stands, and a reviewer looking
at the paper sees a mark on nothing. The text is already in the file it
marks, so writing it in the comment gives nothing away, and applying the
mark removes the annotation along with what it covers.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

from anony_mate_api.services.pdf_marks.box import Box

#: Outline colour of a mark while it is not applied, per category.
COLOURS = {
    "person": (0.85, 0.1, 0.1),
    "organisation": (0.9, 0.5, 0.0),
    "adresse": (0.1, 0.35, 0.85),
    "telefonnummer": (0.1, 0.6, 0.2),
    "e-mail-adresse": (0.55, 0.2, 0.7),
    "signature": (0.0, 0.0, 0.0),
}
OTHER = (0.45, 0.45, 0.45)

#: What the area turns into when the mark is applied.
FILL = (0.0, 0.0, 0.0)

#: Annotation flag "print": the mark shows when the page is printed.
PRINT = 4

#: Longest covered text written into a comment. A mark covers a name, not a
#: paragraph, and a reader shows a comment in a small window.
COMMENT_LIMIT = 120


@dataclass(frozen=True)
class Mark:
    """One area to redact, in the displayed page's points from the top left."""

    box: Box
    label: str
    confidence: float
    #: What the mark covers. Empty for a mark on a picture, which covers no text.
    text: str


def annotate(original: bytes, marks: list[Mark], author: str, when: datetime | None = None) -> bytes:
    """The original with a redaction mark for each of ``marks``.

    The boxes are measured on the page as it is shown, upright; they are
    turned back into each page's own coordinates, whatever its rotation and
    crop box.
    """
    reader = PdfReader(BytesIO(original))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("the PDF needs a password to open")
    writer = PdfWriter(clone_from=reader)
    stamp = _pdf_date(when or datetime.now(UTC))
    for mark in marks:
        page = writer.pages[mark.box.page_no - 1]
        reference = writer._add_object(_redact(_user_rect(page, mark.box), mark, author, stamp))
        existing = page.get("/Annots")
        annotations = existing.get_object() if existing is not None else None
        if isinstance(annotations, ArrayObject):
            annotations.append(reference)
        else:
            page[NameObject("/Annots")] = ArrayObject([reference])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _redact(rect: tuple[float, float, float, float], mark: Mark, author: str, stamp: str) -> DictionaryObject:
    left, bottom, right, top = rect
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Redact"),
        NameObject("/Rect"): _numbers(rect),
        # One quadrilateral: upper left, upper right, lower left, lower right.
        NameObject("/QuadPoints"): _numbers((left, top, right, top, left, bottom, right, bottom)),
        NameObject("/C"): _numbers(COLOURS.get(mark.label, OTHER)),
        NameObject("/IC"): _numbers(FILL),
        NameObject("/F"): NumberObject(PRINT),
        NameObject("/T"): TextStringObject(author),
        NameObject("/Subj"): TextStringObject(f"Schwärzung: {mark.label}"),
        NameObject("/Contents"): TextStringObject(_comment(mark)),
        NameObject("/NM"): TextStringObject(str(uuid4())),
        NameObject("/CreationDate"): TextStringObject(stamp),
        NameObject("/M"): TextStringObject(stamp),
    })


def _comment(mark: Mark) -> str:
    """What a reviewer reads on the mark: its category, how sure the model
    was, and the text it covers.

    >>> _comment(Mark(Box(1, 0, 0, 1, 1), "person", 0.98, "Ruth Zehnder"))
    'person, Konfidenz 0.98: «Ruth Zehnder»'
    """
    said = f"{mark.label}, Konfidenz {mark.confidence:.2f}"
    covered = " ".join(mark.text.split())
    if not covered:
        return said
    if len(covered) > COMMENT_LIMIT:
        covered = covered[: COMMENT_LIMIT - 1] + "…"
    return f"{said}: «{covered}»"


def _user_rect(page, box: Box) -> tuple[float, float, float, float]:
    """``box``, measured from the top left of the page as shown, in the page's
    own coordinates: from the bottom left of its media, before rotation."""
    x0, y0 = float(page.cropbox.left), float(page.cropbox.bottom)
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    rotation = page.rotation % 360
    corners = [(box.l, box.t), (box.r, box.b)]
    points = []
    for u, v in corners:
        if rotation == 0:
            x, y = u, height - v
        elif rotation == 90:
            x, y = v, u
        elif rotation == 180:
            x, y = width - u, v
        else:
            x, y = width - v, height - u
        points.append((x0 + x, y0 + y))
    (xa, ya), (xb, yb) = points
    return min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb)


def _numbers(values) -> ArrayObject:
    return ArrayObject([FloatObject(round(value, 3)) for value in values])


def _pdf_date(when: datetime) -> str:
    return when.astimezone(UTC).strftime("D:%Y%m%d%H%M%SZ")
