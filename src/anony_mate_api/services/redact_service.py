from collections import defaultdict
from collections.abc import Callable
from typing import Any

import httpx
from dcc_backend_common.fastapi_error_handling import ApiErrorException
from dcc_backend_common.logger import get_logger
from fastapi import status
from pydantic import ValidationError

from anony_mate_api.models.error_codes import REDACT_ERROR
from anony_mate_api.models.gliner_models import GlinerInput, GlinerResponse
from anony_mate_api.models.redact_models import Entity, RedactInput, RedactOutput
from anony_mate_api.utils import AppConfig

logger = get_logger("redact_service")


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

    async def __aenter__(self) -> "RedactService":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.client.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def close(self) -> None:
        await self.client.aclose()

    async def _extract_entities(self, input: GlinerInput, **kwargs: Any) -> GlinerResponse:
        url = "/extract_entities"
        headers = {"Authorization": f"Bearer {self.config.gliner_api_key}", **kwargs.pop("headers", {})}
        body = input.model_dump()
        try:
            response = await self.client.request("POST", url, headers=headers, json=body)
            response.raise_for_status()
            return GlinerResponse.model_validate(response.json())
        except httpx.HTTPStatusError as e:
            logger.error(
                "Gliner API HTTP error",
                url=f"{self.client.base_url}{url}",
                status_code=e.response.status_code,
                body=e.response.text,
            )
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Gliner request failed with status {e.response.status_code}",
            }) from e
        except httpx.TimeoutException:
            raise
        except httpx.RequestError as e:
            logger.error("Gliner api connection error", url=f"{self.client.base_url}{url}", error=str(e))
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Gliner connection error: {e!s}",
            }) from e
        except ValidationError as e:
            logger.error("Validation error from gliner", url=f"{self.client.base_url}{url}", error=str(e))
            raise ApiErrorException({
                "errorId": REDACT_ERROR,
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "debugMessage": f"Validation error: {e!s}",
            }) from e

    async def redact(self, input: RedactInput) -> RedactOutput:
        gliner_input = GlinerInput(
            entity_types=input.labels,
            include_confidence=True,
            include_spans=True,
            text=input.text,
            threshold=input.threshold,
        )

        response = await self._extract_entities(gliner_input)
        entity_dict = _create_entities_dict(response)

        redacted_text = _redact_text(
            input.text,
            entity_dict,
            replacement_fn=lambda e: f"{e.label}:{e.id}",
        )

        return RedactOutput(text=redacted_text, entities=entity_dict)
