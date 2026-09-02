from anony_mate_api.models.gliner_models import GlinerEntity, GlinerProgress, GlinerResponse
from anony_mate_api.models.redact_models import Entity
from anony_mate_api.services.redact_service import (
    _create_entities_dict,
    _filter_blacklisted,
    _filter_malformed,
    _propagate_repeats,
)


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
