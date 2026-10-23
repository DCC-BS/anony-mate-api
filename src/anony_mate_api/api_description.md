AnonyMate redacts personal data from texts and documents: names, places and
other entities are detected and replaced by placeholders such as `[person:1]`,
so the text can be shared without exposing who it is about.

## How it works

1. **Detect** — a GLiNER model finds entities in the text.
2. **Redact** — detections above the confidence threshold (`threshold`) are
   replaced by `[label:id]` placeholders. A `blacklist` protects words that
   must never be redacted. The available entity types and their descriptions
   can be queried under `entity_types/{preset}`.
3. **Tasks and resources** — redaction and conversion can run long, so the
   async endpoints hand the work to a background job. See
   [Tasks and resources](#tasks-and-resources) below.

## Tasks and resources

Every `(async)` endpoint works the same way:

1. **Submit** — the endpoint answers immediately with `202` and a
   `task_id`, so a long-running job cannot time out your request.
2. **Poll** — `GET /task/{task_id}` reports the state of the job:
   - `pending` — queued, with `queue_position` telling how many jobs are
     ahead,
   - `running` — being processed, with `progress` (0–1) where the work has a
     natural fraction,
   - `finished` — done; the status carries the `resource_id` of the result,
   - `failed` — something went wrong; `error` says what.

   Keep polling while you wait: a `pending` task that nobody asks about is
   dropped after a minute.
3. **Collect** — `GET /resource/{resource_id}` returns the result itself and
   deletes it: collect it exactly once. Results not collected are kept for
   one hour. Each submitting endpoint documents what its result looks like
   (see its **200** response): a redacted text or a document conversion
   arrives as JSON; a marked PDF arrives as the PDF file itself (its
   download name ends in `.markiert.pdf`, and the `X-Mark-Count` header
   carries the number of marks).

Task and resource ids are plain strings — anyone who has one can poll or
collect it, so treat them as secrets if the results are sensitive.

## Typical flows

- **Text** — `POST /anonymize/` with the text returns the redacted text directly.
- **Document** — `POST /anonymize/file/async` uploads a file (Word, PDF, …);
  it is converted to markdown, redacted, and returned as text via the task
  endpoints.
- **PDF** — `POST /anonymize/pdf/async` scans a PDF without converting it to
  text: by default the detected marks with their page positions come back,
  to draw over the file you kept; with `marked_pdf: true` the PDF itself
  comes back with a redaction mark on every detection.
- **PDF review** — `POST /anonymize/pdf/annotate/async` writes reviewed marks
  onto the PDF itself, so the redaction happens in the document, not only in
  the extracted text.

## Notes

- The `options` form field of the upload endpoints carries its settings as
  one JSON string; a prefilled example is shown with each endpoint.
- All endpoints are also available synchronously where the work is quick;
  use the `(async)` variants for documents and long texts.
