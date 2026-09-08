"""Test-wide environment so importing the package does not crash.

``anony_mate_api`` builds ``AppConfig.from_env()`` at import time (via the
container), and that reads required variables straight from the process
environment. ``mise run test:unit`` sets ``APP_MODE=ci`` but does not run
``varlock load``, so the schema never fills these in for pytest. Set the
minimum the config demands here, before any test imports the package.
"""

import os

os.environ.setdefault("APP_MODE", "ci")
os.environ.setdefault("IS_PROD", "false")
os.environ.setdefault("LLM_API_KEY", "none")
os.environ.setdefault("CLIENT_URL", "http://localhost:3000")
os.environ.setdefault("LLM_URL", "http://localhost:8001/v1")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_HEALTH_CHECK_URL", "http://localhost:8001/health")
os.environ.setdefault("GLINER_API_BASE_URL", "http://localhost:8081")
os.environ.setdefault("GLINER_API_KEY", "none")
