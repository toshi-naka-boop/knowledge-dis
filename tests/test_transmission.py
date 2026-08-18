"""Unit tests for transmission.py in knowledge_discovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.models import Agent, Profile, ProfileItem
from knowledge_discovery.store import InMemoryStore
from knowledge_discovery.transmission import TransmissionLayer


class TestTransmission(unittest.TestCase):
    """Tests for TransmissionLayer validation, rejection paths, and private mask rule."""

    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.transmission = TransmissionLayer(self.store)

        # Setup an agent with specific supported intents
        self.agent1 = Agent(
            agent_id="agent_1",
            employee_id="emp_1",
            display_name="Alice Tanaka",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.store.save_agent(self.agent1)

        # Setup profile with public and private items
        self.profile1 = Profile(
            employee_id="emp_1",
            name="Alice Tanaka",
            role="Production Engineer",
            items=[
                ProfileItem(key="current_work", body="生産管理", visibility="public", reviewed=True),
                ProfileItem(key="past_secret", body="非公開ノウハウ", visibility="private", reviewed=True),
            ],
        )
        self.store.save_profile(self.profile1)

    def test_normal_dispatch(self) -> None:
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={
                "question_summary": "生産管理について",
                "reason_text": "生産管理の知見あり",
                "cited_item_keys": ["current_work"],
                "score": 0.85,
            },
        )
        self.assertEqual(msg.intent, "connect_ask")
        self.assertEqual(msg.payload_type, "connect_ask")
        self.assertFalse(msg.rejected)
        self.assertIsNone(msg.audit_payload)

        # Verify saved in store
        saved = self.store.get_message(msg.audit_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.intent, "connect_ask")

    def test_reject_unregistered_payload_type(self) -> None:
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="custom_intent",
            payload_type="completely_invalid_type",
            payload={"arbitrary": "data"},
        )
        self.assertTrue(msg.rejected)
        self.assertEqual(msg.intent, "reject_unregistered_type")
        self.assertEqual(msg.payload_type, "reject_unregistered_type")
        self.assertIn("completely_invalid_type", msg.payload.get("raw_payload_type", ""))

        # Verify recorded in store audit trail
        saved = self.store.get_message(msg.audit_id)
        self.assertIsNotNone(saved)
        self.assertTrue(saved.rejected)

    def test_reject_unsupported_intent(self) -> None:
        # Send an intent not present in agent1.supported_intents (e.g. 'arbitrary_ping')
        # We need a registered payload_type or query to pass schema check first
        # Let's say intent="query" sent to agent_1 (agent_1 only supports connect_ask, connect_ask_private, no_connection)
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="query",
            payload_type="query",
            payload={"question_text": "質問文", "requester_id": "u1"},
        )
        self.assertTrue(msg.rejected)
        self.assertEqual(msg.intent, "reject_unsupported_intent")
        self.assertEqual(msg.payload_type, "reject_unsupported_intent")
        self.assertEqual(msg.payload.get("target_agent_id"), "agent_1")
        self.assertEqual(msg.payload.get("attempted_intent"), "query")

        # Verify recorded in store audit trail
        saved = self.store.get_message(msg.audit_id)
        self.assertIsNotNone(saved)
        self.assertTrue(saved.rejected)

    def test_private_mask_rule_on_connect_ask(self) -> None:
        # C-18: When cited_item_keys contains private item, mechanically convert to connect_ask_private
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={
                "question_summary": "非公開技術の質問",
                "reason_text": "非公開ノウハウに合致",
                "cited_item_keys": ["past_secret"],  # Private item
                "score": 0.92,
            },
            candidate_profile=self.profile1,
        )
        self.assertEqual(msg.intent, "connect_ask_private")
        self.assertEqual(msg.payload_type, "connect_ask_private")
        self.assertFalse(msg.rejected)
        # Message payload itself is preserved for candidate view
        self.assertEqual(msg.payload["reason_text"], "非公開ノウハウに合致")
        # Audit payload is generated for masked audit view
        self.assertIsNotNone(msg.audit_payload)
        self.assertTrue(msg.audit_payload.get("masked"))
        self.assertIn("connect_ask_private", msg.audit_payload.get("note", ""))
        self.assertEqual(msg.audit_payload.get("score"), 0.92)
        self.assertEqual(msg.audit_payload.get("cited_count"), 1)

    def test_private_mask_rule_on_no_connection(self) -> None:
        # C-21: When no_connection cites a private item, audit_payload is also generated with mask
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="no_connection",
            payload_type="no_connection",
            payload={
                "reason_text": "非公開ノウハウとの接点不十分",
                "cited_item_keys": ["past_secret"],  # Private item
                "score": 0.15,
            },
            candidate_profile=self.profile1,
        )
        self.assertEqual(msg.intent, "no_connection")
        self.assertFalse(msg.rejected)
        self.assertIsNotNone(msg.audit_payload)
        self.assertTrue(msg.audit_payload.get("masked"))
        self.assertIn("no_connection", msg.audit_payload.get("note", ""))

    def test_mask_on_unknown_cited_key(self) -> None:
        """V-1/S-1: LLM-fabricated keys that don't exist in the profile must fail CLOSED."""
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={
                "question_summary": "q",
                "reason_text": "some public-sounding reason",
                "cited_item_keys": ["nonexistent_key"],
                "score": 0.9,
            },
            consent_state="pending",
            candidate_profile=self.profile1,
        )
        self.assertEqual(msg.intent, "connect_ask_private")
        self.assertIsNotNone(msg.audit_payload)
        self.assertTrue(msg.audit_payload.get("masked"))

    def test_mask_on_private_body_fragment_in_reason(self) -> None:
        """V-1/S-1: private body content quoted in reason_text forces the mask
        even when the LLM cites only public keys."""
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={
                "question_summary": "q",
                "reason_text": "公開業務に加えて非公開ノウハウを持っているため適任です",
                "cited_item_keys": ["current_work"],  # public key only
                "score": 0.9,
            },
            consent_state="pending",
            candidate_profile=self.profile1,
        )
        self.assertEqual(msg.intent, "connect_ask_private")
        self.assertTrue(msg.audit_payload.get("masked"))

    def test_no_mask_on_clean_public_reason(self) -> None:
        """Public citation with clean reason stays unmasked (no false positives)."""
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={
                "question_summary": "q",
                "reason_text": "生産管理の経験が質問に直結しています",
                "cited_item_keys": ["current_work"],
                "score": 0.9,
            },
            consent_state="pending",
            candidate_profile=self.profile1,
        )
        self.assertEqual(msg.intent, "connect_ask")
        self.assertIsNone(msg.audit_payload)


if __name__ == "__main__":
    unittest.main()
