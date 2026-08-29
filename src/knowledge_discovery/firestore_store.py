"""Firestore implementation of the Store interface for knowledge discovery.

Follows design.md §3:
- agents collection: agents/{agent_id}
- profiles collection: profiles/{employee_id}
- messages collection: messages/{audit_id}
- In-memory vector similarity computation over fetched profiles (avoiding composite index validation uncertainties).
"""

from __future__ import annotations

from datetime import datetime, timezone
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
from knowledge_discovery.store import Store, _card_domain_key_field, _parse_iso_or_none

try:
    from google.cloud import firestore
    from google.api_core.exceptions import AlreadyExists
except ImportError:
    firestore = None  # type: ignore[assignment]
    AlreadyExists = Exception  # type: ignore[assignment,misc]


class FirestoreStore(Store):
    """Google Cloud Firestore implementation of Store interface."""

    def __init__(
        self,
        client: Any | None = None,
        project: str | None = None,
        database: str | None = None,
    ) -> None:
        """Initialize FirestoreStore with either an injected client or new Firestore Client."""
        if client is not None:
            self.db = client
        elif firestore is not None:
            self.db = firestore.Client(project=project, database=database)
        else:
            raise RuntimeError(
                "google-cloud-firestore is not installed and no custom client was provided."
            )

    # -------------------------------------------------------------------------
    # Agent operations (agents collection / registry)
    # -------------------------------------------------------------------------

    def save_agent(self, agent: Agent) -> None:
        """Save or update an agent record in the agents collection."""
        doc_ref = self.db.collection("agents").document(agent.agent_id)
        doc_ref.set(agent.to_dict())

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by agent_id."""
        doc = self.db.collection("agents").document(agent_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Agent.from_dict(data)

    def get_agent_by_employee_id(self, employee_id: str) -> Agent | None:
        """Retrieve an agent by employee_id."""
        # Query agents collection for matching employee_id
        col = self.db.collection("agents")
        docs = list(col.where("employee_id", "==", employee_id).limit(1).stream())
        if not docs:
            return None
        doc = docs[0]
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Agent.from_dict(data)

    def list_agents(self, active_only: bool = False) -> list[Agent]:
        """List registered agents, optionally filtering for active only."""
        col = self.db.collection("agents")
        if active_only:
            docs = col.where("active", "==", True).stream()
        else:
            docs = col.stream()

        agents: list[Agent] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                agents.append(Agent.from_dict(data))
        return agents

    # -------------------------------------------------------------------------
    # Profile operations (profiles collection)
    # -------------------------------------------------------------------------

    def save_profile(self, profile: Profile) -> None:
        """Save or update an employee knowledge profile."""
        doc_ref = self.db.collection("profiles").document(profile.employee_id)
        doc_ref.set(profile.to_dict())

    def get_profile(self, employee_id: str) -> Profile | None:
        """Retrieve a profile by employee_id."""
        doc = self.db.collection("profiles").document(employee_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Profile.from_dict(data)

    def list_profiles(self) -> list[Profile]:
        """List all employee profiles."""
        col = self.db.collection("profiles")
        docs = col.stream()
        profiles: list[Profile] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                profiles.append(Profile.from_dict(data))
        return profiles

    # -------------------------------------------------------------------------
    # Message operations (messages collection / audit log)
    # -------------------------------------------------------------------------

    def save_message(self, message: Message) -> None:
        """Save or update a message record."""
        doc_ref = self.db.collection("messages").document(message.audit_id)
        doc_ref.set(message.to_dict())

    def get_message(self, audit_id: str) -> Message | None:
        """Retrieve a message by audit_id."""
        doc = self.db.collection("messages").document(audit_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Message.from_dict(data)

    def list_messages(self, limit: int | None = None) -> list[Message]:
        """List all messages in chronological order."""
        col = self.db.collection("messages")
        # In Firestore, retrieve and sort in-memory or by timestamp
        docs = col.stream()
        messages: list[Message] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                messages.append(Message.from_dict(data))

        # Sort chronologically by timestamp
        messages.sort(key=lambda m: m.timestamp)

        if limit is not None and limit > 0:
            return messages[-limit:]
        return messages

    def get_messages_for_entity(self, entity_id: str) -> list[Message]:
        """Retrieve messages involving the given entity (sender, recipient, or participant)."""
        all_msgs = self.list_messages()
        results: list[Message] = []
        for msg in all_msgs:
            participants = (
                msg.payload.get("participants", []) if isinstance(msg.payload, dict) else []
            )
            if (
                msg.from_entity == entity_id
                or msg.to_entity == entity_id
                or entity_id in participants
            ):
                results.append(msg)
        return results

    # -------------------------------------------------------------------------
    # Task operations (tasks collection)
    # -------------------------------------------------------------------------

    def save_task(self, task: Task) -> None:
        """Save or update a task record."""
        doc_ref = self.db.collection("tasks").document(task.task_id)
        doc_ref.set(task.to_dict())

    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a task by task_id."""
        doc = self.db.collection("tasks").document(task_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Task.from_dict(data)

    def list_tasks(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Task]:
        """List tasks, optionally filtered by owner_employee_id and/or source."""
        col = self.db.collection("tasks")
        query = col
        if owner_employee_id is not None:
            query = query.where("owner_employee_id", "==", owner_employee_id)
        if source is not None:
            query = query.where("source", "==", source)
        docs = query.stream()
        tasks: list[Task] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                tasks.append(Task.from_dict(data))
        return tasks

    # -------------------------------------------------------------------------
    # Schedule operations (schedules collection)
    # -------------------------------------------------------------------------

    def save_schedule(self, schedule: Schedule) -> None:
        """Save or update a schedule reminder."""
        doc_ref = self.db.collection("schedules").document(schedule.item_id)
        doc_ref.set(schedule.to_dict())

    def list_schedules(
        self, owner_employee_id: str | None = None, source: str | None = None
    ) -> list[Schedule]:
        """List schedules, optionally filtered by owner_employee_id and/or source."""
        col = self.db.collection("schedules")
        query = col
        if owner_employee_id is not None:
            query = query.where("owner_employee_id", "==", owner_employee_id)
        if source is not None:
            query = query.where("source", "==", source)
        docs = query.stream()
        schedules: list[Schedule] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                schedules.append(Schedule.from_dict(data))
        return schedules

    def delete_schedule(self, item_id: str) -> None:
        """Delete a schedule reminder by item_id (§16.3 Calendar reconciliation)."""
        self.db.collection("schedules").document(item_id).delete()

    # -------------------------------------------------------------------------
    # MailSeed operations (mail_seeds collection)
    # -------------------------------------------------------------------------

    def save_mail_seed(self, mail: MailSeed) -> None:
        """Save or update an email seed record."""
        doc_ref = self.db.collection("mail_seeds").document(mail.mail_id)
        doc_ref.set(mail.to_dict())

    def get_mail_seed(self, mail_id: str) -> MailSeed | None:
        """Retrieve an email seed by mail_id."""
        doc = self.db.collection("mail_seeds").document(mail_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return MailSeed.from_dict(data)

    def list_mail_seeds(
        self, owner_employee_id: str | None = None, unprocessed_only: bool = False
    ) -> list[MailSeed]:
        """List mail seeds, optionally filtered by owner and/or unprocessed status."""
        col = self.db.collection("mail_seeds")
        query = col
        if owner_employee_id is not None:
            query = query.where("owner_employee_id", "==", owner_employee_id)
        if unprocessed_only:
            query = query.where("processed", "==", False)
        docs = query.stream()
        mails: list[MailSeed] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                mails.append(MailSeed.from_dict(data))
        return mails

    def delete_mail_seed(self, mail_id: str) -> None:
        """Delete a mail seed by mail_id (§16.3 Gmail 14-day retention)."""
        self.db.collection("mail_seeds").document(mail_id).delete()

    # -------------------------------------------------------------------------
    # Card operations (cards collection)
    # -------------------------------------------------------------------------

    def save_card(self, card: Card) -> None:
        """Save or update a secretary card."""
        doc_ref = self.db.collection("cards").document(card.card_id)
        doc_ref.set(card.to_dict())

    def get_card(self, card_id: str) -> Card | None:
        """Retrieve a card by card_id."""
        doc = self.db.collection("cards").document(card_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return Card.from_dict(data)

    def list_cards(
        self, owner_employee_id: str | None = None, status: str | None = None
    ) -> list[Card]:
        """List cards, optionally filtered by owner_employee_id and/or status."""
        col = self.db.collection("cards")
        query = col
        if owner_employee_id is not None:
            query = query.where("owner_employee_id", "==", owner_employee_id)
        if status is not None:
            query = query.where("status", "==", status)
        docs = query.stream()
        cards: list[Card] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                cards.append(Card.from_dict(data))
        return cards

    def find_open_card_for_task(self, owner_employee_id: str, task_id: str) -> Card | None:
        """Find an open stagnation card for the given task and owner."""
        col = self.db.collection("cards")
        docs = (
            col.where("owner_employee_id", "==", owner_employee_id)
            .where("type", "==", "stagnation")
            .where("status", "==", "open")
            .where("payload.task_id", "==", task_id)
            .limit(1)
            .stream()
        )
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                return Card.from_dict(data)
        return None

    def find_cards_for_task(self, owner_employee_id: str, task_id: str) -> list[Card]:
        """Find all stagnation cards for the given task and owner."""
        col = self.db.collection("cards")
        docs = (
            col.where("owner_employee_id", "==", owner_employee_id)
            .where("type", "==", "stagnation")
            .where("payload.task_id", "==", task_id)
            .stream()
        )
        cards: list[Card] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if data:
                cards.append(Card.from_dict(data))
        return cards

    def try_confirm_card(self, card_id: str) -> tuple[Card | None, bool]:
        """Atomically transition a card's status from 'open' to 'confirmed' (CAS, §14.4)."""
        doc_ref = self.db.collection("cards").document(card_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> tuple[Card | None, bool]:
            snapshot = doc_ref.get(transaction=txn)
            if hasattr(snapshot, "exists") and not snapshot.exists:
                return None, False
            data = snapshot.to_dict() if hasattr(snapshot, "to_dict") else None
            if not data:
                return None, False
            card = Card.from_dict(data)
            if card.status != "open":
                return card, False
            card.status = "confirmed"
            card.updated_at = utc_now_iso()
            txn.set(doc_ref, card.to_dict())
            return card, True

        return _txn(transaction)

    def find_card_by_domain_key(
        self, owner_employee_id: str, card_type: str, domain_key: str
    ) -> Card | None:
        """Find a card for (owner, type, domain_key) (C-18 legacy-id reuse, round-5 ledger K-1).

        Queries by owner_employee_id only (single .where(), Firestore-index
        friendly and mock-friendly) and filters type/domain_key in-memory —
        the demo-scale per-owner card count makes this cheap. When multiple
        docs match, an 'open' one is preferred over any resolved/terminal one;
        if none is open, the most recently updated match is returned (round-5
        ledger K-1 — an open card is always the live state for that domain key).
        """
        key_field = _card_domain_key_field(card_type)
        if key_field is None:
            return None
        col = self.db.collection("cards")
        docs = col.where("owner_employee_id", "==", owner_employee_id).stream()
        matches: list[dict[str, Any]] = []
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if not data:
                continue
            if data.get("type") != card_type:
                continue
            if str(data.get("payload", {}).get(key_field)) == str(domain_key):
                matches.append(data)
        if not matches:
            return None
        open_matches = [d for d in matches if d.get("status") == "open"]
        pool = open_matches or matches
        latest = max(pool, key=lambda d: d.get("updated_at") or "")
        return Card.from_dict(latest)

    @staticmethod
    def _merge_policy_hold(
        new_payload: dict[str, Any],
        existing_payload: dict[str, Any],
        incoming_payload: dict[str, Any],
        clear_policy_hold: bool,
    ) -> None:
        """round-6 ledger W-1/W-3: mirrors InMemoryStore._merge_policy_hold —
        carry an existing policy_hold marker forward into new_payload (in
        place) unless the incoming payload sets its own (which always wins
        verbatim) or the caller explicitly asks to drop it via
        clear_policy_hold=True. Also carries the audit_epoch lifecycle marker
        (round-7 C-33), which is never cleared — stamped at reopen, it must
        survive every later write of that lifecycle."""
        if "policy_hold" not in incoming_payload:
            if clear_policy_hold:
                new_payload.pop("policy_hold", None)
            elif "policy_hold" in existing_payload:
                new_payload["policy_hold"] = existing_payload["policy_hold"]
        if "audit_epoch" not in incoming_payload and "audit_epoch" in existing_payload:
            new_payload["audit_epoch"] = existing_payload["audit_epoch"]

    def upsert_card_gated(
        self,
        card: Card,
        expected_policy_updated_at: str | None = None,
        clear_policy_hold: bool = False,
    ) -> tuple[Card, str, str | None, str | None]:
        """Conditionally create/update a card under a single CAS (autonomous-agent design §3, round-5/6 ledger B/W-2).

        round-6 ledger W-2: the deterministic-id existence check AND the
        legacy/domain-key lookup (open-preferring, C-18/K-1) both run INSIDE
        the transaction now (via txn.get()), not as plain pre-transaction
        reads — a doc created by another writer between the old pre-read and
        the transaction's write could otherwise be missed, leaving two docs
        for the same domain key. See Store.upsert_card_gated's docstring for
        the never-downgrade (W-1/C-29) and policy_hold carry-forward
        (W-1/W-3) merge rules also enforced inside this transaction.
        """
        col = self.db.collection("cards")
        policy_col = self.db.collection("autonomy_policies")
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> tuple[Card, str, str | None, str | None]:
            target_id = card.card_id
            doc_ref = col.document(target_id)
            snapshot = doc_ref.get(transaction=txn)
            has_existing = not (hasattr(snapshot, "exists") and not snapshot.exists)
            data = snapshot.to_dict() if has_existing and hasattr(snapshot, "to_dict") else None
            existing = Card.from_dict(data) if data else None

            if existing is None:
                # W-2: legacy-id reuse lookup also happens inside the
                # transaction, against the SAME read snapshot the write below
                # is conditioned on.
                key_field = _card_domain_key_field(card.type)
                domain_key = card.payload.get(key_field) if key_field else None
                if domain_key is not None:
                    legacy_query = col.where("owner_employee_id", "==", card.owner_employee_id)
                    matches: list[dict[str, Any]] = []
                    for doc in txn.get(legacy_query):
                        doc_data = doc.to_dict() if hasattr(doc, "to_dict") else None
                        if not doc_data:
                            continue
                        if doc_data.get("type") != card.type:
                            continue
                        if str(doc_data.get("payload", {}).get(key_field)) == str(domain_key):
                            matches.append(doc_data)
                    if matches:
                        open_matches = [d for d in matches if d.get("status") == "open"]
                        pool = open_matches or matches
                        latest = max(pool, key=lambda d: d.get("updated_at") or "")
                        target_id = latest.get("card_id") or target_id
                        doc_ref = col.document(target_id)
                        existing = Card.from_dict(latest)

            prev_status = existing.status if existing is not None else None
            prev_tier = existing.tier if existing is not None else None

            if existing is not None and existing.status in ("confirmed", "dismissed", "applied"):
                return existing, "rejected_terminal", prev_status, prev_tier

            if expected_policy_updated_at is not None:
                policy_snapshot = policy_col.document(card.owner_employee_id).get(transaction=txn)
                policy_data = (
                    policy_snapshot.to_dict()
                    if hasattr(policy_snapshot, "to_dict") and not (hasattr(policy_snapshot, "exists") and not policy_snapshot.exists)
                    else None
                )
                current_policy_updated_at = policy_data.get("updated_at", "") if policy_data else ""
                if current_policy_updated_at != expected_policy_updated_at:
                    ref = existing if existing is not None else card
                    return ref, "rejected_policy_changed", prev_status, prev_tier

            now = utc_now_iso()

            if existing is None:
                new_card = Card(
                    card_id=target_id,
                    owner_employee_id=card.owner_employee_id,
                    type=card.type,
                    tier=card.tier,
                    payload=dict(card.payload),
                    status=card.status or "open",
                    resolved_reason=card.resolved_reason,
                    linked_query_audit_id=card.linked_query_audit_id,
                    created_at=card.created_at or now,
                    updated_at=now,
                )
                txn.set(doc_ref, new_card.to_dict())
                return new_card, "created", prev_status, prev_tier

            if existing.status == "resolved" and card.status != "open":
                # round-5 ledger B: duplicate resolve against an already-resolved
                # card is a no-op — never clobber the first resolve's
                # resolved_reason/timestamps.
                return existing, "unchanged", prev_status, prev_tier

            # round-6 ledger W-1/C-29: never-downgrade guard (mirrors
            # InMemoryStore — see Store.upsert_card_gated's docstring).
            if (
                existing.status == "open"
                and existing.tier == "request_draft"
                and card.tier == "notice"
                and card.status == "open"
            ):
                merged = Card.from_dict(existing.to_dict())
                new_payload = dict(existing.payload)
                for key in ("task_id", "task_title", "score", "evidence_line"):
                    if key in card.payload:
                        new_payload[key] = card.payload[key]
                self._merge_policy_hold(new_payload, existing.payload, card.payload, clear_policy_hold)
                merged.type = card.type
                merged.payload = new_payload
                merged.updated_at = now
                txn.set(doc_ref, merged.to_dict())
                return merged, "updated", prev_status, prev_tier

            merged = Card.from_dict(existing.to_dict())
            merged.type = card.type
            merged.tier = card.tier
            new_payload = dict(card.payload)
            self._merge_policy_hold(new_payload, existing.payload, card.payload, clear_policy_hold)
            merged.payload = new_payload
            merged.updated_at = now

            if existing.status == "resolved":
                # round-7 C-33: stamp a fresh lifecycle marker at reopen (see
                # InMemoryStore.upsert_card_gated for the rationale).
                merged.status = "open"
                merged.resolved_reason = None
                merged.payload["audit_epoch"] = now
                outcome = "reopened"
            else:
                merged.status = card.status
                merged.resolved_reason = card.resolved_reason if card.status == "resolved" else None
                outcome = "updated"

            txn.set(doc_ref, merged.to_dict())
            return merged, outcome, prev_status, prev_tier

        return _txn(transaction)

    # -------------------------------------------------------------------------
    # Autonomy policy operations (autonomy_policies collection)
    # -------------------------------------------------------------------------

    def get_autonomy_policy(self, employee_id: str) -> AutonomyPolicy | None:
        doc = self.db.collection("autonomy_policies").document(employee_id).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return AutonomyPolicy.from_dict(data)

    def save_autonomy_policy(self, policy: AutonomyPolicy) -> None:
        doc_ref = self.db.collection("autonomy_policies").document(policy.employee_id)
        doc_ref.set(policy.to_dict())

    # -------------------------------------------------------------------------
    # Message create-only operation (Z-4)
    # -------------------------------------------------------------------------

    def save_message_if_absent(self, message: Message) -> bool:
        """Create-only save via Firestore's native atomic create() (Z-4).

        create() raises AlreadyExists if the doc is already present; that is
        the "no-op, first writer wins" case, not an error to propagate.
        """
        doc_ref = self.db.collection("messages").document(message.audit_id)
        try:
            doc_ref.create(message.to_dict())
            return True
        except AlreadyExists:
            return False

    # -------------------------------------------------------------------------
    # Sweep run claim/lifecycle (sweep_runs collection)
    # -------------------------------------------------------------------------

    def claim_sweep_run(
        self, run_key: str, origin: str, date: str, ttl_seconds: int
    ) -> tuple[str | None, str]:
        doc_ref = self.db.collection("sweep_runs").document(run_key)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> tuple[str | None, str]:
            snapshot = doc_ref.get(transaction=txn)
            data = (
                snapshot.to_dict()
                if hasattr(snapshot, "to_dict") and not (hasattr(snapshot, "exists") and not snapshot.exists)
                else None
            )
            now = datetime.now(timezone.utc)

            def _claim() -> tuple[str, str]:
                token = uuid.uuid4().hex
                txn.set(
                    doc_ref,
                    {
                        "run_key": run_key,
                        "status": "running",
                        "claim_token": token,
                        "started_at": now.isoformat(),
                        "origin": origin,
                        "date": date,
                    },
                )
                return token, "claimed"

            if data is None or data.get("status") == "failed":
                return _claim()
            if data.get("status") == "done":
                return None, "done"
            if data.get("status") == "running":
                started = _parse_iso_or_none(data.get("started_at"))
                stale = started is None or (now - started).total_seconds() >= ttl_seconds
                if stale:
                    return _claim()
                return None, "in_progress"
            return _claim()

        return _txn(transaction)

    def finish_sweep_run(self, run_key: str, claim_token: str, summary: dict[str, Any]) -> bool:
        doc_ref = self.db.collection("sweep_runs").document(run_key)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> bool:
            snapshot = doc_ref.get(transaction=txn)
            data = (
                snapshot.to_dict()
                if hasattr(snapshot, "to_dict") and not (hasattr(snapshot, "exists") and not snapshot.exists)
                else None
            )
            if data is None or data.get("claim_token") != claim_token:
                return False
            data["status"] = "done"
            data["finished_at"] = utc_now_iso()
            data["summary"] = dict(summary)
            txn.set(doc_ref, data)
            return True

        return _txn(transaction)

    def fail_sweep_run(self, run_key: str, claim_token: str, error: str) -> bool:
        doc_ref = self.db.collection("sweep_runs").document(run_key)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> bool:
            snapshot = doc_ref.get(transaction=txn)
            data = (
                snapshot.to_dict()
                if hasattr(snapshot, "to_dict") and not (hasattr(snapshot, "exists") and not snapshot.exists)
                else None
            )
            if data is None or data.get("claim_token") != claim_token:
                return False
            data["status"] = "failed"
            data["finished_at"] = utc_now_iso()
            data["error"] = error
            txn.set(doc_ref, data)
            return True

        return _txn(transaction)

    def get_sweep_run(self, run_key: str) -> dict[str, Any] | None:
        doc = self.db.collection("sweep_runs").document(run_key).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        return dict(data) if data else None

    def get_latest_sweep_run(self) -> dict[str, Any] | None:
        """No order_by (avoids requiring a composite index, matching this
        module's existing query style): filters to status=='done' with a
        single-field equality query, then picks the max finished_at locally."""
        col = self.db.collection("sweep_runs")
        docs = col.where("status", "==", "done").stream()
        latest: dict[str, Any] | None = None
        for doc in docs:
            data = doc.to_dict() if hasattr(doc, "to_dict") else None
            if not data:
                continue
            if latest is None or (data.get("finished_at") or "") > (latest.get("finished_at") or ""):
                latest = data
        return dict(latest) if latest is not None else None

    # -------------------------------------------------------------------------
    # Identity operations (identities collection, design §16.1 Part A)
    # -------------------------------------------------------------------------

    def get_identity(self, email: str) -> str | None:
        """Resolve a verified email to its employee_id, or None if unregistered."""
        doc = self.db.collection("identities").document(email.strip().lower()).get()
        if hasattr(doc, "exists") and not doc.exists:
            return None
        data = doc.to_dict() if hasattr(doc, "to_dict") else None
        if not data:
            return None
        return data.get("employee_id")

    def save_identity(self, email: str, employee_id: str) -> None:
        """Register (or update) the employee_id bound to an email address."""
        normalized_email = email.strip().lower()
        doc_ref = self.db.collection("identities").document(normalized_email)
        doc_ref.set({"email": normalized_email, "employee_id": employee_id})

    def try_transition_ask_consent(
        self, ask_audit_id: str, agent_id: str, new_consent_state: str
    ) -> tuple[Message | None, str]:
        """Atomically verify and transition an ask message's consent_state (W-1, §16.1)."""
        doc_ref = self.db.collection("messages").document(ask_audit_id)
        transaction = self.db.transaction()

        @firestore.transactional
        def _txn(txn: Any) -> tuple[Message | None, str]:
            snapshot = doc_ref.get(transaction=txn)
            if hasattr(snapshot, "exists") and not snapshot.exists:
                return None, "not_found"
            data = snapshot.to_dict() if hasattr(snapshot, "to_dict") else None
            if not data:
                return None, "not_found"
            msg = Message.from_dict(data)
            if msg.to_entity != agent_id or msg.intent not in ("connect_ask", "connect_ask_private"):
                return msg, "forbidden"
            if msg.consent_state != "pending":
                return msg, "conflict"
            msg.consent_state = new_consent_state
            txn.set(doc_ref, msg.to_dict())
            return msg, "ok"

        return _txn(transaction)

    def clear(self) -> None:
        """Clear all stored data across all collections."""
        for col_name in (
            "agents",
            "profiles",
            "messages",
            "tasks",
            "schedules",
            "mail_seeds",
            "cards",
            "identities",
            "autonomy_policies",
            "sweep_runs",
        ):
            col = self.db.collection(col_name)
            # Unbounded streams over large collections (400 profiles with
            # 3072-dim embeddings) hit Firestore's query timeout, and batched
            # deletes of those docs exceed the per-commit index-entry limit;
            # page the query and delete documents individually.
            while True:
                docs = list(col.limit(300).get())
                if not docs:
                    break
                for doc in docs:
                    doc.reference.delete()
