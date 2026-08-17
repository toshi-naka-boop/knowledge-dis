"""Unit tests for service.py in knowledge_discovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.matching import (
    DeterministicEmbedder,
    FakeConnectionInferencer,
    MatchingEngine,
)
from knowledge_discovery.models import (
    Agent,
    Attachment,
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
    ProfileItem,
)
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore
from knowledge_discovery.transmission import TransmissionLayer


class TestService(unittest.TestCase):
    """Tests for end-to-end KnowledgeDiscoveryService coordinator."""

    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.transmission = TransmissionLayer(self.store)
        self.embedder = DeterministicEmbedder()
        self.inferencer = FakeConnectionInferencer()
        self.matching_engine = MatchingEngine(
            embedder=self.embedder,
            inferencer=self.inferencer,
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
        )
        self.service = KnowledgeDiscoveryService(
            store=self.store,
            transmission=self.transmission,
            matching_engine=self.matching_engine,
        )

        # Setup standard 4 agents and profiles
        self.agent1 = Agent(
            agent_id="agent_01",
            employee_id="emp_01",
            display_name="Alice Tanaka",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile1 = Profile(
            employee_id="emp_01",
            name="Alice Tanaka",
            role="Production Engineer",
            items=[ProfileItem(key="current_work", body="生産管理システムの設計・導入", visibility="public", reviewed=True)],
        )

        self.agent2 = Agent(
            agent_id="agent_02",
            employee_id="emp_02",
            display_name="Bob Sato",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile2 = Profile(
            employee_id="emp_02",
            name="Bob Sato",
            role="Systems Architect",
            items=[
                ProfileItem(key="current_work", body="クラウド基盤設計", visibility="public", reviewed=True),
                ProfileItem(key="secret_experience", body="製造業の生産管理支援", visibility="private", reviewed=True),
            ],
        )

        self.agent3 = Agent(
            agent_id="agent_03",
            employee_id="emp_03",
            display_name="Charlie Suzuki",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile3 = Profile(
            employee_id="emp_03",
            name="Charlie Suzuki",
            role="Quality Assurance",
            items=[ProfileItem(key="current_work", body="生産ラインの品質検査と管理", visibility="public", reviewed=True)],
        )

        # Agent 4: Accounting (intentionally no overlap to be dropped)
        self.agent4 = Agent(
            agent_id="agent_04",
            employee_id="emp_04",
            display_name="David Takahashi",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile4 = Profile(
            employee_id="emp_04",
            name="David Takahashi",
            role="Accountant",
            items=[ProfileItem(key="current_work", body="決算財務諸表作成および税務申告", visibility="public", reviewed=True)],
        )

        for a in [self.agent1, self.agent2, self.agent3, self.agent4]:
            self.store.save_agent(a)
        for p in [self.profile1, self.profile2, self.profile3, self.profile4]:
            self.store.save_profile(p)

    def test_query_submission_and_ask_dispatch(self) -> None:
        # Override Bob's inference to cite his private item
        self.inferencer.set_override(
            "emp_02",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="非公開の製造業支援経験あり", score=0.88),
                cited_item_keys=["secret_experience"],
            ),
        )

        res = self.service.submit_query(requester_id="user_requester", question_text="製造業の生産管理の知見")

        # 3 qualified (Alice, Bob, Charlie) and 1 dropped (David)
        self.assertEqual(len(res.dispatched_asks), 3)
        self.assertEqual(len(res.dropped_candidates), 1)
        self.assertEqual(res.dropped_candidates[0].agent.agent_id, "agent_04")

        # Alice: connect_ask (public)
        alice_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_01"][0]
        self.assertEqual(alice_ask.intent, "connect_ask")
        self.assertIsNone(alice_ask.audit_payload)

        # Bob: connect_ask_private (private mask applied mechanically)
        bob_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_02"][0]
        self.assertEqual(bob_ask.intent, "connect_ask_private")
        self.assertIsNotNone(bob_ask.audit_payload)
        self.assertTrue(bob_ask.audit_payload["masked"])

        # Check dropped message in store
        no_conn_msgs = [m for m in self.store.list_messages() if m.intent == "no_connection"]
        self.assertEqual(len(no_conn_msgs), 1)
        self.assertEqual(no_conn_msgs[0].to_entity, "agent_04")

    def test_consent_flow_granted(self) -> None:
        res = self.service.submit_query(requester_id="user_requester", question_text="製造業の生産管理")
        alice_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_01"][0]

        consent_res = self.service.respond_consent(
            candidate_entity_id="agent_01",
            ask_audit_id=alice_ask.audit_id,
            decision="granted",
        )

        self.assertEqual(consent_res.reply_message.intent, "consent_reply")
        self.assertEqual(consent_res.outcome_message.intent, "match_proposal")
        self.assertEqual(consent_res.outcome_message.payload["meeting_duration"], 15)
        self.assertNotIn("reason_text", consent_res.outcome_message.payload)
        self.assertIn("user_requester", consent_res.outcome_message.payload["participants"])

        # Check requester status
        statuses = self.service.get_requester_status("user_requester")
        alice_status = [s for s in statuses if s.candidate_id == "emp_01"][0]
        self.assertEqual(alice_status.state, "matched")
        self.assertEqual(alice_status.meeting_duration, 15)

    def test_consent_flow_declined_with_attachments(self) -> None:
        res = self.service.submit_query(requester_id="user_requester", question_text="製造業の生産管理")
        alice_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_01"][0]

        attachment = Attachment(type="doc", content="doc_manufacturing_guide_01")
        consent_res = self.service.respond_consent(
            candidate_entity_id="agent_01",
            ask_audit_id=alice_ask.audit_id,
            decision="declined",
            reason_text="現在案件対応中のため、こちらの資料をご参照ください",
            attachment=attachment,
        )

        self.assertEqual(consent_res.reply_message.intent, "consent_reply")
        self.assertEqual(consent_res.outcome_message.intent, "decline_with_reason")
        self.assertEqual(consent_res.outcome_message.payload["reason_text"], "現在案件対応中のため、こちらの資料をご参照ください")
        self.assertEqual(consent_res.outcome_message.payload["attachment"]["type"], "doc")

        # Check requester status
        statuses = self.service.get_requester_status("user_requester")
        alice_status = [s for s in statuses if s.candidate_id == "emp_01"][0]
        self.assertEqual(alice_status.state, "declined")
        self.assertEqual(alice_status.decline_reason, "現在案件対応中のため、こちらの資料をご参照ください")
        self.assertEqual(alice_status.attachment["content"], "doc_manufacturing_guide_01")

    def test_audit_dashboard_records(self) -> None:
        self.inferencer.set_override(
            "emp_02",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="非公開情報", score=0.9),
                cited_item_keys=["secret_experience"],
            ),
        )
        self.service.submit_query(requester_id="u1", question_text="生産管理")

        records = self.service.get_audit_dashboard_records()
        self.assertGreaterEqual(len(records), 5)  # query + 3 asks + 1 no_connection

        # Verify fail-closed masked view on Bob's private ask
        bob_record = [r for r in records if r["to"] == "agent_02" and r["intent"] == "connect_ask_private"][0]
        self.assertTrue(bob_record["display_payload"]["masked"])
        self.assertIn("connect_ask_private", bob_record["display_payload"]["note"])


if __name__ == "__main__":
    unittest.main()
