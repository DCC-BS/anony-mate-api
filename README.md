# anony-mate-api

AnonyMate is the backend of a modern web application for redacting sensitive information from free-form text and PDF documents. Built with FastAPI and Python 3.15, it converts documents to text (Docling), detects sensitive entities (GLiNER), and writes redaction marks back onto PDFs for an editor such as Kofax Power PDF or Acrobat to apply.

This repository contains the backend API; the frontend is built with Nuxt 4 and available at [https://github.com/DCC-BS/anony-mate-frontend](https://github.com/DCC-BS/anony-mate-frontend).

---

<p align="center">
  <a href="https://dcc-bs.github.io/documentation/">DCC Documentation & Guidelines</a> | <a href="https://www.bs.ch/daten/databs/dcc">DCC Website</a>
</p>

---

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![GitHub License](https://img.shields.io/github/license/DCC-BS/anony-mate-api)

## Features

### Core Capabilities

- **Document Conversion**: Uploads (`.docx`, `.txt`, PDF, scans) are converted to reviewable text via [Docling](https://github.com/docling-project/docling) — with OCR for scanned pages and per-word boxes for PDF marking.
- **Entity Detection**: [GLiNER](https://github.com/urchade/GLiNER) finds sensitive entities for a caller-defined schema of entity types (label plus natural-language description), running as async tasks with polling so long scans cannot be cut off.
- **Redaction**: Detected occurrences are replaced from per-type templates (`{name}`, `{label}`, `{subject}`, `{occurrence}`), with a never-redact (blacklist) exclusion list honoured throughout.
- **PDF Marking**: Writes one redaction annotation (ISO 32000) per kept mark onto the original PDF, named by its mark id and carrying the placeholder as its overlay text — nothing is removed; the editor applies the marks.
- **Stateless Task Store**: The API holds no client state — documents and decisions live in the browser (IndexedDB). Task results are polled, TTL-bounded, and dropped when a caller vanishes.
- **Backpressure & Fairness**: Bounded conversion and redaction queues with 429 refusals instead of unbounded upstream queues; abandoned tasks are dropped before a worker picks them up.
- **Entity Type Presets**: Serves curated preset schemas (`default`, `legal`, `full`) the frontend seeds into its local groups.

## Technology Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (ASGI via [Uvicorn](https://www.uvicorn.org/))
- **Python**: 3.15, packaged with [Hatchling](https://hatch.pypa.io/) + version-pioneer
- **Package & Environment Manager**: [uv](https://docs.astral.sh/uv/)
- **Document Conversion**: [Docling / docling-serve](https://github.com/docling-project/docling-serve)
- **Entity Detection**: [GLiNER](https://github.com/urchade/Gliner-api) via dcc-gliner-api
- **PDF Handling**: [pypdf](https://pypdf.readthedocs.io/)
- **Dependency Injection**: [dependency-injector](https://python-dependency-injector.ets-labs.org/)
- **Logging & Tracing**: [structlog](https://www.structlog.org/), [Logfire](https://logfire.pydantic.dev/)
- **Environment & Secrets**: [Varlock](https://varlock.dev/) with Proton Pass plugin
- **Tool Version Manager**: [Mise](https://mise.jdx.dev/)
- **Linter, Formatter & Type Check**: [Ruff](https://docs.astral.sh/ruff/), [ty](https://docs.astral.sh/ty/)
- **Testing**: [pytest](https://docs.pytest.org/) (incl. doctests)

---

## Setup

### Prerequisites

We recommend using [mise](https://mise.jdx.dev/) to automatically manage tool versions (uv, Varlock, pass-cli):

```bash
mise install
```

### Environment Configuration

Create a `.env` file in the project root (see `.env.example`):

```bash
APP_MODE=dev      # dev, ci, build, prod
```

#### Environment Variables

Environment variables are validated using Varlock schemas (`.env.schema`, `.env.dev.schema`, `.env.azure.schema`). Highlights:

| Variable | Description | Default | Type |
|----------|-------------|---------|------|
| **Core Configuration** | | | |
| `APP_MODE` | Application runtime mode (`dev`, `ci`, `build`, `prod`) | `dev` | enum |
| `LOG_LEVEL` | Log level | `debug` (dev/ci), `info` (build/prod) | enum |
| `PORT` | FastAPI backend port | `8000` | port |
| `LLM_MODEL` | Model for the LLM API | `Qwen/Qwen3-32B-AWQ` | string |
| **Services** | | | |
| `LLM_URL` / `LLM_API_PORT` | LLM API URL / port | `http://localhost:8001/v1` | URL / port |
| `GLINER_API_BASE_URL` / `GLINER_PORT` | GLiNER service URL / port | `http://localhost:8081` | URL / port |
| `DOCLING_URL` / `DOCLING_API_PORT` | Docling API URL / port | `http://localhost:5001/v1` | URL / port |
| `LLM_API_KEY` | API key for the LLM (required in prod) | - | string (sensitive) |
| **Docling Tuning** | | | |
| `DOCLING_CONVERSION_TIMEOUT_SECONDS` | Wait for a conversion to finish (large scanned PDFs take minutes) | `1800` | number |
| `DOCLING_PDF_BACKEND` | PDF parsing backend; only docling-parse yields word boxes for marking | `dlparse_v4` | enum |
| `DOCLING_OCR_PRESET` | OCR engine for marking a PDF (needs word boxes on scans) | `pp-ocrv6` | enum |
| `DOCLING_TEXT_OCR_PRESET` | OCR engine for converting a document to text | `glm-ocr-remote` | enum |
| `DOCLING_TABLE_MODE` | Table structure mode (`accurate` / `fast`) | `accurate` | enum |
| **GLiNER Tuning** | | | |
| `GLINER_USE_ASYNC_TASKS` | Submit & poll instead of one held-open request | `true` | boolean |
| `GLINER_USE_BINARY_UPLOAD` | Upload the request as a file (large schemas exceed firewall JSON limits) | `true` | boolean |
| `GLINER_TASK_TIMEOUT_SECONDS` | Bound on a whole extraction task | `3600` | number |
| **Backpressure** | | | |
| `CONVERSION_MAX_CONCURRENT` / `CONVERSION_MAX_QUEUED` | Concurrent / queued conversions before 429 | `3` / `16` | number |
| `REDACTION_MAX_CONCURRENT` / `REDACTION_MAX_QUEUED` | Concurrent / queued redactions before 429 | `32` / `256` | number |
| `TASK_ABANDONED_AFTER_SECONDS` | Drop a queued task nobody has polled for | `60` | number |
| `MAX_UPLOAD_BYTES` | Largest accepted upload | `104857600` (100 MB) | number |
| **Marked PDFs** | | | |
| `MARK_AUTHOR` | Author written into every redaction mark | `Anonymate` | string |

### Varlock & Secrets Management

We use [varlock](https://varlock.dev/) for schema-based validation and optional secret retrieval from Proton Pass:

```bash
# Validate and load secrets into the environment
mise run env
```

---

## Development

Start the FastAPI dev server with auto-reload at `http://localhost:8000`:

```bash
# Using mise
mise run dev

# Or using the Makefile
make dev
```

### Production Server

```bash
mise run run
```

The API documentation (OpenAPI/Swagger) is served at `http://localhost:8000/docs`.

### API Overview

| Router | Purpose |
|--------|---------|
| `/anonymize` | Detect entities in submitted text and return it redacted (single, `batch`, `async`, file and PDF uploads) |
| `/convert` | Upload a document (`/doc`) and convert it to text (async task) |
| `/task/{task_id}` | Poll task status and fetch results |
| `/entity_types` | Entity type presets and schema validation |
| `/blacklist` | Never-redact terms |

### Python Client

The client library ships as its own lightweight package, `anony-mate-lib`
(a standalone project in [`lib/`](lib/); it needs only `httpx` and `pydantic`).
Its API is documented in [`lib/README.md`](lib/README.md):

```bash
uv add "anony-mate-lib @ git+https://github.com/DCC-BS/anony-mate-api#subdirectory=lib"
```

On entering it loads the `default` presets of entity types and blacklist, so
redaction works without settings of its own; the async submissions poll the
task and collect the result as part of the call:

```python
from anony_mate_lib import AnonyMateClient, RedactInput

async with AnonyMateClient("http://localhost:8000") as client:
    result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))
    print(result.text)  # [person:1] wohnt in [ort:1].
```

The request and response models live in `anony_mate_lib.models`; the building
blocks the client itself is made of — preset and task polling helpers for use
with an own `httpx` client — one level below.

---

## Docker Services

The detection and conversion services run as sidecar containers via Docker Compose (GLiNER API, docling-serve; optional vLLM for the LLM):

### Start Services

```bash
mise run docker:up
```

### Follow Logs

```bash
mise run docker:logs
```

### Stop Services

```bash
mise run docker:down
```

> Marking scanned PDFs needs a docling-serve built with PP-OCRv6, PP-DocLayoutV3 and the word-boxes plugin (see `dcc-docling-serve/plugins/word_boxes.py`); the published upstream image does not yet provide these.

---

## Project Architecture

```text
src/anony_mate_api/
├── app.py                  # FastAPI application factory & lifespan
├── container.py            # Dependency-injector wiring
├── routers/                # HTTP endpoints
│   ├── conversion_router.py    # Document upload & conversion
│   ├── redact_router.py        # Text redaction
│   ├── task_router.py          # Task status polling
│   ├── entity_types_router.py  # Entity type presets & schemas
│   └── blacklist_router.py     # Never-redact terms
├── services/               # Business logic
│   ├── document_conversion_service.py  # Docling submission & polling
│   ├── redact_service.py               # GLiNER detection & replacement
│   ├── pdf_marking_service.py          # Redaction annotations on PDFs
│   ├── pdf_marks/                      # Mark geometry & word boxes
│   ├── text_matching.py                # Occurrence matching
│   └── task_store.py                   # In-memory task store (TTL, backpressure)
├── models/                 # Pydantic request/response models
└── utils/                  # Configuration & helpers
tests/                      # pytest suite (incl. doctests)
lib/                        # anony-mate-lib client package (API docs: lib/README.md)
tools/                      # Load testing
```

---

## Testing & Quality Assurance

### Tests (pytest)

```bash
# Run the test suite (including doctests), API server and client library
mise run test:unit
```

### Linting & Type Checking

```bash
# Verify lockfile, format, lint, type-check, and scan for secrets
mise run check

# Same, without modifying files (CI mode)
mise run ci-check

# Everything: checks + tests
mise run ci
```

---

## Production Build & Deployment

### Build

```bash
# Build the wheel file
mise run build
```

### Docker Deployment

Build and run using the multi-stage Dockerfile:

```bash
# Build Docker image
docker build -t anony-mate-api .

# Run container
docker run -p 8000:8000 anony-mate-api
```

---

## License

[MIT](LICENSE) © Data Competence Center Basel-Stadt

<a href="https://www.bs.ch/schwerpunkte/daten/databs/schwerpunkte/datenwissenschaften-und-ki"><img src="https://github.com/DCC-BS/.github/blob/main/_imgs/databs_log.png?raw=true" alt="DCC Logo" width="200" /></a>

Datenwissenschaften und KI <br>
Developed with ❤️ by DCC - Data Competence Center
