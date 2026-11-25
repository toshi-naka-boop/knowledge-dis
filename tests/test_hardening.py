"""Regression tests for the hardening round (input validation, fail-closed key,
decline visibility, generic 500s, inference-failure audit reason).

All external services are fakes/mocks; nothing here touches Firestore or Gemini.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.gemini_adapters import GeminiConnectionInferencer, GeminiEmbedder
from knowledge_discovery.matching import FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import (
    Agent,
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
    ProfileItem,
)
from knowledge_discovery.service import InvalidConsentDecisionError, KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore

try:
    from fastapi.testclient import TestClient

    from knowledge_discovery.server import MAX_QUESTION_CHARS, create_app, create_app_from_env

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

KEY = "test-hardening-key"
H = {"X-API-Key": KEY}


def _build():
    store = InMemoryStore()
    inferencer = FakeConnectionInferencer()
    engine = MatchingEngine(inferencer=inferencer, vector_floor=0.20, connection_threshold=0.50)
    service = KnowledgeDiscoveryService(store=store, matching_engine=engine)
    store.save_agent(
        Agent(
            agent_id="agent_marcus",
            employee_id="emp_marcus",
            display_name="Marcus Delgado",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
    )
    store.save_profile(
        Profile(
            employee_id="emp_marcus",
            name="Marcus Delgado",
            role="Broker",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Brokers medical office buildings and tracks zoning and clinic relocation.",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )
    )
    store.save_profile(Profile(employee_id="emp_jordan", name="Jordan Lee", role="Requester", items=[]))
    inferencer.set_override(
        "emp_marcus",
        ConnectionInferenceResult(
            connection=ConnectionDetails(reason_text="Knows clinic zoning.", score=0.9),
            cited_item_keys=["current_work"],
        ),
    )
    return store, service


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestQueryValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.service = _build()
        self.client = TestClient(create_app(store=self.store, service=self.service, api_key=KEY))

    def _post(self, **body):
        return self.client.post("/api/query", json=body, headers=H)

    def test_empty_question_rejected(self) -> None:
        self.assertEqual(self._post(requester_id="emp_jordan", question_text="").status_code, 422)

    def test_blank_question_rejected(self) -> None:
        self.assertEqual(self._post(requester_id="emp_jordan", question_text="   \n ").status_code, 422)

    def test_overlong_question_rejected_before_any_inference(self) -> None:
        res = self._post(requester_id="emp_jordan", question_text="x" * (MAX_QUESTION_CHARS + 1))
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.inferencer_calls(), 0)

    def inferencer_calls(self) -> int:
        return len(self.service.matching_engine.inferencer.call_history)

    def test_malformed_requester_id_rejected(self) -> None:
        res = self._post(requester_id="../etc/passwd", question_text="clinic zoning question")
        self.assertEqual(res.status_code, 422)

    def test_unknown_requester_is_404_and_nothing_is_recorded(self) -> None:
        res = self._post(requester_id="emp_nobody", question_text="clinic zoning question")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.store.list_messages(), [])

    def test_seeded_requester_without_profile_is_accepted(self) -> None:
        """The demo's requesters (e.g. Jordan Lee) have an autonomy policy but no profile."""
        from scripts.generate_seeds import populate_store

        store = InMemoryStore()
        import contextlib, io

        with contextlib.redirect_stdout(io.StringIO()):
            populate_store(store, dry_run=False)
        self.assertIsNone(store.get_profile("emp_jordan_lee"))
        client = TestClient(create_app(store=store, api_key=KEY))
        res = client.post(
            "/api/query",
            json={"requester_id": "emp_jordan_lee", "question_text": "Who knows medical clinic zoning and relocation?"},
            headers=H,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            client.post(
                "/api/query",
                json={"requester_id": "emp_not_seeded", "question_text": "Who knows medical clinic zoning?"},
                headers=H,
            ).status_code,
            404,
        )

    def test_valid_query_still_works(self) -> None:
        res = self._post(requester_id="emp_jordan", question_text="Who knows medical clinic zoning and relocation?")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["dispatched_count"], 1)


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestConsentDecisionValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.service = _build()
        self.client = TestClient(create_app(store=self.store, service=self.service, api_key=KEY))
        res = self.client.post(
            "/api/query",
            json={"requester_id": "emp_jordan", "question_text": "Who knows medical clinic zoning and relocation?"},
            headers=H,
        )
        self.assertEqual(res.status_code, 200)
        self.ask_id = res.json()["dispatched_asks"][0]

    def _consent(self, decision, **extra):
        return self.client.post(
            "/api/candidate/agent_marcus/consent",
            json={"ask_audit_id": self.ask_id, "decision": decision, **extra},
            headers=H,
        )

    def test_unknown_decision_is_rejected_and_ask_stays_pending(self) -> None:
        for bad in ("maybe", "", "GRANTED", "approved"):
            self.assertEqual(self._consent(bad).status_code, 422, bad)
        ask = self.store.get_message(self.ask_id)
        self.assertEqual(ask.consent_state, "pending")
        # a valid decision still works afterwards
        self.assertEqual(self._consent("granted").status_code, 200)

    def test_service_layer_rejects_unknown_decision_too(self) -> None:
        with self.assertRaises(InvalidConsentDecisionError):
            self.service.respond_consent("agent_marcus", self.ask_id, decision="maybe")
        self.assertEqual(self.store.get_message(self.ask_id).consent_state, "pending")

    def test_overlong_reason_rejected(self) -> None:
        self.assertEqual(self._consent("declined", reason_text="r" * 1001).status_code, 422)

    def test_decline_is_visible_to_requester_with_name_and_reason(self) -> None:
        """Design: declines are never hidden. The UI/docs must not promise otherwise."""
        self.assertEqual(self._consent("declined", reason_text="Out of office this month.").status_code, 200)
        statuses = self.client.get("/api/requester/emp_jordan/status", headers=H).json()["statuses"]
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["state"], "declined")
        self.assertEqual(statuses[0]["respondent_name"], "Marcus Delgado")
        self.assertEqual(statuses[0]["decline_reason"], "Out of office this month.")


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestKeyFailsClosed(unittest.TestCase):
    def test_create_app_from_env_refuses_to_start_without_demo_api_key(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("DEMO_API_KEY", "TENANTS_JSON")}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                create_app_from_env()

    def test_app_without_any_key_rejects_every_guess(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "DEMO_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            store, service = _build()
            client = TestClient(create_app(store=store, service=service))
        for candidate in ("demo", "changeme", "", "none"):
            headers = {"X-API-Key": candidate} if candidate else {}
            self.assertEqual(client.get("/api/agents", headers=headers).status_code, 401, candidate)


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestGenericServerErrors(unittest.TestCase):
    def test_confirm_failure_does_not_leak_exception_text(self) -> None:
        store, service = _build()
        app = create_app(store=store, service=service, api_key=KEY)
        client = TestClient(app)
        secret = "internal-path /srv/secret.txt"
        with mock.patch(
            "knowledge_discovery.secretary.SecretaryService.confirm_stagnation_card",
            side_effect=RuntimeError(secret),
        ), self.assertLogs("knowledge_discovery.server", level="ERROR"):
            res = client.post("/api/secretary/confirm", json={"card_id": "c1", "edited_question": "q"}, headers=H)
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("secret.txt", res.text)
        self.assertIn("try again", res.json()["detail"])


class TestAdapterFailureHandling(unittest.TestCase):
    def test_inference_failure_is_logged_and_audit_reason_has_no_exception_text(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("boom-with-internal-detail")
        inferencer = GeminiConnectionInferencer(api_key="k", client=client)
        profile = Profile(
            employee_id="emp_x",
            name="X",
            role="r",
            items=[ProfileItem(key="k1", body="body", visibility="public", reviewed=True)],
        )
        with self.assertLogs("knowledge_discovery.gemini_adapters", level="WARNING"):
            result = inferencer.infer_connection("question", profile)
        self.assertIsNone(result.connection)
        self.assertIn("Inference evaluation unavailable", result.no_connection_reason)
        self.assertNotIn("boom-with-internal-detail", result.no_connection_reason)

    def test_rate_limit_backoff_is_configurable_and_bounded(self) -> None:
        client = MagicMock()
        client.models.embed_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")
        embedder = GeminiEmbedder(api_key="k", client=client)
        with mock.patch("knowledge_discovery.gemini_adapters.time.sleep") as sleep, mock.patch(
            "knowledge_discovery.gemini_adapters.RATE_LIMIT_BACKOFF_SECONDS", 0.5
        ):
            with self.assertRaises(RuntimeError):
                embedder.embed("text")
        self.assertEqual(client.models.embed_content.call_count, 4)
        sleep.assert_called_with(0.5)


class TestUserFacingTexts(unittest.TestCase):
    """The screens and docs must not promise what the implementation does not do."""

    WEB = Path(__file__).resolve().parents[1] / "src" / "knowledge_discovery" / "web"

    def test_pages_do_not_claim_that_declines_are_invisible(self) -> None:
        for name in ("candidate.html", "requester.html", "audit.html"):
            text = (self.WEB / name).read_text(encoding="utf-8")
            self.assertNotIn("invisible", text.lower(), name)
            self.assertNotIn("decline quietly", text.lower(), name)

    def test_candidate_page_tells_the_decliner_what_the_requester_will_see(self) -> None:
        text = (self.WEB / "candidate.html").read_text(encoding="utf-8")
        self.assertIn("will see your name and the reason you give", text)

    def test_unserved_duplicate_assets_are_gone(self) -> None:
        self.assertFalse((self.WEB / "ui.js").exists())
        self.assertFalse((self.WEB / "ui.css").exists())

    def test_offline_reason_text_is_plain_english_without_internal_keys(self) -> None:
        profile = Profile(
            employee_id="emp_x",
            name="X",
            role="r",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Brokers medical office buildings and tracks zoning and clinic relocation requirements.",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )
        result = FakeConnectionInferencer().infer_connection("medical clinic zoning relocation", profile)
        self.assertIsNotNone(result.connection)
        self.assertNotIn("current_work", result.connection.reason_text)
        self.assertFalse(any("\u3000" <= ch <= "\u9fff" for ch in result.connection.reason_text))


if __name__ == "__main__":
    unittest.main()
