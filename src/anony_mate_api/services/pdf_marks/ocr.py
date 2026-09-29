"""Words on a PDF's pages, each with the box OCR measured for it: dcc-doctr-api.

docTR finds each word's orientation itself, so it reads text across the page,
up and down it, and at any angle, as the street names along the streets of a
map. It is asked only for the words a PDF's text layer does not draw.

The PDF is sent as it is: the service renders each page and reads it in
tiles, so small print is read at the size it was drawn, and nothing here
renders anything.
"""

import math
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import status

from anony_mate_api.models.error_codes import OCR_ERROR

logger = get_logger("ocr")

#: Most two words of one line differ in direction, in degrees.
SAME_DIRECTION = 10


@dataclass(frozen=True)
class OcrWord:
    text: str
    #: Left, top, right, bottom in pixels of the page as the service rendered it.
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class OcrPage:
    """What docTR read on one page, in the pixels it rendered the page at."""

    width: int
    height: int
    lines: list[list[OcrWord]]


class DoctrClient:
    """Reads PDFs with dcc-doctr-api."""

    def __init__(self, base_url: str, timeout_seconds: float, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    async def read_pdf(self, pdf: bytes) -> list[OcrPage]:
        """The lines docTR finds on each page, each as its words in reading order.

        Raises:
            ApiErrorException: dcc-doctr-api could not be reached or failed.
        """
        url = f"{self.base_url}/ocr"
        try:
            response = await self.client.post(url, files=[("files", ("document.pdf", pdf, "application/pdf"))])
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.exception("docTR API HTTP error", url=url, status_code=e.response.status_code)
            raise ApiErrorException({
                "errorId": OCR_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"docTR request failed with status {e.response.status_code}",
            }) from e
        except httpx.RequestError as e:
            logger.exception("docTR connection error", url=url, error=str(e))
            raise ApiErrorException({
                "errorId": OCR_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"docTR connection error: {e!s}",
            }) from e
        return [_page(page) for page in response.json()]


def _page(page: dict[str, Any]) -> OcrPage:
    height, width = page["dimensions"]
    return OcrPage(width=width, height=height, lines=_chain(_words(page)))


def _words(page: dict[str, Any]) -> list[tuple[str, np.ndarray]]:
    """The words of one page of dcc-doctr-api's answer, each with the corners
    of its box in pixels: top left, top right, bottom right, bottom left of
    the text, however it is turned."""
    height, width = page["dimensions"]
    read = []
    for block in page["items"][0]["blocks"]:
        for line in block["lines"]:
            for word in line["words"]:
                geometry = word["geometry"]
                if len(geometry) == 4:
                    left, top, right, bottom = geometry
                    geometry = [left, top, right, top, right, bottom, left, bottom]
                read.append((word["value"], np.asarray(geometry).reshape(4, 2) * [width, height]))
    return read


def _chain(read: list[tuple[str, np.ndarray]]) -> list[list[OcrWord]]:
    """Words joined into lines, each word after the one whose end it starts at.

    docTR gives every word of a line running at an angle a line of its own,
    top to bottom rather than in reading order. A word follows another when
    both run the same way and it starts within a text height of where the
    other ends.
    """
    starts = {index: polygon[0] for index, (_, polygon) in enumerate(read)}
    following: dict[int, int] = {}
    for index, (_, polygon) in enumerate(read):
        end, direction, size = polygon[1], _direction(polygon), _text_height(polygon)
        near = [
            (float(np.linalg.norm(starts[other] - end)), other)
            for other in starts
            if other != index and abs(_angle_between(direction, _direction(read[other][1]))) <= SAME_DIRECTION
        ]
        near = [candidate for candidate in near if candidate[0] <= size]
        if near:
            following[index] = min(near)[1]
    firsts = set(range(len(read))) - set(following.values())
    lines = []
    for first in sorted(firsts):
        line: list[OcrWord] = []
        index: int | None = first
        seen: set[int] = set()
        while index is not None and index not in seen:
            seen.add(index)
            text, polygon = read[index]
            left, top = polygon.min(axis=0)
            right, bottom = polygon.max(axis=0)
            line.append(OcrWord(text, (float(left), float(top), float(right), float(bottom))))
            index = following.get(index)
        lines.append(line)
    return lines


def _direction(polygon: np.ndarray) -> float:
    dx, dy = polygon[1] - polygon[0]
    return math.degrees(math.atan2(dy, dx))


def _angle_between(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


def _text_height(polygon: np.ndarray) -> float:
    return float(np.linalg.norm(polygon[3] - polygon[0]))
