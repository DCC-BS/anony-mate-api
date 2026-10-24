import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from html import escape as escape_html
from pathlib import Path
from typing import Any, cast, get_type_hints

from dcc_backend_common.fastapi_error_handling import inject_api_error_handler
from dcc_backend_common.fastapi_health_probes import health_probe_router
from dcc_backend_common.fastapi_health_probes.router import ServiceDependency
from dcc_backend_common.fastapi_logging_middleware import add_logging_middleware
from dcc_backend_common.logger import get_logger, init_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic_core import PydanticUndefined
from structlog.stdlib import BoundLogger

from anony_mate_api.container import Container
from anony_mate_api.models import conversion, gliner_models, redact_models, tasks
from anony_mate_api.routers import (
    blacklist_router,
    conversion_router,
    entity_types_router,
    redact_router,
    task_router,
)
from anony_mate_api.utils.app_config import AppConfig

config = {}  # TODO load your config here


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Lifespan context manager for startup and shutdown events.
    """
    # The queue workers need a running loop, so they start here rather than
    # when the container builds the store.
    container: Container = app.state.container
    container.task_store().start()
    yield
    # Shutdown: close resources
    logger = get_logger("app")
    logger.info("Shutting down application, closing resources...")
    await container.task_store().stop()
    redact_service = container.redact_service()
    document_conversion_service = container.document_conversion_service()
    await redact_service.close()
    await document_conversion_service.close()
    logger.info("Resources closed successfully")


def _build_fastapi_app() -> FastAPI:
    """
    Instantiate the FastAPI application with metadata and lifespan.
    """
    app = FastAPI(
        title="anony-mate-api",
        description=Path(__file__).parent.joinpath("api_description.md").read_text(encoding="utf-8"),
        version="0.1.0",
        lifespan=_lifespan,
        openapi_tags=[
            {
                "name": "anonymize",
                "description": "Anonymize texts, documents and PDFs, and poll the submitted tasks' results",
            },
            {"name": "entities", "description": "Entity types and blacklists available for redaction"},
            {"name": "convert", "description": "Convert uploaded documents to markdown"},
            {"name": "tasks", "description": "Status and results of submitted tasks"},
            {"name": "probes", "description": "Liveness, readiness and startup probes for orchestration"},
        ],
        docs_url=None,
        redoc_url=None,
    )
    _register_form_option_schemas(app)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/docs", include_in_schema=False)
    async def docs() -> HTMLResponse:
        return HTMLResponse(_docs_html(title=f"{app.title} API Reference"))

    return app


def _docs_html(title: str) -> str:
    """
    The Stoplight Elements page, served from our own static files.
    """
    return f"""<!doctype html>
<html lang="en">
    <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title>{escape_html(title)}</title>
        <link rel="stylesheet" href="/static/elements/styles.min.css"/>
        <script src="/static/elements/web-components.min.js" defer></script>
        <style>html, body {{ margin: 0; padding: 0; }}</style>
    </head>
    <body>
        <elements-api
            apiDescriptionUrl="/openapi.json"
            router="hash"
            layout="sidebar"
            tryItCredentialsPolicy="same-origin"
        ></elements-api>
    </body>
</html>"""


def _pydantic_snippet(model: type[BaseModel]) -> str:
    """
    A readable Pydantic model definition for one schema, for the docs.
    """
    hints = get_type_hints(model)
    lines = [f"class {model.__name__}(BaseModel):"]
    # The model's own docstring, not one inherited from a base.
    doc = model.__dict__.get("__doc__")
    if doc:
        first = doc.splitlines()[0]
        # Skip MkDocs decorations, which are not part of the model itself.
        if not first.startswith(("!!!", "[!", "{")):
            lines.append(f'    """{first}"""')
    for name, field in model.model_fields.items():
        annotation = hints[name]
        annotation = annotation.__name__ if isinstance(annotation, type) else str(annotation)
        annotation = re.sub(r"anony_mate_api\.models\.\w+\.", "", annotation)
        annotation = annotation.replace("typing.", "")
        line = f"    {name}: {annotation}"
        if field.default is not PydanticUndefined:
            line += f" = {field.default!r}"
        elif field.default_factory is not None:
            factory = field.default_factory
            line += (
                f" = {factory()!r}"
                if callable(factory) and factory in (list, dict, set)
                else "  # has a default factory"
            )
        if field.description:
            line += f"  # {field.description.splitlines()[0]}"
        lines.append(line)
    return "\n".join(lines)


def _register_form_option_schemas(app: FastAPI) -> None:
    """
    Add the models referenced by the upload endpoints' JSON form parts.

    FastAPI only emits component schemas for models it references itself, so
    the ``$ref`` in ``json_form_openapi`` would dangle without this. Every
    component backed by one of our Pydantic models also carries its model
    definition as code, so API users see how to build the object.
    """
    our_models = {
        obj.__name__: obj
        for module in (
            redact_models,
            conversion,
            tasks,
            gliner_models,
        )
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__.startswith("anony_mate_api")
    }

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            # FastAPI's ``openapi`` caches its schema and hands the same dict
            # out on every call; injecting again would stack another example
            # onto each description on every request.
            return app.openapi_schema
        schema = _openapi()
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (
            redact_models.RedactFileOptions,
            redact_models.RedactPdfOptions,
            redact_models.RedactPdfAnnotateInput,
            # Result shapes the task endpoints document but FastAPI never
            # references itself.
            redact_models.DocumentRedactOutput,
            redact_models.PdfAnnotationsOutput,
        ):
            model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, sub_schema in model_schema.pop("$defs", {}).items():
                components.setdefault(name, sub_schema)
            components.setdefault(model.__name__, model_schema)
        for name, component in components.items():
            model = our_models.get(name)
            if model is None:
                continue
            snippet = _pydantic_snippet(model)
            description = component.get("description", "")
            component["description"] = (
                f"{description}\n\n**Example Pydantic model**\n\n```python\n{snippet}\n```"
                if description
                else f"**Example Pydantic model**\n\n```python\n{snippet}\n```"
            )
        return schema

    _openapi = app.openapi
    # FastAPI's documented way to customise the schema is replacing
    # ``app.openapi`` on the instance; ty only knows it as a method.
    cast(Any, app).openapi = openapi


def _register_health_routes(app: FastAPI, config: AppConfig) -> None:
    """
    Register health routes for the application.
    """
    service_dependencies: list[ServiceDependency] = [
        ServiceDependency(
            name="llm",
            health_check_url=config.llm_health_check_url,
            api_key=config.llm_api_key,
        ),
    ]
    # The probes come from the common library without a tag; include-time tags
    # group them under their own heading in the docs.
    app.include_router(health_probe_router(service_dependencies=service_dependencies), tags=["probes"])


def _configure_container(app: FastAPI, logger: BoundLogger) -> Container:
    """
    Configure the dependency injection container and attach it to app state.
    """
    logger.debug("Configuring dependency injection container")
    container = Container()
    container.wire(modules=[redact_router, entity_types_router, conversion_router, task_router])
    container.check_dependencies()
    logger.debug("Dependency injection configured")
    app.state.container = container
    return container


def _register_routes(app: FastAPI, logger: BoundLogger) -> None:
    """
    Register API routers.
    """
    logger.debug("Registering API routers")
    app.include_router(redact_router.create_router())
    app.include_router(entity_types_router.create_router())
    app.include_router(blacklist_router.create_router())
    app.include_router(conversion_router.create_router())
    app.include_router(task_router.create_router())
    logger.debug("All routers registered")


def _configure_cors(app: FastAPI, client_url: str, logger: BoundLogger) -> None:
    """
    Apply CORS middleware configuration.
    """
    logger.debug("Setting up CORS middleware")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[client_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.debug("CORS configured", origin=client_url)


def _init_logfire():
    import os

    import logfire

    # Only configure logfire if token is available (avoids interactive prompts)
    if os.getenv("LOGFIRE_TOKEN"):
        logfire.configure()
        logfire.instrument_pydantic_ai()


def create_app() -> FastAPI:
    init_logger(app_name="AnonyMate")

    logger: BoundLogger = get_logger("app")
    logger.info("Starting anony-mate-api API application")

    app = _build_fastapi_app()

    inject_api_error_handler(app)
    container = _configure_container(app, logger)
    config = container.app_config()
    logger.info(f"AppConfig loaded: {config}")

    _register_health_routes(app, config)
    _configure_cors(app, config.client_url, logger)
    add_logging_middleware(app, excluded_paths={"/health", "/docs", "/openapi.json", "/task"})
    _register_routes(app, logger)

    # only in development mode, enable pydantic_ai logfire instrumentation
    if config.environment == "development":
        _init_logfire()

    logger.info("API setup complete")
    return app


app = create_app()
