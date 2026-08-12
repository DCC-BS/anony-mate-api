from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dcc_backend_common.fastapi_error_handling import inject_api_error_handler
from dcc_backend_common.fastapi_health_probes import health_probe_router
from dcc_backend_common.fastapi_health_probes.router import ServiceDependency
from dcc_backend_common.logger import get_logger, init_logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from anony_mate_backend.container import Container
from anony_mate_backend.routers import example_router
from anony_mate_backend.utils.configuration import Configuration
from anony_mate_backend.utils.middleware import add_logging_middleware

config = {} # TODO load your config here

service_dependencies: list[ServiceDependency] = [
    {"name": "llm", "health_check_url": config.llm_health_check_url, "api_key": config.llm_api_key},
]


def create_app() -> FastAPI:
    init_logger()

    logger: BoundLogger = get_logger("app")
    logger.info("Starting anony-mate-backend API application")

    # Set up dependency injection container
    logger.debug("Configuring dependency injection container")
    container = Container()
    container.wire(modules=["anony_mate_backend.routers.example_router"])
    container.check_dependencies()
    logger.info("Dependency injection configured")

    config: Configuration = container.config()
    logger.info(f"Running with configuration: {config}")

    # only in development mode, enable pydantic_ai logfire instrumentation
    if config.environment == "development":
        import os

        import logfire

        # Only configure logfire if token is available (avoids interactive prompts)
        if os.getenv("LOGFIRE_TOKEN"):
            logfire.configure()
            logfire.instrument_pydantic_ai()

    service_dependencies: list[ServiceDependency] = [
        {"name": "llm", "health_check_url": config.llm_health_check_url, "api_key": config.llm_api_key},
    ]

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        """
        Application lifecycle context manager.

        Use this for startup/shutdown tasks like:
        - Loading auth configuration
        - Warming up connections
        - Initializing resources
        """
        yield

    app = FastAPI(
        title="anony-mate-backend",
        description="Tool for anonymize textTool for anonymize text",
        version="0.1.0",
        lifespan=lifespan,
    logger = get_logger("app")
    logger.info("Starting anony_mate_backend application")

    # Initialize FastAPI app
    app = FastAPI(
        title="anony_mate_backend API",
    )

    app.include_router(health_probe_router(service_dependencies))
    inject_api_error_handler(app)

    # Configure CORS
    logger.debug("Setting up CORS middleware")
    app.add_middleware(
        CORSMiddleware,  # ty:ignore[invalid-argument-type]
        allow_origins=[config.client_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info(f"CORS configured with origin: {config.client_url}")

    # Add logging middleware
    add_logging_middleware(app)

    # Include routers
    logger.debug("Registering API routers")
    app.include_router(example_router.create_router())
    logger.info("All routers registered")

    logger.info("API setup complete")
    return app

app = create_app()
