"""The OpenAPI schema must not grow when it is served repeatedly."""

from fastapi import FastAPI

from anony_mate_api.app import _register_form_option_schemas

MARKER = "**Example Pydantic model**"


def _descriptions(schema: dict) -> dict[str, str]:
    return {
        name: component.get("description", "")
        for name, component in schema["components"]["schemas"].items()
        if MARKER in component.get("description", "")
    }


def test_example_models_are_injected_once() -> None:
    app = FastAPI()
    _register_form_option_schemas(app)

    first = app.openapi()
    served = _descriptions(first)
    assert served, "no schema carries an example model"
    assert all(desc.count(MARKER) == 1 for desc in served.values())

    second = app.openapi()
    assert _descriptions(second) == served, "serving the schema again grew the examples"
