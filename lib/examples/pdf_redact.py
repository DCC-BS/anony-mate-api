"""Redact one PDF with the AnonyMate API through the `anony_mate_lib` client.

The client loads the API's `default` entity types on entering, extends them
with one more type, and redacts the PDF with a mark on every detection so the
result can be reviewed and applied in a PDF editor::

    uv run --project lib python lib/examples/pdf_redact.py -f brief.pdf -o brief-markiert.pdf
"""

import argparse
import asyncio
from pathlib import Path

from anony_mate_lib import AnonyMateClient, RedactPdfOptions

API_BASE_URL = "http://localhost:8000"


async def main(pdf_path: Path, output_path: Path) -> None:
    async with AnonyMateClient(API_BASE_URL) as client:
        client.add_entity_type("steuernummer", "The German tax number, 10-13 digits with separators")

        options = RedactPdfOptions(marked_pdf=True)
        pdf = await client.anonymize_pdf(pdf_path.read_bytes(), pdf_path.name, options=options)

    if not isinstance(pdf, bytes):
        raise SystemExit("the API answered with the marks alone instead of a marked PDF")
    output_path.write_bytes(pdf)
    print(f"Redacted PDF written to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--file", required=True, type=Path, help="The PDF to redact")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Where to write the redacted PDF")
    args = parser.parse_args()

    asyncio.run(main(args.file, args.output))
