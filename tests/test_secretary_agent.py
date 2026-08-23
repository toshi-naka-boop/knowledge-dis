"""Unit tests for src/secretary_agent (Milestone 3, design.md §14.7 B段).

Everything here runs offline against fake HTTP sessions / fake ToolContext
objects -- no network, no real Vertex AI, no real Cloud Run. Per the design
goal 22(a), this whole module must skip cleanly (not fail) in an environment
where google-adk / vertexai are not installed, so it never breaks the
existing test suite (`.venv/bin/python3 -m unittest discover -s tests`).

Goal 22(b) is checked separately by running this same module inside the
pinned `.venv-agent` environment (scripts/requirements-agent.txt), where it
should skip 0 tests.
"""

from __future__ import annotations

import os
import sys
import asyncio
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

try:
    import google.adk  # noqa: F401
    import vertexai  # noqa: F401

    _ADK_AVAILABLE = True
    _ADK_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - exercised when ADK isn't installed
    _ADK_AVAILABLE = False
    _ADK_IMPORT_ERROR = exc

if _ADK_AVAILABLE:
    from secretary_agent.agent import build_secretary_llm_agent, get_my_digest
    from secretary_agent.app import SecretaryApp
    from secretary_agent.client import SecretaryApiClient, SecretaryApiError


def _skip_reason() -> str:
    return f"google-adk/vertexai not installed ({_ADK_IMPORT_ERROR}); B段 tests skipped (design goal 22a)"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeSession:
    """Records calls and returns a canned response or raises a canned
    exception, standing in for requests.Session in client.py's tests."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, str, dict]] = []

    def _handle(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)


@unittest.skipUnless(_ADK_AVAILABLE, _skip_reason())
class SecretaryApiClientTest(unittest.TestCase):
    """(a) client.run_sweep / client.fetch_digest against a fake Session."""

    def _client(self, session) -> "SecretaryApiClient":
        return SecretaryApiClient(
            base_url="https://kd.example.com",
            api_key="test-key",
            session=session,
        )

    def test_run_sweep_success_returns_json_and_sends_api_key_header(self):
        session = _FakeSession(response=_FakeResponse(200, {"cards_created": 2}))
        result = self._client(session).run_sweep()

        self.assertEqual(result, {"cards_created": 2})
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://kd.example.com/api/secretary/sweep")
        self.assertEqual(kwargs["headers"]["X-API-Key"], "test-key")

    def test_run_sweep_non_2xx_raises(self):
        session = _FakeSession(response=_FakeResponse(401, text="unauthorized"))
        with self.assertRaises(SecretaryApiError):
            self._client(session).run_sweep()

    def test_run_sweep_connection_failure_raises(self):
        import requests

        session = _FakeSession(raise_exc=requests.exceptions.ConnectionError("boom"))
        with self.assertRaises(SecretaryApiError):
            self._client(session).run_sweep()

    def test_fetch_digest_success_passes_employee_id_as_query_param(self):
        digest = {
            "employee_id": "emp_jordan_lee",
            "date": "2026-08-23",
            "reminders": [],
            "stagnation_cards": [],
            "profile_diff_cards": [],
        }
        session = _FakeSession(response=_FakeResponse(200, digest))
        result = self._client(session).fetch_digest(employee_id="emp_jordan_lee")

        self.assertEqual(result, digest)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://kd.example.com/api/secretary/digest")
        self.assertEqual(kwargs["params"], {"employee_id": "emp_jordan_lee"})
        self.assertEqual(kwargs["headers"]["X-API-Key"], "test-key")

    def test_fetch_digest_non_2xx_raises(self):
        session = _FakeSession(response=_FakeResponse(500, text="boom"))
        with self.assertRaises(SecretaryApiError):
            self._client(session).fetch_digest(employee_id="emp_x")

    def test_fetch_digest_connection_failure_raises(self):
        import requests

        session = _FakeSession(raise_exc=requests.exceptions.ConnectionError("boom"))
        with self.assertRaises(SecretaryApiError):
            self._client(session).fetch_digest(employee_id="emp_x")


@unittest.skipUnless(_ADK_AVAILABLE, _skip_reason())
class SecretaryAppTest(unittest.TestCase):
    """(b) SecretaryApp.run_daily_sweep: LLM non-involvement, result
    passthrough, and failure propagation (design §14.7 入口1)."""

    def test_run_daily_sweep_returns_client_result(self):
        app = SecretaryApp()
        fake_client = mock.Mock()
        fake_client.run_sweep.return_value = {"cards_created": 3, "mail_seeds_processed": 1}

        with mock.patch("secretary_agent.app.SecretaryApiClient", return_value=fake_client):
            result = asyncio.run(app.run_daily_sweep())

        self.assertEqual(result, {"cards_created": 3, "mail_seeds_processed": 1})
        fake_client.run_sweep.assert_called_once_with()

    def test_run_daily_sweep_propagates_failure(self):
        app = SecretaryApp()
        fake_client = mock.Mock()
        fake_client.run_sweep.side_effect = SecretaryApiError("Cloud Run returned 401")

        with mock.patch("secretary_agent.app.SecretaryApiClient", return_value=fake_client):
            with self.assertRaises(SecretaryApiError):
                asyncio.run(app.run_daily_sweep())

    def test_run_daily_sweep_registered_as_standard_non_stream_operation(self):
        app = SecretaryApp()
        operations = app.register_operations()

        self.assertIn("run_daily_sweep", operations.get("async", []))
        self.assertNotIn("run_daily_sweep", operations.get("", []))
        # Never exposed as a stream/async_stream (LLM-facing) operation.
        self.assertNotIn("run_daily_sweep", operations.get("stream", []))
        self.assertNotIn("run_daily_sweep", operations.get("async_stream", []))


@unittest.skipUnless(_ADK_AVAILABLE, _skip_reason())
class SecretaryLlmAgentToolsTest(unittest.TestCase):
    """(c) The LLM agent's tool list is get_my_digest only -- no
    run_daily_sweep, confirm, dismiss, or review (design §14.7 入口2,
    C-33/FR20)."""

    def test_tool_list_is_get_my_digest_only(self):
        agent = build_secretary_llm_agent()
        tool_names = {getattr(t, "__name__", getattr(t, "name", repr(t))) for t in agent.tools}

        self.assertEqual(tool_names, {"get_my_digest"})
        for forbidden in ("run_daily_sweep", "confirm", "dismiss", "review"):
            self.assertNotIn(forbidden, tool_names)

    def test_model_is_pinned_to_global_location(self):
        from google.genai import Client

        agent = build_secretary_llm_agent()
        api_client = agent.model.api_client

        self.assertIsInstance(api_client, Client)
        # google.genai.Client exposes the resolved location it was built
        # with; the whole point of _GlobalGemini is that this is "global"
        # regardless of GOOGLE_CLOUD_LOCATION.
        self.assertEqual(getattr(api_client, "_api_client", api_client).location, "global")


@unittest.skipUnless(_ADK_AVAILABLE, _skip_reason())
class GetMyDigestToolTest(unittest.TestCase):
    """(d) get_my_digest uses the session's user_id, never a caller-supplied
    employee_id (design §14.7 C-34), via a faked ToolContext."""

    def test_uses_session_user_id_not_an_argument(self):
        fake_tool_context = mock.Mock()
        fake_tool_context.user_id = "emp_jordan_lee"

        fake_client = mock.Mock()
        fake_client.fetch_digest.return_value = {
            "employee_id": "emp_jordan_lee",
            "date": "2026-08-23",
            "reminders": [],
            "stagnation_cards": [],
            "profile_diff_cards": [],
        }

        with mock.patch("secretary_agent.agent._build_client", return_value=fake_client):
            result = get_my_digest(fake_tool_context)

        fake_client.fetch_digest.assert_called_once_with(employee_id="emp_jordan_lee")
        self.assertEqual(result["employee_id"], "emp_jordan_lee")

    def test_get_my_digest_signature_has_no_employee_id_parameter(self):
        import inspect

        params = list(inspect.signature(get_my_digest).parameters)
        self.assertNotIn("employee_id", params)


if __name__ == "__main__":
    unittest.main()
