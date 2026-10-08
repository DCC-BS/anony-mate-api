"""A PDF marked for redaction, and what the marking found on it.

Two shapes of answer leave the marking job, decided by the caller's options:
the PDF itself with its marks on it, for an editor to review and apply; or the
marks alone as annotations, for an interface that shows the original PDF and
draws the marks over it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MarkAnnotation:
    """One mark as an interface draws it over the original PDF.

    The boxes are the mark's boxes, measured from the top left of the page as
    shown, in points — the same measurement the redaction mark on the PDF is
    built from, so an overlay drawn from these sits where the mark lands.
    A mention over two lines carries one box per line under a single id.
    """

    id: str
    label: str
    #: What the mark covers. Empty for a mark on a picture.
    text: str
    confidence: float
    #: Boxes as (page, left, top, right, bottom), pages 1-based.
    boxes: list[tuple[int, float, float, float, float]]


@dataclass(frozen=True)
class PdfAnnotations:
    """The marks of a PDF alone: nothing of the file itself leaves here."""

    annotations: list[MarkAnnotation]
    #: Width and height of each page in points, by 1-based page number. The
    #: interface reads the file itself, so it needs the sizes from here.
    page_sizes: dict[int, tuple[float, float]]


@dataclass(frozen=True)
class MarkedPdf:
    """A PDF handed back with redaction marks on it; served as the PDF itself, not as JSON."""

    filename: str
    content: bytes
    #: Number of redaction marks on it.
    marks: int
