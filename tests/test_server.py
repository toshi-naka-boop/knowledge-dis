"""Unit tests for FastAPI server and endpoint projection privacy rules.

Verifies:
- API key authentication (X-API-Key).
- POST /api/query: matching execution and ask dispatch.
- GET /api/requester/{requester_id}/status: Completion Condition 3 (Strict isolation:
  no candidate employee_id while pending, no distinction of private/public asks,
  only reveals respondent_id upon completed match/decline).
- GET /api/candidate/{agent_id}/asks and POST /api/candidate/{agent_id}/consent.
- GET /api/audit/messages: fail-closed masked display payloads and funnel stats.
- GET /attachments/{id}: static document serving.
- Web UI HTML endpoints.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.matching import FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import (
    Agent,
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
    ProfileItem,
)
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore

# Graceful handling if fastapi / starlette test client is not installed.
# server.py itself imports fastapi, so its import must also be guarded here.
try:
    from fastapi.testclient import TestClient

    from knowledge_discovery.server import create_app
    HAS_FASTAPI_TESTCLIENT = True
except ImportError:
    HAS_FASTAPI_TESTCLIENT = False
    TestClient = None  # type: ignore[assignment,misc]
    create_app = None  # type: ignore[assignment]


class TestServerEndpoints(unittest.TestCase):
    """Tests for API routes and privacy rules in server.py."""

    def setUp(self) -> None:
        if not HAS_FASTAPI_TESTCLIENT:
            self.skipTest("FastAPI TestClient is not available in this environment.")

        self.store = InMemoryStore()
        self.inferencer = FakeConnectionInferencer()
        self.matching_engine = MatchingEngine(
            inferencer=self.inferencer,
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )
        self.service = KnowledgeDiscoveryService(
            store=self.store,
            matching_engine=self.matching_engine,
        )

        # Setup standard seed personas
        self.agent_marcus = Agent(
            agent_id="agent_marcus_delgado",
            employee_id="emp_marcus_delgado",
            display_name="Marcus Delgado",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_marcus = Profile(
            employee_id="emp_marcus_delgado",
            name="Marcus Delgado",
            role="Commercial Broker, Healthcare Real Estate",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Brokers medical office buildings and tracks zoning, ADA and clinic relocation requirements.",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        self.agent_rachel = Agent(
            agent_id="agent_rachel_kim",
            employee_id="emp_rachel_kim",
            display_name="Rachel Kim",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_rachel = Profile(
            employee_id="emp_rachel_kim",
            name="Rachel Kim",
            role="Senior Account Manager, Healthcare Staffing",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Manages staffing contracts for hospital clients and handles relocations.",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        self.agent_elena = Agent(
            agent_id="agent_elena_vasquez",
            employee_id="emp_elena_vasquez",
            display_name="Elena Vasquez",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_elena = Profile(
            employee_id="emp_elena_vasquez",
            name="Elena Vasquez",
            role="Transition Advisor, Practice Transition",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Advises independent practices on succession planning.",
                    visibility="public",
                    reviewed=True,
                ),
                ProfileItem(
                    key="transition_pipeline",
                    body="Secret unannounced clinic relocation and sale deals under NDA.",
                    visibility="private",
                    reviewed=True,
                ),
            ],
        )

        self.agent_tom = Agent(
            agent_id="agent_tom_whitfield",
            employee_id="emp_tom_whitfield",
            display_name="Tom Whitfield",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_tom = Profile(
            employee_id="emp_tom_whitfield",
            name="Tom Whitfield",
            role="Senior Accountant, Corporate Services",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Prepares consolidated monthly financial closes and audits.",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        for a in (self.agent_marcus, self.agent_rachel, self.agent_elena, self.agent_tom):
            self.store.save_agent(a)
        for p in (self.profile_marcus, self.profile_rachel, self.profile_elena, self.profile_tom):
            self.store.save_profile(p)

        self.api_key = "test-secret-key-123"
        self.app = create_app(store=self.store, service=self.service, api_key=self.api_key)
        self.client = TestClient(self.app)
        self.auth_headers = {"X-API-Key": self.api_key}

    def test_api_key_authentication(self) -> None:
        """Verify API key enforcement on protected endpoints."""
        # Missing API key -> 401
        res_no_key = self.client.post("/api/query", json={"requester_id": "u1", "question_text": "hello"})
        self.assertEqual(res_no_key.status_code, 401)

        # Invalid API key -> 401
        res_bad_key = self.client.post(
            "/api/query",
            headers={"X-API-Key": "wrong-key"},
            json={"requester_id": "u1", "question_text": "hello"},
        )
        self.assertEqual(res_bad_key.status_code, 401)

        # Valid API key -> 200
        res_ok = self.client.post(
            "/api/query",
            headers=self.auth_headers,
            json={"requester_id": "u1", "question_text": "healthcare zoning"},
        )
        self.assertEqual(res_ok.status_code, 200)

    def test_query_submission_and_funnel_counts(self) -> None:
        """Verify POST /api/query returns funnel candidates and ask count."""
        res = self.client.post(
            "/api/query",
            headers=self.auth_headers,
            json={
                "requester_id": "emp_jordan_lee",
                "question_text": "Looking for medical facility zoning and clinic site acquisition expertise.",
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertIn("query_id", data)
        self.assertEqual(data["requester_id"], "emp_jordan_lee")
        self.assertGreater(data["dispatched_count"], 0)
        self.assertLessEqual(data["dispatched_count"], 3)
        self.assertIn("funnel_candidates", data)

    def test_requester_status_projection_privacy_rules(self) -> None:
        """Completion Condition 3:

        Verify that GET /api/requester/{requester_id}/status:
        1. When pending: does NOT leak candidate employee_id, agent_id, or name.
        2. Does NOT leak whether ask was based on private profile items (connect_ask_private).
        3. Does NOT leak raw consent granted/declined logs.
        4. ONLY reveals the respondent employee_id and name when the lane is completed (matched/declined).
        """
        # Override Elena's inferencer to cite her private item
        self.inferencer.set_override(
            "emp_elena_vasquez",
            ConnectionInferenceResult(
                connection=ConnectionDetails(
                    reason_text="Has confidential clinic succession & relocation pipeline",
                    score=0.90,
                ),
                cited_item_keys=["transition_pipeline"],  # Private item!
            ),
        )

        # 1. Requester submits query
        q_res = self.client.post(
            "/api/query",
            headers=self.auth_headers,
            json={
                "requester_id": "emp_jordan_lee",
                "question_text": "Need expertise on medical clinic relocation.",
            },
        )
        self.assertEqual(q_res.status_code, 200)

        # 2. Check initial pending status from requester view
        status_res = self.client.get(
            "/api/requester/emp_jordan_lee/status",
            headers=self.auth_headers,
        )
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        statuses = status_data["statuses"]
        self.assertGreater(len(statuses), 0)

        # PRIVACY VERIFICATION WHILE PENDING:
        for s in statuses:
            self.assertEqual(s["state"], "pending")
            self.assertEqual(s["display_state"], "Waiting for response")
            # Must NOT leak candidate employee_id or candidate real ID while pending
            self.assertNotIn("candidate_id", s)
            self.assertNotIn("employee_id", s)
            self.assertNotIn("respondent_id", s)
            self.assertNotIn("emp_marcus_delgado", str(s))
            self.assertNotIn("emp_elena_vasquez", str(s))
            self.assertNotIn("emp_rachel_kim", str(s))
            self.assertNotIn("emp_tom_whitfield", str(s))
            # Must NOT leak private item distinction
            self.assertNotIn("transition_pipeline", str(s))
            self.assertNotIn("connect_ask_private", str(s))

        # 3. Candidate 1 (Marcus) grants consent
        marcus_asks_res = self.client.get(
            "/api/candidate/agent_marcus_delgado/asks",
            headers=self.auth_headers,
        )
        marcus_asks = marcus_asks_res.json()["asks"]
        self.assertGreater(len(marcus_asks), 0)
        marcus_ask_id = marcus_asks[0]["ask_audit_id"]

        consent_res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            headers=self.auth_headers,
            json={
                "ask_audit_id": marcus_ask_id,
                "decision": "granted",
            },
        )
        self.assertEqual(consent_res.status_code, 200)

        # 4. Check requester status after Marcus consents (Lane Completed -> Matched)
        status_res2 = self.client.get(
            "/api/requester/emp_jordan_lee/status",
            headers=self.auth_headers,
        )
        statuses2 = status_res2.json()["statuses"]
        matched_items = [s for s in statuses2 if s["state"] == "matched"]
        self.assertEqual(len(matched_items), 1)
        matched_s = matched_items[0]

        # In completed state (matched): ONLY the resolved respondent's ID and name are revealed
        self.assertEqual(matched_s["respondent_id"], "emp_marcus_delgado")
        self.assertEqual(matched_s["respondent_name"], "Marcus Delgado")
        self.assertEqual(matched_s["meeting_duration"], 15)

        # 5. Candidate 2 (Rachel) declines with doc attachment
        rachel_asks_res = self.client.get(
            "/api/candidate/agent_rachel_kim/asks",
            headers=self.auth_headers,
        )
        rachel_asks = rachel_asks_res.json()["asks"]
        self.assertGreater(len(rachel_asks), 0)
        rachel_ask_id = rachel_asks[0]["ask_audit_id"]

        decline_res = self.client.post(
            "/api/candidate/agent_rachel_kim/consent",
            headers=self.auth_headers,
            json={
                "ask_audit_id": rachel_ask_id,
                "decision": "declined",
                "reason_text": "Currently focused on staffing escalations, sharing clinic relocation guide.",
                "attachment": {
                    "type": "doc",
                    "content": "doc_clinic_relocation_guide",
                },
            },
        )
        self.assertEqual(decline_res.status_code, 200)

        # 6. Check requester status after Rachel declines (Lane Completed -> Declined)
        status_res3 = self.client.get(
            "/api/requester/emp_jordan_lee/status",
            headers=self.auth_headers,
        )
        statuses3 = status_res3.json()["statuses"]
        declined_items = [s for s in statuses3 if s["state"] == "declined"]
        self.assertEqual(len(declined_items), 1)
        declined_s = declined_items[0]

        # In completed state (declined): ONLY the resolved respondent's ID, reason, and attachment are revealed
        self.assertEqual(declined_s["respondent_id"], "emp_rachel_kim")
        self.assertEqual(declined_s["respondent_name"], "Rachel Kim")
        self.assertIn("staffing escalations", declined_s["decline_reason"])
        self.assertEqual(declined_s["attachment"]["content"], "doc_clinic_relocation_guide")

    def test_candidate_private_ask_badge(self) -> None:
        """Verify Elena receives private ask indicator when private item is cited."""
        self.inferencer.set_override(
            "emp_elena_vasquez",
            ConnectionInferenceResult(
                connection=ConnectionDetails(
                    reason_text="Confidential succession knowledge",
                    score=0.95,
                ),
                cited_item_keys=["transition_pipeline"],
            ),
        )

        self.client.post(
            "/api/query",
            headers=self.auth_headers,
            json={
                "requester_id": "emp_jordan_lee",
                "question_text": "Practice succession inquiry",
            },
        )

        elena_res = self.client.get(
            "/api/candidate/agent_elena_vasquez/asks",
            headers=self.auth_headers,
        )
        self.assertEqual(elena_res.status_code, 200)
        asks = elena_res.json()["asks"]
        self.assertGreater(len(asks), 0)
        elena_ask = asks[0]
        self.assertTrue(elena_ask["is_private"])
        self.assertIn("🔒", elena_ask["private_notice"])

    def test_audit_messages_and_funnel_stats(self) -> None:
        """Verify GET /api/audit/messages returns fail-closed masked view and funnel stats."""
        self.inferencer.set_override(
            "emp_elena_vasquez",
            ConnectionInferenceResult(
                connection=ConnectionDetails(
                    reason_text="Confidential succession knowledge",
                    score=0.95,
                ),
                cited_item_keys=["transition_pipeline"],
            ),
        )

        self.client.post(
            "/api/query",
            headers=self.auth_headers,
            json={
                "requester_id": "emp_jordan_lee",
                "question_text": "Practice succession inquiry",
            },
        )

        res = self.client.get(
            "/api/audit/messages",
            headers=self.auth_headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertIn("funnel_stats", data)
        self.assertIn("records", data)
        self.assertEqual(data["funnel_stats"]["funnel_limit"], 20)

        # Check that Elena's private ask is masked in audit view
        records = data["records"]
        elena_records = [r for r in records if r["to"] == "agent_elena_vasquez" and r["intent"] == "connect_ask_private"]
        self.assertEqual(len(elena_records), 1)
        self.assertTrue(elena_records[0]["display_payload"]["masked"])
        self.assertIn("connect_ask_private", elena_records[0]["display_payload"]["note"])

    def test_static_attachments(self) -> None:
        """Verify static document delivery endpoint /attachments/{id}."""
        res_doc = self.client.get("/attachments/doc_clinic_relocation_guide")
        self.assertEqual(res_doc.status_code, 200)
        self.assertIn("Zoning & Permitting", res_doc.text)

        res_404 = self.client.get("/attachments/non_existent_doc_id")
        self.assertEqual(res_404.status_code, 404)

    def test_web_ui_routes_and_elements(self) -> None:
        """Verify web UI HTML endpoints and presence of required visual elements."""
        res_req = self.client.get("/requester")
        self.assertEqual(res_req.status_code, 200)
        self.assertIn("My Agent", res_req.text)
        self.assertIn("Find someone who can help", res_req.text)

        res_cand = self.client.get("/candidate")
        self.assertEqual(res_cand.status_code, 200)
        self.assertIn("Your agent screened this request and thinks your experience is relevant.", res_cand.text)
        self.assertIn("🔒 Relates to something only your agent knows about you", res_cand.text)

        res_audit = self.client.get("/audit")
        self.assertEqual(res_audit.status_code, 200)
        self.assertIn("trace-timeline", res_audit.text)
        self.assertIn("policy-blocked", res_audit.text)
        self.assertIn("trace-funnel", res_audit.text)
        self.assertIn("🔒 based on a private item — content masked", res_audit.text)


if __name__ == "__main__":
    unittest.main()
