"""Unit tests for GeminiEmbedder and GeminiConnectionInferencer."""

import json
import math
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.gemini_adapters import GeminiConnectionInferencer, GeminiEmbedder
from knowledge_discovery.models import Profile, ProfileItem


class TestGeminiEmbedder(unittest.TestCase):
    """Tests for GeminiEmbedder vector normalization and similarity calculations."""

    def test_embedder_normalization_and_similarity(self) -> None:
        mock_client = MagicMock()
        # Mock embedding return with unnormalized vector [3.0, 4.0] (norm = 5.0)
        mock_embedding = MagicMock()
        mock_embedding.values = [3.0, 4.0]
        mock_response = MagicMock()
        mock_response.embedding = mock_embedding
        mock_client.models.embed_content.return_value = mock_response

        embedder = GeminiEmbedder(api_key="test-key", client=mock_client)
        vec = embedder.embed("healthcare staffing")

        self.assertEqual(len(vec), 2)
        # 3/5 = 0.6, 4/5 = 0.8
        self.assertAlmostEqual(vec[0], 0.6, places=4)
        self.assertAlmostEqual(vec[1], 0.8, places=4)

        # Norm should be 1.0
        norm = math.sqrt(vec[0] ** 2 + vec[1] ** 2)
        self.assertAlmostEqual(norm, 1.0, places=4)

        # Cosine similarity with self is 1.0
        sim_self = embedder.similarity(vec, vec)
        self.assertAlmostEqual(sim_self, 1.0, places=4)

        # Cosine similarity with orthogonal vector [-0.8, 0.6] -> 0.0
        sim_ortho = embedder.similarity(vec, [-0.8, 0.6])
        self.assertAlmostEqual(sim_ortho, 0.0, places=4)

    def test_embedder_empty_text(self) -> None:
        embedder = GeminiEmbedder(api_key="test-key", client=MagicMock())
        vec = embedder.embed("")
        self.assertEqual(vec, [])


class TestGeminiConnectionInferencer(unittest.TestCase):
    """Tests for GeminiConnectionInferencer prompt structure, parsing, and isolation."""

    def setUp(self) -> None:
        self.profile = Profile(
            employee_id="emp_marcus",
            name="Marcus Delgado",
            role="Commercial Broker",
            items=[
                ProfileItem(
                    key="current_work",
                    body="Brokers medical office buildings and tracks zoning and ADA requirements.",
                    visibility="public",
                ),
                ProfileItem(
                    key="expertise",
                    body="Knows which retail sites can convert to medical use.",
                    visibility="public",
                ),
            ],
        )

    def test_infer_connection_successful_match(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "connection": {
                "reason_text": "Marcus has deep expertise in zoning and retail-to-clinic conversions.",
                "score": 0.92,
            },
            "no_connection_reason": None,
            "cited_item_keys": ["current_work", "expertise"],
        })
        mock_client.models.generate_content.return_value = mock_response

        inferencer = GeminiConnectionInferencer(api_key="test-key", client=mock_client)
        res = inferencer.infer_connection(
            question="Looking for a site that can host a clinic with special zoning needs.",
            profile=self.profile,
        )

        self.assertIsNotNone(res.connection)
        self.assertEqual(res.connection.score, 0.92)
        self.assertIn("Marcus has deep expertise in zoning", res.connection.reason_text)
        self.assertEqual(res.cited_item_keys, ["current_work", "expertise"])
        self.assertIsNone(res.no_connection_reason)

        # Verify prompt included the required disclaimer permission
        call_args = mock_client.models.generate_content.call_args
        prompt_sent = call_args.kwargs.get("contents") or call_args[1].get("contents")
        self.assertIn("意味のある接点が見つからなければ connection: null を返してよい", prompt_sent)

    def test_infer_connection_explicit_null(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "connection": None,
            "no_connection_reason": "No meaningful connection between clinic zoning and corporate accounting.",
            "cited_item_keys": ["current_work"],
        })
        mock_client.models.generate_content.return_value = mock_response

        inferencer = GeminiConnectionInferencer(api_key="test-key", client=mock_client)
        res = inferencer.infer_connection(
            question="Clinic zoning inquiry",
            profile=self.profile,
        )

        self.assertIsNone(res.connection)
        self.assertIn("No meaningful connection", res.no_connection_reason)
        self.assertEqual(res.cited_item_keys, ["current_work"])

    def test_infer_connection_json_fallback_on_parse_error(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This is not valid JSON from the model."
        mock_client.models.generate_content.return_value = mock_response

        inferencer = GeminiConnectionInferencer(api_key="test-key", client=mock_client)
        res = inferencer.infer_connection(
            question="Any inquiry",
            profile=self.profile,
        )

        # Fail-closed: Must gracefully fall back to no_connection without crashing
        self.assertIsNone(res.connection)
        self.assertIsNotNone(res.no_connection_reason)
        self.assertTrue(len(res.cited_item_keys) > 0)


if __name__ == "__main__":
    unittest.main()
