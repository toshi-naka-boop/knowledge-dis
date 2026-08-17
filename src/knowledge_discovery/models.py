"""Data models for knowledge-discovery core backend (Milestone 1).

Follows design.md §3 specifications:
- Agent (agent discovery registry)
- Profile & ProfileItem (implicit/explicit knowledge profiles)
- Message & Attachment (envelope and audit log records)
- Inference and Matching data structures
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Agent:
    """Agent record for the agents registry (agent discovery).

    Attributes:
        agent_id: Unique identifier for the agent (e.g., 'agent_001').
        employee_id: Identifier of the corresponding employee.
        display_name: Human-readable display name of the agent/employee.
        supported_intents: List of intents this agent can receive.
        endpoint: Invocation destination or logical name.
        registered_at: ISO8601 timestamp of registration.
        active: Whether the agent is currently active and can receive dispatches.
    """

    agent_id: str
    employee_id: str
    display_name: str
    supported_intents: list[str] = field(default_factory=list)
    endpoint: str = ""
    registered_at: str = field(default_factory=utc_now_iso)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "employee_id": self.employee_id,
            "display_name": self.display_name,
            "supported_intents": list(self.supported_intents),
            "endpoint": self.endpoint,
            "registered_at": self.registered_at,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        return cls(
            agent_id=data["agent_id"],
            employee_id=data["employee_id"],
            display_name=data.get("display_name", ""),
            supported_intents=list(data.get("supported_intents", [])),
            endpoint=data.get("endpoint", ""),
            registered_at=data.get("registered_at", utc_now_iso()),
            active=data.get("active", True),
        )


@dataclass
class ProfileItem:
    """Individual item within an employee's knowledge profile.

    Attributes:
        key: Item key (e.g., 'current_work', 'expertise', 'background').
        body: Text content describing the knowledge or background.
        source: Provenance of the item ('job_doc' | 'seed_synth' | etc.).
        visibility: 'public' or 'private'.
        reviewed: Whether the item has been reviewed by the employee.
    """

    key: str
    body: str
    source: str = "job_doc"
    visibility: str = "public"
    reviewed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "body": self.body,
            "source": self.source,
            "visibility": self.visibility,
            "reviewed": self.reviewed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileItem:
        return cls(
            key=data["key"],
            body=data.get("body", ""),
            source=data.get("source", "job_doc"),
            visibility=data.get("visibility", "public"),
            reviewed=data.get("reviewed", False),
        )


@dataclass
class Profile:
    """Employee knowledge profile.

    Attributes:
        employee_id: Identifier of the employee.
        name: Name of the employee.
        role: Job role / title.
        items: List of ProfileItem objects.
        embedding: Vector embedding generated from all items (public + private).
    """

    employee_id: str
    name: str
    role: str
    items: list[ProfileItem] = field(default_factory=list)
    embedding: list[float] | None = None

    def get_item(self, key: str) -> ProfileItem | None:
        """Find profile item by its key."""
        for item in self.items:
            if item.key == key:
                return item
        return None

    def is_item_private(self, key: str) -> bool:
        """Check if a specific key has visibility == 'private'."""
        item = self.get_item(key)
        return item is not None and item.visibility == "private"

    def has_any_private(self, keys: list[str]) -> bool:
        """Return True if any of the given keys corresponds to a private item."""
        for k in keys:
            if self.is_item_private(k):
                return True
        return False

    def get_full_text(self) -> str:
        """Combine all profile items (public and private) into text for embedding."""
        parts = [f"{self.name} - {self.role}"]
        for item in self.items:
            parts.append(f"{item.key}: {item.body}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "role": self.role,
            "items": [item.to_dict() for item in self.items],
            "embedding": list(self.embedding) if self.embedding is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        raw_items = data.get("items", [])
        items = [
            ProfileItem.from_dict(item) if isinstance(item, dict) else item
            for item in raw_items
        ]
        return cls(
            employee_id=data["employee_id"],
            name=data.get("name", ""),
            role=data.get("role", ""),
            items=items,
            embedding=data.get("embedding"),
        )


@dataclass
class Attachment:
    """Optional attachment included in decline_with_reason.

    Attributes:
        type: 'link' | 'text' | 'doc'.
        content: Content URL, text snippet, or static document ID.
    """

    type: str  # "link" | "text" | "doc"
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attachment:
        return cls(
            type=data["type"],
            content=data["content"],
        )


@dataclass
class Message:
    """Message envelope serving dual purpose as operational dispatch and audit log.

    Attributes:
        audit_id: Unique message identifier.
        from_entity: Sender identifier (agent_id, employee_id, or 'system').
        to_entity: Recipient identifier (agent_id, employee_id, or 'system').
        intent: Message intent type.
        payload_type: Schema type of the payload.
        payload: Actual payload dict.
        audit_payload: Masked audit view payload if private items cited; None otherwise.
        consent_state: 'n/a' | 'pending' | 'granted' | 'declined'.
        timestamp: ISO8601 timestamp.
        rejected: Whether the message was rejected at transmission layer.
    """

    audit_id: str
    from_entity: str
    to_entity: str
    intent: str
    payload_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    audit_payload: dict[str, Any] | None = None
    consent_state: str = "n/a"
    timestamp: str = field(default_factory=utc_now_iso)
    rejected: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert Message to dictionary matching Firestore schema."""
        return {
            "audit_id": self.audit_id,
            "from": self.from_entity,
            "to": self.to_entity,
            "intent": self.intent,
            "payload_type": self.payload_type,
            "payload": dict(self.payload),
            "audit_payload": dict(self.audit_payload) if self.audit_payload is not None else None,
            "consent_state": self.consent_state,
            "timestamp": self.timestamp,
            "rejected": self.rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        from_entity = data.get("from") or data.get("from_entity", "")
        to_entity = data.get("to") or data.get("to_entity", "")
        return cls(
            audit_id=data["audit_id"],
            from_entity=from_entity,
            to_entity=to_entity,
            intent=data.get("intent", ""),
            payload_type=data.get("payload_type", ""),
            payload=dict(data.get("payload", {})),
            audit_payload=dict(data["audit_payload"]) if data.get("audit_payload") is not None else None,
            consent_state=data.get("consent_state", "n/a"),
            timestamp=data.get("timestamp", utc_now_iso()),
            rejected=data.get("rejected", False),
        )


@dataclass
class ConnectionDetails:
    """Details of a verbalized connection between query and profile."""

    reason_text: str
    score: float


@dataclass
class ConnectionInferenceResult:
    """Result of Stage 2 independent connection inference for a single candidate.

    Attributes:
        connection: ConnectionDetails if meaningful connection found; None if no connection.
        no_connection_reason: Explanation if connection is None.
        cited_item_keys: Profile item keys cited during inference (must be present even when connection is None).
    """

    connection: ConnectionDetails | None
    no_connection_reason: str | None = None
    cited_item_keys: list[str] = field(default_factory=list)


@dataclass
class FunnelCandidate:
    """Candidate representation for Screen Funnel (scale demonstration)."""

    employee_id: str
    name: str
    role: str
    similarity: float


@dataclass
class RequesterCandidateStatus:
    """Candidate status as viewed from requester UI.

    Never reveals whether connection was based on private profile items.
    Status state values:
    - 'pending': 返答待ち
    - 'matched': つながりました（MTG提案あり）
    - 'declined': 今回は難しいそうです（理由+添付表示）
    """

    candidate_id: str
    candidate_name: str
    state: str  # 'pending' | 'matched' | 'declined'
    meeting_duration: int | None = None
    decline_reason: str | None = None
    attachment: dict[str, Any] | None = None
