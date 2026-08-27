from pydantic import BaseModel, Field


class ConversionResult(BaseModel):
    text: str = Field(description="The converted document as markdown text, ready to redact")
    page_offsets: list[int] = Field(
        description=(
            "Character offset where each page starts in `text`. The first entry is always 0. "
            "Empty for formats without pages."
        ),
        default_factory=list,
    )
