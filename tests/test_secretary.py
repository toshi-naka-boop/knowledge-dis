"""Unit tests for secretary.py (Milestone 3 proactive secretary layer).

Follows design.md §14:
- §14.2 stagnation card state machine (notice/request_draft/resolved/dismissed).
- §14.3 stagnation score (weights/thresholds fixed explicitly, not via secretary.py's
  own env-var defaults, so this test suite stays deterministic regardless of the
  developer's shell environment or future default changes).
- §14.4 preview search: no candidate-side trace, public-only isolation.
- §14.4 confirm CAS: double-submit does not duplicate the discovery query.
- §14.5 profile diff proposal apply flow (embedding + embedding_public regeneration).
- §14.6 audit fail-closed masking of the 3 secretary-only intents.

No network, no real LLM, no real Firestore: everything runs against InMemoryStore,
DeterministicEmbedder, and FakeConnectionInferencer.
"""

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Explicitly fix stagnation score weights/thresholds via env vars BEFORE importing
# knowledge_discovery.secretary (module-level constants are computed at import
# time). This decouples every expectation in this file from secretary.py's own
# env-var defaults, per test discipline: values below are pinned once here and
# never assumed implicitly.
os.environ["W_OVERDUE"] = "1.0"
os.environ["W_STALE"] = "1.0"
os.environ["W_RESCHED"] = "2.0"
os.environ["W_NEGLECT"] = "3.0"
os.environ["W_UNTOUCHED"] = "2.0"
os.environ["STAGNATION_T1"] = "3.0"
os.environ["STAGNATION_T2"] = "7.0"
os.environ["STAGNATION_CAP"] = "10"
os.environ["STAGNATION_NEGLECT_WINDOW"] = "3"

from knowledge_discovery.matching import (  # noqa: E402
    DeterministicEmbedder,
    FakeConnectionInferencer,
    MatchingEngine,
)
from knowledge_discovery.models import (  # noqa: E402
    Agent,
    MailSeed,
    Profile,
    ProfileItem,
    Schedule,
    Task,
)
from knowledge_discovery.secretary import SecretaryService  # noqa: E402
from knowledge_discovery.service import KnowledgeDiscoveryService  # noqa: E402
from knowledge_discovery.store import InMemoryStore  # noqa: E402
from knowledge_discovery.transmission import TransmissionLayer  # noqa: E402

# Fixed "today" for all tests (§14.7 DEMO_TODAY equivalent): never depend on the
# real wall-clock date. Passed explicitly to run_sweep()/get_morning_digest()
# via the demo_today argument.
TODAY = date(2026, 6, 15)
TODAY_STR = TODAY.isoformat()


def _iso(d: date) -> str:
    return d.isoformat()


class SecretaryTestBase(unittest.TestCase):
    """Common fixture builder shared by secretary layer tests."""

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
        self.kd_service = KnowledgeDiscoveryService(
            store=self.store,
            transmission=self.transmission,
            matching_engine=self.matching_engine,
        )
        # t1/t2 explicitly fixed via constructor args (§14.3 discipline), matching
        # the env vars pinned above so both mechanisms agree.
        self.secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
        )

    # --- fixture helpers ---------------------------------------------------

    def _add_matching_candidate(self, employee_id: str = "emp_match", agent_id: str = "agent_match") -> None:
        """Register an active agent + public profile that reliably matches the
        deterministic fallback question draft for a task titled
        'Kubernetes upgrade project' (shares 'expertise'/'kubernete'/'upgrade'/
        'project' tokens with FakeConnectionInferencer's >=2 overlap heuristic).
        """
        agent = Agent(
            agent_id=agent_id,
            employee_id=employee_id,
            display_name="Match Employee",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        profile = Profile(
            employee_id=employee_id,
            name="Match Employee",
            role="SRE",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Has strong expertise in Kubernetes upgrade projects and cluster migrations.",
                    source="job_doc",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )
        self.store.save_agent(agent)
        self.store.save_profile(profile)

    def _make_high_score_task(self, task_id: str = "task_high", owner: str = "emp_owner") -> Task:
        """Task engineered to score far above T2 (score=28 with the pinned weights
        above: capped overdue 10 + capped stale 10 + resched 3*2=6 + untouched 2).
        """
        far_past = _iso(TODAY - timedelta(days=20))
        return Task(
            task_id=task_id,
            owner_employee_id=owner,
            title="Kubernetes upgrade project",
            description="",
            status="todo",
            due_date=far_past,
            created_at=far_past,
            last_updated_at=far_past,
            reschedule_count=3,
            status_changed_at=far_past,
        )

    def _make_notice_score_task(self, task_id: str = "task_notice", owner: str = "emp_owner") -> Task:
        """Task engineered to land strictly within [T1, T2) (score=4: overdue
        2*1 + resched 1*2; stale=0 since last_updated_at is today, untouched=0
        since status is 'in_progress').
        """
        near_past = _iso(TODAY - timedelta(days=2))
        return Task(
            task_id=task_id,
            owner_employee_id=owner,
            title="Kubernetes upgrade project",
            description="",
            status="in_progress",
            due_date=near_past,
            created_at=near_past,
            last_updated_at=TODAY_STR,
            reschedule_count=1,
            status_changed_at=near_past,
        )


# (a) Preview leaves no candidate-side trace ---------------------------------


class TestPreviewLeavesNoCandidateTrace(SecretaryTestBase):
    def test_sweep_generates_zero_connect_ask_messages(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card)
        self.assertEqual(card.tier, "request_draft")
        self.assertGreaterEqual(len(card.payload["preview"]["candidates"]), 1)

        intents = [m.intent for m in self.store.list_messages()]
        self.assertNotIn("connect_ask", intents)
        self.assertNotIn("connect_ask_private", intents)


# (b) Preview is structurally public-only: private items never leak ---------


class TestPreviewPublicOnlyIsolation(SecretaryTestBase):
    def test_private_items_do_not_leak_into_preview_candidates(self) -> None:
        # Candidate whose only relevant knowledge is marked private: preview must
        # not surface it even though a full-profile match would succeed, because
        # preview_search() only ever passes public items to Stage-2 inference.
        priv_agent = Agent(
            agent_id="agent_priv",
            employee_id="emp_priv",
            display_name="Private Match Employee",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        priv_profile = Profile(
            employee_id="emp_priv",
            name="Private Match Employee",
            role="Ops",
            items=[
                ProfileItem(
                    key="public_topic",
                    body="Handles quarterly expense reports.",
                    source="job_doc",
                    visibility="public",
                    reviewed=True,
                ),
                ProfileItem(
                    key="secret_topic",
                    body="Secretly led the Kubernetes upgrade project rollout for leadership only.",
                    source="job_doc",
                    visibility="private",
                    reviewed=True,
                ),
            ],
        )
        self.store.save_agent(priv_agent)
        self.store.save_profile(priv_profile)

        # Also register a genuine PUBLIC match so the preview is non-empty overall,
        # proving the private-only candidate is excluded specifically (not merely
        # that preview happened to find nothing at all).
        self._add_matching_candidate(employee_id="emp_match", agent_id="agent_match")

        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")
        candidates = card.payload["preview"]["candidates"]
        candidate_ids = {c["employee_id"] for c in candidates}

        self.assertIn("emp_match", candidate_ids)
        self.assertNotIn("emp_priv", candidate_ids)

        for c in candidates:
            self.assertNotIn("secret_topic", c["cited_item_keys"])
            self.assertNotIn("Secretly led the Kubernetes upgrade project rollout", c["reason_text"])


# (c) Stagnation card state machine ------------------------------------------


class TestStagnationCardStateMachine(SecretaryTestBase):
    def test_notice_card_promotes_to_request_draft_on_score_increase(self) -> None:
        self._add_matching_candidate()
        task = self._make_notice_score_task()
        self.store.save_task(task)

        result1 = self.secretary.run_sweep(demo_today=TODAY_STR)
        self.assertEqual(result1["cards_created"], 1)

        card1 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card1)
        self.assertEqual(card1.tier, "notice")
        self.assertNotIn("question_draft", card1.payload)
        card_id = card1.card_id

        # Push the task's score well past T2 (heavier reschedule + deeper overdue).
        far_past = _iso(TODAY - timedelta(days=20))
        task.due_date = far_past
        task.last_updated_at = far_past
        task.reschedule_count = 5
        self.store.save_task(task)

        result2 = self.secretary.run_sweep(demo_today=TODAY_STR)
        self.assertEqual(result2["cards_promoted"], 1)
        self.assertEqual(result2["cards_created"], 0)

        card2 = self.store.get_card(card_id)
        self.assertEqual(card2.card_id, card_id)  # same card, not a new one
        self.assertEqual(card2.tier, "request_draft")
        self.assertIn("question_draft", card2.payload)

        all_cards_for_task = self.store.find_cards_for_task("emp_owner", task.task_id)
        self.assertEqual(len(all_cards_for_task), 1)

    def test_task_done_resolves_open_card(self) -> None:
        task = self._make_notice_score_task(task_id="task_done_case")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card)

        task.status = "done"
        self.store.save_task(task)
        result = self.secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(result["cards_resolved"], 1)
        resolved_card = self.store.get_card(card.card_id)
        self.assertEqual(resolved_card.status, "resolved")
        self.assertEqual(resolved_card.resolved_reason, "task_done")

    def test_dismissed_task_card_not_recreated(self) -> None:
        task = self._make_notice_score_task(task_id="task_dismiss_case")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card)

        self.secretary.dismiss_card(card.card_id)

        # Task remains just as stagnant; re-sweep must not resurrect a card.
        self.secretary.run_sweep(demo_today=TODAY_STR)
        self.assertIsNone(self.store.find_open_card_for_task("emp_owner", task.task_id))
        all_cards = self.store.find_cards_for_task("emp_owner", task.task_id)
        self.assertEqual(len(all_cards), 1)
        self.assertEqual(all_cards[0].status, "dismissed")

    def test_same_day_resweep_does_not_duplicate_open_card(self) -> None:
        task = self._make_notice_score_task(task_id="task_dup_case")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)
        self.secretary.run_sweep(demo_today=TODAY_STR)

        cards = self.store.find_cards_for_task("emp_owner", task.task_id)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].status, "open")


# (d) Confirm CAS: double confirmation dispatches the query only once -------


class TestConfirmCardCAS(SecretaryTestBase):
    def test_double_confirm_only_submits_query_once(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_confirm_case")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")

        first = self.secretary.confirm_stagnation_card(
            card.card_id, edited_question="Edited: need advice on the Kubernetes upgrade."
        )
        self.assertEqual(first["status"], "confirmed")
        self.assertIsNotNone(first["query_audit_id"])

        messages_after_first = self.store.list_messages()
        query_messages_after_first = [m for m in messages_after_first if m.intent == "query"]
        self.assertEqual(len(query_messages_after_first), 1)

        second = self.secretary.confirm_stagnation_card(
            card.card_id, edited_question="A different edit that should be ignored."
        )
        self.assertEqual(second["status"], "already_confirmed")
        self.assertEqual(second["query_audit_id"], first["query_audit_id"])

        messages_after_second = self.store.list_messages()
        self.assertEqual(len(messages_after_second), len(messages_after_first))
        query_messages_after_second = [m for m in messages_after_second if m.intent == "query"]
        self.assertEqual(len(query_messages_after_second), 1)

        final_card = self.store.get_card(card.card_id)
        self.assertEqual(final_card.status, "confirmed")
        self.assertEqual(final_card.linked_query_audit_id, first["query_audit_id"])


# (e) Morning digest schedule due-date categorization ------------------------


class TestMorningDigestDateRules(SecretaryTestBase):
    def test_schedule_reminders_categorized_by_due_date(self) -> None:
        owner = "emp_digest"
        overdue = Schedule(
            item_id="sch_overdue",
            owner_employee_id=owner,
            kind="expense_deadline",
            title="Overdue expense report",
            due_date=_iso(TODAY - timedelta(days=5)),
        )
        today_item = Schedule(
            item_id="sch_today",
            owner_employee_id=owner,
            kind="weekly_report",
            title="Weekly report",
            due_date=TODAY_STR,
        )
        tomorrow_item = Schedule(
            item_id="sch_tomorrow",
            owner_employee_id=owner,
            kind="meeting_prep",
            title="Meeting prep",
            due_date=_iso(TODAY + timedelta(days=1)),
        )
        upcoming_item = Schedule(
            item_id="sch_upcoming",
            owner_employee_id=owner,
            kind="monthly_report",
            title="Monthly report",
            due_date=_iso(TODAY + timedelta(days=3)),
        )

        for s in [overdue, today_item, tomorrow_item, upcoming_item]:
            self.store.save_schedule(s)

        digest = self.secretary.get_morning_digest(owner, demo_today=TODAY_STR)
        reminders = digest["reminders"]

        # No item dropped, duplicated, or misclassified outside the four buckets.
        self.assertEqual(len(reminders), 4)
        by_id = {r["item_id"]: r for r in reminders}
        self.assertEqual(by_id["sch_overdue"]["due_category"], "overdue")
        self.assertEqual(by_id["sch_today"]["due_category"], "today")
        self.assertEqual(by_id["sch_tomorrow"]["due_category"], "tomorrow")
        self.assertEqual(by_id["sch_upcoming"]["due_category"], "upcoming")

        # Ordering: overdue -> today -> tomorrow -> upcoming.
        self.assertEqual(
            [r["item_id"] for r in reminders],
            ["sch_overdue", "sch_today", "sch_tomorrow", "sch_upcoming"],
        )


# (f) Profile diff proposal: apply adds item and regenerates both embeddings -


class TestProfileDiffApply(SecretaryTestBase):
    def test_apply_profile_diff_adds_item_and_regenerates_both_embeddings(self) -> None:
        owner = "emp_mail"
        profile = Profile(
            employee_id=owner,
            name="Mail Owner",
            role="Engineer",
            items=[
                ProfileItem(
                    key="background",
                    body="Backend service maintenance.",
                    source="job_doc",
                    visibility="public",
                    reviewed=True,
                )
            ],
        )
        self.matching_engine.compute_profile_embedding(profile)
        self.store.save_profile(profile)
        original_embedding = list(profile.embedding)
        original_embedding_public = list(profile.embedding_public)

        mail = MailSeed(
            mail_id="mail_1",
            owner_employee_id=owner,
            subject="Project update",
            body="I have been leading the new logistics optimization project this quarter.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        reloaded_mail = self.store.get_mail_seed("mail_1")
        self.assertTrue(reloaded_mail.processed)

        diff_cards = [
            c for c in self.store.list_cards(owner_employee_id=owner, status="open") if c.type == "profile_diff"
        ]
        self.assertEqual(len(diff_cards), 1)
        diff_card = diff_cards[0]

        result = self.secretary.review_profile_diff(diff_card.card_id, action="apply")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["visibility"], "public")

        updated_profile = self.store.get_profile(owner)

        # A genuinely NEW item was added (distinct key from the pre-existing one).
        new_item = updated_profile.get_item(result["item_key"])
        self.assertIsNotNone(new_item)
        self.assertNotEqual(new_item.key, "background")
        self.assertTrue(new_item.reviewed)
        self.assertEqual(new_item.source, "mail_seed")

        # The pre-existing item is untouched.
        background_item = updated_profile.get_item("background")
        self.assertIsNotNone(background_item)
        self.assertEqual(background_item.source, "job_doc")

        # Both embedding and embedding_public were regenerated (§14.5/§9.3).
        self.assertIsNotNone(updated_profile.embedding)
        self.assertIsNotNone(updated_profile.embedding_public)
        self.assertNotEqual(updated_profile.embedding, original_embedding)
        self.assertNotEqual(updated_profile.embedding_public, original_embedding_public)

        applied_card = self.store.get_card(diff_card.card_id)
        self.assertEqual(applied_card.status, "applied")


# (g) Audit masking of the 3 secretary-only intents (fail-closed, §14.6) ----


class TestAuditMasksSecretaryIntents(SecretaryTestBase):
    def test_secretary_intents_are_masked_in_audit_dashboard(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_audit_case")
        self.store.save_task(task)

        mail = MailSeed(
            mail_id="mail_audit",
            owner_employee_id="emp_owner",
            subject="Confidential project note",
            body="Working on a sensitive client migration that should stay private.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        records = self.kd_service.get_audit_dashboard_records()
        masked_intents = {"stagnation_detected", "preview_search", "profile_diff_proposed"}
        found_intents = set()

        for r in records:
            if r["intent"] in masked_intents:
                found_intents.add(r["intent"])
                self.assertTrue(r["display_payload"].get("masked"), f"{r['intent']} should be masked in audit view")
                # Raw sensitive fields (task id, candidate list, item key) must
                # never surface in the audit display payload (plain-text
                # whitelist excludes these 3 intents entirely, §14.6/C-21).
                self.assertNotIn("task_id", r["display_payload"])
                self.assertNotIn("candidates", r["display_payload"])
                self.assertNotIn("item_key", r["display_payload"])

        self.assertEqual(found_intents, masked_intents)


if __name__ == "__main__":
    unittest.main()
