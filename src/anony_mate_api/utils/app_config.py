from __future__ import annotations

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

    glen_api_url: str = Field(description="The Glen base API url")

    @classmethod
    @override
    def from_env(cls) -> AppConfig:
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
            glen_api_url=get_env_or_throw("GLEN_API_URL"),
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
            glen_api_url={self.glen_api_url}
        )
        """
