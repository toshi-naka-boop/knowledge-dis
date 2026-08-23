"""HTTP client for the Cloud Run knowledge-discovery secretary API.

This package (src/secretary_agent) is deliberately independent of
src/knowledge_discovery: it only talks to the already-deployed Cloud Run
service over its public HTTP API (X-API-Key auth), exactly like any other
GEAP-registered client would (design.md §14.7 "パッケージング").
"""

from __future__ import annotations

import os
from typing import Any

import requests


class SecretaryApiError(RuntimeError):
    """Raised when the Cloud Run secretary API returns a non-2xx response or
    is unreachable.

    Callers (app.py's run_daily_sweep) must let this propagate rather than
    swallow it: design §14.7 入口1 requires Cloud Run failures to make the
    Agent Engine `:query` response itself non-2xx, so Cloud Scheduler can
    detect and retry the failed sweep (C-31: failures are not silenced).
    """


class SecretaryApiClient:
    """Thin client for POST /api/secretary/sweep and GET /api/secretary/digest."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout: float | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ["KD_API_BASE_URL"]).rstrip("/")
        self._api_key = api_key or os.environ["KD_API_KEY"]
        # Injectable so tests can substitute a fake Session instead of making
        # real network calls.
        self._session = session or requests.Session()
        # B-1 (round-11): the first sweep after a reseed took ~31s in production
        # (embedding_public + LLM question draft), so 30s produced a false
        # failure. Default 120s stays inside the Scheduler attempt deadline (180s).
        self._timeout = timeout if timeout is not None else float(os.environ.get("KD_API_TIMEOUT", "120"))

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    def run_sweep(self) -> dict[str, Any]:
        """POST /api/secretary/sweep (design §14.1, §14.7 入口1).

        Raises SecretaryApiError on a non-2xx response or connection
        failure.
        """
        try:
            resp = self._session.post(
                f"{self._base_url}/api/secretary/sweep",
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise SecretaryApiError(f"sweep request failed: {exc}") from exc
        if not (200 <= resp.status_code < 300):
            raise SecretaryApiError(
                f"sweep returned HTTP {resp.status_code}: {resp.text}"
            )
        return resp.json()

    def fetch_digest(self, employee_id: str) -> dict[str, Any]:
        """GET /api/secretary/digest?employee_id=<employee_id> (design §14.2, §14.8).

        Raises SecretaryApiError on a non-2xx response or connection
        failure.
        """
        try:
            resp = self._session.get(
                f"{self._base_url}/api/secretary/digest",
                params={"employee_id": employee_id},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise SecretaryApiError(f"digest request failed: {exc}") from exc
        if not (200 <= resp.status_code < 300):
            raise SecretaryApiError(
                f"digest returned HTTP {resp.status_code}: {resp.text}"
            )
        return resp.json()
