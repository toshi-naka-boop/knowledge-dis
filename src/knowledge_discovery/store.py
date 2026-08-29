"""Storage abstraction and in-memory implementation for knowledge discovery.

Follows design.md §3:
- Store interface defining contracts for agents, profiles, and messages.
- InMemoryStore providing a standalone, testable implementation without Firestore dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime, timezone
import threading
from typing import Any
import uuid

from knowledge_discovery.models import (
    Agent,
    AutonomyPolicy,
    Card,
    MailSeed,
    Message,
    Profile,
    Schedule,
    Task,
    utc_now_iso,
)


def _card_domain_key_field(card_type: str) -> str | None:
    """Payload key holding the domain identifier used for legacy-id reuse (C-18).

    'stagnation' cards key on their task_id, 'profile_diff' cards key on the
    source mail_id. Any other type has no domain-key lookup defined.
    """
    if card_type == "stagnation":
        return "task_id"
    if card_type == "profile_diff":
        return "source_mail_id"
    return None


def _parse_iso_or_none(value: str | None) -> datetime | None:
    """Best-effort ISO8601 parse; returns None on any failure (treated as stale)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class Store(ABC):
    """Abstract storage interface for knowledge discovery entities."""

    # Agent operations (agents collection / registry)
    @abstractmethod
    def save_agent(self, agent: Agent) -> None:
        """Save or update an agent record in the registry."""
        pass

    @abstractmethod
    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by agent_id."""
        pass

    @abstractmethod
    def get_agent_by_employee_id(self, employee_id: str) -> Agent | None:
        """Retrieve an agent by employee_id."""
        pass

    @abstractmethod
    def list_agents(self, active_only: bool = False) -> list[Agent]:
        """List registered agents, optionally filtering for active only."""
        pass

    # Profile operations (profiles collection)
    @abstractmethod
    def save_profile(self, profile: Profile) -> None:
        """Save or update an employee knowledge profile."""
        pass

    @abstractmethod
    def get_profile(self, employee_id: str) -> Profile | None:
        """Retrieve a profile by employee_id."""
        pass

    @abstractmethod
    def list_profiles(self) -> list[Profile]:
        """List all employee profiles."""
        pass

    # Message operations (messages collection / audit log)
    @abstractmethod
    def save_message(self, message: Message) -> None:
        """Save or update a message record."""
        pass

    @abstractmethod
    def get_message(self, audit_id: str) -> Message | None:
        """Retrieve a message by audit_id."""
        pass

    @abstractmethod
    def list_messages(self, limit: int | None = None) -> list[Message]:
        """List all messages in chronological order."""
        pass

    @abstractmethod
    def get_messages_for_entity(self, entity_id: str) -> list[Message]:
        """Retrieve messages involving the given entity (as sender, recipient, or participant)."""
        pass

    # Task operations (tasks collection §14.2)
    @abstractmethod
    def save_task(self, task: Task) -> None:
        """Save or update a task record."""
        pass

    @abstractmethod
    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a task by task_id."""
        pass

    @abstractmethod
    def list_tasks(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Task]:
        """List tasks, optionally filtered by owner_employee_id and/or source."""
        pass

    # Schedule operations (schedules collection §14.2)
    @abstractmethod
    def save_schedule(self, schedule: Schedule) -> None:
        """Save or update a schedule reminder."""
        pass

    @abstractmethod
    def list_schedules(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Schedule]:
        """List schedules, optionally filtered by owner_employee_id and/or source."""
        pass

    @abstractmethod
    def delete_schedule(self, item_id: str) -> None:
        """Delete a schedule reminder by item_id (§16.3 Calendar reconciliation).

        No-op if the item_id doesn't exist.
        """
        pass

    # MailSeed operations (mail_seeds collection §14.2, §14.5)
    @abstractmethod
    def save_mail_seed(self, mail: MailSeed) -> None:
        """Save or update an email seed record."""
        pass

    @abstractmethod
    def get_mail_seed(self, mail_id: str) -> MailSeed | None:
        """Retrieve an email seed by mail_id."""
        pass

    @abstractmethod
    def list_mail_seeds(
        self, owner_employee_id: str | None = None, unprocessed_only: bool = False
    ) -> list[MailSeed]:
        """List mail seeds, optionally filtered by owner and/or unprocessed status."""
        pass

    @abstractmethod
    def delete_mail_seed(self, mail_id: str) -> None:
        """Delete a mail seed by mail_id (§16.3 Gmail 14-day retention).

        No-op if the mail_id doesn't exist.
        """
        pass

    # Card operations (cards collection §14.2)
    @abstractmethod
    def save_card(self, card: Card) -> None:
        """Save or update a secretary card."""
        pass

    @abstractmethod
    def get_card(self, card_id: str) -> Card | None:
        """Retrieve a card by card_id."""
        pass

    @abstractmethod
    def list_cards(
        self, owner_employee_id: str | None = None, status: str | None = None
    ) -> list[Card]:
        """List cards, optionally filtered by owner_employee_id and/or status."""
        pass

    @abstractmethod
    def find_open_card_for_task(self, owner_employee_id: str, task_id: str) -> Card | None:
        """Find an open stagnation card for the given task and owner."""
        pass

    @abstractmethod
    def find_cards_for_task(self, owner_employee_id: str, task_id: str) -> list[Card]:
        """Find all stagnation cards for the given task and owner."""
        pass

    @abstractmethod
    def try_confirm_card(self, card_id: str) -> tuple[Card | None, bool]:
        """Atomically transition a card's status from 'open' to 'confirmed' (CAS, §14.4).

        Returns (card, won):
        - (None, False) if no card with this card_id exists.
        - (card, True) if THIS call performed the open->confirmed transition; the
          returned card reflects the post-transition ('confirmed') state.
        - (card, False) if the card exists but was not in 'open' status (already
          'confirmed' by a concurrent caller, or 'dismissed'/'resolved'/'applied');
          the returned card reflects its current stored state.

        Implementations must guarantee at most one caller ever observes won=True
        for a given card_id (Firestore: @firestore.transactional; in-memory: a
        lock guarding the read-check-write).
        """
        pass

    @abstractmethod
    def find_card_by_domain_key(
        self, owner_employee_id: str, card_type: str, domain_key: str
    ) -> Card | None:
        """Find a card for (owner, type, domain_key) (autonomous-agent design C-18, round-5 ledger K-1/B).

        domain_key is payload.task_id for 'stagnation', payload.source_mail_id
        for 'profile_diff'. Used by upsert_card_gated to reuse a legacy
        random-id card instead of creating a second doc for the same task/mail.

        When multiple docs share the same (owner, type, domain_key) — e.g. a
        legacy random-id doc plus a doc already written under the new
        deterministic id — an 'open' doc is preferred over any resolved/terminal
        one (round-5 ledger K-1: an open card is always the live state for that
        domain key); if none is open, the most recently updated match is
        returned.
        """
        pass

    @abstractmethod
    def upsert_card_gated(
        self,
        card: Card,
        expected_policy_updated_at: str | None = None,
        clear_policy_hold: bool = False,
    ) -> tuple[Card, str, str | None, str | None]:
        """Conditionally create/update a card under a single CAS (autonomous-agent design §3, round-5 ledger B).

        Resolution order: direct lookup by card.card_id, then (if absent)
        legacy-id reuse via find_card_by_domain_key (C-18). Both the lookup
        and the merge decisions below happen INSIDE the same CAS transaction
        (InMemoryStore: inside the card lock; FirestoreStore: inside
        @firestore.transactional) — round-6 ledger W-1/W-2: a caller's own
        pre-read of the card (done before calling this method, and therefore
        possibly stale under a concurrent writer) must never be trusted to
        decide what gets written; only the state read inside this
        transaction may drive the merge.

        Two merge rules are enforced unconditionally inside the transaction,
        regardless of what the caller's `card` argument asks for:

        - Never-downgrade (round-6 ledger W-1/C-29, generalizing C-19): if the
          existing doc is 'open' with tier 'request_draft' and the incoming
          card.tier is 'notice', the write is NOT allowed to downgrade it —
          tier stays 'request_draft', payload keys other than
          task_id/task_title/score/evidence_line (e.g. question_draft/preview)
          are kept from the existing doc, and only those evidence keys are
          refreshed from the incoming payload. outcome is still 'updated' (the
          caller must not treat this as a promotion).
        - policy_hold carry-forward (round-6 ledger W-1/W-3): if the existing
          doc's payload has a 'policy_hold' key and the incoming card.payload
          does NOT itself set one, the existing hold is carried into the
          merged payload — for both an in-place update and a resolved->open
          reopen — UNLESS clear_policy_hold=True, in which case it is
          dropped. An incoming payload that DOES set 'policy_hold' always
          wins verbatim (an explicit new/refreshed hold). Callers should pass
          clear_policy_hold=True only for a write that reflects a
          fully-evaluated, unrestricted policy (so a stale hold marker from
          before a policy change cannot linger).

        Returns (card, outcome, prev_status, prev_tier). prev_status/prev_tier
        are the existing doc's status/tier as read INSIDE this CAS's
        transaction, before the write — None/None when no doc existed. Callers
        must use these (not any pre-read done before calling this method) to
        decide notice->request_draft promotion and band-change counting/audit
        emission, so that decision is never based on a value that a concurrent
        writer could have already superseded (round-5 ledger B / V-2/K-5).

        outcome is one of:
        - 'created': no existing card found; card.card_id (deterministic) written as new.
        - 'updated': existing card was 'open'; fields merged in, id/created_at preserved.
          Also covers an explicit open->resolved write (R4-H1: resolve goes through
          this same CAS, never overwriting a 'confirmed' card) and the
          never-downgrade guard above.
        - 'reopened': existing card was 'resolved' and the incoming card.status is
          'open' (a genuine re-detection intent); re-opened and resolved_reason
          cleared (C-13 — recovers re-detection of a task that stalls again).
        - 'unchanged': existing card was 'resolved' and the incoming card.status is
          NOT 'open' (e.g. a duplicate resolve) — a no-op; the existing doc is
          returned untouched (round-5 ledger B — a second resolve attempt must
          never clobber the first resolve's resolved_reason/timestamps).
        - 'rejected_terminal': existing card is 'confirmed'/'dismissed'/'applied'; not written.
        - 'rejected_policy_changed': expected_policy_updated_at was given and no
          longer matches the employee's current autonomy_policies.updated_at
          (Z-2 — stops a stale scheduled-run write racing a policy edit).

        expected_policy_updated_at=None means no policy gating (manual override).
        """
        pass

    # Autonomy policy operations (autonomy_policies collection, autonomous-agent design §5.1)
    @abstractmethod
    def get_autonomy_policy(self, employee_id: str) -> AutonomyPolicy | None:
        """Retrieve the persisted autonomy policy for an employee, or None if never saved."""
        pass

    @abstractmethod
    def save_autonomy_policy(self, policy: AutonomyPolicy) -> None:
        """Save or update an employee's autonomy policy."""
        pass

    @abstractmethod
    def save_message_if_absent(self, message: Message) -> bool:
        """Create-only save (autonomous-agent design §3 Z-4): writes only if audit_id is unseen.

        Returns True if this call created the doc, False if a doc with this
        audit_id already existed (no-op — the first writer's content, including
        timestamp, is preserved).
        """
        pass

    # Sweep run claim/lifecycle (sweep_runs collection, autonomous-agent design §3)
    @abstractmethod
    def claim_sweep_run(
        self, run_key: str, origin: str, date: str, ttl_seconds: int
    ) -> tuple[str | None, str]:
        """Attempt to claim a sweep run for execution (CAS).

        Returns (claim_token, state), state one of:
        - 'claimed': this call now owns the run (doc absent, or previously
          'failed', or 'running' but stale past ttl_seconds); claim_token is set.
        - 'done': run already completed; claim_token is None.
        - 'in_progress': another non-stale attempt currently owns the run; claim_token is None.
        """
        pass

    @abstractmethod
    def finish_sweep_run(self, run_key: str, claim_token: str, summary: dict[str, Any]) -> bool:
        """Token-CAS transition to 'done' with the given summary (R4-H4: confirmed before audit emission).

        Returns True if this call's claim_token matched and the transition was
        applied; False if the token no longer matches (no-op, summary untouched).
        """
        pass

    @abstractmethod
    def fail_sweep_run(self, run_key: str, claim_token: str, error: str) -> bool:
        """Token-CAS transition to 'failed' (C-14/Z-1: makes the run immediately re-claimable).

        Returns True if applied, False if claim_token no longer matched.
        """
        pass

    @abstractmethod
    def get_sweep_run(self, run_key: str) -> dict[str, Any] | None:
        """Retrieve the raw sweep_runs doc (status/claim_token/summary/etc.), or None."""
        pass

    @abstractmethod
    def get_latest_sweep_run(self) -> dict[str, Any] | None:
        """Retrieve the most recently finished ('done') sweep_runs doc for this
        tenant, or None if no run has ever finished (autonomous-agent design §8:
        digest's `last_sweep` field)."""
        pass

    # Identity operations (identities collection, design §16.1 Part A)
    @abstractmethod
    def get_identity(self, email: str) -> str | None:
        """Resolve a verified email to its employee_id, or None if unregistered."""
        pass

    @abstractmethod
    def save_identity(self, email: str, employee_id: str) -> None:
        """Register (or update) the employee_id bound to an email address."""
        pass

    @abstractmethod
    def try_transition_ask_consent(
        self, ask_audit_id: str, agent_id: str, new_consent_state: str
    ) -> tuple[Message | None, str]:
        """Atomically verify and transition an ask message's consent_state (W-1, §16.1).

        Verifies: message exists, message.to_entity == agent_id, message.intent
        is 'connect_ask' or 'connect_ask_private', and message.consent_state == 'pending'
        -- then transitions consent_state to new_consent_state, all under one lock/transaction.

        Returns (message, outcome), outcome one of:
        - 'ok': transition performed; message reflects the new consent_state.
        - 'not_found': no message with this audit_id.
        - 'forbidden': message exists but to_entity != agent_id, or intent is
          not a connect_ask variant.
        - 'conflict': message exists and belongs to this agent, but consent_state
          is not 'pending' (e.g. a duplicate POST after the first has resolved it).
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all stored data (useful for test isolation)."""
        pass


class InMemoryStore(Store):
    """In-memory implementation of Store using Python data structures."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._profiles: dict[str, Profile] = {}
        self._messages: list[Message] = []
        self._messages_by_id: dict[str, Message] = {}
        self._tasks: dict[str, Task] = {}
        self._schedules: dict[str, Schedule] = {}
        self._mail_seeds: dict[str, MailSeed] = {}
        self._cards: dict[str, Card] = {}
        self._card_lock = threading.Lock()
        self._identities: dict[str, str] = {}
        self._consent_lock = threading.Lock()
        self._autonomy_policies: dict[str, AutonomyPolicy] = {}
        self._messages_lock = threading.Lock()
        self._sweep_runs: dict[str, dict[str, Any]] = {}
        self._sweep_run_lock = threading.Lock()

    def save_agent(self, agent: Agent) -> None:
        """Save or update an agent record in the registry."""
        stored = deepcopy(agent)
        self._agents[stored.agent_id] = stored

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by agent_id."""
        agent = self._agents.get(agent_id)
        return deepcopy(agent) if agent is not None else None

    def get_agent_by_employee_id(self, employee_id: str) -> Agent | None:
        """Retrieve an agent by employee_id."""
        for agent in self._agents.values():
            if agent.employee_id == employee_id:
                return deepcopy(agent)
        return None

    def list_agents(self, active_only: bool = False) -> list[Agent]:
        """List registered agents, optionally filtering for active only."""
        agents = list(self._agents.values())
        if active_only:
            agents = [a for a in agents if a.active]
        return [deepcopy(a) for a in agents]

    def save_profile(self, profile: Profile) -> None:
        """Save or update an employee knowledge profile."""
        stored = deepcopy(profile)
        self._profiles[stored.employee_id] = stored

    def get_profile(self, employee_id: str) -> Profile | None:
        """Retrieve a profile by employee_id."""
        profile = self._profiles.get(employee_id)
        return deepcopy(profile) if profile is not None else None

    def list_profiles(self) -> list[Profile]:
        """List all employee profiles."""
        return [deepcopy(p) for p in self._profiles.values()]

    def save_message(self, message: Message) -> None:
        """Save or update a message record."""
        stored = deepcopy(message)
        if stored.audit_id in self._messages_by_id:
            for idx, existing in enumerate(self._messages):
                if existing.audit_id == stored.audit_id:
                    self._messages[idx] = stored
                    break
        else:
            self._messages.append(stored)
        self._messages_by_id[stored.audit_id] = stored

    def get_message(self, audit_id: str) -> Message | None:
        """Retrieve a message by audit_id."""
        msg = self._messages_by_id.get(audit_id)
        return deepcopy(msg) if msg is not None else None

    def list_messages(self, limit: int | None = None) -> list[Message]:
        """List all messages in chronological order."""
        messages = [deepcopy(m) for m in self._messages]
        if limit is not None and limit > 0:
            return messages[-limit:]
        return messages

    def get_messages_for_entity(self, entity_id: str) -> list[Message]:
        """Retrieve messages involving the given entity (as sender, recipient, or participant)."""
        results: list[Message] = []
        for msg in self._messages:
            participants = msg.payload.get("participants", []) if isinstance(msg.payload, dict) else []
            if (
                msg.from_entity == entity_id
                or msg.to_entity == entity_id
                or entity_id in participants
            ):
                results.append(deepcopy(msg))
        return results

    # Task operations
    def save_task(self, task: Task) -> None:
        stored = deepcopy(task)
        self._tasks[stored.task_id] = stored

    def get_task(self, task_id: str) -> Task | None:
        t = self._tasks.get(task_id)
        return deepcopy(t) if t is not None else None

    def list_tasks(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Task]:
        tasks = list(self._tasks.values())
        if owner_employee_id is not None:
            tasks = [t for t in tasks if t.owner_employee_id == owner_employee_id]
        if source is not None:
            tasks = [t for t in tasks if t.source == source]
        return [deepcopy(t) for t in tasks]

    # Schedule operations
    def save_schedule(self, schedule: Schedule) -> None:
        stored = deepcopy(schedule)
        self._schedules[stored.item_id] = stored

    def list_schedules(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Schedule]:
        schedules = list(self._schedules.values())
        if owner_employee_id is not None:
            schedules = [s for s in schedules if s.owner_employee_id == owner_employee_id]
        if source is not None:
            schedules = [s for s in schedules if s.source == source]
        return [deepcopy(s) for s in schedules]

    def delete_schedule(self, item_id: str) -> None:
        self._schedules.pop(item_id, None)

    # MailSeed operations
    def save_mail_seed(self, mail: MailSeed) -> None:
        stored = deepcopy(mail)
        self._mail_seeds[stored.mail_id] = stored

    def get_mail_seed(self, mail_id: str) -> MailSeed | None:
        m = self._mail_seeds.get(mail_id)
        return deepcopy(m) if m is not None else None

    def list_mail_seeds(
        self, owner_employee_id: str | None = None, unprocessed_only: bool = False
    ) -> list[MailSeed]:
        mails = list(self._mail_seeds.values())
        if owner_employee_id is not None:
            mails = [m for m in mails if m.owner_employee_id == owner_employee_id]
        if unprocessed_only:
            mails = [m for m in mails if not m.processed]
        return [deepcopy(m) for m in mails]

    def delete_mail_seed(self, mail_id: str) -> None:
        self._mail_seeds.pop(mail_id, None)

    # Card operations
    def save_card(self, card: Card) -> None:
        stored = deepcopy(card)
        self._cards[stored.card_id] = stored

    def get_card(self, card_id: str) -> Card | None:
        c = self._cards.get(card_id)
        return deepcopy(c) if c is not None else None

    def list_cards(
        self, owner_employee_id: str | None = None, status: str | None = None
    ) -> list[Card]:
        cards = list(self._cards.values())
        if owner_employee_id is not None:
            cards = [c for c in cards if c.owner_employee_id == owner_employee_id]
        if status is not None:
            cards = [c for c in cards if c.status == status]
        return [deepcopy(c) for c in cards]

    def find_open_card_for_task(self, owner_employee_id: str, task_id: str) -> Card | None:
        for c in self._cards.values():
            if (
                c.owner_employee_id == owner_employee_id
                and c.type == "stagnation"
                and c.status == "open"
                and c.payload.get("task_id") == task_id
            ):
                return deepcopy(c)
        return None

    def find_cards_for_task(self, owner_employee_id: str, task_id: str) -> list[Card]:
        results: list[Card] = []
        for c in self._cards.values():
            if (
                c.owner_employee_id == owner_employee_id
                and c.type == "stagnation"
                and c.payload.get("task_id") == task_id
            ):
                results.append(deepcopy(c))
        return results

    def try_confirm_card(self, card_id: str) -> tuple[Card | None, bool]:
        """Atomically transition a card's status from 'open' to 'confirmed' (CAS)."""
        with self._card_lock:
            card = self._cards.get(card_id)
            if card is None:
                return None, False
            if card.status != "open":
                return deepcopy(card), False
            card.status = "confirmed"
            card.updated_at = utc_now_iso()
            self._cards[card.card_id] = deepcopy(card)
            return deepcopy(card), True

    def _find_card_by_domain_key_raw(
        self, owner_employee_id: str, card_type: str, domain_key: Any
    ) -> Card | None:
        """Live (non-deepcopy) lookup shared by find_card_by_domain_key and the
        legacy-id resolution inside upsert_card_gated (round-5 ledger K-1):
        an 'open' match is preferred over any resolved/terminal match; if none
        is open, the most recently updated match is returned."""
        key_field = _card_domain_key_field(card_type)
        if key_field is None:
            return None
        matches = [
            c
            for c in self._cards.values()
            if c.owner_employee_id == owner_employee_id
            and c.type == card_type
            and str(c.payload.get(key_field)) == str(domain_key)
        ]
        if not matches:
            return None
        open_matches = [c for c in matches if c.status == "open"]
        pool = open_matches or matches
        return max(pool, key=lambda c: c.updated_at or "")

    def find_card_by_domain_key(
        self, owner_employee_id: str, card_type: str, domain_key: str
    ) -> Card | None:
        found = self._find_card_by_domain_key_raw(owner_employee_id, card_type, domain_key)
        return deepcopy(found) if found is not None else None

    @staticmethod
    def _merge_policy_hold(
        new_payload: dict[str, Any],
        existing_payload: dict[str, Any],
        incoming_payload: dict[str, Any],
        clear_policy_hold: bool,
    ) -> None:
        """round-6 ledger W-1/W-3: carry an existing policy_hold marker
        forward into new_payload (in place) unless the incoming payload sets
        its own (which always wins verbatim) or the caller explicitly asks to
        drop it via clear_policy_hold=True. Also carries the audit_epoch
        lifecycle marker (round-7 C-33), which is never cleared — it is stamped
        at reopen and must survive every later write of that lifecycle."""
        if "policy_hold" not in incoming_payload:
            if clear_policy_hold:
                new_payload.pop("policy_hold", None)
            elif "policy_hold" in existing_payload:
                new_payload["policy_hold"] = deepcopy(existing_payload["policy_hold"])
        if "audit_epoch" not in incoming_payload and "audit_epoch" in existing_payload:
            new_payload["audit_epoch"] = existing_payload["audit_epoch"]

    def upsert_card_gated(
        self,
        card: Card,
        expected_policy_updated_at: str | None = None,
        clear_policy_hold: bool = False,
    ) -> tuple[Card, str, str | None, str | None]:
        with self._card_lock:
            existing = self._cards.get(card.card_id)
            if existing is None:
                key_field = _card_domain_key_field(card.type)
                domain_key = card.payload.get(key_field) if key_field else None
                if domain_key is not None:
                    existing = self._find_card_by_domain_key_raw(
                        card.owner_employee_id, card.type, domain_key
                    )

            prev_status = existing.status if existing is not None else None
            prev_tier = existing.tier if existing is not None else None

            if existing is not None and existing.status in ("confirmed", "dismissed", "applied"):
                return deepcopy(existing), "rejected_terminal", prev_status, prev_tier

            if expected_policy_updated_at is not None:
                policy = self._autonomy_policies.get(card.owner_employee_id)
                current_policy_updated_at = policy.updated_at if policy is not None else ""
                if current_policy_updated_at != expected_policy_updated_at:
                    ref = existing if existing is not None else card
                    return deepcopy(ref), "rejected_policy_changed", prev_status, prev_tier

            now = utc_now_iso()

            if existing is None:
                new_card = deepcopy(card)
                new_card.status = card.status or "open"
                new_card.created_at = card.created_at or now
                new_card.updated_at = now
                self._cards[new_card.card_id] = deepcopy(new_card)
                return deepcopy(new_card), "created", prev_status, prev_tier

            if existing.status == "resolved" and card.status != "open":
                # round-5 ledger B: a second write against an already-resolved
                # card that is NOT a re-detection (incoming status != 'open',
                # e.g. a duplicate resolve) is a no-op — never clobber the
                # first resolve's resolved_reason/timestamps.
                return deepcopy(existing), "unchanged", prev_status, prev_tier

            # round-6 ledger W-1/C-29: never-downgrade guard, enforced HERE
            # against the `existing` doc just read inside this lock — a
            # concurrent writer's stale "notice" write can never clobber a
            # promotion decided by a different writer, because that decision
            # is checked against the CURRENT stored state, not a pre-read.
            if (
                existing.status == "open"
                and existing.tier == "request_draft"
                and card.tier == "notice"
                and card.status == "open"
            ):
                merged = deepcopy(existing)
                merged.type = card.type
                new_payload = dict(existing.payload)
                for key in ("task_id", "task_title", "score", "evidence_line"):
                    if key in card.payload:
                        new_payload[key] = card.payload[key]
                self._merge_policy_hold(new_payload, existing.payload, card.payload, clear_policy_hold)
                merged.payload = new_payload
                merged.updated_at = now
                self._cards[merged.card_id] = deepcopy(merged)
                return deepcopy(merged), "updated", prev_status, prev_tier

            merged = deepcopy(existing)
            merged.type = card.type
            merged.tier = card.tier
            new_payload = dict(card.payload)
            self._merge_policy_hold(new_payload, existing.payload, card.payload, clear_policy_hold)
            merged.payload = new_payload
            merged.updated_at = now

            if existing.status == "resolved":
                # C-13: incoming card.status == 'open' here (checked above) —
                # a genuine re-detection re-opens the card, clearing resolved_reason.
                # round-7 C-33: stamp a fresh lifecycle marker so this reopen's
                # audit rows get their own deterministic ids; it persists in the
                # doc, so a crash-retry (even in a later run) reuses it.
                merged.status = "open"
                merged.resolved_reason = None
                merged.payload["audit_epoch"] = now
                outcome = "reopened"
            else:
                # existing.status == "open" here (terminal already rejected above).
                merged.status = card.status
                merged.resolved_reason = (
                    card.resolved_reason if card.status == "resolved" else None
                )
                outcome = "updated"

            self._cards[merged.card_id] = deepcopy(merged)
            return deepcopy(merged), outcome, prev_status, prev_tier

    # Autonomy policy operations
    def get_autonomy_policy(self, employee_id: str) -> AutonomyPolicy | None:
        p = self._autonomy_policies.get(employee_id)
        return deepcopy(p) if p is not None else None

    def save_autonomy_policy(self, policy: AutonomyPolicy) -> None:
        stored = deepcopy(policy)
        self._autonomy_policies[stored.employee_id] = stored

    def save_message_if_absent(self, message: Message) -> bool:
        with self._messages_lock:
            if message.audit_id in self._messages_by_id:
                return False
            stored = deepcopy(message)
            self._messages.append(stored)
            self._messages_by_id[stored.audit_id] = stored
            return True

    # Sweep run claim/lifecycle
    def claim_sweep_run(
        self, run_key: str, origin: str, date: str, ttl_seconds: int
    ) -> tuple[str | None, str]:
        with self._sweep_run_lock:
            run = self._sweep_runs.get(run_key)
            now = datetime.now(timezone.utc)

            def _claim() -> tuple[str, str]:
                token = uuid.uuid4().hex
                self._sweep_runs[run_key] = {
                    "run_key": run_key,
                    "status": "running",
                    "claim_token": token,
                    "started_at": now.isoformat(),
                    "origin": origin,
                    "date": date,
                }
                return token, "claimed"

            if run is None or run.get("status") == "failed":
                return _claim()
            if run.get("status") == "done":
                return None, "done"
            if run.get("status") == "running":
                started = _parse_iso_or_none(run.get("started_at"))
                stale = started is None or (now - started).total_seconds() >= ttl_seconds
                if stale:
                    return _claim()
                return None, "in_progress"
            # Unknown/corrupt status: treat as claimable (defensive).
            return _claim()

    def finish_sweep_run(self, run_key: str, claim_token: str, summary: dict[str, Any]) -> bool:
        with self._sweep_run_lock:
            run = self._sweep_runs.get(run_key)
            if run is None or run.get("claim_token") != claim_token:
                return False
            run["status"] = "done"
            run["finished_at"] = utc_now_iso()
            run["summary"] = dict(summary)
            return True

    def fail_sweep_run(self, run_key: str, claim_token: str, error: str) -> bool:
        with self._sweep_run_lock:
            run = self._sweep_runs.get(run_key)
            if run is None or run.get("claim_token") != claim_token:
                return False
            run["status"] = "failed"
            run["finished_at"] = utc_now_iso()
            run["error"] = error
            return True

    def get_sweep_run(self, run_key: str) -> dict[str, Any] | None:
        run = self._sweep_runs.get(run_key)
        return deepcopy(run) if run is not None else None

    def get_latest_sweep_run(self) -> dict[str, Any] | None:
        with self._sweep_run_lock:
            done_runs = [r for r in self._sweep_runs.values() if r.get("status") == "done"]
            if not done_runs:
                return None
            latest = max(done_runs, key=lambda r: r.get("finished_at") or "")
            return deepcopy(latest)

    # Identity operations
    def get_identity(self, email: str) -> str | None:
        return self._identities.get(email.strip().lower())

    def save_identity(self, email: str, employee_id: str) -> None:
        self._identities[email.strip().lower()] = employee_id

    def try_transition_ask_consent(
        self, ask_audit_id: str, agent_id: str, new_consent_state: str
    ) -> tuple[Message | None, str]:
        """Atomically verify and transition an ask message's consent_state."""
        with self._consent_lock:
            msg = self._messages_by_id.get(ask_audit_id)
            if msg is None:
                return None, "not_found"
            if msg.to_entity != agent_id or msg.intent not in ("connect_ask", "connect_ask_private"):
                return deepcopy(msg), "forbidden"
            if msg.consent_state != "pending":
                return deepcopy(msg), "conflict"
            msg.consent_state = new_consent_state
            return deepcopy(msg), "ok"

    def clear(self) -> None:
        """Clear all stored data."""
        self._agents.clear()
        self._profiles.clear()
        self._messages.clear()
        self._messages_by_id.clear()
        self._tasks.clear()
        self._schedules.clear()
        self._mail_seeds.clear()
        self._cards.clear()
        self._identities.clear()
        self._autonomy_policies.clear()
        self._sweep_runs.clear()
