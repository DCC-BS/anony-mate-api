"""Tests for the building blocks of a marked PDF: words, placing, marks and the docTR client."""

import asyncio
import ctypes
import json
from io import BytesIO

import httpx
import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from docling_core.types.doc import BoundingBox, CoordOrigin, DoclingDocument, ProvenanceItem, Size, TableItem
from docling_core.types.doc.document import TableCell, TableData
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from anony_mate_api.services.pdf_marks.box import Box
from anony_mate_api.services.pdf_marks.marks import Mark, annotate
from anony_mate_api.services.pdf_marks.ocr import DoctrClient
from anony_mate_api.services.pdf_marks.words import (
    Layout,
    TextUnit,
    Word,
    _cell_boxes,
    group,
    padded,
    shown_words,
    unit_of,
)

NAME = "Hildegard Zwyssig"


def word(text: str, left: float, top: float = 100.0, drawn: bool = True) -> Word:
    return Word(text, Box(1, left, top, left + 10 * len(text), top + 12), drawn)


def table_item(cells: list[tuple[float, float, float, float]]) -> TableItem:
    """A one-row table on page 1 whose cells lie at the given boxes."""
    document = DoclingDocument(name="table")
    document.add_page(page_no=1, size=Size(width=200, height=200))
    data = TableData(
        num_rows=1,
        num_cols=len(cells),
        table_cells=[
            TableCell(
                text=str(column),
                start_row_offset_idx=0,
                end_row_offset_idx=1,
                start_col_offset_idx=column,
                end_col_offset_idx=column + 1,
                bbox=bounds(cell),
            )
            for column, cell in enumerate(cells)
        ],
    )
    return document.add_table(
        data=data,
        prov=ProvenanceItem(page_no=1, bbox=bounds((0, 0, 200, 20)), charspan=(0, 0)),
    )


def bounds(box: tuple[float, float, float, float]) -> BoundingBox:
    left, top, right, bottom = box
    return BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT)


def boxes(units: list[TextUnit], needle: str) -> list[Box]:
    start = units[0].text.index(needle)
    return Layout(units, [], {}).boxes(0, start, start + len(needle))


# Words and where they are


def test_a_unit_is_its_words_and_knows_where_each_stands() -> None:
    unit = unit_of([[word("Herr", 0), word("Hans", 50)], [word("Muster", 0, top=120)]])

    assert unit.text == "Herr Hans Muster"
    assert [unit.text[start:end] for start, end in unit.spans] == ["Herr", "Hans", "Muster"]


def test_a_word_the_page_broke_over_two_lines_is_written_whole() -> None:
    unit = unit_of([[word("am", 0), word("Sep-", 30)], [word("tember", 0, top=120), word("2026", 70, top=120)]])

    assert unit.text == "am September 2026"
    assert [unit.text[start:end] for start, end in unit.spans] == ["am", "Sep", "tember", "2026"]


def test_a_hyphen_between_anything_but_letters_is_not_a_break() -> None:
    unit = unit_of([[word("Fr.", 0), word("120.-", 40)], [word("(reduziert)", 0, top=120)]])

    assert unit.text == "Fr. 120.- (reduziert)"


def test_a_mark_on_a_broken_word_covers_both_of_its_halves() -> None:
    unit = unit_of([[word("Sep-", 0)], [word("tember", 200, top=120)]])

    covered = Layout([unit], [], {}).boxes(0, 0, len("September"))

    assert [(box.l, box.t) for box in covered] == [(-2.8, 97.2), (197.2, 117.2)]


def test_a_name_covers_its_words_as_one_bar() -> None:
    units = [unit_of([[word("Herr", 0), word("Hans", 50), word("Muster", 100), word("kommt", 170)]])]

    assert boxes(units, "Hans Muster") == [padded(Box(1, 50, 100, 160, 112))]


def test_part_of_a_word_covers_the_whole_word() -> None:
    units = [unit_of([[word("Herr", 0), word("Mustermann,", 50)]])]

    assert boxes(units, "Mustermann") == [padded(word("Mustermann,", 50).box)]


def test_a_name_running_up_the_page_is_one_bar() -> None:
    first = Word("Hildegard", Box(1, 500, 300, 512, 360))
    second = Word("Zwyssig", Box(1, 500, 250, 512, 296))

    assert len(boxes([unit_of([[first, second]])], NAME)) == 1


def test_words_go_to_the_smallest_region_they_lie_in_and_lines_are_read_top_down() -> None:
    table, cell = Box(1, 0, 0, 400, 300), Box(1, 0, 0, 100, 50)
    lines = [[word("unten", 150, top=200)], [word("Name", 10, top=10), word("oben", 150, top=10)]]

    assert [unit.text for unit in group([table, cell], lines)] == ["oben unten", "Name"]


def test_a_table_offers_each_of_its_cells_as_a_region() -> None:
    table = table_item([(0, 0, 50, 20), (50, 0, 100, 20)])

    assert _cell_boxes(table, {1: (200.0, 200.0)}) == [Box(1, 0, 0, 50, 20), Box(1, 50, 0, 100, 20)]


def test_words_in_no_region_are_read_line_by_line() -> None:
    lines = [[word("Erste", 0), word("Zeile", 60)], [word("Schild", 300, top=400)], [word("Tafel", 300, top=500)]]

    units = group([Box(1, 0, 90, 200, 130)], lines)

    assert [unit.text for unit in units] == ["Erste Zeile", "Schild", "Tafel"]


def test_drawn_text_is_read_from_the_text_layer_and_ocr_only_elsewhere() -> None:
    text = [[word("Hildegard", 0)]]
    ocr = [[word("Hildegarc", 2)], [word("Schild", 300, top=400)]]

    assert [[w.text for w in line] for line in shown_words(text, ocr)] == [["Hildegard"], ["Schild"]]


def test_a_word_ocr_reads_taller_than_the_page_draws_it_is_the_same_word() -> None:
    drawn = [[Word("tag,", Box(1, 112.9, 155.7, 128.0, 161.2))]]
    ocr = [[Word("tag,", Box(1, 111.3, 152.4, 127.6, 162.5))]]

    assert [[w.text for w in line] for line in shown_words(drawn, ocr)] == [["tag,"]]


def test_invisible_text_is_not_what_the_page_shows() -> None:
    hidden = [[word("Riehen", 2, drawn=False)]]

    assert [[w.text for w in line] for line in shown_words(hidden, [[word("Hildegard", 0)]])] == [["Hildegard"]]


# Marks on the original


def page_with_name(rotation: int = 0, crop: tuple[float, float, float, float] | None = None) -> bytes:
    document = pdfium.PdfDocument.new()
    page = document.new_page(595, 842)
    text = raw.FPDFPageObj_NewTextObj(document, b"Helvetica", 14.0)
    buffer = ctypes.create_string_buffer((NAME + "\0").encode("utf-16-le"))
    raw.FPDFText_SetText(text, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)))
    raw.FPDFPageObj_Transform(text, 1, 0, 0, 1, 150, 600)
    raw.FPDFPage_InsertObject(page, text)
    raw.FPDFPage_GenerateContent(page)
    output = BytesIO()
    document.save(output)
    writer = PdfWriter(clone_from=PdfReader(BytesIO(output.getvalue())))
    writer.pages[0].rotate(rotation)
    if crop:
        writer.pages[0].cropbox = RectangleObject(crop)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def shown_box(pdf: bytes) -> Box:
    """The name's box on the page as shown, in points from the top left."""
    page = pdfium.PdfDocument(pdf)[0]
    textpage = page.get_textpage()
    left, bottom, right, top = textpage.get_charbox(0)
    for index in range(1, textpage.count_chars()):
        box = textpage.get_charbox(index)
        left, bottom, right, top = min(left, box[0]), min(bottom, box[1]), max(right, box[2]), max(top, box[3])
    width, height = page.get_size()
    corners = []
    for x, y in [(left, bottom), (right, top)]:
        # 1000 device units per point keep the rounding below a thousandth of a point.
        dx, dy = ctypes.c_int(), ctypes.c_int()
        raw.FPDF_PageToDevice(page, 0, 0, int(width * 1000), int(height * 1000), 0, x, y, dx, dy)
        corners.append((dx.value / 1000, dy.value / 1000))
    (ua, va), (ub, vb) = corners
    return Box(1, min(ua, ub), min(va, vb), max(ua, ub), max(va, vb))


def text_under_marks(pdf: bytes) -> str:
    page = pdfium.PdfDocument(pdf)[0].get_textpage()
    rects = [
        [float(value) for value in annotation.get_object()["/Rect"]]
        for annotation in PdfReader(BytesIO(pdf)).pages[0]["/Annots"]
    ]
    return "".join(page.get_text_bounded(*rect) for rect in rects)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [None, (40, 60, 560, 800)])
def test_a_mark_lands_on_the_word_whatever_the_rotation_and_crop(
    rotation: int, crop: tuple[float, float, float, float] | None
) -> None:
    original = page_with_name(rotation, crop)

    marked = annotate(original, [Mark(shown_box(original).grown(1), "person", 0.93, NAME)], author="Anonymate")

    assert text_under_marks(marked).strip() == NAME


def test_a_mark_carries_the_standard_fields_and_not_the_text() -> None:
    original = page_with_name()

    marked = annotate(original, [Mark(shown_box(original), "person", 0.93, NAME)], author="Anonymate")

    annotation = PdfReader(BytesIO(marked)).pages[0]["/Annots"][0].get_object()
    assert annotation["/Subtype"] == "/Redact"
    assert annotation["/T"] == "Anonymate"
    assert annotation["/Contents"] == "person, Konfidenz 0.93"
    assert annotation["/Subj"] == "Schwärzung: person"
    assert not any("Zwyssig" in str(value) for value in annotation.values())


# docTR over HTTP


def answer(words: list[tuple[str, list[float]]], width: int, height: int) -> dict:
    """dcc-doctr-api's answer for one tile, with these words on it."""
    return {
        "name": "tile.png",
        "dimensions": [height, width],
        "orientation": {"value": None, "confidence": None},
        "language": {"value": None, "confidence": None},
        "items": [
            {
                "blocks": [
                    {
                        "geometry": [0, 0, 1, 1],
                        "objectness_score": 1.0,
                        "lines": [
                            {
                                "geometry": [0, 0, 1, 1],
                                "objectness_score": 1.0,
                                "words": [
                                    {
                                        "value": value,
                                        "geometry": geometry,
                                        "objectness_score": 1.0,
                                        "confidence": 0.99,
                                        "crop_orientation": {"value": 0, "confidence": None},
                                    }
                                    for value, geometry in words
                                ],
                            }
                        ],
                    }
                ]
            }
        ],
    }


def doctr_answering(pages: list[dict], requests: list[bytes]) -> DoctrClient:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ocr"
        requests.append(request.content)
        return httpx.Response(200, text=json.dumps(pages))

    return DoctrClient("http://doctr", 10, transport=httpx.MockTransport(handle))


def test_the_pdf_goes_whole_and_words_come_back_in_page_pixels() -> None:
    turned = [0.5, 0.75, 0.5, 0.25, 0.55, 0.25, 0.55, 0.75]  # up the page, read bottom to top
    requests: list[bytes] = []
    client = doctr_answering(
        [answer([("Hildegard", [0.1, 0.1, 0.3, 0.15]), ("Riehenring", turned)], 1000, 800)], requests
    )

    [page] = asyncio.run(client.read_pdf(b"%PDF-1.7 ..."))

    words = {w.text: w.box for line in page.lines for w in line}
    assert len(requests) == 1 and b"%PDF-1.7 ..." in requests[0]
    assert (page.width, page.height) == (1000, 800)
    assert words["Hildegard"] == (100, 80, 300, 120)
    assert words["Riehenring"] == (500, 200, 550, 600)


def test_words_running_the_same_way_are_joined_into_one_line() -> None:
    # Two words up the page, one after the other: docTR gives each its own line.
    first = [0.5, 0.9, 0.5, 0.6, 0.53, 0.6, 0.53, 0.9]
    second = [0.5, 0.58, 0.5, 0.3, 0.53, 0.3, 0.53, 0.58]
    client = doctr_answering([answer([("Hildegard", first), ("Zwyssig", second)], 1000, 1000)], [])

    [page] = asyncio.run(client.read_pdf(b"%PDF"))

    assert [[w.text for w in line] for line in page.lines] == [["Hildegard", "Zwyssig"]]
