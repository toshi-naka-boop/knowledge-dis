"""Regression tests for the second review round.

Covers: attachment validation before the consent state change, the requester is
never asked, confirm-path input limits, per-question history, no double requests,
no orphan query when the embedding service fails, the measured cost of one
question, and the per-question numbers in the audit header.

All external services are fakes; nothing here touches Firestore or Gemini.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.matching import DeterministicEmbedder, FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import (
    Agent,
    Card,
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
    ProfileItem,
)
from knowledge_discovery.service import (
    InvalidAttachmentError,
    KnowledgeDiscoveryService,
)
from knowledge_discovery.store import InMemoryStore

try:
    from fastapi.testclient import TestClient

    from knowledge_discovery.server import MAX_QUESTION_CHARS, create_app

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

KEY = "test-round2-key"
H = {"X-API-Key": KEY}
QUESTION = "Who knows clinic zoning and relocation?"


class CountingEmbedder(DeterministicEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return super().embed(text)


class FailingEmbedder(DeterministicEmbedder):
    def embed(self, text: str) -> list[float]:
        raise RuntimeError("Gemini embedding API call failed: 503 secret-detail")


def _agent(name: str) -> tuple[Agent, Profile]:
    emp = f"emp_{name}"
    agent = Agent(
        agent_id=f"agent_{name}",
        employee_id=emp,
        display_name=name.title(),
        supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
        active=True,
    )
    profile = Profile(
        employee_id=emp,
        name=name.title(),
        role="Broker",
        items=[
            ProfileItem(
                key="current_work",
                body="Brokers medical office buildings and tracks clinic zoning and relocation.",
                visibility="public",
                reviewed=True,
            )
        ],
    )
    return agent, profile


def _build(names=("marcus", "rachel"), embedder=None):
    store = InMemoryStore()
    inferencer = FakeConnectionInferencer()
    engine = MatchingEngine(
        embedder=embedder or DeterministicEmbedder(),
        inferencer=inferencer,
        vector_floor=0.20,
        connection_threshold=0.50,
    )
    service = KnowledgeDiscoveryService(store=store, matching_engine=engine)
    for n in names:
        agent, profile = _agent(n)
        store.save_agent(agent)
        store.save_profile(profile)
        inferencer.set_override(
            profile.employee_id,
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="Knows clinic zoning.", score=0.9),
                cited_item_keys=["current_work"],
            ),
        )
    store.save_profile(Profile(employee_id="emp_jordan", name="Jordan Lee", role="Requester", items=[]))
    return store, service, inferencer


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestConsentAttachmentValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.service, _ = _build()
        self.client = TestClient(create_app(store=self.store, service=self.service, api_key=KEY))
        res = self.client.post("/api/query", json={"requester_id": "emp_jordan", "question_text": QUESTION}, headers=H)
        self.assertEqual(res.status_code, 200)
        self.ask_id = res.json()["dispatched_asks"][0]
        self.agent_id = self.store.get_message(self.ask_id).to_entity

    def _consent(self, **body):
        return self.client.post(f"/api/candidate/{self.agent_id}/consent", json=body, headers=H)

    def test_unknown_attachment_type_is_422_and_ask_stays_pending(self) -> None:
        res = self._consent(
            ask_audit_id=self.ask_id,
            decision="declined",
            reason_text="Sorry, busy",
            attachment={"type": "pdf", "content": "x"},
        )
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.store.get_message(self.ask_id).consent_state, "pending")
        # the candidate can still answer, and the reason is kept
        ok = self._consent(ask_audit_id=self.ask_id, decision="declined", reason_text="Sorry, busy")
        self.assertEqual(ok.status_code, 200)
        statuses = self.client.get("/api/requester/emp_jordan/status", headers=H).json()["statuses"]
        reasons = [s["decline_reason"] for s in statuses if s["state"] == "declined"]
        self.assertIn("Sorry, busy", reasons)

    def test_service_layer_rejects_bad_attachment_before_transition(self) -> None:
        with self.assertRaises(InvalidAttachmentError):
            self.service.respond_consent(
                self.agent_id, self.ask_id, "declined", "r", attachment={"type": "pdf", "content": "x"}
            )
        self.assertEqual(self.store.get_message(self.ask_id).consent_state, "pending")
        with self.assertRaises(InvalidAttachmentError):
            self.service.respond_consent(
                self.agent_id, self.ask_id, "declined", "r", attachment={"type": "link", "content": None}
            )
        self.assertEqual(self.store.get_message(self.ask_id).consent_state, "pending")

    def test_valid_attachment_still_works(self) -> None:
        res = self._consent(
            ask_audit_id=self.ask_id,
            decision="declined",
            reason_text="See this",
            attachment={"type": "link", "content": "https://example.com/guide"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["outcome_intent"], "decline_with_reason")


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestRequesterNeverAsked(unittest.TestCase):
    def test_agent_owner_asking_is_not_asked_and_not_screened(self) -> None:
        store, service, inferencer = _build()
        client = TestClient(create_app(store=store, service=service, api_key=KEY))
        res = client.post("/api/query", json={"requester_id": "emp_marcus", "question_text": QUESTION}, headers=H)
        self.assertEqual(res.status_code, 200)
        asked_agents = [store.get_message(a).to_entity for a in res.json()["dispatched_asks"]]
        self.assertNotIn("agent_marcus", asked_agents)
        self.assertIn("agent_rachel", asked_agents)
        self.assertNotIn("emp_marcus", [c["employee_id"] for c in res.json()["funnel_candidates"]])
        self.assertNotIn("emp_marcus", [e for _, e in inferencer.call_history])


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestConfirmLimits(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.service, _ = _build()
        self.store.save_card(
            Card(
                card_id="card_x",
                owner_employee_id="emp_jordan",
                type="stagnation",
                tier="notice",
                payload={"task_title": "t", "evidence_line": "e"},
                status="open",
            )
        )
        self.client = TestClient(create_app(store=self.store, service=self.service, api_key=KEY))

    def test_blank_and_overlong_questions_are_rejected_and_card_stays_open(self) -> None:
        for text in ("", "   \n ", "x" * (MAX_QUESTION_CHARS + 1)):
            res = self.client.post(
                "/api/secretary/confirm", json={"card_id": "card_x", "edited_question": text}, headers=H
            )
            self.assertEqual(res.status_code, 422, repr(text[:10]))
        self.assertEqual(self.store.get_card("card_x").status, "open")
        self.assertEqual(self.store.list_messages(), [])

    def test_valid_confirm_still_works(self) -> None:
        res = self.client.post(
            "/api/secretary/confirm", json={"card_id": "card_x", "edited_question": QUESTION}, headers=H
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "confirmed")


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestHistoryAndDuplicates(unittest.TestCase):
    def setUp(self) -> None:
        self.store, self.service, _ = _build()
        self.client = TestClient(create_app(store=self.store, service=self.service, api_key=KEY))

    def _ask(self, text=QUESTION):
        res = self.client.post("/api/query", json={"requester_id": "emp_jordan", "question_text": text}, headers=H)
        self.assertEqual(res.status_code, 200)
        return res.json()

    def _answer(self, ask_id, decision, **kw):
        agent_id = self.store.get_message(ask_id).to_entity
        return self.client.post(
            f"/api/candidate/{agent_id}/consent",
            json={"ask_audit_id": ask_id, "decision": decision, **kw},
            headers=H,
        )

    def test_pending_contact_is_not_asked_twice(self) -> None:
        first = self._ask()
        self.assertEqual(first["dispatched_count"], 2)
        second = self._ask()
        self.assertEqual(second["dispatched_count"], 0)
        self.assertEqual(sorted(second["already_asked_names"]), ["Marcus", "Rachel"])
        asks = [m for m in self.store.list_messages() if m.intent == "connect_ask"]
        self.assertEqual(len(asks), 2)
        # the skip is explained in the audit trail
        skips = [m for m in self.store.list_messages() if m.intent == "no_connection"]
        self.assertTrue(all("Not asked again" in m.payload["reason_text"] for m in skips))

    def test_accepted_contact_is_not_asked_again_but_declined_one_is(self) -> None:
        first = self._ask()
        ask_marcus = next(a for a in first["dispatched_asks"] if self.store.get_message(a).to_entity == "agent_marcus")
        ask_rachel = next(a for a in first["dispatched_asks"] if self.store.get_message(a).to_entity == "agent_rachel")
        self.assertEqual(self._answer(ask_marcus, "granted").status_code, 200)
        self.assertEqual(self._answer(ask_rachel, "declined", reason_text="busy").status_code, 200)
        # same question again: accepted Marcus is skipped, declined Rachel is asked again
        again = self._ask(QUESTION)
        asked = [self.store.get_message(a).to_entity for a in again["dispatched_asks"]]
        self.assertEqual(asked, ["agent_rachel"])
        self.assertEqual(again["already_asked_names"], ["Marcus"])

    def test_different_question_can_reach_an_accepted_contact(self) -> None:
        first = self._ask()
        ask_marcus = next(a for a in first["dispatched_asks"] if self.store.get_message(a).to_entity == "agent_marcus")
        self.assertEqual(self._answer(ask_marcus, "granted").status_code, 200)
        other = self._ask("Another clinic zoning relocation question")
        asked = {self.store.get_message(a).to_entity for a in other["dispatched_asks"]}
        self.assertIn("agent_marcus", asked)
        self.assertEqual(other["already_asked_names"], [])

    def test_second_question_keeps_the_first_answer_in_history(self) -> None:
        first = self._ask()
        ask_marcus = next(a for a in first["dispatched_asks"] if self.store.get_message(a).to_entity == "agent_marcus")
        ask_rachel = next(a for a in first["dispatched_asks"] if self.store.get_message(a).to_entity == "agent_rachel")
        self._answer(ask_marcus, "granted")
        self._answer(ask_rachel, "declined", reason_text="busy this week")
        self._ask("Another clinic zoning relocation question")
        data = self.client.get("/api/requester/emp_jordan/status", headers=H).json()
        self.assertEqual(len(data["history"]), 2)
        newest, oldest = data["history"]
        self.assertEqual(oldest["question_text"], QUESTION)
        states = {s["respondent_name"]: s for s in oldest["statuses"]}
        self.assertEqual(states["Marcus"]["state"], "matched")
        self.assertEqual(states["Rachel"]["state"], "declined")
        self.assertEqual(states["Rachel"]["decline_reason"], "busy this week")
        self.assertEqual(newest["question_text"], "Another clinic zoning relocation question")

    def test_audit_header_shows_numbers_of_the_latest_question(self) -> None:
        self._ask()
        a1 = self.client.get("/api/audit/messages", headers=H).json()["funnel_stats"]["latest_query"]
        self.assertEqual((a1["asked"], a1["not_asked"]), (2, 0))
        self.assertEqual(a1["agents_considered"], 2)
        self.assertEqual(a1["profiles_screened"], 2)  # marcus, rachel (the requester is left out)
        self.assertEqual(a1["shortlisted"], 2)
        self._ask()  # both skipped: the numbers must describe THIS question, not a total
        a2 = self.client.get("/api/audit/messages", headers=H).json()["funnel_stats"]["latest_query"]
        self.assertEqual((a2["asked"], a2["not_asked"]), (0, 2))

    def test_audit_header_is_empty_before_any_question(self) -> None:
        stats = self.client.get("/api/audit/messages", headers=H).json()["funnel_stats"]
        self.assertIsNone(stats["latest_query"])
        self.assertEqual(stats["registered_agents_count"], 2)


@unittest.skipUnless(HAS_FASTAPI, "FastAPI TestClient is not available")
class TestEmbeddingFailure(unittest.TestCase):
    def test_failure_is_503_without_orphan_query_or_internal_detail(self) -> None:
        store, service, _ = _build(embedder=FailingEmbedder())
        client = TestClient(create_app(store=store, service=service, api_key=KEY))
        res = client.post("/api/query", json={"requester_id": "emp_jordan", "question_text": QUESTION}, headers=H)
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("secret-detail", res.text)
        self.assertEqual(store.list_messages(), [])
        self.assertEqual(client.get("/api/requester/emp_jordan/status", headers=H).json()["statuses"], [])


class TestCostOfOneQuestion(unittest.TestCase):
    """The README's cost sentence is derived from these measured numbers."""

    def test_one_embedding_call_and_at_most_one_model_call_per_agent(self) -> None:
        embedder = CountingEmbedder()
        store, service, inferencer = _build(names=("marcus", "rachel", "elena", "tom"), embedder=embedder)
        # profile embeddings are precomputed in the seed data; do the same here
        for p in store.list_profiles():
            service.matching_engine.compute_profile_embedding(p)
            store.save_profile(p)
        embedder.calls = 0
        service.submit_query("emp_jordan", QUESTION)
        self.assertEqual(embedder.calls, 1)
        self.assertLessEqual(len(inferencer.call_history), 4)
        self.assertEqual(len(inferencer.call_history), 4)


if __name__ == "__main__":
    unittest.main()
