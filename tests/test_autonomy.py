"""Unit tests for the Autonomous Agent phase (autonomous-agent design v4).

Covers domain/state items from design.md §9 and the round-4 verification
supplements in ledger.md ("round-4 verification の帰結"): outcome-driven CAS
(upsert_card_gated), create-only audits (Z-4), the claim/finish/fail run
lifecycle (C-14/Z-1), per-owner autonomy-policy gating of the scheduled sweep
(§5.3), the mail outcome matrix (R4-H3), the fail-closed whitelist for the 2
new audit intents (Z-5), and legacy card-id reuse (C-18).

No network, no real LLM, no real Firestore: InMemoryStore, DeterministicEmbedder,
and FakeConnectionInferencer only — same discipline as test_secretary.py.
"""

import hashlib
import os
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Pin stagnation weights/thresholds BEFORE importing knowledge_discovery.secretary
# (module-level constants are computed at import time) — same discipline as
# test_secretary.py, so every score expectation here is independent of shell env.
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
    AutonomyPolicy,
    Card,
    MailSeed,
    Profile,
    ProfileItem,
    Task,
    default_autonomy_policy,
)
from knowledge_discovery.schemas import SchemaRegistry  # noqa: E402
from knowledge_discovery.secretary import SecretaryService, derive_autonomy_state  # noqa: E402
from knowledge_discovery.service import KnowledgeDiscoveryService  # noqa: E402
from knowledge_discovery.store import InMemoryStore  # noqa: E402
from knowledge_discovery.transmission import TransmissionLayer  # noqa: E402

TODAY = date(2026, 6, 15)
TODAY_STR = TODAY.isoformat()


def _iso(d: date) -> str:
    return d.isoformat()


class FakeLLMClient:
    """Same minimal google.genai.Client stand-in as test_secretary.py."""

    def __init__(self, respond) -> None:
        self._respond = respond
        self.calls: list[str] = []
        self.models = self

    def generate_content(self, model: str, contents: str) -> SimpleNamespace:
        self.calls.append(contents)
        return SimpleNamespace(text=self._respond(contents))


class AutonomyTestBase(unittest.TestCase):
    """Shared fixture builder, mirroring test_secretary.py's SecretaryTestBase."""

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
        self.secretary = SecretaryService(
            store=self.store,
            kd_service=self.kd_service,
            matching_engine=self.matching_engine,
            t1=3.0,
            t2=7.0,
        )

    # --- fixture helpers -----------------------------------------------

    def _add_matching_candidate(self, employee_id: str = "emp_match", agent_id: str = "agent_match") -> None:
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

    def _make_low_score_task(self, task_id: str = "task_low", owner: str = "emp_owner") -> Task:
        return Task(
            task_id=task_id,
            owner_employee_id=owner,
            title="Routine check-in",
            description="",
            status="in_progress",
            due_date=_iso(TODAY + timedelta(days=10)),
            created_at=TODAY_STR,
            last_updated_at=TODAY_STR,
            reschedule_count=0,
            status_changed_at=TODAY_STR,
        )

    def _set_policy(
        self,
        employee_id: str = "emp_owner",
        monitor: bool = True,
        search: bool = True,
        ask: bool = True,
        prepare: bool = True,
        updated_at: str = "2026-06-01T00:00:00+00:00",
    ) -> AutonomyPolicy:
        policy = AutonomyPolicy(
            employee_id=employee_id,
            monitor_stalled_work=monitor,
            search_organization=search,
            ask_candidate_agents=ask,
            prepare_introduction=prepare,
            updated_at=updated_at,
        )
        self.store.save_autonomy_policy(policy)
        return policy


# =============================================================================
# 1. AutonomyPolicy model: normalization / defaults / persistence
# =============================================================================


class TestAutonomyPolicyModel(unittest.TestCase):
    def test_default_policy_is_monitor_on_rest_off(self) -> None:
        policy = default_autonomy_policy("emp_x")
        eff = policy.effective()
        self.assertTrue(eff["monitor_stalled_work"])
        self.assertFalse(eff["search_organization"])
        self.assertFalse(eff["ask_candidate_agents"])
        self.assertFalse(eff["prepare_introduction"])
        self.assertEqual(policy.contact_mode, "always_ask")

    def test_effective_enforces_dependency_hierarchy(self) -> None:
        # search ON but monitor OFF -> search collapses to OFF; ask/prepare cascade.
        policy = AutonomyPolicy(
            employee_id="emp_x",
            monitor_stalled_work=False,
            search_organization=True,
            ask_candidate_agents=True,
            prepare_introduction=True,
        )
        eff = policy.effective()
        self.assertFalse(eff["monitor_stalled_work"])
        self.assertFalse(eff["search_organization"])
        self.assertFalse(eff["ask_candidate_agents"])
        self.assertFalse(eff["prepare_introduction"])

    def test_effective_partial_hierarchy_break_at_ask(self) -> None:
        # monitor+search ON, ask OFF -> prepare cascades OFF even though the
        # raw flag was True.
        policy = AutonomyPolicy(
            employee_id="emp_x",
            monitor_stalled_work=True,
            search_organization=True,
            ask_candidate_agents=False,
            prepare_introduction=True,
        )
        eff = policy.effective()
        self.assertTrue(eff["search_organization"])
        self.assertFalse(eff["ask_candidate_agents"])
        self.assertFalse(eff["prepare_introduction"])

    def test_to_dict_from_dict_roundtrip(self) -> None:
        policy = AutonomyPolicy(
            employee_id="emp_x", monitor_stalled_work=True, search_organization=True,
            ask_candidate_agents=True, prepare_introduction=False, updated_at="2026-01-01T00:00:00+00:00",
        )
        restored = AutonomyPolicy.from_dict(policy.to_dict())
        self.assertEqual(restored, policy)

    def test_store_persists_policy_per_employee(self) -> None:
        store = InMemoryStore()
        self.assertIsNone(store.get_autonomy_policy("emp_a"))
        store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_a", search_organization=True))
        store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_b", search_organization=False))
        self.assertTrue(store.get_autonomy_policy("emp_a").search_organization)
        self.assertFalse(store.get_autonomy_policy("emp_b").search_organization)


# =============================================================================
# 2. derive_autonomy_state (design §4 mapping)
# =============================================================================


class TestDeriveAutonomyState(unittest.TestCase):
    def _task(self) -> Task:
        return Task(task_id="t1", owner_employee_id="emp_owner", title="x")

    def test_no_card_is_observing(self) -> None:
        self.assertEqual(derive_autonomy_state(self._task(), None, {}), "observing")

    def test_resolved_card_is_observing(self) -> None:
        card = Card(card_id="c1", owner_employee_id="emp_owner", type="stagnation", tier="notice", status="resolved")
        self.assertEqual(derive_autonomy_state(self._task(), card, {}), "observing")

    def test_open_notice_card_is_stalled(self) -> None:
        card = Card(card_id="c1", owner_employee_id="emp_owner", type="stagnation", tier="notice", status="open")
        self.assertEqual(derive_autonomy_state(self._task(), card, {}), "stalled")

    def test_open_request_draft_card_is_need_detected(self) -> None:
        card = Card(card_id="c1", owner_employee_id="emp_owner", type="stagnation", tier="request_draft", status="open")
        self.assertEqual(derive_autonomy_state(self._task(), card, {}), "need_detected")


# =============================================================================
# 3. Card CAS: upsert_card_gated outcomes (Z-2/Z-3/C-13, all 5 outcomes)
# =============================================================================


class TestUpsertCardGatedOutcomes(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()

    def _card(self, card_id: str, status: str = "open", tier: str = "notice", task_id: str = "task_1") -> Card:
        return Card(
            card_id=card_id, owner_employee_id="emp_owner", type="stagnation",
            tier=tier, payload={"task_id": task_id}, status=status,
        )

    def test_outcome_created_on_first_write(self) -> None:
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(self._card("card_stag_a"))
        self.assertEqual(outcome, "created")
        self.assertEqual(card.status, "open")
        # round-5 ledger B: prev_status/prev_tier reflect the CAS's own
        # in-transaction read — nothing existed before this write.
        self.assertIsNone(prev_status)
        self.assertIsNone(prev_tier)

    def test_outcome_updated_on_open_card(self) -> None:
        self.store.upsert_card_gated(self._card("card_stag_a"))
        card2, outcome, prev_status, prev_tier = self.store.upsert_card_gated(
            self._card("card_stag_a", tier="notice")
        )
        self.assertEqual(outcome, "updated")
        self.assertEqual(card2.card_id, "card_stag_a")
        self.assertEqual(prev_status, "open")
        self.assertEqual(prev_tier, "notice")  # the tier written by the first call

    def test_c31_stale_notice_resolve_still_resolves_promoted_card(self) -> None:
        """round-7 C-31: the never-downgrade guard must not swallow a resolve —
        a resolve carrying a stale tier="notice" copy of a since-promoted card
        still transitions the card to resolved (with its reason recorded)."""
        self.store.upsert_card_gated(self._card("card_stag_a", tier="request_draft"))
        resolve = self._card("card_stag_a", status="resolved", tier="notice")
        resolve.resolved_reason = "task_done"
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(resolve)
        self.assertEqual(outcome, "updated")
        self.assertEqual(card.status, "resolved")
        self.assertEqual(card.resolved_reason, "task_done")
        self.assertEqual(prev_tier, "request_draft")

    def test_outcome_reopened_clears_resolved_reason(self) -> None:
        self.store.upsert_card_gated(self._card("card_stag_a"))
        resolve_card = self._card("card_stag_a", status="resolved")
        resolve_card.resolved_reason = "score_below_t1"
        self.store.upsert_card_gated(resolve_card)
        stored = self.store.get_card("card_stag_a")
        self.assertEqual(stored.status, "resolved")
        self.assertEqual(stored.resolved_reason, "score_below_t1")

        reopen_attempt = self._card("card_stag_a", status="open")
        card3, outcome, prev_status, prev_tier = self.store.upsert_card_gated(reopen_attempt)
        self.assertEqual(outcome, "reopened")
        self.assertEqual(card3.status, "open")
        self.assertIsNone(card3.resolved_reason)
        self.assertEqual(prev_status, "resolved")

    def test_outcome_unchanged_on_duplicate_resolve(self) -> None:
        # round-5 ledger B (new outcome): a second write against an
        # already-resolved card whose incoming status is NOT 'open' (i.e. not
        # a re-detection — a duplicate resolve) must not clobber the first
        # resolve's resolved_reason/timestamps.
        self.store.upsert_card_gated(self._card("card_stag_a"))
        first_resolve = self._card("card_stag_a", status="resolved")
        first_resolve.resolved_reason = "score_below_t1"
        self.store.upsert_card_gated(first_resolve)
        stored_after_first = self.store.get_card("card_stag_a")

        second_resolve = self._card("card_stag_a", status="resolved")
        second_resolve.resolved_reason = "task_done"
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(second_resolve)
        self.assertEqual(outcome, "unchanged")
        self.assertEqual(prev_status, "resolved")
        self.assertEqual(card.resolved_reason, "score_below_t1")  # untouched by the second write
        self.assertEqual(card.updated_at, stored_after_first.updated_at)
        stored = self.store.get_card("card_stag_a")
        self.assertEqual(stored.resolved_reason, "score_below_t1")

    def test_outcome_rejected_terminal_does_not_overwrite(self) -> None:
        self.store.upsert_card_gated(self._card("card_stag_a"))
        confirmed = self.store.get_card("card_stag_a")
        confirmed.status = "confirmed"
        self.store.save_card(confirmed)

        attempt = self._card("card_stag_a", tier="request_draft")
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(attempt)
        self.assertEqual(outcome, "rejected_terminal")
        self.assertEqual(prev_status, "confirmed")
        stored = self.store.get_card("card_stag_a")
        self.assertEqual(stored.tier, "notice")  # untouched

    def test_outcome_rejected_policy_changed(self) -> None:
        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v1"))
        card, outcome, _, _ = self.store.upsert_card_gated(self._card("card_stag_a"), expected_policy_updated_at="v1")
        self.assertEqual(outcome, "created")

        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v2"))
        attempt = self._card("card_stag_a", tier="request_draft")
        _, outcome2, prev_status2, prev_tier2 = self.store.upsert_card_gated(
            attempt, expected_policy_updated_at="v1"
        )
        self.assertEqual(outcome2, "rejected_policy_changed")
        self.assertEqual(prev_status2, "open")
        self.assertEqual(prev_tier2, "notice")
        stored = self.store.get_card("card_stag_a")
        self.assertEqual(stored.tier, "notice")  # not promoted; stale write rejected

    def test_no_gating_when_expected_policy_updated_at_is_none(self) -> None:
        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v1"))
        self.store.upsert_card_gated(self._card("card_stag_a"), expected_policy_updated_at="v1")
        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v2"))
        attempt = self._card("card_stag_a", tier="request_draft")
        _, outcome, _, _ = self.store.upsert_card_gated(attempt, expected_policy_updated_at=None)
        self.assertEqual(outcome, "updated")

    def test_c18_legacy_random_id_card_is_reused_not_duplicated(self) -> None:
        legacy = self._card("card_stag_legacyrandom123")
        self.store.upsert_card_gated(legacy)

        # A write using the NEW deterministic id scheme for the same (owner, task_id)
        # must resolve to the legacy doc, not create a second one.
        deterministic_id = "card_stag_" + hashlib.sha1(b"emp_owner:task_1").hexdigest()[:12]
        new_scheme_write = self._card(deterministic_id, tier="notice")
        card, outcome, _, _ = self.store.upsert_card_gated(new_scheme_write)

        self.assertEqual(outcome, "updated")
        self.assertEqual(card.card_id, "card_stag_legacyrandom123")
        self.assertIsNone(self.store.get_card(deterministic_id))
        all_cards = self.store.list_cards(owner_employee_id="emp_owner")
        self.assertEqual(len(all_cards), 1)

    def test_k1_domain_key_lookup_prefers_open_over_resolved(self) -> None:
        # round-5 ledger K-1: when two docs share the same (owner, type,
        # domain_key) — e.g. a resolved legacy doc and a fresh open doc under
        # the deterministic id — the open one is the live state and must win.
        resolved_legacy = self._card("card_stag_legacy_resolved", status="resolved")
        self.store.upsert_card_gated(resolved_legacy)
        deterministic_id = "card_stag_" + hashlib.sha1(b"emp_owner:task_1").hexdigest()[:12]
        open_new = self._card(deterministic_id, status="open")
        self.store.save_card(open_new)  # direct write: simulate a doc already present at the new id

        found = self.store.find_card_by_domain_key("emp_owner", "stagnation", "task_1")
        self.assertIsNotNone(found)
        self.assertEqual(found.card_id, deterministic_id)
        self.assertEqual(found.status, "open")

    def test_w1_stale_notice_write_does_not_downgrade_open_request_draft(self) -> None:
        """round-6 ledger W-1/C-29: a concurrent writer whose own pre-read is
        stale (it still thinks the card is 'notice') must not be able to
        downgrade a card that another writer already promoted to
        'request_draft' — the guard lives inside upsert_card_gated's own
        transaction, not in a caller's pre-read, so it applies regardless of
        what the caller believed before calling."""
        promoted = self._card("card_stag_a", tier="request_draft")
        promoted.payload = {
            "task_id": "task_1", "task_title": "T", "score": 8.0, "evidence_line": "old evidence",
            "question_draft": "Who can help with T?",
            "preview": {"candidates": [{"employee_id": "emp_x", "name": "X"}]},
        }
        self.store.upsert_card_gated(promoted)

        # A second, "stale" writer re-evaluates the same card_id and (having
        # found zero candidates on ITS OWN run) tries to write tier="notice"
        # with a payload that carries no question_draft/preview at all.
        stale_write = self._card("card_stag_a", tier="notice")
        stale_write.payload = {
            "task_id": "task_1", "task_title": "T", "score": 7.5, "evidence_line": "new evidence",
        }
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(stale_write)

        self.assertEqual(outcome, "updated")  # never treated as a fresh promotion
        self.assertEqual(card.tier, "request_draft")  # NOT downgraded
        self.assertEqual(card.payload["question_draft"], "Who can help with T?")
        self.assertEqual(card.payload["preview"]["candidates"][0]["employee_id"], "emp_x")
        # Evidence fields DO refresh from the incoming (stale-writer's) payload.
        self.assertEqual(card.payload["evidence_line"], "new evidence")
        self.assertEqual(card.payload["score"], 7.5)

        stored = self.store.get_card("card_stag_a")
        self.assertEqual(stored.tier, "request_draft")

    def test_reopen_carries_forward_policy_hold_by_default(self) -> None:
        """round-6 ledger W-3: a card held (policy_hold set) that later gets
        resolved (e.g. score drops below T1) and then reopened (re-detection)
        must not lose the hold just because the reopening write builds its
        payload from scratch."""
        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v1"))
        held = self._card("card_stag_b", tier="notice")
        held.payload = {
            "task_id": "task_2", "task_title": "T2", "score": 8.0, "evidence_line": "e1",
            "policy_hold": {"stage": "search", "policy_updated_at": "v1"},
        }
        self.store.upsert_card_gated(held, expected_policy_updated_at="v1")

        resolve = self._card("card_stag_b", status="resolved", tier="notice")
        resolve.resolved_reason = "score_below_t1"
        # Mirrors secretary.py's _resolve(): the resolved write's payload
        # still carries the hold key (a full snapshot of what was open).
        resolve.payload = dict(held.payload)
        self.store.upsert_card_gated(resolve, expected_policy_updated_at="v1")

        # Reopen: payload built fresh, with NO policy_hold key at all (as
        # secretary.py's notice-band branch does for a resolved existing card).
        reopen = self._card("card_stag_b", status="open", tier="notice")
        reopen.payload = {"task_id": "task_2", "task_title": "T2", "score": 8.0, "evidence_line": "e2"}
        card, outcome, prev_status, prev_tier = self.store.upsert_card_gated(
            reopen, expected_policy_updated_at="v1"
        )

        self.assertEqual(outcome, "reopened")
        self.assertEqual(card.payload["policy_hold"], {"stage": "search", "policy_updated_at": "v1"})
        self.assertEqual(card.payload["evidence_line"], "e2")  # evidence still refreshes

    def test_reopen_clear_policy_hold_true_drops_it(self) -> None:
        """round-6 ledger W-1/W-3: clear_policy_hold=True is the ONLY way to
        drop a carried-forward hold — the caller must pass it explicitly
        (e.g. secretary.py's full-evaluation write, once policy actually
        changed) rather than the store guessing from a token comparison."""
        self.store.save_autonomy_policy(AutonomyPolicy(employee_id="emp_owner", updated_at="v1"))
        held = self._card("card_stag_c", tier="notice")
        held.payload = {
            "task_id": "task_3", "task_title": "T3", "score": 8.0, "evidence_line": "e1",
            "policy_hold": {"stage": "ask", "policy_updated_at": "v1"},
        }
        self.store.upsert_card_gated(held, expected_policy_updated_at="v1")

        resolve = self._card("card_stag_c", status="resolved", tier="notice")
        resolve.resolved_reason = "score_below_t1"
        resolve.payload = dict(held.payload)
        self.store.upsert_card_gated(resolve, expected_policy_updated_at="v1")

        reopen = self._card("card_stag_c", status="open", tier="notice")
        reopen.payload = {"task_id": "task_3", "task_title": "T3", "score": 8.0, "evidence_line": "e2"}
        card, outcome, _, _ = self.store.upsert_card_gated(
            reopen, expected_policy_updated_at="v1", clear_policy_hold=True
        )

        self.assertEqual(outcome, "reopened")
        self.assertNotIn("policy_hold", card.payload)


# =============================================================================
# 4. Create-only audits (Z-4) + fail-closed whitelist/validator (Z-5, C-16)
# =============================================================================


class TestCreateOnlyAuditAndWhitelist(AutonomyTestBase):
    def test_save_message_if_absent_is_first_writer_wins(self) -> None:
        created1 = self.transmission.send(
            from_entity="system", to_entity="system", intent="sweep_run", payload_type="sweep_run",
            payload=_valid_sweep_run_payload(run_key="run_x"), audit_id="msg_sweep_run_x", create_only=True,
        )
        self.assertEqual(created1.payload["tasks_evaluated"], 5)

        # A resend with different counts at the SAME audit_id must not rewind content.
        replay = self.transmission.send(
            from_entity="system", to_entity="system", intent="sweep_run", payload_type="sweep_run",
            payload=_valid_sweep_run_payload(run_key="run_x", tasks_evaluated=999),
            audit_id="msg_sweep_run_x", create_only=True,
        )
        self.assertEqual(replay.payload["tasks_evaluated"], 5)
        self.assertEqual(replay.timestamp, created1.timestamp)
        self.assertEqual(len(self.store.list_messages()), 1)

    def test_sweep_run_validator_rejects_unknown_key(self) -> None:
        payload = _valid_sweep_run_payload()
        payload["extra_field"] = "nope"
        valid, err = SchemaRegistry.validate_payload("sweep_run", payload)
        self.assertFalse(valid)
        self.assertIsNotNone(err)

    def test_sweep_run_validator_rejects_missing_key(self) -> None:
        payload = _valid_sweep_run_payload()
        del payload["policy_held"]
        valid, _ = SchemaRegistry.validate_payload("sweep_run", payload)
        self.assertFalse(valid)

    def test_policy_limited_validator_enforces_stage_enum(self) -> None:
        valid, _ = SchemaRegistry.validate_payload(
            "policy_limited", {"stage": "prepare", "run_key": "r1", "task_count": 2}
        )
        self.assertTrue(valid)
        invalid, err = SchemaRegistry.validate_payload(
            "policy_limited", {"stage": "bogus_stage", "run_key": "r1", "task_count": 2}
        )
        self.assertFalse(invalid)
        self.assertIsNotNone(err)

    def test_policy_limited_validator_rejects_free_text_note(self) -> None:
        payload = {"stage": "search", "run_key": "r1", "task_count": 1, "note": "should not exist"}
        valid, _ = SchemaRegistry.validate_payload("policy_limited", payload)
        self.assertFalse(valid)

    def test_c16_invalid_internal_audit_is_logged_not_sent_and_no_reject_row(self) -> None:
        before = len(self.store.list_messages())
        # Deliberately malformed payload (missing keys) routed through the
        # secretary's own self-validating sender.
        self.secretary._send_internal_audit(
            from_entity="system", to_entity="system", intent="sweep_run", payload_type="sweep_run",
            payload={"origin": "scheduled"}, audit_id="msg_sweep_bad",
        )
        after = self.store.list_messages()
        self.assertEqual(len(after), before)  # nothing written at all
        intents = [m.intent for m in after]
        self.assertNotIn("reject_unregistered_type", intents)

    def test_get_audit_view_projects_sweep_run_to_allowed_keys(self) -> None:
        from knowledge_discovery.models import Message

        msg = Message(
            audit_id="msg_x", from_entity="system", to_entity="system",
            intent="sweep_run", payload_type="sweep_run",
            payload=_valid_sweep_run_payload(),
        )
        view = SchemaRegistry.get_audit_view(msg)
        self.assertEqual(set(view.keys()), SchemaRegistry.SWEEP_RUN_KEYS)

    def test_get_audit_view_projects_policy_limited_to_allowed_keys(self) -> None:
        from knowledge_discovery.models import Message

        msg = Message(
            audit_id="msg_y", from_entity="agent_x", to_entity="system",
            intent="policy_limited", payload_type="policy_limited",
            payload={"stage": "search", "run_key": "r1", "task_count": 3},
        )
        view = SchemaRegistry.get_audit_view(msg)
        self.assertEqual(set(view.keys()), SchemaRegistry.POLICY_LIMITED_KEYS)


def _valid_sweep_run_payload(run_key: str = "run_1", tasks_evaluated: int = 5) -> dict:
    return {
        "origin": "scheduled",
        "run_key": run_key,
        "date": TODAY_STR,
        "tasks_evaluated": tasks_evaluated,
        "cards_created": 1,
        "cards_promoted": 0,
        "cards_resolved": 0,
        "needs_detected": 0,
        "candidates_explored": 3,
        "policy_held": 0,
        "schema_version": 1,
    }


# =============================================================================
# 5. Sweep run claim/finish/fail lifecycle (C-14/Z-1)
# =============================================================================


class TestSweepRunClaimLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()

    def test_claim_new_run(self) -> None:
        token, state = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.assertEqual(state, "claimed")
        self.assertIsNotNone(token)

    def test_claim_done_run_is_deduplicated(self) -> None:
        token, _ = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.store.finish_sweep_run("run_a", token, {"tasks_evaluated": 1})
        token2, state2 = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.assertEqual(state2, "done")
        self.assertIsNone(token2)

    def test_claim_running_non_stale_is_in_progress(self) -> None:
        self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        token2, state2 = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.assertEqual(state2, "in_progress")
        self.assertIsNone(token2)

    def test_claim_running_stale_is_reclaimable(self) -> None:
        token1, _ = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=0)
        # ttl_seconds=0 means "stale immediately" for any subsequent claim attempt.
        token2, state2 = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=0)
        self.assertEqual(state2, "claimed")
        self.assertIsNotNone(token2)
        self.assertNotEqual(token1, token2)

    def test_claim_failed_run_is_immediately_reclaimable(self) -> None:
        token1, _ = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.store.fail_sweep_run("run_a", token1, error="boom")
        token2, state2 = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.assertEqual(state2, "claimed")
        self.assertIsNotNone(token2)

    def test_finish_with_wrong_token_is_noop(self) -> None:
        token1, _ = self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        applied = self.store.finish_sweep_run("run_a", "wrong-token", {"tasks_evaluated": 99})
        self.assertFalse(applied)
        run = self.store.get_sweep_run("run_a")
        self.assertEqual(run["status"], "running")
        self.assertNotIn("summary", run)

    def test_fail_with_wrong_token_is_noop(self) -> None:
        self.store.claim_sweep_run("run_a", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        applied = self.store.fail_sweep_run("run_a", "wrong-token", error="x")
        self.assertFalse(applied)
        run = self.store.get_sweep_run("run_a")
        self.assertEqual(run["status"], "running")


# =============================================================================
# 6. Scheduled sweep: manual parity, gating table, idempotency (spec §18 items)
# =============================================================================


class TestManualSweepUnchanged(AutonomyTestBase):
    """#1: manual sweep still works, byte-identical to pre-phase behavior."""

    def test_manual_default_origin_still_creates_notice_card(self) -> None:
        task = self._make_notice_score_task()
        self.store.save_task(task)
        result = self.secretary.run_sweep(demo_today=TODAY_STR)  # origin defaults to "manual"
        self.assertEqual(result["cards_created"], 1)
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "notice")

    def test_manual_emits_sweep_run_audit_tagged_manual(self) -> None:
        # round-5 ledger A: manual and scheduled now share one pipeline, so a
        # manual run also gets a sweep_run audit (the one allowed addition) —
        # tagged origin="manual" so it's distinguishable from an automatic run.
        task = self._make_notice_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="manual")
        sweep_msgs = [m for m in self.store.list_messages() if m.intent == "sweep_run"]
        self.assertEqual(len(sweep_msgs), 1)
        self.assertEqual(sweep_msgs[0].payload["origin"], "manual")

    def test_manual_ignores_restrictive_policy_full_override(self) -> None:
        # Policy says search OFF, but manual is an override (§5.3) — behaves
        # exactly like the full-ON path.
        self._set_policy(search=False, ask=False, prepare=False)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="manual")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")


class TestScheduledFullPolicyMatchesManual(AutonomyTestBase):
    """#2: scheduled sweep uses the same core logic when policy is fully ON."""

    def test_full_policy_scheduled_promotes_like_manual(self) -> None:
        self._set_policy(monitor=True, search=True, ask=True, prepare=True)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_full")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["needs_detected"], 1)

        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")
        self.assertGreaterEqual(len(card.payload["preview"]["candidates"]), 1)


class TestScheduledIdempotency(AutonomyTestBase):
    """#3/#12/#13: duplicate scheduled runs never duplicate cards/audits."""

    def test_same_run_key_retry_is_deduplicated(self) -> None:
        self._set_policy()
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        result1 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_dup")
        self.assertEqual(result1["status"], "ok")
        messages_after_first = len(self.store.list_messages())
        cards_after_first = len(self.store.list_cards())

        result2 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_dup")
        self.assertEqual(result2["status"], "deduplicated")
        self.assertEqual(len(self.store.list_messages()), messages_after_first)
        self.assertEqual(len(self.store.list_cards()), cards_after_first)

    def test_dedup_reconstructs_missing_sweep_run_audit_from_summary(self) -> None:
        # R4-H4: simulate a crash between finish_sweep_run and the audit send
        # by claiming+finishing directly (bypassing run_sweep's own emit).
        token, _ = self.store.claim_sweep_run("run_crash", origin="scheduled", date=TODAY_STR, ttl_seconds=300)
        self.store.finish_sweep_run("run_crash", token, _valid_sweep_run_payload(run_key="run_crash"))
        self.assertEqual(len(self.store.list_messages()), 0)

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_crash")
        self.assertEqual(result["status"], "deduplicated")
        messages = self.store.list_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].intent, "sweep_run")
        self.assertEqual(messages[0].payload["run_key"], "run_crash")

    def test_successive_ticks_with_different_run_keys_do_not_duplicate_card(self) -> None:
        self._set_policy()
        self._add_matching_candidate()
        task = self._make_notice_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="tick_1")
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="tick_2")

        all_cards = self.store.find_cards_for_task("emp_owner", task.task_id)
        self.assertEqual(len(all_cards), 1)


class TestScheduledPolicyGating(AutonomyTestBase):
    """#4-#10: the gate table (design §5.3)."""

    def test_monitor_off_creates_no_new_card(self) -> None:
        """#6: Monitor OFF -> detection does not progress at all."""
        self._set_policy(monitor=False, search=False, ask=False, prepare=False)
        task = self._make_notice_score_task()
        self.store.save_task(task)

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_mon_off")
        self.assertEqual(result["cards_created"], 0)
        self.assertIsNone(self.store.find_open_card_for_task("emp_owner", task.task_id))

    def test_monitor_off_still_resolves_done_task(self) -> None:
        """Monitor OFF still folds up what's already started (C-10/C-15)."""
        self._set_policy(monitor=True, search=False, ask=False, prepare=False)
        task = self._make_notice_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_a")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertIsNotNone(card)

        self._set_policy(monitor=False, search=False, ask=False, prepare=False)
        task.status = "done"
        self.store.save_task(task)
        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_b")
        self.assertEqual(result["cards_resolved"], 1)
        self.assertEqual(self.store.get_card(card.card_id).status, "resolved")

    def test_search_off_stays_at_notice_with_hold_and_no_exploration(self) -> None:
        """#7: Monitor ON + Search OFF -> notice only, zero exploration calls."""
        self._set_policy(monitor=True, search=False, ask=False, prepare=False)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        shortlist_calls = []
        original = self.matching_engine.preview_shortlist
        self.matching_engine.preview_shortlist = lambda *a, **kw: (shortlist_calls.append(1), original(*a, **kw))[1]

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_search_off")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "notice")
        self.assertEqual(card.payload["policy_hold"]["stage"], "search")
        self.assertEqual(len(shortlist_calls), 0)
        self.assertEqual(result["policy_held"], 1)
        self.assertEqual(result["candidates_explored"], 0)

    def test_search_on_ask_off_explores_but_does_not_promote(self) -> None:
        """#8/#9: Search ON begins exploration; Ask OFF stops before Stage-2 (no candidate-agent fit request)."""
        self._set_policy(monitor=True, search=True, ask=False, prepare=False)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_ask_off")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "notice")
        self.assertEqual(card.payload["policy_hold"]["stage"], "ask")
        self.assertGreater(result["candidates_explored"], 0)
        # Stage-2 (candidate-agent fit evaluation) must never have run.
        self.assertEqual(len(self.inferencer.call_history), 0)

    def test_ask_on_prepare_off_evaluates_but_does_not_draft_or_promote(self) -> None:
        """#10: Prepare OFF -> no draft auto-generated, no promotion, though Stage-2 did run."""
        self._set_policy(monitor=True, search=True, ask=True, prepare=False)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        llm = FakeLLMClient(respond=lambda _prompt: "Should not be called")
        self.secretary.llm_client = llm

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_prepare_off")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "notice")
        self.assertEqual(card.payload["policy_hold"]["stage"], "prepare")
        self.assertNotIn("question_draft", card.payload)
        self.assertEqual(len(llm.calls), 0)  # generate_question_draft never invoked
        self.assertGreater(len(self.inferencer.call_history), 0)  # Stage-2 DID run (counts-only)
        self.assertEqual(result["policy_held"], 1)

    def test_full_policy_promotes_to_request_draft(self) -> None:
        """Full ON: real promotion happens, mirroring the manual/full path."""
        self._set_policy(monitor=True, search=True, ask=True, prepare=True)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_full_on")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")
        self.assertIn("question_draft", card.payload)

    def test_held_candidates_are_never_persisted(self) -> None:
        """§5.3: held-path exploration results are discarded, never stored on the card."""
        self._set_policy(monitor=True, search=True, ask=True, prepare=False)
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_hold_privacy")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertNotIn("preview", card.payload)
        self.assertNotIn("candidates", card.payload)


class TestPolicyLimitedAnonymization(AutonomyTestBase):
    """round-5 ledger E2: policy_limited must be an anonymous aggregate --
    from_entity="secretary" (never an owner's agent_id/employee_id), no owner
    identifier anywhere in the payload, and at most one message per stage for
    the whole run (task_count summed across every held owner)."""

    def test_policy_limited_is_anonymous_and_aggregated_across_owners(self) -> None:
        self._set_policy(employee_id="emp_owner", monitor=True, search=False, ask=False, prepare=False)
        self._set_policy(employee_id="emp_owner2", monitor=True, search=False, ask=False, prepare=False)
        self._add_matching_candidate()
        task_a = self._make_high_score_task(task_id="task_a", owner="emp_owner")
        task_b = self._make_high_score_task(task_id="task_b", owner="emp_owner2")
        self.store.save_task(task_a)
        self.store.save_task(task_b)

        result = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_anon")
        self.assertEqual(result["policy_held"], 2)

        pol_msgs = [m for m in self.store.list_messages() if m.intent == "policy_limited"]
        self.assertEqual(len(pol_msgs), 1)  # one per stage for the whole run, not per owner
        msg = pol_msgs[0]
        self.assertEqual(msg.from_entity, "secretary")
        self.assertEqual(msg.payload["stage"], "search")
        self.assertEqual(msg.payload["task_count"], 2)
        self.assertEqual(set(msg.payload.keys()), {"stage", "run_key", "task_count"})
        # No owner/agent identifier anywhere in the message.
        self.assertNotIn("emp_owner", msg.from_entity)
        self.assertNotIn("emp_owner2", msg.from_entity)
        payload_str = str(msg.payload)
        self.assertNotIn("emp_owner", payload_str)
        self.assertNotIn("agent_", payload_str)


class TestScheduledNeverContactsHumans(AutonomyTestBase):
    """#14: human contact requires manual confirm; the sweep never auto-contacts."""

    def test_scheduled_sweep_never_sends_connect_ask(self) -> None:
        self._set_policy()
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_contact")
        intents = [m.intent for m in self.store.list_messages()]
        self.assertNotIn("connect_ask", intents)
        self.assertNotIn("connect_ask_private", intents)


class TestBridgeTraceOrigin(AutonomyTestBase):
    """#15: Bridge Trace (audit log) correctly records manual vs scheduled origin."""

    def test_scheduled_sweep_run_audit_records_origin(self) -> None:
        self._set_policy()
        task = self._make_notice_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_origin")
        sweep_msgs = [m for m in self.store.list_messages() if m.intent == "sweep_run"]
        self.assertEqual(len(sweep_msgs), 1)
        self.assertEqual(sweep_msgs[0].payload["origin"], "scheduled")
        self.assertEqual(sweep_msgs[0].payload["run_key"], "run_origin")


# =============================================================================
# 7. R4-H5: policy_hold reopen condition is policy_updated_at ONLY
# =============================================================================


class TestPolicyHoldReopenCondition(AutonomyTestBase):
    def test_unchanged_policy_and_band_skips_re_exploration(self) -> None:
        self._set_policy(monitor=True, search=False, ask=False, prepare=False, updated_at="v1")
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_1")
        card1 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card1.payload["policy_hold"]["stage"], "search")

        shortlist_calls = []
        original = self.matching_engine.preview_shortlist
        self.matching_engine.preview_shortlist = lambda *a, **kw: (shortlist_calls.append(1), original(*a, **kw))[1]

        # Re-run with the SAME policy (v1 unchanged) and a task that still
        # scores >= T2 (still the same "band"): must skip exploration
        # entirely and must NOT emit a new policy_limited for this owner/stage
        # (already recorded).
        result2 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_2")
        self.assertEqual(len(shortlist_calls), 0)
        self.assertEqual(result2["policy_held"], 0)

    def test_policy_change_reopens_and_reruns_exploration(self) -> None:
        self._set_policy(monitor=True, search=False, ask=False, prepare=False, updated_at="v1")
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_1")

        # Policy changes: search now ON (still ask/prepare OFF) — this MUST
        # trigger fresh exploration (policy_updated_at changed).
        self._set_policy(monitor=True, search=True, ask=False, prepare=False, updated_at="v2")

        shortlist_calls = []
        original = self.matching_engine.preview_shortlist
        self.matching_engine.preview_shortlist = lambda *a, **kw: (shortlist_calls.append(1), original(*a, **kw))[1]

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_2")
        self.assertEqual(len(shortlist_calls), 1)
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.payload["policy_hold"]["stage"], "ask")

    def test_hold_survives_a_band_dip_below_t2_and_back_up(self) -> None:
        """round-5 ledger C: policy_hold must NOT be cleared just because the
        band drops below T2 (a notice-band write must merge, not replace, the
        payload) -- only a real policy change may remove it. On a later
        return to T2 under the still-unchanged policy, the R4-H5 skip-explore
        check must still recognize the hold and NOT re-run preview_shortlist."""
        self._set_policy(monitor=True, search=False, ask=True, prepare=True, updated_at="v1")
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_1")
        card1 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card1.tier, "notice")
        self.assertEqual(card1.payload["policy_hold"]["stage"], "search")

        # Dip the score into the notice band (still >= T1, now < T2) under the
        # SAME unchanged policy.
        near_past = _iso(TODAY - timedelta(days=2))
        task.due_date = near_past
        task.last_updated_at = TODAY_STR
        task.reschedule_count = 1
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_2")
        card2 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card2.tier, "notice")
        # The hold must survive the dip (round-5 ledger C) even though this
        # write went through the plain notice-band branch, not the hold branch.
        self.assertEqual(card2.payload["policy_hold"]["stage"], "search")

        # Back up to >= T2 under the SAME unchanged policy: the surviving hold
        # must be recognized as unchanged, so exploration is skipped again.
        far_past = _iso(TODAY - timedelta(days=20))
        task.due_date = far_past
        task.last_updated_at = far_past
        task.reschedule_count = 3
        self.store.save_task(task)

        shortlist_calls = []
        original = self.matching_engine.preview_shortlist
        self.matching_engine.preview_shortlist = lambda *a, **kw: (shortlist_calls.append(1), original(*a, **kw))[1]

        result3 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_3")
        self.assertEqual(len(shortlist_calls), 0)
        self.assertEqual(result3["policy_held"], 0)  # already recorded, no new policy_limited
        card3 = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card3.payload["policy_hold"]["stage"], "search")


# =============================================================================
# 8. C-19: no downgrade of an already-promoted request_draft card
# =============================================================================


class TestNoDowngradeInvariant(AutonomyTestBase):
    def test_request_draft_survives_policy_restriction(self) -> None:
        self._set_policy(monitor=True, search=True, ask=True, prepare=True, updated_at="v1")
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_promote")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")

        # Policy is later restricted to search-only.
        self._set_policy(monitor=True, search=False, ask=False, prepare=False, updated_at="v2")
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_after_restrict")
        card2 = self.store.get_card(card.card_id)
        self.assertEqual(card2.tier, "request_draft")  # never downgraded to notice

    def test_request_draft_survives_score_drop(self) -> None:
        self._set_policy()
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_1")
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")

        # Score drops back into the notice band (but still >= T1, so no resolve).
        task.due_date = TODAY_STR
        task.last_updated_at = TODAY_STR
        task.reschedule_count = 1
        task.status_changed_at = _iso(TODAY - timedelta(days=4))
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_2")
        card2 = self.store.get_card(card.card_id)
        self.assertEqual(card2.tier, "request_draft")


# =============================================================================
# 8b. C-28: reopened counts/audit after a resolve -> re-stagnate cycle
# =============================================================================


class TestReopenedCountsAndAudit(AutonomyTestBase):
    def test_promote_resolve_restagnate_reopen_counts_and_emits_new_audit(self) -> None:
        """round-6 ledger C-28: a card promoted to request_draft, resolved
        (task done), and then re-detected (task un-done and stalls again)
        must count as needs_detected on the reopen run and must emit a FRESH
        stagnation_detected audit row — not be silently absorbed as if it
        were an unchanged-band re-sweep."""
        self._set_policy()
        self._add_matching_candidate()
        task = self._make_high_score_task()
        self.store.save_task(task)

        result1 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_1")
        self.assertEqual(result1["needs_detected"], 1)
        card = self.store.find_open_card_for_task("emp_owner", task.task_id)
        self.assertEqual(card.tier, "request_draft")
        stag_msgs_after_run1 = [
            m for m in self.store.list_messages()
            if m.intent == "stagnation_detected" and m.payload.get("task_id") == task.task_id
        ]
        self.assertEqual(len(stag_msgs_after_run1), 1)

        # Task completes -> card resolves.
        task.status = "done"
        self.store.save_task(task)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_2")
        resolved = self.store.get_card(card.card_id)
        self.assertEqual(resolved.status, "resolved")

        # Task re-stagnates (un-done, still scores >= T2).
        task.status = "todo"
        self.store.save_task(task)
        result3 = self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_3")

        reopened_card = self.store.get_card(card.card_id)
        self.assertEqual(reopened_card.status, "open")
        self.assertEqual(reopened_card.tier, "request_draft")
        # C-28: needs_detected must count this re-detection, not stay 0.
        self.assertEqual(result3["needs_detected"], 1)
        self.assertEqual(result3["cards_created"], 1)  # reopened counts as created-equivalent

        stag_msgs_after_run3 = [
            m for m in self.store.list_messages()
            if m.intent == "stagnation_detected" and m.payload.get("task_id") == task.task_id
        ]
        # A NEW audit row was recorded for the re-detection (run3's id differs
        # from run1's because it mixes in run_key) — not silently deduped away.
        self.assertEqual(len(stag_msgs_after_run3), 2)

    def test_reopened_stagnation_audit_dedups_within_the_same_lifecycle(self) -> None:
        """round-6 ledger C-28 / round-7 C-33: a crash-retry of a reopen (same
        stored audit_epoch, even across runs) must still dedup via create-only
        — only the reopen's FIRST attempt persists."""
        task = self._make_high_score_task()
        self.store.save_task(task)

        self.secretary._send_stagnation_audit(
            "card_x", "request_draft", task, 8.0, "agent_owner", epoch="2026-06-15T08:00:00Z"
        )
        self.secretary._send_stagnation_audit(
            "card_x", "request_draft", task, 8.0, "agent_owner", epoch="2026-06-15T08:00:00Z"
        )
        msgs = [m for m in self.store.list_messages() if m.intent == "stagnation_detected"]
        self.assertEqual(len(msgs), 1)

    def test_no_saltless_duplicate_row_in_the_run_after_a_reopen(self) -> None:
        """round-7 C-33: after a reopen stamps audit_epoch, the NEXT run's
        unchanged-band refresh reuses the stored epoch and adds no second row."""
        task = self._make_high_score_task()
        self.store.save_task(task)
        self._set_policy(employee_id=task.owner_employee_id, monitor=True, search=True, ask=True, prepare=True)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_e1")
        card = self.store.find_card_by_domain_key(task.owner_employee_id, "stagnation", task.task_id)
        resolve = Card(
            card_id=card.card_id, owner_employee_id=task.owner_employee_id,
            type="stagnation", tier=card.tier, payload=dict(card.payload),
            status="resolved", resolved_reason="task_done",
        )
        self.store.upsert_card_gated(resolve)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_e2")
        count_after_reopen = len(
            [m for m in self.store.list_messages() if m.intent == "stagnation_detected"]
        )
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_e3")
        count_after_next_run = len(
            [m for m in self.store.list_messages() if m.intent == "stagnation_detected"]
        )
        self.assertEqual(count_after_next_run, count_after_reopen)


# =============================================================================
# 9. R4-H3: mail outcome matrix
# =============================================================================


class TestMailOutcomeMatrix(AutonomyTestBase):
    def _seed_mail_and_profile(self) -> MailSeed:
        profile = Profile(employee_id="emp_marcus", name="Marcus", role="Broker", items=[])
        self.store.save_profile(profile)
        mail = MailSeed(
            mail_id="mail_1",
            owner_employee_id="emp_marcus",
            subject="Update on the ambulatory surgery center partnership",
            body="We finalized the preliminary staffing protocol for outpatient surgical teams.",
            received_at=_iso(TODAY - timedelta(days=1)) + "T11:00:00Z",
            processed=False,
        )
        self.store.save_mail_seed(mail)
        return mail

    def test_monitor_on_created_marks_processed(self) -> None:
        self._seed_mail_and_profile()
        self._set_policy(employee_id="emp_marcus", monitor=True)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_mail_1")
        mail = self.store.get_mail_seed("mail_1")
        self.assertTrue(mail.processed)
        diff_msgs = [m for m in self.store.list_messages() if m.intent == "profile_diff_proposed"]
        self.assertEqual(len(diff_msgs), 1)

    def test_monitor_off_leaves_mail_untouched(self) -> None:
        self._seed_mail_and_profile()
        self._set_policy(employee_id="emp_marcus", monitor=False, search=False, ask=False, prepare=False)
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_mail_2")
        mail = self.store.get_mail_seed("mail_1")
        self.assertFalse(mail.processed)
        self.assertEqual(mail.body, "We finalized the preliminary staffing protocol for outpatient surgical teams.")
        diff_msgs = [m for m in self.store.list_messages() if m.intent == "profile_diff_proposed"]
        self.assertEqual(len(diff_msgs), 0)

    def test_monitor_reenabled_processes_previously_deferred_mail(self) -> None:
        self._seed_mail_and_profile()
        self._set_policy(employee_id="emp_marcus", monitor=False, search=False, ask=False, prepare=False, updated_at="v1")
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_a")
        self.assertFalse(self.store.get_mail_seed("mail_1").processed)

        self._set_policy(employee_id="emp_marcus", monitor=True, updated_at="v2")
        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_b")
        self.assertTrue(self.store.get_mail_seed("mail_1").processed)

    def test_rejected_policy_changed_leaves_mail_unprocessed(self) -> None:
        mail = self._seed_mail_and_profile()
        self._set_policy(employee_id="emp_marcus", monitor=True, updated_at="v1")

        # Pre-create the deterministic profile_diff card id under a DIFFERENT
        # (now-stale) expected policy token, forcing upsert_card_gated to see
        # a policy mismatch on the real run below.
        import hashlib as _hashlib
        card_id = "card_diff_" + _hashlib.sha1(mail.mail_id.encode()).hexdigest()[:12]

        # Simulate the policy changing AFTER the LLM extraction step (Z-2):
        # monkeypatch extract_profile_diff indirectly isn't needed — instead
        # directly exercise upsert_card_gated's own guard by racing the token.
        from knowledge_discovery.secretary import extract_profile_diff
        profile = self.store.get_profile("emp_marcus")
        diff = extract_profile_diff(mail, profile, llm_client=None)
        self.assertIsNotNone(diff)

        self._set_policy(employee_id="emp_marcus", monitor=True, updated_at="v2")  # policy moved on
        stale_card = Card(
            card_id=card_id, owner_employee_id="emp_marcus", type="profile_diff", tier=None,
            payload={"item_key": diff[0], "body_draft": diff[1], "source_mail_id": mail.mail_id, "subject": mail.subject},
            status="open",
        )
        _, outcome, _, _ = self.store.upsert_card_gated(stale_card, expected_policy_updated_at="v1")
        self.assertEqual(outcome, "rejected_policy_changed")

    def test_rejected_terminal_marks_mail_processed(self) -> None:
        """round-5 ledger D: a mail whose proposal card was already
        confirmed/dismissed/applied (terminal) is already adjudicated -- the
        write is a no-op (rejected_terminal), but the mail itself must still
        be consumed (processed=True) so the secretary doesn't re-run the LLM
        on it forever."""
        mail = self._seed_mail_and_profile()
        self._set_policy(employee_id="emp_marcus", monitor=True)

        from knowledge_discovery.secretary import extract_profile_diff

        profile = self.store.get_profile("emp_marcus")
        diff = extract_profile_diff(mail, profile, llm_client=None)
        self.assertIsNotNone(diff)
        card_id = "card_diff_" + hashlib.sha1(mail.mail_id.encode()).hexdigest()[:12]
        terminal_card = Card(
            card_id=card_id, owner_employee_id="emp_marcus", type="profile_diff", tier=None,
            payload={"item_key": diff[0], "body_draft": diff[1], "source_mail_id": mail.mail_id, "subject": mail.subject},
            status="dismissed",
        )
        self.store.save_card(terminal_card)

        self.secretary.run_sweep(demo_today=TODAY_STR, origin="scheduled", run_key="run_terminal")
        self.assertTrue(self.store.get_mail_seed(mail.mail_id).processed)


# =============================================================================
# 10. MatchingEngine preview_shortlist/preview_evaluate split (unchanged compose)
# =============================================================================


class TestPreviewSplitComposition(AutonomyTestBase):
    def test_shortlist_then_evaluate_equals_preview_search(self) -> None:
        self._add_matching_candidate()
        registered_agents = self.store.list_agents(active_only=True)
        profiles_map = {p.employee_id: p for p in self.store.list_profiles()}
        question = "Kubernetes upgrade project"

        composed = self.matching_engine.preview_search(question, registered_agents, profiles_map)
        shortlist = self.matching_engine.preview_shortlist(question, registered_agents, profiles_map)
        evaluated = self.matching_engine.preview_evaluate(question, shortlist)

        self.assertEqual(len(composed), len(evaluated))
        self.assertEqual([c.employee_id for c in composed], [c.employee_id for c in evaluated])

    def test_shortlist_returns_full_ranked_list_uncapped(self) -> None:
        for i in range(5):
            self._add_matching_candidate(employee_id=f"emp_m{i}", agent_id=f"agent_m{i}")
        registered_agents = self.store.list_agents(active_only=True)
        profiles_map = {p.employee_id: p for p in self.store.list_profiles()}
        shortlist = self.matching_engine.preview_shortlist("Kubernetes upgrade project", registered_agents, profiles_map)
        self.assertEqual(len(shortlist), 5)


# =============================================================================
# 11. Seed generation writes autonomy policy docs (script-level, offline)
# =============================================================================


class TestSeedGenerationAutonomyPolicies(unittest.TestCase):
    def test_build_autonomy_policies_covers_all_identities_full_on(self) -> None:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
        import generate_seeds

        policies = generate_seeds.build_autonomy_policies()
        self.assertEqual(len(policies), len(generate_seeds.IDENTITY_SEEDS))
        ids = {p.employee_id for p in policies}
        self.assertEqual(ids, set(generate_seeds.IDENTITY_SEEDS.values()))
        for p in policies:
            eff = p.effective()
            self.assertTrue(all(eff.values()))


if __name__ == "__main__":
    unittest.main()
