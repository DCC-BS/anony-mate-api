import asyncio
import json

import httpx
import pytest
from dcc_backend_common.fastapi_error_handling import ApiErrorException

from anony_mate_api.models.error_codes import REDACT_ERROR
from anony_mate_api.models.gliner_models import GlinerEntity, GlinerProgress, GlinerResponse
from anony_mate_api.models.redact_models import Entity, RedactBatchInput, RedactInput
from anony_mate_api.services.redact_service import (
    RedactService,
    _create_entities_dict,
    _filter_blacklisted,
    _filter_malformed,
    _propagate_repeats,
    _redact_text,
    _repeatable_mentions,
)
from anony_mate_api.utils.app_config import AppConfig


def _gliner_response() -> GlinerResponse:
    return GlinerResponse(
        entities={
            "person": [
                GlinerEntity(text="John", confidence=1.0, start=0, end=4),
                GlinerEntity(text="Acme Corp", confidence=1.0, start=11, end=20),
            ],
            "location": [GlinerEntity(text="Basel", confidence=1.0, start=24, end=29)],
        },
        progress=GlinerProgress(current=1, length=1, progress=1.0),
    )


def test_filter_blacklisted_removes_case_insensitive_substring() -> None:
    entities = _create_entities_dict(_gliner_response())
    filtered = _filter_blacklisted(entities, ["acme"])
    assert "Acme Corp" not in [e.text for e in filtered["person"]]
    assert [e.text for e in filtered["person"]] == ["John"]
    assert [e.text for e in filtered["location"]] == ["Basel"]


def test_filter_blacklisted_empty_returns_same_entities() -> None:
    entities = _create_entities_dict(_gliner_response())
    assert _filter_blacklisted(entities, []) == entities


def test_malformed_postal_codes_are_dropped():
    """A Swiss postal code is always four digits, so a lone digit is not one."""
    entities = {
        "plz": [
            Entity(label="plz", id="1", text="4127", start=0, end=4, confidence=1.0),
            Entity(label="plz", id="2", text="3", start=9, end=10, confidence=0.9),
            Entity(label="plz", id="3", text="0421", start=20, end=24, confidence=0.8),
        ],
        "person": [Entity(label="person", id="1", text="A", start=30, end=31, confidence=1.0)],
    }
    filtered = _filter_malformed(entities)
    assert [e.text for e in filtered["plz"]] == ["4127"]
    # a label with no fixed shape is left exactly as it was
    assert [e.text for e in filtered["person"]] == ["A"]


def test_a_name_detected_once_is_found_at_its_other_mentions():
    """A bare surname reads like an ordinary word, so the model passes over it."""
    text = "Als Zeuge wird Andreas Mueller benannt. Herr Mueller hat zwei Personen beobachtet."
    entities = {
        "person": [Entity(label="person", id="1", text="Andreas Mueller", start=15, end=30, confidence=0.99)],
    }
    grown = _propagate_repeats(text, entities)

    assert [(e.text, e.start) for e in grown["person"]] == [("Andreas Mueller", 15), ("Mueller", 45)]


def test_repeats_never_overwrite_what_the_model_already_labelled():
    text = "Basel und Basel"
    entities = {
        "ort": [Entity(label="ort", id="1", text="Basel", start=0, end=5, confidence=0.9)],
        "organisation": [Entity(label="organisation", id="1", text="Basel", start=10, end=15, confidence=0.8)],
    }
    grown = _propagate_repeats(text, entities)

    assert len(grown["ort"]) == 1
    assert len(grown["organisation"]) == 1


def test_a_label_that_does_not_repeat_is_left_alone():
    text = "Am 12. August 2024 und am 12. August 2024."
    entities = {"datum": [Entity(label="datum", id="1", text="12. August 2024", start=3, end=18, confidence=0.9)]}

    assert _propagate_repeats(text, entities) == entities


def test_a_repeat_inside_a_compound_is_not_taken():
    """A hyphen joins a word to another, it does not end it."""
    text = "Wohnhaft in Basel. Die Sozialhilfe Basel-Stadt entschied."
    entities = {"ort": [Entity(label="ort", id="1", text="Basel", start=12, end=17, confidence=0.9)]}

    assert _propagate_repeats(text, entities) == entities


def test_create_entities_dict_flattens_labels_and_ids() -> None:
    response = _gliner_response()
    entity_dict = _create_entities_dict(response)

    assert set(entity_dict) == {"person", "location"}
    assert [e.text for e in entity_dict["person"]] == ["John", "Acme Corp"]
    assert [e.id for e in entity_dict["person"]] == ["1", "2"]
    assert entity_dict["person"][0].label == "person"


def test_repeatable_mentions_include_surnames_for_people() -> None:
    entities = [
        Entity(label="person", id="1", text="Andreas Mueller", start=0, end=15, confidence=0.99),
        Entity(label="person", id="2", text="A", start=20, end=21, confidence=0.9),
    ]
    mentions = _repeatable_mentions("person", entities)

    assert "Andreas Mueller" in mentions
    assert "Mueller" in mentions
    # a single letter is too short to carry anywhere
    assert "A" not in mentions


def test_redact_text_writes_label_and_id_in_place() -> None:
    text = "Herr Mueller wohnt in Basel."
    entities = {
        "person": [Entity(label="person", id="1", text="Mueller", start=5, end=12, confidence=1.0)],
        "ort": [Entity(label="ort", id="1", text="Basel", start=22, end=27, confidence=1.0)],
    }

    redacted = _redact_text(text, entities, replacement_fn=lambda e: f"{e.label}:{e.id}")

    assert redacted == "Herr [person:1] wohnt in [ort:1]."


def test_redact_text_keeps_text_before_first_and_after_last_entity() -> None:
    text = "Vorher. Mueller. Nachher."
    entities = {"person": [Entity(label="person", id="1", text="Mueller", start=8, end=15, confidence=1.0)]}

    redacted = _redact_text(text, entities, replacement_fn=lambda e: f"{e.label}:{e.id}")

    assert redacted == "Vorher. [person:1]. Nachher."


def _config() -> AppConfig:
    return AppConfig(
        client_url="http://localhost:3000",
        llm_api_key="none",
        llm_url="http://localhost:8001/v1",
        llm_model="test-model",
        llm_health_check_url="http://localhost:8001/health",
        gliner_api_base_url="http://gliner.test",
        gliner_api_key="test-key",
        gliner_use_binary_upload=False,
        gliner_use_async_tasks=False,
        gliner_poll_interval_seconds=0.0,
    )


def _service(handler) -> RedactService:
    return RedactService(_config(), transport=httpx.MockTransport(handler))


def _json_response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def test_redact_sync_flow_returns_redacted_output() -> None:
    """With async tasks off, one held-open request carries the whole scan."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/extract_entities"
        assert request.headers["Authorization"] == "Bearer test-key"
        return _json_response({
            "entities": {
                "person": [{"text": "Mueller", "confidence": 0.99, "start": 5, "end": 12}],
            },
            "progress": None,
        })

    async def scenario() -> str:
        service = _service(handler)
        try:
            result = await service.redact(RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]))
            return result.text
        finally:
            await service.close()

    assert asyncio.run(scenario()) == "Herr [person:1] wohnt."


def test_redact_async_flow_submits_polls_and_fetches() -> None:
    """The task API: submit, poll until finished, then fetch the resource once."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/extract_entities/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/task/task-1":
            return _json_response({"status": "finished", "progress": 1.0, "resource_id": "res-1"})
        if request.url.path == "/resource/res-1":
            return _json_response({
                "entities": {
                    "person": [{"text": "Mueller", "confidence": 0.99, "start": 5, "end": 12}],
                },
                "progress": None,
            })
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> str:
        config = _config()
        config.gliner_use_async_tasks = True
        service = RedactService(config, transport=httpx.MockTransport(handler))
        try:
            result = await service.redact(RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]))
            return result.text
        finally:
            await service.close()

    assert asyncio.run(scenario()) == "Herr [person:1] wohnt."
    assert calls == ["/extract_entities/async", "/task/task-1", "/resource/res-1"]


def test_redact_async_flow_polls_until_finished() -> None:
    """A task that is still running is polled again rather than returned early."""
    polls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/extract_entities/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/task/task-1":
            polls["count"] += 1
            if polls["count"] == 1:
                return _json_response({"status": "running", "progress": 0.5})
            return _json_response({"status": "finished", "progress": 1.0, "resource_id": "res-1"})
        if request.url.path == "/resource/res-1":
            return _json_response({"entities": {}, "progress": None})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        config = _config()
        config.gliner_use_async_tasks = True
        service = RedactService(config, transport=httpx.MockTransport(handler))
        try:
            _ = await service.redact(RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]))
        finally:
            await service.close()

    asyncio.run(scenario())
    assert polls["count"] == 2


def test_redact_async_flow_reports_progress() -> None:
    progress: list[float | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/extract_entities/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/task/task-1":
            return _json_response({"status": "finished", "progress": 0.75, "resource_id": "res-1"})
        if request.url.path == "/resource/res-1":
            return _json_response({"entities": {}, "progress": None})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        config = _config()
        config.gliner_use_async_tasks = True
        service = RedactService(config, transport=httpx.MockTransport(handler))
        try:
            _ = await service.redact(
                RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]),
                on_progress=lambda p: progress.append(p),
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert progress == [0.75]


def test_redact_async_flow_raises_when_task_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/extract_entities/async":
            return _json_response({"task_id": "task-1"})
        if request.url.path == "/task/task-1":
            return _json_response({"status": "failed", "error": "model exploded"})
        raise AssertionError(f"unexpected path {request.url.path}")

    async def scenario() -> None:
        config = _config()
        config.gliner_use_async_tasks = True
        service = RedactService(config, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(Exception) as excinfo:
                _ = await service.redact(RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]))
            assert "model exploded" in str(excinfo.value)
        finally:
            await service.close()

    asyncio.run(scenario())


def test_redact_raises_on_gliner_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async def scenario() -> None:
        service = _service(handler)
        try:
            with pytest.raises(ApiErrorException) as excinfo:
                _ = await service.redact(RedactInput(text="Herr Mueller wohnt.", entity_types=["person"]))
            assert excinfo.value.error_response["errorId"] == REDACT_ERROR
        finally:
            await service.close()

    asyncio.run(scenario())


def test_redact_batch_returns_one_output_per_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/batch_extract_entities"
        body = json.loads(request.content)
        assert body["texts"] == ["Herr Mueller.", "Frau Holzer."]
        return _json_response([
            {
                "entities": {
                    "person": [{"text": "Mueller", "confidence": 0.99, "start": 5, "end": 12}],
                },
                "progress": None,
            },
            {
                "entities": {
                    "person": [{"text": "Holzer", "confidence": 0.99, "start": 5, "end": 11}],
                },
                "progress": None,
            },
        ])

    async def scenario() -> list[str]:
        service = _service(handler)
        try:
            results = await service.redact_batch(
                RedactBatchInput(texts=["Herr Mueller.", "Frau Holzer."], entity_types=["person"])
            )
            return [r.text for r in results]
        finally:
            await service.close()

    assert asyncio.run(scenario()) == ["Herr [person:1].", "Frau [person:1]."]
