from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dcc_backend_common.fastapi_error_handling import inject_api_error_handler
from dcc_backend_common.fastapi_health_probes import health_probe_router
from dcc_backend_common.fastapi_health_probes.router import ServiceDependency
from dcc_backend_common.fastapi_logging_middleware import add_logging_middleware
from dcc_backend_common.logger import get_logger, init_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from structlog.stdlib import BoundLogger

from anony_mate_api.container import Container
from anony_mate_api.routers import blacklist_router, entity_types_router, redact_router
from anony_mate_api.utils.app_config import AppConfig

config = {}  # TODO load your config here


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """
    Lifespan context manager for startup and shutdown events.
    """
    # Startup: nothing to do here, container is configured synchronously
    yield
    # Shutdown: close resources
    logger = get_logger("app")
    logger.info("Shutting down application, closing resources...")
    container: Container = app.state.container
    redact_service = container.redact_service()
    await redact_service.aclose()
    logger.info("Resources closed successfully")


def _build_fastapi_app() -> FastAPI:
    """
    Instantiate the FastAPI application with metadata and lifespan.
    """
    app = FastAPI(
        title="anony-mate-api",
        description="Tool for anonymize textTool for anonymize text",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    return app


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
    app.include_router(health_probe_router(service_dependencies=service_dependencies))


def _configure_container(app: FastAPI, logger: BoundLogger) -> Container:
    """
    Configure the dependency injection container and attach it to app state.
    """
    logger.debug("Configuring dependency injection container")
    container = Container()
    container.wire(modules=[redact_router, entity_types_router])
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
    add_logging_middleware(app, excluded_paths={"/health", "/docs", "/openapi.json"})
    _register_routes(app, logger)

    # only in development mode, enable pydantic_ai logfire instrumentation
    if config.environment == "development":
        _init_logfire()

    logger.info("API setup complete")
    return app


app = create_app()
