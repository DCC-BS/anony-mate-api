"""A PDF marked for redaction from end to end, with the two services stood in for.

The PDF, its text layer and the marks are real; docling-serve and GLiNER
answer from here, docling-serve with the words of the page as it would read
them.
"""

import asyncio
import ctypes
import json
from io import BytesIO

import httpx
import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from dcc_backend_common.fastapi_error_handling import ApiErrorException
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, ProvenanceItem, Size
from docling_core.types.doc.page import (
    BoundingRectangle,
    PdfPageBoundaryType,
    PdfPageGeometry,
    SegmentedPdfPage,
    TextCell,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from anony_mate_api.models.error_codes import INVALID_MIME_TYPE
from anony_mate_api.models.marked_pdf import MarkedPdf
from anony_mate_api.models.redact_models import RedactFileOptions
from anony_mate_api.routers import task_router
from anony_mate_api.routers.redact_router import _marking_job
from anony_mate_api.services.document_conversion_service import ReadLayout
from anony_mate_api.services.pdf_marking_service import PdfMarkingService, _picture_marks, merge_same
from anony_mate_api.services.pdf_marks.box import Box
from anony_mate_api.services.pdf_marks.marks import Mark
from anony_mate_api.services.pdf_marks.words import Picture
from anony_mate_api.services.redact_service import RedactService
from anony_mate_api.services.task_store import LaneConfig, TaskStore
from anony_mate_api.utils.app_config import AppConfig

NAME = "Hildegard Zwyssig"
LINES = ["Baugesuch Nr. 4711", f"Bauherrschaft: {NAME}, Riehenring 7", f"Unterschrift: {NAME}"]


def make_pdf() -> bytes:
    document = pdfium.PdfDocument.new()
    page = document.new_page(595, 842)
    for number, line in enumerate(LINES):
        text = raw.FPDFPageObj_NewTextObj(document, b"Helvetica", 12.0)
        buffer = ctypes.create_string_buffer((line + "\0").encode("utf-16-le"))
        raw.FPDFText_SetText(text, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)))
        raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 72, 760 - 24 * number)
        raw.FPDFPage_InsertObject(page, text)
    raw.FPDFPage_GenerateContent(page)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class Docling:
    """docling-serve's layout: one paragraph region over the letter."""

    def __init__(self) -> None:
        self.statuses: list[str] = []

    async def read_layout(self, pdf: bytes, filename: str, on_status=None) -> ReadLayout:
        if on_status:
            on_status("started", None)
        document = DoclingDocument(name=filename)
        document.add_page(page_no=1, size=Size(width=595, height=842))
        region = BoundingBox(l=60, t=780, r=540, b=690, coord_origin=CoordOrigin.BOTTOMLEFT)
        document.add_text(
            label=DocItemLabel.TEXT, text="", prov=ProvenanceItem(page_no=1, bbox=region, charspan=(0, 0))
        )
        return ReadLayout(document=document, pages={1: served_page(pdf)})


def gliner(request: httpx.Request) -> httpx.Response:
    """GLiNER finding the name wherever it stands in the text it is sent."""
    assert request.url.path == "/extract_entities"
    text = json.loads(request.content)["text"]
    start = text.index(NAME)
    return httpx.Response(
        200,
        json={
            "entities": {"person": [{"text": NAME, "confidence": 0.97, "start": start, "end": start + len(NAME)}]},
            "progress": None,
        },
    )


def text_words(pdf: bytes) -> list[tuple[str, BoundingBox]]:
    """Every word the page draws, with the box its characters stand in."""
    page = pdfium.PdfDocument(pdf)[0]
    height = page.get_size()[1]
    textpage = page.get_textpage()
    words: list[tuple[str, BoundingBox]] = []
    letters, box = "", None
    for index in range(textpage.count_chars() + 1):
        character = textpage.get_text_range(index, 1) if index < textpage.count_chars() else " "
        if character.strip():
            left, bottom, right, top = textpage.get_charbox(index)
            here = BoundingBox(l=left, t=height - top, r=right, b=height - bottom, coord_origin=CoordOrigin.TOPLEFT)
            box = (
                here
                if box is None
                else BoundingBox(
                    l=min(box.l, here.l),
                    t=min(box.t, here.t),
                    r=max(box.r, here.r),
                    b=max(box.b, here.b),
                    coord_origin=CoordOrigin.TOPLEFT,
                )
            )
            letters += character
        elif letters and box is not None:
            words.append((letters, box))
            letters, box = "", None
    return words


def served_page(pdf: bytes) -> SegmentedPdfPage:
    """The page as docling-serve reads it: every word with its own box."""
    whole = BoundingBox(l=0, t=0, r=595, b=842, coord_origin=CoordOrigin.TOPLEFT)
    return SegmentedPdfPage(
        dimension=PdfPageGeometry(
            angle=0,
            rect=BoundingRectangle.from_bounding_box(whole),
            boundary_type=PdfPageBoundaryType.CROP_BOX,
            art_bbox=whole,
            bleed_bbox=whole,
            crop_bbox=whole,
            media_bbox=whole,
            trim_bbox=whole,
        ),
        word_cells=[
            TextCell(
                index=index,
                text=text,
                orig=text,
                from_ocr=False,
                rect=BoundingRectangle.from_bounding_box(box),
            )
            for index, (text, box) in enumerate(text_words(pdf))
        ],
        textline_cells=[],
        char_cells=[],
        has_words=True,
        has_lines=False,
        has_chars=False,
    )


def service() -> PdfMarkingService:
    config = AppConfig(
        client_url="http://localhost:3000",
        llm_api_key="none",
        llm_url="http://localhost:8001/v1",
        llm_model="test-model",
        llm_health_check_url="http://localhost:8001/health",
        gliner_api_base_url="http://gliner.test",
        gliner_api_key="test-key",
        gliner_use_binary_upload=False,
        gliner_use_async_tasks=False,
        gliner_poll_interval_seconds=0.0,
    )
    return PdfMarkingService(
        document_conversion_service=Docling(),  # ty: ignore[invalid-argument-type]
        redact_service=RedactService(config, transport=httpx.MockTransport(gliner)),
        author="Anonymate",
    )


def test_every_mention_of_a_detected_name_is_marked_on_the_original() -> None:
    original = make_pdf()
    progress: list[float | None] = []

    marked = asyncio.run(
        service().mark(original, "baugesuch.pdf", RedactFileOptions(entity_types=["person"]), None, progress.append)
    )

    assert marked.filename == "baugesuch.markiert.pdf"
    assert marked.marks == 2
    textpage = pdfium.PdfDocument(original)[0].get_textpage()
    annotations = [a.get_object() for a in PdfReader(BytesIO(marked.content)).pages[0]["/Annots"]]
    under = [textpage.get_text_bounded(*[float(v) for v in a["/Rect"]]).strip(" ,") for a in annotations]
    assert under == [NAME, NAME]
    assert {a["/Subtype"] for a in annotations} == {"/Redact"}
    assert {a["/T"] for a in annotations} == {"Anonymate"}


def test_the_blacklist_keeps_a_name_unmarked() -> None:
    options = RedactFileOptions(entity_types=["person"], blacklist=["Zwyssig"])

    marked = asyncio.run(service().mark(make_pdf(), "baugesuch.pdf", options))

    assert marked.marks == 0
    assert "/Annots" not in PdfReader(BytesIO(marked.content)).pages[0]


def test_a_name_read_twice_is_marked_once_over_both_readings() -> None:
    """A page whose text layer is invisible holds every name twice: as text
    nobody sees, and in the picture OCR read. One box goes over both."""
    hidden = Mark(Box(1, 100, 100, 180, 107), "person", 0.67, NAME)
    read = Mark(Box(1, 99, 96, 182, 111), "person", 0.73, NAME)

    merged = merge_same([hidden, read])

    assert len(merged) == 1
    assert merged[0].box == Box(1, 99, 96, 182, 111)
    assert merged[0].confidence == 0.73


def test_two_names_next_to_each_other_stay_two_marks() -> None:
    first = Mark(Box(1, 100, 100, 160, 112), "person", 0.9, "Hildegard")
    second = Mark(Box(1, 165, 100, 220, 112), "person", 0.9, "Zwyssig")

    assert len(merge_same([first, second])) == 2


def test_a_signature_is_marked_whole_and_a_logo_is_not() -> None:
    signature = Picture(Box(1, 300, 700, 450, 760), {"signature": 0.31, "logo": 0.6})
    logo = Picture(Box(1, 40, 20, 120, 80), {"logo": 0.95, "signature": 0.05})

    marks = _picture_marks([signature, logo])

    assert [(mark.box, mark.label) for mark in marks] == [(signature.box, "signature")]


def test_only_a_pdf_can_come_back_marked() -> None:
    upload = (b"PK...", "brief.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    with pytest.raises(ApiErrorException) as refused:
        _marking_job(service(), upload, RedactFileOptions())

    assert refused.value.error_response["errorId"] == INVALID_MIME_TYPE


def test_a_marked_pdf_is_collected_as_the_pdf_itself() -> None:
    async def finished_resource(store: TaskStore) -> str:
        store.start()

        async def work(_task: object) -> MarkedPdf:
            return MarkedPdf(filename="baugesuch.markiert.pdf", content=b"%PDF-1.7 ...", marks=3)

        task = store.submit(work, lane="convert")
        for _ in range(50):
            await asyncio.sleep(0)
        resource_id = store.poll(task.id).resource_id  # ty: ignore[possibly-missing-attribute]
        await store.stop()
        return resource_id

    store = TaskStore(lanes={"convert": LaneConfig(workers=1, max_queued=1)})
    resource_id = asyncio.run(finished_resource(store))
    app = FastAPI()
    app.include_router(task_router.create_router(task_store=store))

    response = TestClient(app).get(f"/resource/{resource_id}")

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-mark-count"] == "3"
    assert "baugesuch.markiert.pdf" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.7 ..."
