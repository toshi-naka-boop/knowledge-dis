"""Gemini API adapters for vector embedding and candidate-isolated connection inference.

Follows design.md §2:
- GeminiEmbedder: Embedder implementation using Gemini embedding models.
- GeminiConnectionInferencer: Stage 2 candidate-isolated connection inference using Gemini 3.7 Flash.
- Strict requirement: Candidate receives ONLY the question and their own profile.
- Prompts explicitly allow connection: null ("意味のある接点が見つからなければ connection: null を返してよい（無理に理由をひねり出さない）").
- Structured JSON output with robust fallback on parse failure.
- API keys read from GEMINI_API_KEY environment variable (no hardcoded keys).
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from knowledge_discovery.matching import ConnectionInferencer, Embedder
from knowledge_discovery.models import (
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
)

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]


class GeminiEmbedder(Embedder):
    """Vector embedder powered by Gemini embedding models."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Initialize GeminiEmbedder.

        Args:
            api_key: Gemini API key. Defaults to GEMINI_API_KEY env var.
            model: Embedding model ID. Defaults to GEMINI_EMBEDDING_MODEL or 'text-embedding-004'.
            client: Optional pre-configured genai.Client instance (useful for testing/mocking).
        """
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
        if client is not None:
            self.client = client
        elif genai is not None and self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def embed(self, text: str) -> list[float]:
        """Generate normalized embedding vector for text using Gemini API."""
        if not text or not text.strip():
            return []

        if self.client is None:
            raise RuntimeError(
                "Gemini client is not initialized. Ensure GEMINI_API_KEY is set or google-genai is installed."
            )

        try:
            # google-genai SDK call
            response = self.client.models.embed_content(
                model=self.model,
                contents=text,
            )
            # Extract vector values from SDK response
            values: list[float] = []
            if hasattr(response, "embedding") and hasattr(response.embedding, "values"):
                values = [float(v) for v in response.embedding.values]
            elif hasattr(response, "embeddings") and len(response.embeddings) > 0:
                values = [float(v) for v in response.embeddings[0].values]
            elif isinstance(response, dict):
                if "embedding" in response and "values" in response["embedding"]:
                    values = [float(v) for v in response["embedding"]["values"]]
                elif "embeddings" in response and len(response["embeddings"]) > 0:
                    values = [float(v) for v in response["embeddings"][0]["values"]]

            # L2 normalize
            norm = math.sqrt(sum(v * v for v in values))
            if norm > 0.0:
                return [v / norm for v in values]
            return values
        except Exception as exc:
            raise RuntimeError(f"Gemini embedding API call failed: {exc}") from exc

    def similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity between two normalized vectors, clamped to [0.0, 1.0]."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        return max(0.0, min(1.0, float(dot)))


class GeminiConnectionInferencer(ConnectionInferencer):
    """Stage 2 candidate-isolated connection inferencer using Gemini 3.7 Flash (C-17)."""

    SYSTEM_PROMPT = """You are an expert AI knowledge broker analyzing tacit and explicit connections between a colleague's inquiry and a candidate's background.

Critical Rules:
1. Candidate Isolation: You receive ONLY the inquiry question and this single candidate's profile items.
2. Connection Reasoning: Identify direct and indirect/tacit connections (e.g. past career experience, related problem spaces, specialized domain context, client nuances).
3. Grounding & Citation: You MUST list the exact `cited_item_keys` (from the candidate's profile items) that support your reasoning.
4. Permission to Disclaim: 意味のある接点が見つからなければ connection: null を返してよい（無理に理由をひねり出さない）. If no meaningful, actionable connection exists, return `connection: null` and explain why in `no_connection_reason`.
5. Score: When connection is found, provide a confidence score between 0.0 and 1.0 (typical strong match is >= 0.70).

Output JSON Schema:
{
  "connection": {
    "reason_text": "string explaining the verbalized connection in concise English",
    "score": 0.85
  } | null,
  "no_connection_reason": "string explaining why there is no meaningful connection (if connection is null)" | null,
  "cited_item_keys": ["key1", "key2"]
}
"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Initialize GeminiConnectionInferencer.

        Args:
            api_key: Gemini API key. Defaults to GEMINI_API_KEY env var.
            model: Gemini model ID. Defaults to GEMINI_MODEL or 'gemini-3.7-flash'.
            client: Optional pre-configured genai.Client instance for testing.
        """
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
        if client is not None:
            self.client = client
        elif genai is not None and self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

    def _format_profile_context(self, profile: Profile) -> str:
        """Format candidate profile items into text for isolated inference."""
        lines = [
            f"Candidate: {profile.name} ({profile.role})",
            "Profile Items:",
        ]
        for item in profile.items:
            lines.append(f"- [{item.key}] (visibility={item.visibility}): {item.body}")
        return "\n".join(lines)

    def infer_connection(self, question: str, profile: Profile) -> ConnectionInferenceResult:
        """Perform isolated Stage 2 connection inference for a single candidate."""
        if self.client is None:
            raise RuntimeError(
                "Gemini client is not initialized. Ensure GEMINI_API_KEY is set or google-genai is installed."
            )

        candidate_context = self._format_profile_context(profile)
        user_prompt = f"""Inquiry Question:
\"{question}\"

{candidate_context}

Analyze if this candidate has relevant knowledge or background for the inquiry.
Output strictly valid JSON matching the required schema.
意味のある接点が見つからなければ connection: null を返してよい（無理に理由をひねり出さない）。"""

        all_item_keys = [item.key for item in profile.items]

        try:
            # Set up config with structured JSON output if types module available
            config_kwargs: dict[str, Any] = {}
            if types is not None and hasattr(types, "GenerateContentConfig"):
                config_kwargs["config"] = types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                )

            response = self.client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                **config_kwargs,
            )

            response_text = ""
            if hasattr(response, "text") and response.text:
                response_text = response.text
            elif isinstance(response, dict) and "text" in response:
                response_text = response["text"]
            elif isinstance(response, str):
                response_text = response

            # Clean code fences if present
            cleaned_text = response_text.strip()
            if cleaned_text.startswith("```"):
                cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text)
                cleaned_text = re.sub(r"\s*```$", "", cleaned_text)

            data = json.loads(cleaned_text)
            return self._parse_json_result(data, all_item_keys)

        except Exception as exc:
            # Fallback to no_connection on API or parsing failure (fail-closed)
            first_key = all_item_keys[:1] if all_item_keys else []
            return ConnectionInferenceResult(
                connection=None,
                no_connection_reason=f"Inference evaluation unavailable: {exc}",
                cited_item_keys=first_key,
            )

    def _parse_json_result(
        self, data: dict[str, Any], all_item_keys: list[str]
    ) -> ConnectionInferenceResult:
        """Parse dictionary into ConnectionInferenceResult with strict validation."""
        cited_keys = data.get("cited_item_keys")
        if not isinstance(cited_keys, list) or not cited_keys:
            # Default to first available profile key if LLM did not provide one
            cited_keys = all_item_keys[:1] if all_item_keys else []

        connection_data = data.get("connection")
        if isinstance(connection_data, dict):
            reason_text = str(connection_data.get("reason_text", "")).strip()
            try:
                score = float(connection_data.get("score", 0.8))
            except (ValueError, TypeError):
                score = 0.8

            if reason_text:
                return ConnectionInferenceResult(
                    connection=ConnectionDetails(reason_text=reason_text, score=score),
                    no_connection_reason=None,
                    cited_item_keys=[str(k) for k in cited_keys],
                )

        # Connection is null or invalid
        no_conn_reason = data.get("no_connection_reason") or "No meaningful connection found"
        return ConnectionInferenceResult(
            connection=None,
            no_connection_reason=str(no_conn_reason),
            cited_item_keys=[str(k) for k in cited_keys],
        )
