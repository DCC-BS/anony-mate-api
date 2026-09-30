"""How many pages a PDF has, counted here rather than taken from docling.

The count is what says whether docling read the whole file. Taken from
docling's own answer it would say nothing: a page it never read is a page it
never reports.
"""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError


class UnreadableDocument(ValueError):
    """The PDF can't be read completely, so nobody could look for names on all of it."""


def page_count(pdf: bytes) -> int:
    """How many pages the file has.

    Raises:
        UnreadableDocument: The file cannot be opened, or has no pages.
    """
    try:
        reader = PdfReader(BytesIO(pdf))
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnreadableDocument("the PDF needs a password to open")
        pages = len(reader.pages)
    except PyPdfError as error:
        raise UnreadableDocument(f"pypdf cannot read the file: {error!r}") from error
    if not pages:
        raise UnreadableDocument("the PDF has no pages")
    return pages
