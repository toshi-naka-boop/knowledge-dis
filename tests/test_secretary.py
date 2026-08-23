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
import threading
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

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

from knowledge_discovery.connectors import (  # noqa: E402
    SeedConnector,
    build_connector_from_env,
)
from knowledge_discovery.connectors.base import (  # noqa: E402
    FetchResult,
    SourceConnector,
    TaskRecord,
)
from knowledge_discovery.matching import (  # noqa: E402
    DeterministicEmbedder,
    FakeConnectionInferencer,
    MatchingEngine,
)
from knowledge_discovery.models import (  # noqa: E402
    Agent,
    Card,
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


class FakeLLMClient:
    """Minimal stand-in for a google.genai.Client, exposing only the
    `.models.generate_content(model=, contents=)` surface that
    generate_question_draft()/extract_profile_diff() call (§14.4/§14.5,
    V-8/E-7/S-6 LLM wiring). `respond` maps the prompt text to a response text.
    """

    def __init__(self, respond) -> None:  # respond: Callable[[str], str]
        self._respond = respond
        self.calls: list[str] = []
        self.models = self

    def generate_content(self, model: str, contents: str) -> SimpleNamespace:
        self.calls.append(contents)
        return SimpleNamespace(text=self._respond(contents))


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


# (h) Concurrent confirm: CAS allows only one query dispatch (V-6/S-8) --------


class TestConfirmCardConcurrentCAS(SecretaryTestBase):
    def test_concurrent_double_confirm_dispatches_query_only_once(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_concurrent_confirm")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")

        results: list[dict] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def _confirm() -> None:
            try:
                res = self.secretary.confirm_stagnation_card(
                    card.card_id, edited_question="Need advice on the Kubernetes upgrade."
                )
                with lock:
                    results.append(res)
            except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_confirm) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        query_messages = [m for m in self.store.list_messages() if m.intent == "query"]
        self.assertEqual(len(query_messages), 1)

        final_card = self.store.get_card(card.card_id)
        self.assertEqual(final_card.status, "confirmed")
        self.assertEqual(final_card.linked_query_audit_id, query_messages[0].audit_id)

        statuses = {r["status"] for r in results}
        self.assertIn("confirmed", statuses)
        self.assertTrue(statuses <= {"confirmed", "already_confirmed"})


# (i) Profile diff reflection is additive: never overwrites/reflips existing --
# items (V-7/S-7)


class TestProfileDiffReflectionIsAdditive(SecretaryTestBase):
    def test_apply_with_key_collision_adds_new_item_without_overwriting_existing(self) -> None:
        owner = "emp_collide"
        profile = Profile(
            employee_id=owner,
            name="Collide Owner",
            role="Engineer",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Original hand-written current work description.",
                    source="job_doc",
                    visibility="private",
                    reviewed=True,
                )
            ],
        )
        self.matching_engine.compute_profile_embedding(profile)
        self.store.save_profile(profile)

        mail = MailSeed(
            mail_id="mail_collide",
            owner_employee_id=owner,
            subject="Update",
            body="Leading a new logistics optimization project this quarter for the region.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)
        diff_cards = [
            c for c in self.store.list_cards(owner_employee_id=owner, status="open") if c.type == "profile_diff"
        ]
        self.assertEqual(len(diff_cards), 1)
        # Heuristic fallback (no llm_client configured in this fixture) always
        # proposes item_key="current_work", colliding with the existing item.
        self.assertEqual(diff_cards[0].payload["item_key"], "current_work")

        result = self.secretary.review_profile_diff(diff_cards[0].card_id, action="apply")
        self.assertEqual(result["status"], "applied")
        self.assertNotEqual(result["item_key"], "current_work")
        self.assertTrue(result["item_key"].startswith("current_work_mail_"))

        updated = self.store.get_profile(owner)
        original_item = updated.get_item("current_work")
        self.assertIsNotNone(original_item)
        self.assertEqual(original_item.body, "Original hand-written current work description.")
        self.assertEqual(original_item.source, "job_doc")
        self.assertEqual(original_item.visibility, "private")  # never flipped to public

        new_item = updated.get_item(result["item_key"])
        self.assertIsNotNone(new_item)
        self.assertEqual(new_item.visibility, "public")
        self.assertEqual(new_item.source, "mail_seed")

    def test_apply_without_existing_profile_raises_lookup_error_not_fabricated(self) -> None:
        card = Card(
            card_id="card_diff_orphan",
            owner_employee_id="emp_no_profile",
            type="profile_diff",
            tier=None,
            payload={"item_key": "current_work", "body_draft": "Some proposed text.", "source_mail_id": "mail_x"},
            status="open",
        )
        self.store.save_card(card)

        with self.assertRaises(LookupError):
            self.secretary.review_profile_diff(card.card_id, action="apply")

        # No dummy profile was created as a side effect of the failed attempt.
        self.assertIsNone(self.store.get_profile("emp_no_profile"))


# (j) Re-sweep on an already-promoted request_draft card is a no-op beyond ----
# score/evidence_line (V-9/E-6)


class TestRequestDraftResweepIsIdempotent(SecretaryTestBase):
    def test_resweep_within_same_band_does_not_rerun_preview_or_draft(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_idempotent_resweep")
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR)
        card1 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card1.tier, "request_draft")
        first_draft = card1.payload["question_draft"]
        first_preview = card1.payload["preview"]

        preview_msgs_after_first = [m for m in self.store.list_messages() if m.intent == "preview_search"]
        self.assertEqual(len(preview_msgs_after_first), 1)

        # Push the score further within the SAME (>= T2) band; no tier change.
        task.reschedule_count += 2
        self.store.save_task(task)

        result2 = self.secretary.run_sweep(demo_today=TODAY_STR)
        self.assertEqual(result2["cards_promoted"], 0)

        card2 = self.store.get_card(card1.card_id)
        self.assertEqual(card2.tier, "request_draft")
        # Score/evidence_line DO update...
        self.assertNotEqual(card2.payload["score"], card1.payload["score"])
        # ...but the draft and preview are frozen from the initial promotion.
        self.assertEqual(card2.payload["question_draft"], first_draft)
        self.assertEqual(card2.payload["preview"], first_preview)

        preview_msgs_after_second = [m for m in self.store.list_messages() if m.intent == "preview_search"]
        self.assertEqual(len(preview_msgs_after_second), 1)  # no new audit row


# (k) dismiss_card is restricted to stagnation/open cards (E-8) ---------------


class TestDismissCardValidation(SecretaryTestBase):
    def test_dismiss_rejects_profile_diff_card(self) -> None:
        diff_card = Card(
            card_id="card_diff_reject",
            owner_employee_id="emp_x",
            type="profile_diff",
            tier=None,
            payload={"item_key": "current_work", "body_draft": "text"},
            status="open",
        )
        self.store.save_card(diff_card)
        with self.assertRaises(ValueError):
            self.secretary.dismiss_card(diff_card.card_id)

    def test_dismiss_rejects_non_open_stagnation_card(self) -> None:
        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_dismiss_reject")
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR)
        stag_card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.secretary.confirm_stagnation_card(stag_card.card_id, edited_question="Need help.")

        with self.assertRaises(ValueError):
            self.secretary.dismiss_card(stag_card.card_id)


# (l) LLM wiring: question draft & diff extraction use llm_client when -------
# configured; explicit null diff never falls back to the heuristic (V-8/E-7/S-6)


class TestLLMClientWiring(SecretaryTestBase):
    @staticmethod
    def _llm_respond(contents: str) -> str:
        if "Write a single natural question" in contents:
            return "LLM-drafted question: any advice on the Kubernetes upgrade project?"
        if "extract a suggested profile item" in contents:
            return '{"item_key": "current_work", "body_draft": "LLM-summarized project update."}'
        return "null"

    def test_question_draft_and_diff_extraction_use_llm_when_configured(self) -> None:
        fake_llm = FakeLLMClient(self._llm_respond)
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            llm_client=fake_llm,
            t1=3.0,
            t2=7.0,
        )

        self._add_matching_candidate()
        task = self._make_high_score_task(task_id="task_llm_wired")
        self.store.save_task(task)

        mail = MailSeed(
            mail_id="mail_llm_wired",
            owner_employee_id="emp_owner",
            subject="Project note",
            body="We wrapped up the migration ahead of schedule.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        secretary.run_sweep(demo_today=TODAY_STR)

        stag_card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(stag_card.tier, "request_draft")
        self.assertEqual(
            stag_card.payload["question_draft"],
            "LLM-drafted question: any advice on the Kubernetes upgrade project?",
        )

        diff_cards = [
            c
            for c in self.store.list_cards(owner_employee_id="emp_owner", status="open")
            if c.type == "profile_diff"
        ]
        self.assertEqual(len(diff_cards), 1)
        self.assertEqual(diff_cards[0].payload["item_key"], "current_work")
        self.assertEqual(diff_cards[0].payload["body_draft"], "LLM-summarized project update.")

        # The LLM path was actually exercised, not the deterministic
        # template / heuristic (raw email paste) fallback.
        self.assertTrue(any("Write a single natural question" in c for c in fake_llm.calls))
        self.assertTrue(any("extract a suggested profile item" in c for c in fake_llm.calls))

    def test_llm_explicit_null_diff_creates_no_card_and_skips_heuristic_fallback(self) -> None:
        fake_llm = FakeLLMClient(lambda contents: "null")
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            llm_client=fake_llm,
            t1=3.0,
            t2=7.0,
        )
        mail = MailSeed(
            mail_id="mail_llm_null",
            owner_employee_id="emp_owner",
            subject="FYI",
            body="Nothing notable here, just routine correspondence.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        secretary.run_sweep(demo_today=TODAY_STR)

        reloaded = self.store.get_mail_seed("mail_llm_null")
        self.assertTrue(reloaded.processed)
        diff_cards = [c for c in self.store.list_cards(status="open") if c.type == "profile_diff"]
        self.assertEqual(len(diff_cards), 0)

    def test_llm_failure_leaves_mail_unprocessed_and_next_sweep_retries(self) -> None:
        # R-2: an LLM failure (exception or empty response) must not consume the
        # mail seed as if it were an explicit "no diff" — the next sweep retries.
        def failing(contents: str) -> str:
            if "extract a suggested profile item" in contents:
                raise RuntimeError("api down")
            return "null"

        fake_llm = FakeLLMClient(failing)
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            llm_client=fake_llm,
            t1=3.0,
            t2=7.0,
        )
        mail = MailSeed(
            mail_id="mail_llm_fail",
            owner_employee_id="emp_owner",
            subject="Zoning update",
            body="Confidential client detail that must not be pasted verbatim.",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        secretary.run_sweep(demo_today=TODAY_STR)

        reloaded = self.store.get_mail_seed("mail_llm_fail")
        self.assertFalse(reloaded.processed, "failure must leave the mail retryable")
        diff_cards = [c for c in self.store.list_cards(status="open") if c.type == "profile_diff"]
        self.assertEqual(len(diff_cards), 0, "no heuristic mail-paste card on failure")

        # Same for an empty response (API hiccup, not a deliberate null).
        fake_llm._respond = lambda contents: ""
        secretary.run_sweep(demo_today=TODAY_STR)
        self.assertFalse(self.store.get_mail_seed("mail_llm_fail").processed)

        # Recovery: once the LLM answers, the retried mail yields a card.
        fake_llm._respond = lambda contents: (
            '{"item_key": "expertise", "body_draft": "Zoning negotiation experience."}'
            if "extract a suggested profile item" in contents
            else "null"
        )
        secretary.run_sweep(demo_today=TODAY_STR)
        self.assertTrue(self.store.get_mail_seed("mail_llm_fail").processed)
        diff_cards = [c for c in self.store.list_cards(status="open") if c.type == "profile_diff"]
        self.assertEqual(len(diff_cards), 1)
        self.assertEqual(diff_cards[0].payload["item_key"], "expertise")


class FakeSourceConnector(SourceConnector):
    """Test double: `results` maps employee_id -> a FetchResult, or a
    zero-arg callable returning one (so a test can change what the next
    sweep sees). Missing owners get an empty complete FetchResult. Raises
    if the mapped value is an Exception instance (to exercise the
    per-owner failure path without stopping the whole sweep, §16.3).
    """

    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.results: dict[str, object] = results or {}
        self.calls: list[str] = []

    def fetch(self, owner_employee_id: str, today: str) -> FetchResult:
        self.calls.append(owner_employee_id)
        value = self.results.get(owner_employee_id, FetchResult(complete=True))
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value()
        return value


# (g) Sync-then-detect (§16.3, C-2) ------------------------------------------


class TestSyncThenDetect(SecretaryTestBase):
    def _register_owner(self, owner: str) -> None:
        """Registers owner as an agent+profile so the sync step's target
        owner set (agents ∪ profiles) includes them, without engineering a
        matching candidate.
        """
        self.store.save_agent(
            Agent(
                agent_id=f"agent_{owner}",
                employee_id=owner,
                display_name=owner,
                supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
                active=True,
            )
        )
        self.store.save_profile(Profile(employee_id=owner, name=owner, role="Role"))

    def test_default_connector_is_seed_and_behavior_is_unchanged(self) -> None:
        # No connector passed to SecretaryTestBase's fixture: must default to
        # a no-op SeedConnector, so a seed-populated task is scored exactly
        # as before sync-then-detect existed.
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        result = self.secretary.run_sweep(demo_today=TODAY_STR)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card)
        self.assertEqual(card.tier, "request_draft")
        self.assertEqual(result["sync_tasks"], 0)
        self.assertEqual(result["sync_errors"], 0)

    def test_synced_task_is_detected_and_resolves_when_source_marks_it_done(self) -> None:
        owner = "emp_owner"
        self._register_owner(owner)
        self._add_matching_candidate()

        far_past = _iso(TODAY - timedelta(days=20))
        connector = FakeSourceConnector(
            {
                owner: FetchResult(
                    tasks=[
                        TaskRecord(
                            source_id="gws_task_x",
                            title="Kubernetes upgrade project",
                            due_date=far_past,
                            status="todo",
                            last_updated_at=far_past + "T00:00:00Z",
                        )
                    ],
                    complete=True,
                )
            }
        )
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
            connector=connector,
        )

        result = secretary.run_sweep(demo_today=TODAY_STR)
        # _add_matching_candidate() also registers "emp_match" as an
        # agent+profile, so it's synced too (harmlessly, with an empty
        # FetchResult default) — only assert the owner under test was synced.
        self.assertIn(owner, connector.calls)
        self.assertEqual(result["sync_tasks"], 1)

        synced_task = self.store.get_task("gws_task_x")
        self.assertIsNotNone(synced_task)
        self.assertEqual(synced_task.source, "gws")

        card = self.store.find_open_card_for_task(owner, "gws_task_x")
        self.assertIsNotNone(card)
        self.assertEqual(card.tier, "request_draft")

        # The source no longer reports the task at all (e.g. deleted/completed
        # upstream): a complete resync marks it done, and the next sweep's
        # detection pass resolves the open card.
        connector.results[owner] = FetchResult(tasks=[], complete=True)
        secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(self.store.get_task("gws_task_x").status, "done")
        resolved_card = self.store.get_card(card.card_id)
        self.assertEqual(resolved_card.status, "resolved")
        self.assertEqual(resolved_card.resolved_reason, "task_done")

    def test_self_employee_id_mode_skips_other_owners(self) -> None:
        owner_a = "emp_a"
        owner_b = "emp_b"
        self._register_owner(owner_a)
        self._register_owner(owner_b)
        connector = FakeSourceConnector({owner_a: FetchResult(complete=True), owner_b: FetchResult(complete=True)})
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
            connector=connector,
        )

        with mock.patch.dict(os.environ, {"GWS_SELF_EMPLOYEE_ID": owner_a}):
            result = secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(connector.calls, [owner_a])
        self.assertEqual(result["sync_skipped_owners"], 1)

    def test_self_employee_id_mode_syncs_owner_with_no_agent_or_profile_registered(self) -> None:
        """round-14 V-11: single-owner mode's sync target is `GWS_SELF_EMPLOYEE_ID`
        itself, not a filter over agents ∪ profiles -- so it must be fetched
        even against a completely empty Store (no agents, no profiles). This is
        the mechanism design §10 goal 28's empty-`InMemoryStore` manual gate
        relies on to see any data at all.
        """
        self_owner = "emp_unregistered"
        connector = FakeSourceConnector(
            {
                self_owner: FetchResult(
                    tasks=[
                        TaskRecord(
                            source_id="gws_task_solo",
                            title="Solo task",
                            status="todo",
                            last_updated_at="2026-06-01T00:00:00Z",
                        )
                    ],
                    complete=True,
                )
            }
        )
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
            connector=connector,
        )

        self.assertEqual(self.store.list_agents(), [])
        self.assertEqual(self.store.list_profiles(), [])

        with mock.patch.dict(os.environ, {"GWS_SELF_EMPLOYEE_ID": self_owner}):
            result = secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(connector.calls, [self_owner])
        self.assertEqual(result["sync_tasks"], 1)
        self.assertEqual(result["sync_skipped_owners"], 0)
        self.assertIsNotNone(self.store.get_task("gws_task_solo"))

    def test_google_workspace_without_self_employee_id_syncs_nothing_and_records_errors(self) -> None:
        """round-14 V-11/V-13/S-11: SOURCE_CONNECTOR=google_workspace with
        GWS_SELF_EMPLOYEE_ID unset must not attribute one author's data to
        every registered owner. build_connector_from_env() must return a
        connector whose fetch() always fails instead of the real one, so
        _sync_owners' existing per-owner error handling records it in
        sync_errors and detection still runs over whatever is already in
        Store -- fail-closed without a server crash.
        """
        owner = "emp_owner"
        self._register_owner(owner)
        task = self._make_high_score_task(owner=owner)
        self.store.save_task(task)

        with mock.patch.dict(
            os.environ, {"SOURCE_CONNECTOR": "google_workspace"}, clear=False
        ):
            os.environ.pop("GWS_SELF_EMPLOYEE_ID", None)
            connector = build_connector_from_env()
            secretary = SecretaryService(
                store=self.store,
                kd_service=self.kd_service,
                matching_engine=self.matching_engine,
                t1=3.0,
                t2=7.0,
                connector=connector,
            )
            result = secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(result["sync_tasks"], 0)
        self.assertEqual(result["sync_schedules"], 0)
        self.assertEqual(result["sync_mails"], 0)
        self.assertGreaterEqual(result["sync_errors"], 1)
        # Pre-existing (seed) data is untouched and detection still ran.
        card = self.store.find_open_card_for_task(owner, task.task_id)
        self.assertIsNotNone(card)

    def test_seed_connector_never_calls_apply_fetch_result(self) -> None:
        """round-14 V-12: switching (or defaulting) to SeedConnector must not
        run apply_fetch_result at all -- not even with an empty/no-op
        FetchResult -- so it can never mark previously-synced source="gws"
        tasks done or delete source="gws" schedules, and demo mode pays no
        extra Store queries per owner.
        """
        owner = "emp_owner"
        self._register_owner(owner)

        gws_task = Task(
            task_id="gws_task_existing",
            owner_employee_id=owner,
            title="Existing gws task",
            status="todo",
            due_date="",
            created_at=TODAY_STR,
            last_updated_at=TODAY_STR,
            status_changed_at=TODAY_STR,
            source="gws",
        )
        self.store.save_task(gws_task)
        gws_schedule = Schedule(
            item_id="gws_cal_ev1_meeting_prep",
            owner_employee_id=owner,
            kind="meeting_prep",
            title="Existing gws meeting",
            due_date=TODAY_STR,
            source="gws",
        )
        self.store.save_schedule(gws_schedule)

        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
            connector=SeedConnector(),
        )

        with mock.patch(
            "knowledge_discovery.secretary.apply_fetch_result",
            side_effect=AssertionError("apply_fetch_result must not be called for SeedConnector"),
        ):
            result = secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(result["sync_tasks"], 0)
        self.assertEqual(self.store.get_task("gws_task_existing").status, "todo")
        self.assertIsNotNone(
            next(
                (s for s in self.store.list_schedules(owner_employee_id=owner) if s.item_id == "gws_cal_ev1_meeting_prep"),
                None,
            )
        )

    def test_sync_failure_for_one_owner_does_not_halt_the_sweep(self) -> None:
        owner_failing = "emp_failing"
        owner_ok = "emp_owner"
        self._register_owner(owner_failing)
        self._register_owner(owner_ok)
        self._add_matching_candidate()  # registers a separate candidate, emp_match

        connector = FakeSourceConnector({owner_failing: RuntimeError("boom")})
        secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
            connector=connector,
        )

        # Detection must still run over pre-existing (e.g. seed) data even
        # though one owner's sync raised.
        task = self._make_high_score_task(owner=owner_ok)
        self.store.save_task(task)

        result = secretary.run_sweep(demo_today=TODAY_STR)

        self.assertEqual(result["sync_errors"], 1)
        card = self.store.find_open_card_for_task(owner_ok, task.task_id)
        self.assertIsNotNone(card)
        self.assertEqual(card.tier, "request_draft")


class TestMailRetention(SecretaryTestBase):
    """§16.3 Gmail part D retention: clear processed bodies, 14-day delete.

    Runs inside run_sweep() regardless of connector (SeedConnector never
    produces mail_seeds, but retention still applies to any mail_seed
    already in Store, matching how generate_seeds.py seeds them directly).
    """

    def test_body_cleared_after_processing_same_sweep(self) -> None:
        mail = MailSeed(
            mail_id="mail_ret_1",
            owner_employee_id="emp_owner",
            subject="Update",
            body="This body is definitely longer than ten characters so the heuristic fires.",
            received_at=_iso(TODAY) + "T09:00:00Z",
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        reloaded = self.store.get_mail_seed("mail_ret_1")
        self.assertTrue(reloaded.processed)
        self.assertEqual(reloaded.body, "")

    def test_mail_at_or_past_retention_window_is_deleted(self) -> None:
        old_received = _iso(TODAY - timedelta(days=14)) + "T00:00:00Z"
        mail = MailSeed(
            mail_id="mail_ret_old",
            owner_employee_id="emp_owner",
            subject="Old",
            body="Old body text that should be purged outright.",
            received_at=old_received,
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        self.assertIsNone(self.store.get_mail_seed("mail_ret_old"))

    def test_mail_within_retention_window_is_kept(self) -> None:
        recent_received = _iso(TODAY - timedelta(days=1)) + "T00:00:00Z"
        mail = MailSeed(
            mail_id="mail_ret_recent",
            owner_employee_id="emp_owner",
            subject="Recent",
            body="Recent body text.",
            received_at=recent_received,
            processed=False,
        )
        self.store.save_mail_seed(mail)

        self.secretary.run_sweep(demo_today=TODAY_STR)

        self.assertIsNotNone(self.store.get_mail_seed("mail_ret_recent"))


if __name__ == "__main__":
    unittest.main()
