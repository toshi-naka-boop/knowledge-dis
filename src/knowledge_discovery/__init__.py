"""Knowledge Discovery Core Package (Milestone 2).

Connects tacit and explicit knowledge across human agents while enforcing strict visibility and review rules.
"""

# Optional-dependency modules: importing them must not break environments where
# google-cloud-firestore / google-genai / fastapi are not installed (M1 test envs)
try:
    from knowledge_discovery.firestore_store import FirestoreStore
except ImportError:  # pragma: no cover
    FirestoreStore = None  # type: ignore[assignment,misc]
try:
    from knowledge_discovery.gemini_adapters import (
        GeminiConnectionInferencer,
        GeminiEmbedder,
    )
except ImportError:  # pragma: no cover
    GeminiConnectionInferencer = None  # type: ignore[assignment,misc]
    GeminiEmbedder = None  # type: ignore[assignment,misc]
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

try:
    from knowledge_discovery.server import create_app
except ImportError:  # pragma: no cover
    create_app = None  # type: ignore[assignment]
from knowledge_discovery.service import (
    ConsentResult,
    KnowledgeDiscoveryService,
    QuerySubmissionResult,
)
from knowledge_discovery.store import InMemoryStore, Store
from knowledge_discovery.transmission import TransmissionError, TransmissionLayer

__version__ = "0.2.0"

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
    "FirestoreStore",
    "SchemaRegistry",
    "TransmissionLayer",
    "TransmissionError",
    "Embedder",
    "DeterministicEmbedder",
    "GeminiEmbedder",
    "ConnectionInferencer",
    "FakeConnectionInferencer",
    "GeminiConnectionInferencer",
    "QualifiedCandidate",
    "DroppedCandidate",
    "MatchingResult",
    "MatchingEngine",
    "KnowledgeDiscoveryService",
    "QuerySubmissionResult",
    "ConsentResult",
    "create_app",
]
