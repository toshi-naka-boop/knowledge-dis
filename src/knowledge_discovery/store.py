"""Storage abstraction and in-memory implementation for knowledge discovery.

Follows design.md §3:
- Store interface defining contracts for agents, profiles, and messages.
- InMemoryStore providing a standalone, testable implementation without Firestore dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

from knowledge_discovery.models import Agent, Message, Profile


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

    def save_agent(self, agent: Agent) -> None:
        """Save or update an agent record in the registry."""
        # Store a deepcopy to avoid external mutable state side-effects
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
            # Update existing message in list and dict
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

    def clear(self) -> None:
        """Clear all stored data."""
        self._agents.clear()
        self._profiles.clear()
        self._messages.clear()
        self._messages_by_id.clear()
