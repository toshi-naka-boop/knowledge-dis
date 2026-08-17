"""Unit tests for schemas.py in knowledge_discovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.models import Message
from knowledge_discovery.schemas import SchemaRegistry


class TestSchemaRegistry(unittest.TestCase):
    """Tests for SchemaRegistry validation and fail-closed audit views."""

    def test_registered_types_check(self) -> None:
        self.assertTrue(SchemaRegistry.is_registered_type("query"))
        self.assertTrue(SchemaRegistry.is_registered_type("connect_ask"))
        self.assertTrue(SchemaRegistry.is_registered_type("connect_ask_private"))
        self.assertTrue(SchemaRegistry.is_registered_type("no_connection"))
        self.assertTrue(SchemaRegistry.is_registered_type("consent_reply"))
        self.assertTrue(SchemaRegistry.is_registered_type("match_proposal"))
        self.assertTrue(SchemaRegistry.is_registered_type("decline_with_reason"))
        self.assertTrue(SchemaRegistry.is_registered_type("reject_unregistered_type"))
        self.assertTrue(SchemaRegistry.is_registered_type("reject_unsupported_intent"))
        self.assertFalse(SchemaRegistry.is_registered_type("unknown_custom_type"))

    def test_query_validation(self) -> None:
        valid, err = SchemaRegistry.validate_payload("query", {"question_text": "生産管理について", "requester_id": "u1"})
        self.assertTrue(valid)
        self.assertIsNone(err)

        invalid, err = SchemaRegistry.validate_payload("query", {"question_text": ""})
        self.assertFalse(invalid)
        self.assertIsNotNone(err)

    def test_connect_ask_validation(self) -> None:
        payload = {
            "question_summary": "生産管理の改善",
            "reason_text": "SE経験あり",
            "cited_item_keys": ["current_work"],
            "score": 0.88,
        }
        valid, _ = SchemaRegistry.validate_payload("connect_ask", payload)
        self.assertTrue(valid)

        valid_priv, _ = SchemaRegistry.validate_payload("connect_ask_private", payload)
        self.assertTrue(valid_priv)

        invalid, err = SchemaRegistry.validate_payload("connect_ask", {"reason_text": "理由のみ"})
        self.assertFalse(invalid)

    def test_no_connection_validation(self) -> None:
        payload = {
            "reason_text": "接点なし",
            "cited_item_keys": ["current_work"],
            "score": 0.1,
        }
        valid, _ = SchemaRegistry.validate_payload("no_connection", payload)
        self.assertTrue(valid)

    def test_consent_reply_validation(self) -> None:
        valid_granted, _ = SchemaRegistry.validate_payload("consent_reply", {"decision": "granted", "ask_audit_id": "m1"})
        self.assertTrue(valid_granted)

        valid_declined, _ = SchemaRegistry.validate_payload("consent_reply", {"decision": "declined", "ask_audit_id": "m1"})
        self.assertTrue(valid_declined)

        invalid_decision, err = SchemaRegistry.validate_payload("consent_reply", {"decision": "maybe", "ask_audit_id": "m1"})
        self.assertFalse(invalid_decision)

    def test_match_proposal_validation_and_no_reason_text_rule(self) -> None:
        # Valid proposal
        valid_payload = {
            "meeting_duration": 15,
            "proposed_by": "system",
            "participants": ["u1", "u2"],
        }
        valid, err = SchemaRegistry.validate_payload("match_proposal", valid_payload)
        self.assertTrue(valid)

        # C-19: reason_text is strictly prohibited in match_proposal
        invalid_with_reason = {
            "meeting_duration": 15,
            "proposed_by": "system",
            "participants": ["u1", "u2"],
            "reason_text": "This should not be here!",
        }
        invalid, err = SchemaRegistry.validate_payload("match_proposal", invalid_with_reason)
        self.assertFalse(invalid)
        self.assertIn("reason_text", err or "")

    def test_decline_with_reason_validation(self) -> None:
        payload = {
            "reason_text": "現在は繁忙期のため難しいです",
            "attachment": {"type": "link", "content": "https://example.com/doc"},
        }
        valid, _ = SchemaRegistry.validate_payload("decline_with_reason", payload)
        self.assertTrue(valid)

        doc_payload = {
            "reason_text": "資料を参照ください",
            "attachment": {"type": "doc", "content": "doc_arch_99"},
        }
        valid_doc, _ = SchemaRegistry.validate_payload("decline_with_reason", doc_payload)
        self.assertTrue(valid_doc)

        invalid_att_type = {
            "reason_text": "理由",
            "attachment": {"type": "unknown_file", "content": "data"},
        }
        invalid, _ = SchemaRegistry.validate_payload("decline_with_reason", invalid_att_type)
        self.assertFalse(invalid)

    def test_fail_closed_audit_view(self) -> None:
        # 1. When audit_payload is present, it is returned
        msg_with_mask = Message(
            audit_id="m1",
            from_entity="system",
            to_entity="a1",
            intent="connect_ask_private",
            payload_type="connect_ask_private",
            payload={"reason_text": "秘密情報に基づく知見", "cited_item_keys": ["private_k"], "score": 0.9},
            audit_payload={"masked": True, "note": "非公開項目に基づくconnect_ask_private（内容非表示）"},
        )
        audit_view = SchemaRegistry.get_audit_view(msg_with_mask)
        self.assertTrue(audit_view.get("masked"))
        self.assertIn("connect_ask_private", audit_view.get("note", ""))

        # 2. When audit_payload is None and payload_type is in whitelist, payload is returned
        msg_whitelisted = Message(
            audit_id="m2",
            from_entity="system",
            to_entity="a1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={"reason_text": "公開情報に基づく知見", "cited_item_keys": ["public_k"], "score": 0.8},
            audit_payload=None,
        )
        view_whitelisted = SchemaRegistry.get_audit_view(msg_whitelisted)
        self.assertEqual(view_whitelisted["reason_text"], "公開情報に基づく知見")

        # 3. When audit_payload is None and payload_type is NOT in whitelist (e.g. connect_ask_private or unknown type)
        # fail-closed fallback returns masked view
        msg_unwhitelisted = Message(
            audit_id="m3",
            from_entity="system",
            to_entity="a1",
            intent="connect_ask_private",
            payload_type="connect_ask_private",
            payload={"reason_text": "マスク漏れの機密情報"},
            audit_payload=None,
        )
        view_fail_closed = SchemaRegistry.get_audit_view(msg_unwhitelisted)
        self.assertTrue(view_fail_closed.get("masked"))
        self.assertEqual(view_fail_closed.get("note"), "表示不可（マスク既定）")


if __name__ == "__main__":
    unittest.main()
