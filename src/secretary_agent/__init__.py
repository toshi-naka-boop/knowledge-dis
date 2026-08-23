"""Runtime secretary package (design.md §14.7 B段).

Independent of src/knowledge_discovery -- deployed separately onto GEAP
Agent Runtime (Vertex AI Agent Engine) and only reaches the existing
Cloud Run service over its public HTTP API.
"""

from .agent import build_secretary_llm_agent, get_my_digest
from .app import SecretaryApp
from .client import SecretaryApiClient, SecretaryApiError

__all__ = [
    "SecretaryApiClient",
    "SecretaryApiError",
    "SecretaryApp",
    "build_secretary_llm_agent",
    "get_my_digest",
]
