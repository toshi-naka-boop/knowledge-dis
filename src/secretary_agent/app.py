"""Agent Engine app wrapper (design §14.7 入口1: 定期起動 = 決定的オペレーション).

run_daily_sweep is registered as a standard (non-stream) Agent Engine
operation -- invoked over the `:query` REST verb, since vertexai.agent_engines
treats the "" key of register_operations() as the standard/non-stream API
mode. It never touches the LLM: it is a thin passthrough to
SecretaryApiClient.run_sweep() and its raw JSON result.

Any non-2xx/unreachable Cloud Run response raises out of
SecretaryApiClient.run_sweep() (see client.py) and is deliberately not
caught here, so the Agent Engine `:query` response itself becomes non-2xx
and Cloud Scheduler records the attempt as a failure and retries
(design §14.7 C-31: failures are not silenced).
"""

from __future__ import annotations

import asyncio
from typing import Any

from vertexai.agent_engines import AdkApp

from .agent import build_secretary_llm_agent
from .client import SecretaryApiClient


class SecretaryApp(AdkApp):
    """AdkApp subclass exposing run_daily_sweep as a standard operation."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("agent", build_secretary_llm_agent())
        super().__init__(**kwargs)

    async def run_daily_sweep(self) -> dict[str, Any]:
        """Deterministic entry point for Cloud Scheduler (§14.7 入口1).

        No LLM involvement: calls the existing Cloud Run
        POST /api/secretary/sweep and returns its JSON result unchanged.

        Declared async and executed in a worker thread so the ~10s blocking
        HTTP call never stalls the Agent Engine server's event loop: a
        synchronous version made the replica unresponsive to health probes
        mid-sweep and Vertex answered "Service Unavailable" (HTTP 400
        FAILED_PRECONDITION) even though the sweep completed on Cloud Run.
        """
        client = SecretaryApiClient()
        return await asyncio.to_thread(client.run_sweep)

    def register_operations(self) -> dict[str, list[str]]:
        """Publishes run_daily_sweep as a standard *async* (non-stream)
        operation -- still invoked via the `:query` verb -- in addition to
        AdkApp's default session-management operations."""
        operations = super().register_operations()
        operations["async"] = [*operations.get("async", []), "run_daily_sweep"]
        return operations
