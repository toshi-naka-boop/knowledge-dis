"""Unit tests for models.py in knowledge_discovery."""

import os
import sys
import unittest

# Ensure src is on Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.models import (
    Agent,
    Attachment,
    ConnectionDetails,
    ConnectionInferenceResult,
    FunnelCandidate,
    Message,
    Profile,
    ProfileItem,
    RequesterCandidateStatus,
)


class TestModels(unittest.TestCase):
    """Tests for core data models."""

    def test_agent_serialization(self) -> None:
        agent = Agent(
            agent_id="agent_001",
            employee_id="emp_001",
            display_name="Alice Tanaka",
            supported_intents=["connect_ask", "connect_ask_private"],
            endpoint="local_adk",
            active=True,
        )
        data = agent.to_dict()
        self.assertEqual(data["agent_id"], "agent_001")
        self.assertEqual(data["employee_id"], "emp_001")
        self.assertEqual(data["display_name"], "Alice Tanaka")
        self.assertEqual(data["supported_intents"], ["connect_ask", "connect_ask_private"])
        self.assertTrue(data["active"])

        restored = Agent.from_dict(data)
        self.assertEqual(restored.agent_id, agent.agent_id)
        self.assertEqual(restored.employee_id, agent.employee_id)
        self.assertEqual(restored.display_name, agent.display_name)
        self.assertEqual(restored.supported_intents, agent.supported_intents)
        self.assertEqual(restored.active, agent.active)

    def test_profile_and_items(self) -> None:
        item_public = ProfileItem(
            key="current_work",
            body="生産管理システムの設計・開発",
            source="job_doc",
            visibility="public",
            reviewed=True,
        )
        item_private = ProfileItem(
            key="background",
            body="前職での製造業現場改善プロジェクト",
            source="seed_synth",
            visibility="private",
            reviewed=True,
        )
        profile = Profile(
            employee_id="emp_001",
            name="Alice Tanaka",
            role="Production Engineer",
            items=[item_public, item_private],
            embedding=[0.1, 0.2, 0.3],
        )

        self.assertFalse(profile.is_item_private("current_work"))
        self.assertTrue(profile.is_item_private("background"))
        self.assertFalse(profile.is_item_private("non_existent_key"))

        self.assertTrue(profile.has_any_private(["current_work", "background"]))
        self.assertFalse(profile.has_any_private(["current_work"]))

        data = profile.to_dict()
        self.assertEqual(len(data["items"]), 2)
        restored = Profile.from_dict(data)
        self.assertEqual(restored.employee_id, "emp_001")
        self.assertEqual(len(restored.items), 2)
        self.assertTrue(restored.is_item_private("background"))

    def test_profile_full_text(self) -> None:
        item1 = ProfileItem(key="expertise", body="Python and Cloud Run", visibility="public")
        item2 = ProfileItem(key="secret_project", body="Next-gen AI prototype", visibility="private")
        profile = Profile(
            employee_id="emp_002",
            name="Bob Sato",
            role="Tech Lead",
            items=[item1, item2],
        )
        full_text = profile.get_full_text()
        self.assertIn("Bob Sato - Tech Lead", full_text)
        self.assertIn("expertise: Python and Cloud Run", full_text)
        self.assertIn("secret_project: Next-gen AI prototype", full_text)

    def test_attachment_serialization(self) -> None:
        att = Attachment(type="doc", content="doc_12345")
        data = att.to_dict()
        self.assertEqual(data["type"], "doc")
        self.assertEqual(data["content"], "doc_12345")

        restored = Attachment.from_dict(data)
        self.assertEqual(restored.type, "doc")
        self.assertEqual(restored.content, "doc_12345")

    def test_message_serialization(self) -> None:
        msg = Message(
            audit_id="msg_001",
            from_entity="system",
            to_entity="agent_001",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={"reason_text": "生産管理の知見", "cited_item_keys": ["current_work"], "score": 0.8},
            audit_payload={"masked": True, "note": "非公開項目に基づくconnect_ask_private（内容非表示）"},
            consent_state="pending",
            rejected=False,
        )
        data = msg.to_dict()
        self.assertEqual(data["audit_id"], "msg_001")
        self.assertEqual(data["from"], "system")
        self.assertEqual(data["to"], "agent_001")
        self.assertEqual(data["intent"], "connect_ask")
        self.assertEqual(data["payload_type"], "connect_ask")
        self.assertIsNotNone(data["audit_payload"])
        self.assertEqual(data["consent_state"], "pending")
        self.assertFalse(data["rejected"])

        restored = Message.from_dict(data)
        self.assertEqual(restored.audit_id, "msg_001")
        self.assertEqual(restored.from_entity, "system")
        self.assertEqual(restored.to_entity, "agent_001")
        self.assertEqual(restored.intent, "connect_ask")
        self.assertIsNotNone(restored.audit_payload)
        self.assertEqual(restored.audit_payload["masked"], True)


if __name__ == "__main__":
    unittest.main()
