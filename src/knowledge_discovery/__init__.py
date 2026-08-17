"""Knowledge Discovery Core Package (Milestone 1).

Connects implicit knowledge across human agents while enforcing strict visibility and review rules.
"""

from knowledge_discovery.matching import (
    ConnectionInferencer,
    DeterministicEmbedder,
    DroppedCandidate,
    Embedder,
    FakeConnectionInferencer,
    FunnelCandidate,
    MatchingEngine,
    MatchingResult,
    QualifiedCandidate,
)
from knowledge_discovery.models import (
    Agent,
    Attachment,
    ConnectionDetails,
    ConnectionInferenceResult,
    Message,
    Profile,
    ProfileItem,
    RequesterCandidateStatus,
    utc_now_iso,
)
from knowledge_discovery.schemas import SchemaRegistry
from knowledge_discovery.service import (
    ConsentResult,
    KnowledgeDiscoveryService,
    QuerySubmissionResult,
)
from knowledge_discovery.store import InMemoryStore, Store
from knowledge_discovery.transmission import TransmissionError, TransmissionLayer

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "ProfileItem",
    "Profile",
    "Attachment",
    "Message",
    "ConnectionDetails",
    "ConnectionInferenceResult",
    "FunnelCandidate",
    "RequesterCandidateStatus",
    "utc_now_iso",
    "Store",
    "InMemoryStore",
    "SchemaRegistry",
    "TransmissionLayer",
    "TransmissionError",
    "Embedder",
    "DeterministicEmbedder",
    "ConnectionInferencer",
    "FakeConnectionInferencer",
    "QualifiedCandidate",
    "DroppedCandidate",
    "MatchingResult",
    "MatchingEngine",
    "KnowledgeDiscoveryService",
    "QuerySubmissionResult",
    "ConsentResult",
]
