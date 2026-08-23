"""Firestore implementation of the Store interface for knowledge discovery.

Follows design.md §3:
- agents collection: agents/{agent_id}
- profiles collection: profiles/{employee_id}
- messages collection: messages/{audit_id}
- In-memory vector similarity computation over fetched profiles (avoiding composite index validation uncertainties).
"""

from __future__ import annotations

from typing import Any

from knowledge_discovery.models import (
    Agent,
    Card,
    MailSeed,
    Message,
    Profile,
    Schedule,
    Task,
    utc_now_iso,
)
from knowledge_discovery.store import Store

try:
    from google.cloud import firestore
except ImportError:
    firestore = None  # type: ignore[assignment]


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
        ):
            col = self.db.collection(col_name)
            docs = col.stream()
            for doc in docs:
                doc.reference.delete()
