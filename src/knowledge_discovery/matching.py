"""Exploratory matching engine (2 tracks × 2 stages) for knowledge discovery.

Follows design.md §2:
- Embedder interface + DeterministicEmbedder for vector similarity.
- ConnectionInferencer interface + FakeConnectionInferencer for candidate-isolated inference (C-17).
- Two Tracks (C-16):
  1. Screen Funnel (画面用ファネル): Top 20 across all profiles for scale demonstration.
  2. Delivery Ranking (配送用ランキング): Filtered to active registered agents.
- Two Stages:
  1. Vector similarity ranking with deterministic lower floor (VECTOR_FLOOR, C-23).
  2. Independent Stage-2 inference per candidate with connection score threshold and drop tracking.
"""

from __future__ import annotations

import math
import re
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from knowledge_discovery.models import (
    Agent,
    ConnectionDetails,
    ConnectionInferenceResult,
    FunnelCandidate,
    PreviewCandidate,
    Profile,
    ProfileItem,
)


class Embedder(ABC):
    """Abstract vector embedding interface."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate normalized vector embedding for text."""
        pass

    @abstractmethod
    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two normalized vectors."""
        pass


class DeterministicEmbedder(Embedder):
    """Deterministic, pure-Python embedder using token and character n-gram hashing."""

    def __init__(self, dimension: int = 128) -> None:
        self.dimension = dimension

    def _tokenize(self, text: str) -> list[str]:
        """Extract language-aware tokens.

        ASCII words: word + simple plural strip + 4-char prefix (character grams on
        English are dominated by common bigrams like 'on'/'in' and make unrelated
        texts look similar). Non-ASCII (CJK) words: the word plus character
        uni/bigrams, since spaceless CJK text does not split into words.
        """
        if not text:
            return []
        cleaned = text.lower().strip()
        tokens: list[str] = []
        for word in re.findall(r"[\w]+", cleaned):
            if word.isascii():
                base = word.rstrip("s") if len(word) > 3 else word
                tokens.append(base)
                if len(base) > 4:
                    tokens.append(base[:4])
            else:
                tokens.append(word)
                tokens.extend(word)
                tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
        return tokens

    def embed(self, text: str) -> list[float]:
        """Generate normalized vector embedding."""
        tokens = self._tokenize(text)
        vec = [0.0] * self.dimension
        if not tokens:
            return vec

        for token in tokens:
            # crc32 is stable across processes; builtin hash() is salted per run.
            # Signed hashing: colliding unrelated tokens cancel in expectation,
            # keeping cosine noise between unrelated texts below VECTOR_FLOOR
            data = token.encode("utf-8")
            idx = zlib.crc32(data) % self.dimension
            sign = 1.0 if zlib.crc32(data + b"#") & 1 else -1.0
            vec[idx] += sign

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity clamped to [0.0, 1.0]."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        return max(0.0, min(1.0, float(dot)))


class ConnectionInferencer(ABC):
    """Abstract interface for Stage 2 candidate-isolated connection inference (C-17).

    Strict process/data boundary: Each candidate inference receives ONLY the question
    and that specific candidate's profile.
    """

    @abstractmethod
    def infer_connection(self, question: str, profile: Profile) -> ConnectionInferenceResult:
        """Infer implicit/explicit connection between question and candidate profile."""
        pass


class FakeConnectionInferencer(ConnectionInferencer):
    """Configurable fake inferencer for tests and deterministic simulations."""

    def __init__(self) -> None:
        self._overrides: dict[str, ConnectionInferenceResult] = {}
        self.call_history: list[tuple[str, str]] = []  # (question, employee_id)

    def set_override(self, employee_id: str, result: ConnectionInferenceResult) -> None:
        """Set explicit override for an employee_id."""
        self._overrides[employee_id] = result

    def infer_connection(self, question: str, profile: Profile) -> ConnectionInferenceResult:
        """Infer connection with isolation logging."""
        self.call_history.append((question, profile.employee_id))

        if profile.employee_id in self._overrides:
            return self._overrides[profile.employee_id]

        # Default heuristic: shared language-aware tokens (same scheme as the
        # DeterministicEmbedder) between item body and question. Requires >= 2
        # shared tokens so single common words do not create a connection.
        _tok = DeterministicEmbedder()._tokenize
        q_tokens = set(_tok(question))
        matched_items = []
        for item in profile.items:
            overlap = len(q_tokens & set(_tok(item.body)))
            if overlap >= 2:
                matched_items.append(item)

        if matched_items:
            best_item = matched_items[0]
            reason = f"質問のテーマに関して「{best_item.key}」の知見（{best_item.body[:40]}...）を持っています"
            return ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text=reason, score=0.85),
                no_connection_reason=None,
                cited_item_keys=[best_item.key],
            )

        # No meaningful connection found
        first_key = []  # V-1: never fabricate a citation key
        return ConnectionInferenceResult(
            connection=None,
            no_connection_reason=f"質問「{question[:30]}」に対する意味のある接点が見つかりませんでした",
            cited_item_keys=first_key,
        )


@dataclass
class QualifiedCandidate:
    """Candidate that passed both vector floor and Stage 2 inference."""

    agent: Agent
    profile: Profile
    reason_text: str
    cited_item_keys: list[str]
    score: float


@dataclass
class DroppedCandidate:
    """Candidate that was dropped at Gate 1 (vector floor) or Gate 2 (inference null/threshold)."""

    agent: Agent
    profile: Profile
    reason_text: str
    cited_item_keys: list[str]
    score: float
    drop_stage: str  # 'vector_floor' | 'stage2_null' | 'stage2_threshold' | 'k_limit'


@dataclass
class MatchingResult:
    """Output of exploratory matching engine execution."""

    funnel_candidates: list[FunnelCandidate]
    qualified_candidates: list[QualifiedCandidate]
    dropped_candidates: list[DroppedCandidate]


class MatchingEngine:
    """Exploratory matching engine managing screen funnel and delivery ranking."""

    def __init__(
        self,
        embedder: Embedder | None = None,
        inferencer: ConnectionInferencer | None = None,
        vector_floor: float = 0.20,
        connection_threshold: float = 0.50,
        max_dispatch_k: int = 3,
        funnel_limit: int = 20,
    ) -> None:
        self.embedder = embedder or DeterministicEmbedder()
        self.inferencer = inferencer or FakeConnectionInferencer()
        self.vector_floor = vector_floor
        self.connection_threshold = connection_threshold
        self.max_dispatch_k = max_dispatch_k
        self.funnel_limit = funnel_limit

    def compute_profile_embedding(self, profile: Profile) -> list[float]:
        """Compute and set embedding and embedding_public for a profile.

        - embedding: generated from all items (public + private).
        - embedding_public: generated from only public items (preview search §14.4).
        """
        item_text = " ".join(item.body for item in profile.items)
        emb = self.embedder.embed(item_text)
        profile.embedding = emb

        public_items = [item for item in profile.items if item.visibility == "public"]
        public_item_text = " ".join(item.body for item in public_items)
        emb_pub = self.embedder.embed(public_item_text)
        profile.embedding_public = emb_pub

        return emb

    def preview_search(
        self,
        question: str,
        registered_agents: list[Agent],
        profiles: dict[str, Profile],
        max_candidates: int = 3,
        exclude_employee_id: str | None = None,
    ) -> list[PreviewCandidate]:
        """Execute pure, candidate-isolated preview search (public only, no side effects, §14.4).

        Strict Rules:
        1. 1st stage vector ranking strictly targets embedding_public.
        2. VECTOR_FLOOR is NOT applied to preview.
        3. 2nd stage inference receives ONLY a public-items view of the profile.
        4. Completely pure: zero message dispatch, zero notifications, zero candidate trace.
        """
        q_emb = self.embedder.embed(question)
        active_agents = [
            a for a in registered_agents
            if a.active and (exclude_employee_id is None or a.employee_id != exclude_employee_id)
        ]

        # 1st stage: rank by embedding_public
        ranked: list[tuple[Agent, Profile, float]] = []
        for agent in active_agents:
            prof = profiles.get(agent.employee_id)
            if prof is None:
                continue
            if prof.embedding_public is None:
                self.compute_profile_embedding(prof)
            sim = self.embedder.similarity(q_emb, prof.embedding_public or [])
            ranked.append((agent, prof, sim))

        # Sort descending by vector similarity
        ranked.sort(key=lambda x: x[2], reverse=True)

        candidates: list[PreviewCandidate] = []
        for agent, prof, sim in ranked:
            # Build strictly public-only profile context for isolated Stage-2 inference (C-26/X-1)
            public_items = [
                ProfileItem(
                    key=item.key,
                    body=item.body,
                    source=item.source,
                    visibility="public",
                    reviewed=item.reviewed,
                )
                for item in prof.items
                if item.visibility == "public"
            ]
            public_prof = Profile(
                employee_id=prof.employee_id,
                name=prof.name,
                role=prof.role,
                items=public_items,
                embedding=prof.embedding_public,
                embedding_public=prof.embedding_public,
            )

            res = self.inferencer.infer_connection(question, public_prof)
            if res.connection is not None and res.connection.score >= self.connection_threshold:
                # Cited item keys must only come from public items
                safe_cited = [k for k in res.cited_item_keys if public_prof.get_item(k) is not None]
                candidates.append(
                    PreviewCandidate(
                        employee_id=prof.employee_id,
                        name=prof.name,
                        reason_text=res.connection.reason_text,
                        cited_item_keys=safe_cited,
                        score=res.connection.score,
                    )
                )
                if len(candidates) >= max_candidates:
                    break

        return candidates

    def screen_funnel(self, question: str, all_profiles: list[Profile]) -> list[FunnelCandidate]:
        """Track 1: Screen funnel ranking top 20 across all profiles for scale display (C-16)."""
        q_emb = self.embedder.embed(question)
        scored: list[tuple[Profile, float]] = []

        for prof in all_profiles:
            if prof.embedding is None:
                self.compute_profile_embedding(prof)
            sim = self.embedder.similarity(q_emb, prof.embedding or [])
            scored.append((prof, sim))

        # Sort by similarity descending
        scored.sort(key=lambda x: x[1], reverse=True)

        top_profiles = scored[: self.funnel_limit]
        return [
            FunnelCandidate(
                employee_id=p.employee_id,
                name=p.name,
                role=p.role,
                similarity=sim,
            )
            for p, sim in top_profiles
        ]

    def delivery_ranking(
        self,
        question: str,
        registered_agents: list[Agent],
        profiles: dict[str, Profile],
    ) -> list[tuple[Agent, Profile, float]]:
        """Track 2: Delivery ranking filtered strictly to active registered agents (C-16)."""
        q_emb = self.embedder.embed(question)
        active_agents = [a for a in registered_agents if a.active]
        ranked: list[tuple[Agent, Profile, float]] = []

        for agent in active_agents:
            prof = profiles.get(agent.employee_id)
            if prof is None:
                continue
            if prof.embedding is None:
                self.compute_profile_embedding(prof)
            sim = self.embedder.similarity(q_emb, prof.embedding or [])
            ranked.append((agent, prof, sim))

        # Sort descending by vector similarity
        ranked.sort(key=lambda x: x[2], reverse=True)
        return ranked

    def run_matching(
        self,
        question: str,
        registered_agents: list[Agent],
        all_profiles: list[Profile],
    ) -> MatchingResult:
        """Execute full exploratory matching pipeline (2 tracks × 2 stages)."""
        profiles_by_id = {p.employee_id: p for p in all_profiles}

        # Track 1: Screen Funnel
        funnel = self.screen_funnel(question, all_profiles)

        # Track 2: Delivery Ranking
        delivery_candidates = self.delivery_ranking(question, registered_agents, profiles_by_id)

        qualified: list[QualifiedCandidate] = []
        dropped: list[DroppedCandidate] = []

        # Stage 2: Independent inference per candidate (C-17)
        for agent, profile, vector_sim in delivery_candidates:
            # Gate 1: Deterministic Vector Floor (C-23)
            if vector_sim < self.vector_floor:
                dropped.append(
                    DroppedCandidate(
                        agent=agent,
                        profile=profile,
                        reason_text=f"ベクトル類似度({vector_sim:.3f})が下限({self.vector_floor:.3f})を下回ったため落選",
                        cited_item_keys=[],
                        score=vector_sim,
                        drop_stage="vector_floor",
                    )
                )
                continue

            # Gate 2: Candidate-isolated inference (passing ONLY question and profile)
            inference_res = self.inferencer.infer_connection(question, profile)

            if inference_res.connection is None:
                dropped.append(
                    DroppedCandidate(
                        agent=agent,
                        profile=profile,
                        reason_text=inference_res.no_connection_reason or "意味のある接点が見つからないため落選",
                        cited_item_keys=list(inference_res.cited_item_keys),
                        score=0.0,
                        drop_stage="stage2_null",
                    )
                )
                continue

            if inference_res.connection.score < self.connection_threshold:
                dropped.append(
                    DroppedCandidate(
                        agent=agent,
                        profile=profile,
                        reason_text=f"接続スコア({inference_res.connection.score:.2f})が閾値({self.connection_threshold:.2f})未満のため落選",
                        cited_item_keys=list(inference_res.cited_item_keys),
                        score=inference_res.connection.score,
                        drop_stage="stage2_threshold",
                    )
                )
                continue

            # Candidate qualified
            qualified.append(
                QualifiedCandidate(
                    agent=agent,
                    profile=profile,
                    reason_text=inference_res.connection.reason_text,
                    cited_item_keys=list(inference_res.cited_item_keys),
                    score=inference_res.connection.score,
                )
            )

        # Limit to max k candidates
        if len(qualified) > self.max_dispatch_k:
            overflow = qualified[self.max_dispatch_k :]
            qualified = qualified[: self.max_dispatch_k]
            for c in overflow:
                dropped.append(
                    DroppedCandidate(
                        agent=c.agent,
                        profile=c.profile,
                        reason_text=f"最大配送数(k={self.max_dispatch_k})の上限による落選",
                        cited_item_keys=c.cited_item_keys,
                        score=c.score,
                        drop_stage="k_limit",
                    )
                )

        return MatchingResult(
            funnel_candidates=funnel,
            qualified_candidates=qualified,
            dropped_candidates=dropped,
        )
