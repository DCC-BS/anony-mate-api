# anony-mate-lib

Async Python client for the AnonyMate API:
redact personal data from texts and documents without running anything yourself.

```bash
uv add "anony-mate-lib @ git+https://github.com/DCC-BS/anony-mate-api#subdirectory=lib"
```

## Usage

```python
from anony_mate_lib import AnonyMateClient, RedactInput

async with AnonyMateClient("http://localhost:8000") as client:
    result = await client.anonymize_text(RedactInput(text="Anna wohnt in Berlin."))
    print(result.text)  # [person:1] wohnt in [ort:1].
```

Entering loads the API's `default` presets of entity types and blacklist, so
the redaction calls work without settings of their own; the async submissions
poll the task and collect the result as part of the call. The request and
response models live in `anony_mate_lib.models`; the building blocks the
client is made of — preset and task polling helpers for use with an own
`httpx` client — one level below.

## Development

The library lives in this repository next to the API server, as a standalone
project in `lib/`:

```bash
uv run --project lib pytest lib/tests          # tests
uv build --out-dir dist lib                    # wheel into dist/
```
