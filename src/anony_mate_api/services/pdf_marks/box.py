# l, t, r, b: the edges of a box, as docling names them.
# ruff: noqa: E741
"""Rectangles on a page."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """A rectangle on one page, in PDF points, measured from the top left."""

    page_no: int
    l: float
    t: float
    r: float
    b: float

    @property
    def area(self) -> float:
        return max(0.0, self.r - self.l) * max(0.0, self.b - self.t)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.l + self.r) / 2, (self.t + self.b) / 2

    def contains(self, x: float, y: float) -> bool:
        return self.l <= x <= self.r and self.t <= y <= self.b

    def covered_by(self, other: "Box") -> float:
        """Share of this box that ``other`` covers."""
        if self.page_no != other.page_no or self.area == 0:
            return 0.0
        width = max(0.0, min(self.r, other.r) - max(self.l, other.l))
        height = max(0.0, min(self.b, other.b) - max(self.t, other.t))
        return width * height / self.area

    def grown(self, by: float) -> "Box":
        return Box(self.page_no, self.l - by, self.t - by, self.r + by, self.b + by)

    def union(self, other: "Box") -> "Box":
        return Box(self.page_no, min(self.l, other.l), min(self.t, other.t), max(self.r, other.r), max(self.b, other.b))
