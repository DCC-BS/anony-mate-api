# anony-mate-lib

Async Python client for the AnonyMate API:
redact personal data from texts and documents without running anything yourself.

## Install

```bash
uv add "anony-mate-lib @ git+https://github.com/DCC-BS/anony-mate-api#subdirectory=lib"
```

The package needs only `httpx` and `pydantic`. It speaks to a running AnonyMate
API — see the [repository root's README](../README.md) for how to run one.

## Quick start

```python
from anony_mate_lib import AnonyMateClient, RedactInput

async with AnonyMateClient("http://localhost:8000") as client:
    result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))
    print(result.text)  # [person:1] wohnt in [ort:1].
```

Entering loads the API's `default` presets of entity types and blacklist, so
the redaction calls work without settings of their own. The async submissions
answer at once and are waited on here — the polling of the task and the
one-time collection of the result are part of the call.

## The client

```python
AnonyMateClient(base_url, *, timeout=60.0, poll_interval=0.5, transport=None)
```

| Argument | Meaning |
|----------|---------|
| `base_url` | Where the API stands, e.g. `http://localhost:8000` |
| `timeout` | How long **one HTTP call** may take, in seconds — a job running long is not held open, it is waited on via task polling, which is made of short calls again |
| `poll_interval` | Seconds between two polls of a submitted job |
| `transport` | An own `httpx.AsyncBaseTransport`, e.g. `httpx.MockTransport` for tests |

One method per thing the API can do — everything waits for its result:

| Method | Endpoint | Returns |
|--------|----------|---------|
| `anonymize_text(options)` | `POST /anonymize/` | `RedactOutput` |
| `anonymize_texts(options)` | `POST /anonymize/batch` | `list[RedactOutput]`, one per input text, in order |
| `anonymize_document(content, filename, *, options)` | `POST /anonymize/file/async` | `DocumentRedactOutput` |
| `anonymize_pdf(content, filename, *, options)` | `POST /anonymize/pdf/async` | `bytes` (marked PDF) or `PdfAnnotationsOutput` |
| `annotate_pdf(content, filename, annotations)` | `POST /anonymize/pdf/annotate/async` | `bytes` |

## Entity types and blacklist

The presets are loaded on entering and sent along with every redaction call
whose options leave `entity_types` / `blacklist` empty:

```python
from anony_mate_lib import AnonyMateClient, EntityType, BlacklistType

async with AnonyMateClient("http://localhost:8000") as client:
    # Replace the presets with another one: default, legal (types: full too).
    await client.load_entity_types(EntityType.legal)
    await client.load_blacklist(BlacklistType.legal)

    # Or extend what is loaded.
    client.add_entity_type("steuernummer", "The German tax number, 10-13 digits with separators")
    client.add_to_blacklist("Kanton Basel-Stadt")
```

Settings given on a call replace the presets for that call:

```python
result = await client.anonymize_text(
    RedactInput(
        text="Anna wohnt in Berlin.",
        threshold=0.9,  # confidence cut-off, default 0.8
        entity_types={"person": "A natural person"},  # label: description
        blacklist=["Berlin"],
        repeat_everywhere=True,  # find every detection wherever else its text stands
    )
)
```

## Redacting texts

One text or several in one call; the answer reports every detection with its
label, offsets and the model's confidence:

```python
from anony_mate_lib import AnonyMateClient, RedactBatchInput

async with AnonyMateClient("http://localhost:8000") as client:
    outputs = await client.anonymize_texts(
        RedactBatchInput(texts=["Anna wohnt in Berlin.", "Der Bericht stammt von Dr. Meier."])
    )

for out in outputs:
    print(out.text)  # detections written as [label:id]
    for label, entities in out.entities.items():
        for entity in entities:
            print(f"  {label}: {entity.text!r} at {entity.start}–{entity.end} ({entity.confidence:.0%})")
```

## Redacting documents

An uploaded document (Word, PDF, …) is converted to markdown and redacted in
one submission:

```python
from pathlib import Path

from anony_mate_lib import AnonyMateClient

async with AnonyMateClient("http://localhost:8000") as client:
    result = await client.anonymize_document(Path("bericht.docx").read_bytes(), "bericht.docx")

print(result.redacted_text)  # the markdown with every detection written as [label:id]
print(result.page_offsets)  # where each page starts in result.text (the unredacted markdown)
```

## Redacting PDFs

A PDF is scanned for redaction without being turned into text. Without
`marked_pdf` — the default — the answer is the detected marks with their page
positions, for an interface that keeps the file and draws them over it:

```python
from anony_mate_lib import AnonyMateClient

async with AnonyMateClient("http://localhost:8000") as client:
    marks = await client.anonymize_pdf(Path("scan.pdf").read_bytes(), "scan.pdf")

for mark in marks.annotations:
    print(mark.id, mark.label, repr(mark.text), mark.boxes)  # one box per line, in points
print(marks.page_sizes)  # {"1": [595.0, 842.0], …} — width and height per page, in points
```

With `marked_pdf=True` the answer is the PDF itself, a redaction mark on every
detection, for an editor such as Kofax Power PDF or Acrobat to review and
apply:

```python
from anony_mate_lib import AnonyMateClient, RedactPdfOptions

async with AnonyMateClient("http://localhost:8000") as client:
    pdf = await client.anonymize_pdf(
        Path("scan.pdf").read_bytes(), "scan.pdf", options=RedactPdfOptions(marked_pdf=True)
    )

Path("scan-markiert.pdf").write_bytes(pdf)
```

A runnable version of the latter stands in
[`examples/pdf_redact.py`](examples/pdf_redact.py).

## Writing reviewed marks onto a PDF

Once an interface has reviewed the marks, `annotate_pdf` writes them onto the
PDF as `/Redact` annotations — the redaction happens in the document when the
marks are applied. The `id` is kept as the annotation's `/NM`, so a mark stays
the same annotation across passes:

```python
from anony_mate_lib import AnonyMateClient, MarkAnnotationInput, MarkBox

marks = [
    MarkAnnotationInput(
        id="person-1",
        label="person",
        text="Anna Muster",
        boxes=[MarkBox(page=1, left=72, top=100, right=200, bottom=112)],
        overlay="[person:1]",  # written into the area once applied; without one it is filled
    )
]

async with AnonyMateClient("http://localhost:8000") as client:
    pdf = await client.annotate_pdf(Path("scan.pdf").read_bytes(), "scan.pdf", marks)
```

## Building blocks for an own `httpx` client

Everything the client is made of is exported one level down, for callers that
drive their own `httpx.AsyncClient`:

```python
from httpx import AsyncClient

from anony_mate_lib import get_entity_types, get_json_resource, poll_status

async with AsyncClient(base_url="http://localhost:8000", timeout=60.0) as http:
    entity_types = await get_entity_types(http)  # {"person": "…", …}, preset "default"
    verbose = await get_entity_types(http, verbose=True)  # {"person": {"name": …, "description": …}}

    response = await http.post("/anonymize/", json={"text": "…", "entity_types": entity_types})
    task_id = response.json()["task_id"]

    task = await poll_status(http, task_id)  # polling keeps the task alive
    result = await get_json_resource(http, task.resource_id)  # fetched exactly once
```

The helpers: `get_entity_types` / `get_blacklist` read the presets,
`poll_status` waits for a job to leave the queue (raise `TaskFailedError` if
it failed), `get_json_resource` / `get_file_resource` collect a finished job's
result — which the API deletes once fetched, so collect it exactly once.

## Errors

```python
from anony_mate_lib import AnonyMateClient, AnonyMateError, RedactInput, TaskFailedError

try:
    async with AnonyMateClient("http://localhost:8000") as client:
        result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))
except TaskFailedError as error:
    ...  # the submitted job failed; str(error) carries the API's error text
except AnonyMateError as error:
    ...  # e.g. MissingResourceError: a finished task answered without a result to fetch
```

`AnonyMateError` is the base of what the client raises itself
(`TaskFailedError`, `MissingResourceError`). HTTP-level failures — an
unreachable API, 429 when the queues are full, other 4xx/5xx — surface as
`httpx` exceptions, since every call checks its status.

## Models

The request and response models — `RedactInput`, `RedactOutput`,
`RedactPdfOptions`, `MarkAnnotationInput`, the task models and their peers —
live in `anony_mate_lib.models`, shared field-for-field with the API server.

## Development

The library lives in this repository next to the API server, as a standalone
project in `lib/`:

```bash
uv run --project lib pytest lib/tests          # tests
uv build --out-dir dist lib                    # wheel into dist/
```
