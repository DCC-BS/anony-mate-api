import asyncio
import gzip
import json
import re
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import status
from pydantic import ValidationError

from anony_mate_api.models.error_codes import REDACT_ERROR, REDACT_TIMEOUT
from anony_mate_api.models.gliner_models import GlinerInput, GlinerResponse
from anony_mate_api.models.redact_models import Entity, RedactBatchInput, RedactInput, RedactOutput
from anony_mate_api.utils import AppConfig

logger = get_logger("redact_service")

#: Echoed in Gliner's log, so one call can be traced across both services.
CORRELATION_HEADER = "X-Correlation-Id"


def _create_entities_dict(gliner_response: GlinerResponse) -> dict[str, list[Entity]]:
    entity_dict: dict[str, list[Entity]] = defaultdict(list, [])
    for label, items in gliner_response.entities.items():
        for i, item in enumerate(items):
            entity_dict[label].append(
                Entity(
                    label=label,
                    id=str(i + 1),
                    text=item.text,
                    start=item.start,
                    end=item.end,
                    confidence=item.confidence,
                )
            )
    return entity_dict


#: Labels whose shape is fixed by law or convention, and the shape they take.
#:
#: The model reads meaning, not format, so it will offer a lone digit as a
#: postal code where the surrounding words fit. Where a label has one true
#: form, checking it is cheaper and surer than describing it away.
LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    # A Swiss postal code is always four digits, 1000 to 9999.
    "plz": re.compile(r"^[1-9]\d{3}$"),
}


def _filter_malformed(entities: dict[str, list[Entity]]) -> dict[str, list[Entity]]:
    """Drop detections that cannot be what their label says they are."""
    filtered: dict[str, list[Entity]] = {}

    for label, entity_list in entities.items():
        pattern = LABEL_PATTERNS.get(label)
        if pattern is None:
            filtered[label] = entity_list
            continue

        kept = [entity for entity in entity_list if pattern.fullmatch(entity.text.strip())]
        dropped = len(entity_list) - len(kept)
        if dropped:
            logger.debug("Dropped malformed detections", label=label, dropped=dropped)
        filtered[label] = kept

    return filtered


#: Labels whose mentions repeat verbatim through a document.
#:
#: The model reads each mention on its own evidence, so a name it names once
#: it may pass over the next time — a bare surname after "Herr" reads like an
#: ordinary word where the full name did not. What it recognised anywhere it
#: has recognised everywhere.
REPEATING_LABELS = frozenset({"person", "organisation", "adresse", "ort", "e-mail-adresse", "telefonnummer"})

#: A surname worth carrying to its other mentions: letters, at least three of
#: them, hyphens and apostrophes allowed inside.
_SURNAME = re.compile(r"^[^\W\d_][\w'\-]{2,}$")


def _propagate_repeats(text: str, entities: dict[str, list[Entity]]) -> dict[str, list[Entity]]:
    """Give every repeat of a detected mention the label it was given once.

    Both the whole mention and, for a person, its last name: "Andreas Mueller"
    detected once makes every later "Mueller" a person too. Positions already
    covered by a detection are left alone, whatever their label, so this only
    ever adds to what the model found.
    """
    taken = [(entity.start, entity.end) for entity_list in entities.values() for entity in entity_list]
    grown = {label: list(entity_list) for label, entity_list in entities.items()}

    for label in REPEATING_LABELS & entities.keys():
        found = entities[label]
        confidence = min((entity.confidence for entity in found), default=1.0)

        for needle in _repeatable_mentions(label, found):
            # Not \b: a hyphen is a word boundary to it, so "Basel" would be
            # taken out of "Basel-Stadt". A repeat only counts where the
            # mention stands on its own.
            for match in re.finditer(rf"(?<![\w'\-]){re.escape(needle)}(?![\w'\-])", text):
                if any(match.start() < end and start < match.end() for start, end in taken):
                    continue

                taken.append((match.start(), match.end()))
                grown[label].append(
                    Entity(
                        label=label,
                        id=str(len(grown[label]) + 1),
                        text=match.group(),
                        start=match.start(),
                        end=match.end(),
                        confidence=confidence,
                    )
                )

    return grown


def _repeatable_mentions(label: str, entities: list[Entity]) -> set[str]:
    """The strings a label's detections make worth searching for again."""
    mentions = {entity.text.strip() for entity in entities if len(entity.text.strip()) > 2}

    if label == "person":
        for entity in entities:
            surname = entity.text.strip().split()[-1:]
            if surname and _SURNAME.fullmatch(surname[0]):
                mentions.add(surname[0])

    return mentions


def _filter_blacklisted(entities: dict[str, list[Entity]], blacklist: list[str]) -> dict[str, list[Entity]]:
    if not blacklist:
        return entities
    lowered_blacklist = [entry.lower() for entry in blacklist]
    return {
        label: [
            entity for entity in entity_list if not any(entry in entity.text.lower() for entry in lowered_blacklist)
        ]
        for label, entity_list in entities.items()
    }


def _redact_text(text: str, entities: dict[str, list[Entity]], replacement_fn: Callable[[Entity], str]):
    """
    Redacts the text based on the entities in the GLiNER response.
    """

    sorted_entities = sorted(
        (entity for entities_list in entities.values() for entity in entities_list),
        key=lambda e: e.start,
    )
    redacted_text = ""
    cursor = 0
    for entity in sorted_entities:
        redacted_text += text[cursor : entity.start]
        redacted_text += f"[{replacement_fn(entity)}]"
        cursor = entity.end
    redacted_text += text[cursor:]
    return redacted_text


class RedactService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.gliner_api_base_url, timeout=config.gliner_http_timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self,
        url: str,
        body: dict[str, Any] | None = None,
        method: str = "POST",
        files: dict[str, tuple[str, bytes, str]] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        # Sent along so the same call can be found in Gliner's log. If it never
        # appears there, something between the two rejected the request.
        correlation_id = uuid4().hex[:12]
        headers = {
            "Authorization": f"Bearer {self.config.gliner_api_key}",
            CORRELATION_HEADER: correlation_id,
        }
        # A multipart upload carries the document as file bytes; only its size
        # is worth logging, the parts themselves are not JSON.
        payload = (
            b"".join(content for _, content, _ in files.values())
            if files is not None
            else json.dumps(body).encode()
            if body is not None
            else b""
        )
        logger.debug(
            "Calling Gliner",
            base_url=str(self.client.base_url),
            path=url,
            method=method,
            request_bytes=len(payload),
            correlation_id=correlation_id,
        )
        try:
            if files is not None:
                response = await self.client.request(method, url, headers=headers, files=files, data=data or {})
            else:
                response = await self.client.request(method, url, headers=headers, json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            content_type = e.response.headers.get("content-type", "")
            logger.exception(
                "Gliner API HTTP error",
                url=str(e.request.url),
                status_code=e.response.status_code,
                # A gateway answers with its own HTML page; Gliner answers JSON.
                # That tells you which of the two rejected the call.
                responded_by="gateway" if "html" in content_type else "gliner",
                content_type=content_type,
                request_bytes=len(payload),
                correlation_id=correlation_id,
                body=e.response.text[:2000],
            )
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Gliner request failed with status {e.response.status_code}",
            }) from e
        except httpx.TimeoutException as e:
            logger.exception(
                "Gliner API timed out",
                url=str(e.request.url),
                timeout=self.config.gliner_http_timeout_seconds,
                request_bytes=len(payload),
                correlation_id=correlation_id,
            )
            raise ApiErrorException({
                "errorId": REDACT_TIMEOUT,
                "status": status.HTTP_504_GATEWAY_TIMEOUT,
                "debugMessage": (f"Gliner request timed out after {self.config.gliner_http_timeout_seconds:.0f}s"),
            }) from e
        except httpx.RequestError as e:
            logger.exception(
                "Gliner api connection error", url=f"{self.client.base_url}{url.lstrip(chr(47))}", error=str(e)
            )
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Gliner connection error: {e!s}",
            }) from e
        except ValidationError as e:
            logger.exception(
                "Validation error from gliner", url=f"{self.client.base_url}{url.lstrip(chr(47))}", error=str(e)
            )
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Validation error: {e!s}",
            }) from e
        else:
            return response

    async def _get(self, url: str) -> httpx.Response:
        return await self._request(url, body=None, method="GET")

    async def _submit_extraction(self, payload: GlinerInput) -> str:
        """Hand the text to Gliner and return the task id it answers with.

        As a file part the document is an ordinary upload rather than one very
        large JSON string, which is what a firewall between the two services
        objects to; gzip keeps it small on top of that. The JSON endpoint is
        still there, and the flag switches back to it.
        """
        if not self.config.gliner_use_binary_upload:
            accepted = await self._request("/extract_entities/async", payload.model_dump())
            return accepted.json()["task_id"]

        options = payload.model_dump(exclude={"text"})
        files = {"file": ("document.txt.gz", gzip.compress(payload.text.encode()), "application/gzip")}
        accepted = await self._request(
            "/extract_entities/async/upload",
            files=files,
            data={"options": json.dumps(options), "charset": "utf-8"},
        )
        return accepted.json()["task_id"]

    async def _extract_entities(
        self,
        payload: GlinerInput,
        on_progress: Callable[[float | None], None] | None = None,
    ) -> GlinerResponse:
        """Run an extraction through Gliner's task API and collect the result.

        Submitting and polling keeps every individual request short, so a long
        scan cannot be cut off by a proxy the way one held-open request is.
        """
        logger.info("Submitting extraction to Gliner", text_chars=len(payload.text))

        task_id = await self._submit_extraction(payload)
        logger.debug("Gliner accepted extraction", gliner_task_id=task_id)

        # The scan itself may take minutes; each poll is its own short request,
        # so this budget is about the whole job, not one HTTP call.
        deadline = time.monotonic() + self.config.gliner_task_timeout_seconds
        while True:
            state = (await self._get(f"/task/{task_id}")).json()
            status_value = state.get("status")

            if on_progress:
                on_progress(state.get("progress"))

            if status_value == "finished":
                break
            if status_value == "failed":
                raise ApiErrorException({
                    "errorId": REDACT_ERROR,
                    "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "debugMessage": f"Gliner task failed: {state.get('error')}",
                })
            if time.monotonic() > deadline:
                raise ApiErrorException({
                    "errorId": REDACT_TIMEOUT,
                    "status": status.HTTP_504_GATEWAY_TIMEOUT,
                    "debugMessage": (
                        f"Gliner task did not finish within {self.config.gliner_task_timeout_seconds:.0f}s"
                    ),
                })

            await asyncio.sleep(self.config.gliner_poll_interval_seconds)

        result = await self._get(f"/resource/{state['resource_id']}")
        return GlinerResponse.model_validate(result.json())

    async def _extract_entities_batch(
        self, texts: list[str], entity_types: Any, threshold: float
    ) -> list[GlinerResponse]:
        body = {
            "entity_types": entity_types,
            "include_confidence": True,
            "include_spans": True,
            "texts": texts,
            "threshold": threshold,
        }
        response = await self._request("/batch_extract_entities", body)
        return [GlinerResponse.model_validate(item) for item in response.json()]

    def _redact_single(
        self,
        text: str,
        entity_dict: dict[str, list[Entity]],
        blacklist: list[str],
    ) -> RedactOutput:
        entity_dict = _filter_malformed(entity_dict)
        entity_dict = _propagate_repeats(text, entity_dict)
        entity_dict = _filter_blacklisted(entity_dict, blacklist)
        redacted_text = _redact_text(
            text,
            entity_dict,
            replacement_fn=lambda e: f"{e.label}:{e.id}",
        )
        return RedactOutput(text=redacted_text, entities=entity_dict)

    async def redact(
        self,
        payload: RedactInput,
        on_progress: Callable[[float | None], None] | None = None,
    ) -> RedactOutput:
        gliner_input = GlinerInput(
            entity_types=payload.entity_types,
            include_confidence=True,
            include_spans=True,
            text=payload.text,
            threshold=payload.threshold,
        )

        response = await self._extract_entities(gliner_input, on_progress)
        entity_dict = _create_entities_dict(response)

        return self._redact_single(payload.text, entity_dict, payload.blacklist)

    async def redact_batch(self, payload: RedactBatchInput) -> list[RedactOutput]:
        responses = await self._extract_entities_batch(payload.texts, payload.entity_types, payload.threshold)
        return [
            self._redact_single(text, _create_entities_dict(response), payload.blacklist)
            for text, response in zip(payload.texts, responses, strict=True)
        ]
