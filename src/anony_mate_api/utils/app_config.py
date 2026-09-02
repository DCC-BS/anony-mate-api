import os
from typing import override

from dcc_backend_common.config import get_env_or_throw, log_secret
from dcc_backend_common.config.app_config import LlmConfig
from pydantic import Field


class AppConfig(LlmConfig):
    """Application configuration loaded from environment variables."""

    client_url: str = Field(description="The URL for client application", default="http://localhost:3000")
    environment: str = Field(description="The application environment", default="development")
    llm_health_check_url: str = Field(
        description="The URL for LLM health check API", default="http://localhost:8001/health"
    )

    gliner_api_base_url: str = Field(description="The Glen base API url")
    gliner_api_key: str = Field(description="The Gliner API key")
    gliner_use_binary_upload: bool = Field(
        description="Send the text to Gliner as an uploaded file instead of a JSON field",
    )
    gliner_task_timeout_seconds: float = Field(
        description="Maximum seconds to wait for a Gliner extraction task to finish; a long document scans for minutes",
        default=3600.0,
    )
    gliner_http_timeout_seconds: float = Field(
        description="The HTTP timeout for Gliner API calls; long documents take minutes to scan",
        default=600.0,
    )
    gliner_poll_interval_seconds: float = Field(
        description="Interval in seconds between Gliner task status polling requests",
        default=3.0,
    )

    docling_url: str = Field(description="The URL for Docling service", default="http://localhost:5001/v1")
    docling_api_key: str = Field(
        description="The API key for Docling service, set it to none if none is required", default="none"
    )
    docling_poll_interval_seconds: float = Field(
        description="Interval in seconds between Docling task status polling requests",
        default=3.0,
    )
    docling_conversion_timeout_seconds: float = Field(
        description="Maximum seconds to wait for Docling conversion task to complete; large scanned PDFs take minutes",
        default=1800.0,
    )
    docling_http_timeout_seconds: float = Field(
        description="Per-request HTTP timeout in seconds for Docling API calls; the upload itself can be slow",
        default=300.0,
    )
    docling_pdf_backend: str = Field(
        description="Docling PDF parsing backend; pypdfium2 is faster, docling_parse more thorough",
        default="pypdfium2",
    )
    docling_table_mode: str = Field(
        description="Docling table structure mode: fast or accurate",
        default="accurate",
    )
    conversion_max_concurrent: int = Field(
        description="Conversions run at once; Docling serves two, so a little overshoot keeps it fed",
        default=3,
    )
    conversion_max_queued: int = Field(
        description="Conversions allowed to wait before submissions are refused",
        default=16,
    )
    redaction_max_concurrent: int = Field(
        description="Redactions run at once; measured flat at 32 concurrent against GLiNER",
        default=32,
    )
    redaction_max_queued: int = Field(
        description="Redactions allowed to wait before submissions are refused",
        default=256,
    )
    max_upload_bytes: int = Field(
        description="Largest accepted upload; queued documents are held in memory until converted",
        default=100 * 1024 * 1024,
    )
    task_abandoned_after_seconds: float = Field(
        description="A queued task nobody has polled for this long is dropped before it runs",
        default=60.0,
    )
    task_result_ttl_seconds: float = Field(
        description="Seconds an abandoned task and its result are kept before the store drops them",
        default=3600.0,
    )

    @classmethod
    @override
    def from_env(cls) -> "AppConfig":
        llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not llm_api_key:
            raise ValueError("LLM_API_KEY environment variable must be set")

        return cls(
            client_url=get_env_or_throw("CLIENT_URL"),
            llm_api_key=llm_api_key,
            llm_url=get_env_or_throw("LLM_URL"),
            llm_model=get_env_or_throw("LLM_MODEL"),
            llm_health_check_url=get_env_or_throw("LLM_HEALTH_CHECK_URL"),
            environment=os.getenv("ENVIRONMENT", "production"),
            gliner_api_base_url=get_env_or_throw("GLINER_API_BASE_URL"),
            gliner_task_timeout_seconds=float(os.getenv("GLINER_TASK_TIMEOUT_SECONDS", "3600.0")),
            gliner_http_timeout_seconds=float(os.getenv("GLINER_HTTP_TIMEOUT_SECONDS", "600.0")),
            gliner_poll_interval_seconds=float(os.getenv("GLINER_POLL_INTERVAL_SECONDS", "3.0")),
            gliner_api_key=get_env_or_throw("GLINER_API_KEY"),
            gliner_use_binary_upload=os.getenv("GLINER_USE_BINARY_UPLOAD", "true").lower() == "true",
            # TEMPORARY: DOCLING_URL/DOCLING_API_KEY are not declared in
            # .env.schema, so varlock never supplies them and get_env_or_throw
            # aborts startup. Fall back to the field defaults until the schema
            # declares them; then restore get_env_or_throw for both.
            docling_url=os.getenv("DOCLING_URL", "http://localhost:5001/v1"),
            docling_api_key=os.getenv("DOCLING_API_KEY", "none"),
            docling_poll_interval_seconds=float(os.getenv("DOCLING_POLL_INTERVAL_SECONDS", "3.0")),
            docling_conversion_timeout_seconds=float(os.getenv("DOCLING_CONVERSION_TIMEOUT_SECONDS", "1800.0")),
            docling_http_timeout_seconds=float(os.getenv("DOCLING_HTTP_TIMEOUT_SECONDS", "300.0")),
            docling_pdf_backend=os.getenv("DOCLING_PDF_BACKEND", "pypdfium2"),
            docling_table_mode=os.getenv("DOCLING_TABLE_MODE", "accurate"),
            conversion_max_concurrent=int(os.getenv("CONVERSION_MAX_CONCURRENT", "3")),
            conversion_max_queued=int(os.getenv("CONVERSION_MAX_QUEUED", "16")),
            redaction_max_concurrent=int(os.getenv("REDACTION_MAX_CONCURRENT", "32")),
            redaction_max_queued=int(os.getenv("REDACTION_MAX_QUEUED", "256")),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
            task_abandoned_after_seconds=float(os.getenv("TASK_ABANDONED_AFTER_SECONDS", "60.0")),
            task_result_ttl_seconds=float(os.getenv("TASK_RESULT_TTL_SECONDS", "3600.0")),
        )

    @override
    def __str__(self) -> str:
        return f"""
        Configuration(
            client_url={self.client_url},
            llm_api_key={log_secret(self.llm_api_key)},
            llm_url={self.llm_url},
            llm_model={self.llm_model},
            llm_health_check_url={self.llm_health_check_url},
            environment={self.environment},
            gliner_api_base_url={self.gliner_api_base_url},
            gliner_task_timeout_seconds={self.gliner_task_timeout_seconds},
            gliner_http_timeout_seconds={self.gliner_http_timeout_seconds},
            gliner_poll_interval_seconds={self.gliner_poll_interval_seconds},
            gliner_api_key={log_secret(self.gliner_api_key)},
            gliner_use_binary_upload={self.gliner_use_binary_upload},
            docling_url={self.docling_url},
            docling_api_key={log_secret(self.docling_api_key)},
            docling_poll_interval_seconds={self.docling_poll_interval_seconds},
            docling_conversion_timeout_seconds={self.docling_conversion_timeout_seconds},
            docling_http_timeout_seconds={self.docling_http_timeout_seconds},
            docling_pdf_backend={self.docling_pdf_backend},
            docling_table_mode={self.docling_table_mode},
            conversion_max_concurrent={self.conversion_max_concurrent},
            conversion_max_queued={self.conversion_max_queued},
            redaction_max_concurrent={self.redaction_max_concurrent},
            redaction_max_queued={self.redaction_max_queued},
            max_upload_bytes={self.max_upload_bytes},
            task_abandoned_after_seconds={self.task_abandoned_after_seconds},
            task_result_ttl_seconds={self.task_result_ttl_seconds},
        )
        """
