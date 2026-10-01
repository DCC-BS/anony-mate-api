from dataclasses import dataclass


@dataclass(frozen=True)
class MarkedPdf:
    """A PDF handed back with redaction marks on it; served as the PDF itself, not as JSON."""

    filename: str
    content: bytes
    #: Number of redaction marks on it.
    marks: int
