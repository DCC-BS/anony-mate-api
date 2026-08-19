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
    gliner_http_timeout_seconds: float = Field(description="The HTTP timeout for Gliner API calls", default=30.0)

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
            gliner_http_timeout_seconds=float(os.getenv("GLINER_HTTP_TIMEOUT_SECONDS", 30.0)),
            gliner_api_key=get_env_or_throw("GLINER_API_KEY"),
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
            glen_http_timeout_seconds={self.gliner_http_timeout_seconds},
            gliner_api_key={log_secret(self.gliner_api_key)},
        )
        """
