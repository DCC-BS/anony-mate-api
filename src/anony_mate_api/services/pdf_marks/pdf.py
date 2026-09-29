"""A PDF the way it is shown: form values, annotations and rotation folded in.

PDFium flattens; pypdf turns rotated pages upright. Rendering pages as
pictures is dcc-doctr-api's, which reads them.

PDFium is not thread-safe: two calls at once, or a page freed on one thread
while another renders, crash the whole process. So every PDFium call here
runs on one thread of its own (``in_pdfium``), and every document and page
is closed before the call returns: nothing of PDFium outlives it, to be
freed later on some other thread.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

_PDFIUM = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")


async def in_pdfium[**P, R](function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Run a function that calls PDFium on the one thread PDFium is called from."""
    return await asyncio.get_running_loop().run_in_executor(_PDFIUM, lambda: function(*args, **kwargs))


class UnreadableDocument(ValueError):
    """The PDF can't be read completely, so nobody could look for names on all of it."""


def flatten(pdf: bytes) -> bytes:
    """Fold form fields, annotations and page rotation into the page content.

    What a form field or a sticky note shows is drawn on top of the page but
    kept apart from its text, so a text reader passes it by. A rotated page
    is drawn turned but stored upright, and docling reads no text on it at
    all. Flattened, all of it is plain page content the way it is shown:
    docling reads it and the render draws it, and both see the same page.
    """
    try:
        document = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError as error:
        raise UnreadableDocument(f"PDFium cannot open the file: {error}") from error
    try:
        sizes = [page.get_size() for page in document]
        rotated = any(page.get_rotation() % 360 for page in document)
        if not sizes or any(width <= 0 or height <= 0 for width, height in sizes):
            raise UnreadableDocument(f"pages without size, or no pages: {sizes}")
        # With a form environment PDFium draws each field from its value, so a
        # field whose stored appearance is stale is flattened as it reads.
        document.init_forms()
        for page in document:
            # Called raw: the helper refuses a document without a form, but
            # annotations need flattening all the same.
            if pdfium.raw.FPDFPage_Flatten(page, pdfium.raw.FLAT_NORMALDISPLAY) == pdfium.raw.FLATTEN_FAIL:
                raise UnreadableDocument("PDFium failed to flatten annotations and form fields")
        # PDFium opened the file, so if it is encrypted it opens without a
        # password; the flattened copy is written without the encryption.
        buffer = BytesIO()
        document.save(buffer, flags=pdfium.raw.FPDF_REMOVE_SECURITY)
    finally:
        document.close()

    if not rotated:
        return buffer.getvalue()
    try:
        writer = PdfWriter(clone_from=PdfReader(BytesIO(buffer.getvalue())))
    except PyPdfError as error:
        raise UnreadableDocument(f"pypdf cannot read the file to turn its pages upright: {error!r}") from error
    for page in writer.pages:
        if page.rotation % 360:
            page.transfer_rotation_to_content()
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def page_count(pdf: bytes) -> int:
    document = pdfium.PdfDocument(pdf)
    try:
        return len(document)
    finally:
        document.close()
