"""Every word a PDF shows, and where on the page it shows it.

Unlike docling's text, where a paragraph knows its box but not where each of
its words is, every word here carries its own box, and every character of a
unit's text points at its word. A span of text found in a unit is placed on
the page exactly: the boxes of the words it touches.

Everything comes from docling-serve, which reads the file once and answers
``include_word_boxes``. Its words are of two kinds, each kept where it is
right, decided per page by place, never by comparing text:

- text the page draws comes from the text layer: exact letters, and the box
  measured for each word;
- what only the pixels show — scans, pictures, plans, stamps, outlines,
  labels at an angle — is read by OCR, and added where the text layer draws
  nothing;
- invisible text, the layer a scanner lays under its picture, is read as
  well, in units of its own: it is content of the file, left behind by
  whoever removes only what is marked, and it need not lie where its picture
  shows the words. Kept apart, it neither doubles the words OCR reads in the
  picture nor mixes into their text.

Docling's layout (from docling-serve) groups the words: its regions and their
reading order, down to the single cell of a table. A word belongs to the
smallest region its centre lies in; a region's words, line by line from the
top, are one unit of text. Words in no region are read line by line.

Boxes are in points from the top left of the page as shown, 1-based pages.
"""

from dataclasses import dataclass

from docling_core.types.doc import (
    BoundingBox,
    ContentLayer,
    CoordOrigin,
    DocItem,
    DoclingDocument,
    PictureItem,
    TableItem,
)
from docling_core.types.doc.page import PdfCellRenderingMode, PdfTextCell, SegmentedPdfPage

from anony_mate_api.services.pdf_marks.box import Box

#: Every layer docling sorts content into. Headers, footers and stamps land in
#: furniture, and they carry names as often as the body does.
ALL_LAYERS = set(ContentLayer)

#: Between two words, and between two lines, of one unit.
WORD_SEPARATOR = " "

#: Added around every box: a point for the anti-aliased fringe a glyph leaves
#: on neighbouring pixels, and a share of the text's height for accents and
#: descenders a tight box leaves out, as OCR's boxes hug the ink.
PADDING = 1.0
PADDING_SHARE = 0.15


@dataclass(frozen=True)
class Word:
    text: str
    box: Box
    #: False for text-layer text the page does not draw.
    drawn: bool = True
    #: True for a word read from the pixels rather than drawn by the text layer.
    read: bool = False


@dataclass(frozen=True)
class TextUnit:
    """A piece of text read as one, and the words it is made of."""

    text: str
    box: Box
    words: list[Word]
    #: Where each word stands in ``text``, as start and end.
    spans: list[tuple[int, int]]


@dataclass(frozen=True)
class Picture:
    """A picture docling found, and what its classifier takes it for."""

    box: Box
    #: Class name to confidence, for the classes the classifier offered.
    classes: dict[str, float]


@dataclass
class Layout:
    units: list[TextUnit]
    pictures: list[Picture]
    #: Page sizes in points, by 1-based page number.
    page_sizes: dict[int, tuple[float, float]]

    def boxes(self, unit: int, start: int, end: int) -> list[Box]:
        """Where characters ``start:end`` of a unit's text are: the words they
        touch, whole, with a margin, run together into one bar per line."""
        text = self.units[unit]
        touched = [
            padded(word.box)
            for word, (first, last) in zip(text.words, text.spans, strict=True)
            if first < end and start < last
        ]
        return join_lines(touched)


def read_layout(document: DoclingDocument, pages: dict[int, SegmentedPdfPage]) -> Layout:
    """Every word the document shows, grouped into units of text.

    Args:
        document: Docling's reading of the file, for the regions and pictures.
        pages: The pages docling-serve read, each with the words of the text
            layer and the words OCR read on it.
    """
    page_sizes = {no: (page.size.width, page.size.height) for no, page in document.pages.items()}
    regions: list[Box] = []
    pictures: list[Picture] = []
    for item, _ in document.iterate_items(included_content_layers=ALL_LAYERS):
        if not isinstance(item, DocItem):
            continue
        boxes = [_to_box(prov.bbox, prov.page_no, page_sizes) for prov in item.prov]
        regions += boxes
        if isinstance(item, TableItem):
            regions += _cell_boxes(item, page_sizes)
        if isinstance(item, PictureItem):
            pictures += [Picture(box, _classes(item)) for box in boxes]

    shown: list[list[Word]] = []
    hidden: list[list[Word]] = []
    for page_no in page_sizes:
        lines = _text_lines(pages.get(page_no), page_no)
        on_show = _only(lines, drawn=True)
        shown += shown_words(_read(on_show, read=False), _read(on_show, read=True))
        hidden += _only(lines, drawn=False)

    return Layout(group(regions, shown) + group(regions, hidden), pictures, page_sizes)


def _read(lines: list[list[Word]], read: bool) -> list[list[Word]]:
    """The words of each line that OCR read, or the ones the text layer drew.

    A line docling grouped by place can hold both, where OCR read a word the
    text layer also draws, so they are told apart word by word.
    """
    picked = ([word for word in line if word.read == read] for line in lines)
    return [line for line in picked if line]


def shown_words(text: list[list[Word]], ocr: list[list[Word]]) -> list[list[Word]]:
    """The lines of words a page shows: what its text layer draws, and what
    OCR reads where the text layer draws nothing."""
    drawn = _only(text, drawn=True)
    return drawn + uncovered(ocr, drawn)


def uncovered(lines: list[list[Word]], by: list[list[Word]]) -> list[list[Word]]:
    """The words of ``lines`` whose centre no word of ``by`` lies on, in their lines.

    OCR reads a word in a box as tall as its line, the text layer in one that
    hugs the ink, so the two never cover each other by area; where the centres
    meet they are the same word.
    """
    others = [word.box for other in by for word in other]
    kept = []
    for line in lines:
        words = [word for word in line if not any(box.contains(*word.box.centre) for box in others)]
        if words:
            kept.append(words)
    return kept


def group(regions: list[Box], lines: list[list[Word]]) -> list[TextUnit]:
    """The words of each region as one unit, in reading order, then each run of
    a line that lies in no region as a unit of its own."""
    inside: dict[int, list[list[Word]]] = {}
    outside: list[list[Word]] = []
    for line in lines:
        runs: dict[int | None, list[Word]] = {}
        for word in line:
            runs.setdefault(_region_of(word.box, regions), []).append(word)
        for region, words in runs.items():
            if region is None:
                outside.append(words)
            else:
                inside.setdefault(region, []).append(words)
    units = [unit_of(sorted(inside[region], key=_top_left)) for region in sorted(inside)]
    return units + [unit_of([words]) for words in outside]


def unit_of(lines: list[list[Word]]) -> TextUnit:
    """One text of the words of ``lines``, remembering where each word stands.

    A word the page broke over two lines is written whole: the next word
    follows it without hyphen or space ("Sep-" and "tember" are "September").
    """
    words, line_ends = [], set()
    for line in lines:
        words += line
        line_ends.add(len(words) - 1)

    text, spans = "", []
    broken = False
    for index, word in enumerate(words):
        if text and not broken:
            text += WORD_SEPARATOR
        start = len(text)
        following = words[index + 1].text if index + 1 < len(words) else ""
        broken = index in line_ends and _breaks_a_word(word.text, following)
        text += word.text[:-1] if broken else word.text
        spans.append((start, len(text)))

    box = words[0].box
    for word in words[1:]:
        box = box.union(word.box)
    return TextUnit(text, box, words, spans)


def _breaks_a_word(word: str, following: str) -> bool:
    """Whether a hyphen ending a line breaks one word over two lines.

    Only between letters: "Sep-" and "tember" are one word, "Fr. 120.-" and
    "(reduzierter" are two.
    """
    return len(word) > 1 and word.endswith("-") and word[-2].isalpha() and following[:1].isalpha()


def padded(box: Box) -> Box:
    """The box with margin enough that no part of its glyphs shows."""
    return box.grown(PADDING + PADDING_SHARE * min(box.r - box.l, box.b - box.t))


def join_lines(boxes: list[Box]) -> list[Box]:
    """Run the boxes of one line of text together into one bar.

    Separate boxes leave the gaps between words showing, and with them how
    many words there were and how long each was. Boxes are in one line when
    they overlap by more than half across it: vertically for text running
    across the page, horizontally for text running up or down it.
    """
    joined: list[Box] = []
    for box in boxes:
        for index, other in enumerate(joined):
            if other.page_no == box.page_no and (_across(other, box) or _along(other, box)):
                joined[index] = other.union(box)
                break
        else:
            joined.append(box)
    return joined if len(joined) == len(boxes) else join_lines(joined)


def _text_lines(page: SegmentedPdfPage | None, page_no: int) -> list[list[Word]]:
    """The text layer's words, grouped by the text line they stand in, in the
    order the page draws them."""
    if page is None:
        return []
    height = page.dimension.height
    lines = [_rect_box(line.rect, page_no, height) for line in page.textline_cells]
    grouped: dict[int, list[Word]] = {}
    for cell in page.word_cells:
        if not cell.text.strip():
            continue
        invisible = isinstance(cell, PdfTextCell) and cell.rendering_mode == PdfCellRenderingMode.INVISIBLE
        word = Word(cell.text, _rect_box(cell.rect, page_no, height), drawn=not invisible, read=bool(cell.from_ocr))
        line = next((index for index, box in enumerate(lines) if box.contains(*word.box.centre)), None)
        grouped.setdefault(line if line is not None else -1 - len(grouped), []).append(word)
    return list(grouped.values())


def _only(lines: list[list[Word]], drawn: bool) -> list[list[Word]]:
    picked = ([word for word in line if word.drawn == drawn] for line in lines)
    return [line for line in picked if line]


def _region_of(box: Box, regions: list[Box]) -> int | None:
    """The smallest region the word's centre lies in: a table cell before its
    table, a caption before the picture around it."""
    containing = [
        index for index, region in enumerate(regions) if region.page_no == box.page_no and region.contains(*box.centre)
    ]
    if not containing:
        return None
    return min(containing, key=lambda index: regions[index].area)


def _top_left(line: list[Word]) -> tuple[float, float]:
    return min(word.box.t for word in line), min(word.box.l for word in line)


def _across(a: Box, b: Box) -> bool:
    overlap = min(a.b, b.b) - max(a.t, b.t)
    return overlap > 0.5 * min(a.b - a.t, b.b - b.t)


def _along(a: Box, b: Box) -> bool:
    overlap = min(a.r, b.r) - max(a.l, b.l)
    return overlap > 0.5 * min(a.r - a.l, b.r - b.l)


def _cell_boxes(table: TableItem, page_sizes: dict[int, tuple[float, float]]) -> list[Box]:
    """A table's cells, each as a region of its own.

    Without them a table is one region, and its rows and columns run together
    into a single text where a place from one cell reads as part of a name in
    the next.
    """
    if not table.prov:
        return []
    page_no = table.prov[0].page_no
    return [_to_box(cell.bbox, page_no, page_sizes) for cell in table.data.table_cells if cell.bbox is not None]


def _classes(item: PictureItem) -> dict[str, float]:
    classification = item.meta.classification if item.meta is not None else None
    if classification is None:
        return {}
    return {prediction.class_name: prediction.confidence or 0.0 for prediction in classification.predictions}


def _rect_box(rect, page_no: int, page_height: float) -> Box:
    bbox = rect.to_bounding_box()
    if bbox.coord_origin != CoordOrigin.TOPLEFT:
        bbox = bbox.to_top_left_origin(page_height=page_height)
    return Box(page_no, bbox.l, bbox.t, bbox.r, bbox.b)


def _to_box(bbox: BoundingBox, page_no: int, page_sizes: dict[int, tuple[float, float]]) -> Box:
    if bbox.coord_origin != CoordOrigin.TOPLEFT:
        bbox = bbox.to_top_left_origin(page_height=page_sizes[page_no][1])
    return Box(page_no, bbox.l, bbox.t, bbox.r, bbox.b)
