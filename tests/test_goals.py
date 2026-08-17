"""Unit tests validating Milestone 1 verification goals from design.md §10.

Goals tested:
- Goal 1: 4 registered agents run independent Stage 2 inference; only candidates with verbalized connections (max 3) are dispatched.
- Goal 2: Drop candidate with no connection (e.g., accounting profile) via vector floor or Stage 2 null, recorded as no_connection in audit log.
- Goal 4: Private items cited in connect_ask mechanically convert to connect_ask_private, masked in audit view, and requester view does not leak private distinction. Private items cited in no_connection are also masked in audit view (C-21).
- Goal 4b: Intent not supported by destination agent is rejected as reject_unsupported_intent (rejected=True) and recorded in audit log (C-25).
- Goal 5: Granted consent generates match_proposal (without reason_text) for both participants (C-19).
- Goal 6: Declined consent generates decline_with_reason with reason text and link/doc attachment.
- Goal 7: Unregistered payload_type is rejected as reject_unregistered_type (rejected=True) and recorded in audit log.
- Goal 8: 4 registered agents vs 400 employee profiles: unregistered profiles are never dispatched.
"""

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
from knowledge_discovery.schemas import SchemaRegistry
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore
from knowledge_discovery.transmission import TransmissionLayer


class TestMilestone1Goals(unittest.TestCase):
    """Formal verification of Milestone 1 requirements matching design.md §10."""

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
            funnel_limit=20,
        )
        self.service = KnowledgeDiscoveryService(
            store=self.store,
            transmission=self.transmission,
            matching_engine=self.matching_engine,
        )

        # Seed 4 registered agents (3 technical/production, 1 accounting intentionally designed to drop)
        self.agent_alice = Agent(
            agent_id="agent_alice",
            employee_id="emp_alice",
            display_name="Alice Tanaka",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_alice = Profile(
            employee_id="emp_alice",
            name="Alice Tanaka",
            role="Production Engineer",
            items=[
                ProfileItem(
                    key="current_work",
                    body="自動車部品工場の生産管理システム設計およびサプライチェーン改善",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        self.agent_bob = Agent(
            agent_id="agent_bob",
            employee_id="emp_bob",
            display_name="Bob Sato",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_bob = Profile(
            employee_id="emp_bob",
            name="Bob Sato",
            role="Cloud Systems Architect",
            items=[
                ProfileItem(
                    key="current_work",
                    body="社内クラウド基盤の運用とDevOps自動化",
                    visibility="public",
                    reviewed=True,
                ),
                ProfileItem(
                    key="secret_experience",
                    body="前職での製造業向けMES（製造実行システム）導入プロジェクトリーダー",
                    visibility="private",
                    reviewed=True,
                ),
            ],
        )

        self.agent_charlie = Agent(
            agent_id="agent_charlie",
            employee_id="emp_charlie",
            display_name="Charlie Suzuki",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_charlie = Profile(
            employee_id="emp_charlie",
            name="Charlie Suzuki",
            role="Factory Automation Engineer",
            items=[
                ProfileItem(
                    key="current_work",
                    # NOTE: fixture body shares character grams with the goal-1 question
                    # (生産管理/システム/導入) because the deterministic fake embedder
                    # matches at gram level, not semantic level
                    body="工場の生産ライン自動化と生産管理システムのセンサー導入",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        self.agent_david = Agent(
            agent_id="agent_david",
            employee_id="emp_david",
            display_name="David Takahashi",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_david = Profile(
            employee_id="emp_david",
            name="David Takahashi",
            role="Chief Accountant",
            items=[
                ProfileItem(
                    key="current_work",
                    body="全社決算、財務諸表作成、税務申告および会計監査対応",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )

        for a in [self.agent_alice, self.agent_bob, self.agent_charlie, self.agent_david]:
            self.store.save_agent(a)
        for p in [self.profile_alice, self.profile_bob, self.profile_charlie, self.profile_david]:
            self.store.save_profile(p)

    def test_goal_1_independent_inference_and_k_bounded_dispatch(self) -> None:
        """Goal 1: 4 registered agents run independent Stage 2 inference, max k=3 dispatched."""
        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )

        # Confirm that Stage 2 inference was called independently per candidate
        called_employees = [emp_id for q, emp_id in self.inferencer.call_history]
        self.assertIn("emp_alice", called_employees)
        self.assertIn("emp_bob", called_employees)
        self.assertIn("emp_charlie", called_employees)

        # Max k=3 candidates dispatched
        self.assertLessEqual(len(res.dispatched_asks), 3)
        self.assertEqual(len(res.dispatched_asks), 3)

        dispatched_targets = {msg.to_entity for msg in res.dispatched_asks}
        self.assertEqual(dispatched_targets, {"agent_alice", "agent_bob", "agent_charlie"})

    def test_goal_2_no_connection_drop_and_audit_logging(self) -> None:
        """Goal 2: Candidate with no connection (accounting David) dropped with reason in audit log."""
        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )

        # David (accounting) must be dropped
        dropped_agent_ids = {d.agent.agent_id for d in res.dropped_candidates}
        self.assertIn("agent_david", dropped_agent_ids)

        # Check audit log for no_connection message
        audit_messages = self.store.list_messages()
        no_conn_msgs = [m for m in audit_messages if m.intent == "no_connection" and m.to_entity == "agent_david"]
        self.assertEqual(len(no_conn_msgs), 1)
        self.assertFalse(no_conn_msgs[0].rejected)
        self.assertTrue(len(no_conn_msgs[0].payload.get("reason_text", "")) > 0)

    def test_goal_4_private_mask_on_connect_ask_and_no_connection(self) -> None:
        """Goal 4: Private item cited -> connect_ask_private, masked audit view, seamless requester view.

        Also validates that private item cited in no_connection is masked in audit view (C-21).
        """
        # Configure Bob's inferencer result to cite his private item 'secret_experience'
        self.inferencer.set_override(
            "emp_bob",
            ConnectionInferenceResult(
                connection=ConnectionDetails(
                    reason_text="前職での製造業向けMES導入経験が該当します",
                    score=0.88,
                ),
                cited_item_keys=["secret_experience"],  # Private item
            ),
        )

        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )

        # 1. Bob's ask is mechanically connect_ask_private
        bob_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_bob"][0]
        self.assertEqual(bob_ask.intent, "connect_ask_private")
        self.assertEqual(bob_ask.payload_type, "connect_ask_private")
        self.assertIsNotNone(bob_ask.audit_payload)
        self.assertTrue(bob_ask.audit_payload["masked"])
        self.assertEqual(bob_ask.audit_payload["note"], "非公開項目に基づくconnect_ask_private（内容非表示）")

        # 2. Audit Dashboard view is masked
        audit_records = self.service.get_audit_dashboard_records()
        bob_audit_record = [r for r in audit_records if r["to"] == "agent_bob" and r["intent"] == "connect_ask_private"][0]
        self.assertTrue(bob_audit_record["display_payload"]["masked"])
        self.assertEqual(bob_audit_record["display_payload"]["note"], "非公開項目に基づくconnect_ask_private（内容非表示）")

        # 3. Requester view does NOT leak private distinction
        requester_statuses = self.service.get_requester_status("user_requester", res.query_message.audit_id)
        bob_status = [s for s in requester_statuses if s.candidate_id == "emp_bob"][0]
        alice_status = [s for s in requester_statuses if s.candidate_id == "emp_alice"][0]
        self.assertEqual(bob_status.state, "pending")
        self.assertEqual(alice_status.state, "pending")

        # 4. Private mask on no_connection (C-21)
        # Create a candidate whose drop cites a private item
        prof_priv_drop = Profile(
            employee_id="emp_priv_drop",
            name="Eve",
            role="Specialist",
            items=[ProfileItem(key="secret_lab", body="極秘研究室での検証", visibility="private", reviewed=True)],
        )
        self.store.save_profile(prof_priv_drop)

        no_conn_msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_eve",
            intent="no_connection",
            payload_type="no_connection",
            payload={
                "reason_text": "極秘研究室での検証は該当しません",
                "cited_item_keys": ["secret_lab"],
                "score": 0.1,
            },
            candidate_profile=prof_priv_drop,
        )
        self.assertEqual(no_conn_msg.intent, "no_connection")
        self.assertIsNotNone(no_conn_msg.audit_payload)
        self.assertTrue(no_conn_msg.audit_payload["masked"])
        self.assertEqual(no_conn_msg.audit_payload["note"], "非公開項目に基づくno_connection（内容非表示）")

    def test_goal_4b_reject_unsupported_intent(self) -> None:
        """Goal 4b: Message with intent unsupported by destination agent is rejected with rejected=True (C-25)."""
        # agent_alice only supports ["connect_ask", "connect_ask_private", "no_connection"]
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_alice",
            intent="query",  # unsupported intent for agent_alice
            payload_type="query",
            payload={"question_text": "テスト質問", "requester_id": "u1"},
        )
        self.assertTrue(msg.rejected)
        self.assertEqual(msg.intent, "reject_unsupported_intent")
        self.assertEqual(msg.payload_type, "reject_unsupported_intent")
        self.assertEqual(msg.payload["target_agent_id"], "agent_alice")
        self.assertEqual(msg.payload["attempted_intent"], "query")

        # Check audit record
        saved = self.store.get_message(msg.audit_id)
        self.assertIsNotNone(saved)
        self.assertTrue(saved.rejected)

    def test_goal_5_consent_granted_generates_match_proposal_without_reason_text(self) -> None:
        """Goal 5: Granted consent -> match_proposal with meeting_duration=15, participants, NO reason_text (C-19)."""
        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )
        alice_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_alice"][0]

        consent_res = self.service.respond_consent(
            candidate_entity_id="agent_alice",
            ask_audit_id=alice_ask.audit_id,
            decision="granted",
        )

        match_proposal = consent_res.outcome_message
        self.assertEqual(match_proposal.intent, "match_proposal")
        self.assertEqual(match_proposal.payload_type, "match_proposal")
        self.assertEqual(match_proposal.payload["meeting_duration"], 15)
        self.assertEqual(match_proposal.payload["proposed_by"], "system")
        self.assertIn("user_requester", match_proposal.payload["participants"])
        self.assertIn("emp_alice", match_proposal.payload["participants"])
        # Critical privacy rule: reason_text must NOT exist in match_proposal
        self.assertNotIn("reason_text", match_proposal.payload)

    def test_goal_6_consent_declined_with_attachments(self) -> None:
        """Goal 6: Declined consent with link and doc attachments returns decline_with_reason to requester."""
        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )
        alice_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_alice"][0]

        # Decline with link attachment
        link_att = Attachment(type="link", content="https://wiki.internal/production-docs")
        consent_link_res = self.service.respond_consent(
            candidate_entity_id="agent_alice",
            ask_audit_id=alice_ask.audit_id,
            decision="declined",
            reason_text="現在別プロジェクトで対応が難しいため、こちらの社内Wikiをご参照ください",
            attachment=link_att,
        )
        self.assertEqual(consent_link_res.outcome_message.intent, "decline_with_reason")
        self.assertEqual(
            consent_link_res.outcome_message.payload["reason_text"],
            "現在別プロジェクトで対応が難しいため、こちらの社内Wikiをご参照ください",
        )
        self.assertEqual(consent_link_res.outcome_message.payload["attachment"]["type"], "link")
        self.assertEqual(consent_link_res.outcome_message.payload["attachment"]["content"], "https://wiki.internal/production-docs")

        # Decline with doc attachment
        charlie_ask = [m for m in res.dispatched_asks if m.to_entity == "agent_charlie"][0]
        doc_att = Attachment(type="doc", content="doc_factory_automation_guide_2026")
        consent_doc_res = self.service.respond_consent(
            candidate_entity_id="agent_charlie",
            ask_audit_id=charlie_ask.audit_id,
            decision="declined",
            reason_text="添付の自動化ガイドラインをご確認ください",
            attachment=doc_att,
        )
        self.assertEqual(consent_doc_res.outcome_message.intent, "decline_with_reason")
        self.assertEqual(consent_doc_res.outcome_message.payload["attachment"]["type"], "doc")
        self.assertEqual(consent_doc_res.outcome_message.payload["attachment"]["content"], "doc_factory_automation_guide_2026")

    def test_goal_7_reject_unregistered_payload_type(self) -> None:
        """Goal 7: Unregistered payload_type rejected as reject_unregistered_type (rejected=True)."""
        msg = self.transmission.send(
            from_entity="system",
            to_entity="agent_alice",
            intent="custom_probe",
            payload_type="unregistered_probe_payload_type",
            payload={"arbitrary": 123},
        )
        self.assertTrue(msg.rejected)
        self.assertEqual(msg.intent, "reject_unregistered_type")
        self.assertEqual(msg.payload_type, "reject_unregistered_type")

        # Verify recorded in store audit trail
        saved = self.store.get_message(msg.audit_id)
        self.assertIsNotNone(saved)
        self.assertTrue(saved.rejected)

    def test_goal_8_unregistered_profiles_never_dispatched(self) -> None:
        """Goal 8: 4 agents registered, 400 profiles exist -> only registered agents can receive asks."""
        # Add 396 synthetic profiles without registered agents (total 400 profiles)
        for i in range(5, 401):
            synth_prof = Profile(
                employee_id=f"emp_{i:04d}",
                name=f"Synth Employee {i}",
                role="General Staff",
                items=[
                    ProfileItem(
                        key="current_work",
                        body=f"製造業生産管理および業務改善プロジェクト {i}",
                        visibility="public",
                        reviewed=True,
                    )
                ],
            )
            self.store.save_profile(synth_prof)

        all_profiles = self.store.list_profiles()
        self.assertEqual(len(all_profiles), 400)
        self.assertEqual(len(self.store.list_agents(active_only=True)), 4)

        res = self.service.submit_query(
            requester_id="user_requester",
            question_text="製造業の生産管理システム導入に関する知見",
        )

        # Screen funnel should show top 20 across all 400 profiles (scale display)
        self.assertEqual(len(res.funnel_candidates), 20)

        # Dispatched asks MUST ONLY target registered agents (Alice, Bob, Charlie)
        for ask in res.dispatched_asks:
            self.assertIn(ask.to_entity, {"agent_alice", "agent_bob", "agent_charlie"})
            # Verify recipient agent is registered in store
            agent = self.store.get_agent(ask.to_entity)
            self.assertIsNotNone(agent)
            self.assertTrue(agent.active)


if __name__ == "__main__":
    unittest.main()
