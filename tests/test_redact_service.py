from anony_mate_api.models.gliner_models import GlinerEntity, GlinerResponse
from anony_mate_api.services.redact_service import _create_entities_dict, _filter_blacklisted


def _gliner_response() -> GlinerResponse:
    return GlinerResponse(
        entities={
            "person": [
                GlinerEntity(text="John", confidence=1.0, start=0, end=4),
                GlinerEntity(text="Acme Corp", confidence=1.0, start=11, end=20),
            ],
            "location": [GlinerEntity(text="Basel", confidence=1.0, start=24, end=29)],
        }
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
