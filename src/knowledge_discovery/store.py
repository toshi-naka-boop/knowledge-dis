"""Storage abstraction and in-memory implementation for knowledge discovery.

Follows design.md §3:
- Store interface defining contracts for agents, profiles, and messages.
- InMemoryStore providing a standalone, testable implementation without Firestore dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
import threading
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
