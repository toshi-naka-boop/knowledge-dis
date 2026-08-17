"""Unit tests for matching.py in knowledge_discovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.matching import (
    DeterministicEmbedder,
    FakeConnectionInferencer,
    MatchingEngine,
)
from knowledge_discovery.models import (
    Agent,
    ConnectionDetails,
    ConnectionInferenceResult,
    Profile,
    ProfileItem,
)


class TestMatching(unittest.TestCase):
    """Tests for exploratory matching engine, embedder, and inferencer."""

    def setUp(self) -> None:
        self.embedder = DeterministicEmbedder()
        self.inferencer = FakeConnectionInferencer()
        self.engine = MatchingEngine(
            embedder=self.embedder,
            inferencer=self.inferencer,
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )

    def test_deterministic_embedder(self) -> None:
        text1 = "製造業の生産管理システム開発"
        text2 = "生産管理と工場IoTの改善"
        text3 = "決算書と財務諸表の会計監査業務"

        v1 = self.embedder.embed(text1)
        v2 = self.embedder.embed(text2)
        v3 = self.embedder.embed(text3)

        self.assertEqual(len(v1), 128)
        sim_1_2 = self.embedder.similarity(v1, v2)
        sim_1_3 = self.embedder.similarity(v1, v3)

        # High similarity for related manufacturing topics, lower for accounting
        self.assertGreater(sim_1_2, 0.20)
        self.assertGreater(sim_1_2, sim_1_3)

    def test_fake_connection_inferencer_isolation(self) -> None:
        # C-17: Process/data boundary: Inferencer receives ONLY question and one candidate profile
        prof = Profile(
            employee_id="emp_01",
            name="Alice",
            role="SE",
            items=[ProfileItem(key="current_work", body="生産管理パッケージの開発")],
        )
        res = self.inferencer.infer_connection("生産管理の現場知識について", prof)
        self.assertIsNotNone(res.connection)
        self.assertIn("current_work", res.cited_item_keys)
        self.assertEqual(len(self.inferencer.call_history), 1)
        self.assertEqual(self.inferencer.call_history[0], ("生産管理の現場知識について", "emp_01"))

    def test_screen_funnel_scale_demonstration(self) -> None:
        # Create 30 profiles (simulating large employee base)
        profiles = []
        for i in range(30):
            body = "生産管理の専門家" if i < 5 else f"その他の業務 {i}"
            profiles.append(
                Profile(
                    employee_id=f"emp_{i}",
                    name=f"Employee {i}",
                    role="Staff",
                    items=[ProfileItem(key="current_work", body=body)],
                )
            )

        funnel = self.engine.screen_funnel("生産管理について", profiles)
        # Funnel limit is 20
        self.assertEqual(len(funnel), 20)
        # Top candidates should have higher similarity
        self.assertGreaterEqual(funnel[0].similarity, funnel[-1].similarity)

    def test_delivery_ranking_filters_to_active_agents_only(self) -> None:
        # 4 profiles exist
        p1 = Profile(employee_id="emp_1", name="Alice", role="SE", items=[ProfileItem(key="w", body="生産管理")])
        p2 = Profile(employee_id="emp_2", name="Bob", role="SE", items=[ProfileItem(key="w", body="生産管理")])
        p3 = Profile(employee_id="emp_3", name="Charlie", role="SE", items=[ProfileItem(key="w", body="生産管理")])
        p4 = Profile(employee_id="emp_4", name="David", role="SE", items=[ProfileItem(key="w", body="生産管理")])

        profiles = {p.employee_id: p for p in [p1, p2, p3, p4]}

        # Only emp_1 and emp_2 are active agents. emp_3 is inactive, emp_4 is unregistered
        agents = [
            Agent(agent_id="a1", employee_id="emp_1", display_name="Alice", active=True),
            Agent(agent_id="a2", employee_id="emp_2", display_name="Bob", active=True),
            Agent(agent_id="a3", employee_id="emp_3", display_name="Charlie", active=False),
        ]

        delivery_ranked = self.engine.delivery_ranking("生産管理", agents, profiles)
        self.assertEqual(len(delivery_ranked), 2)
        agent_ids = {a.agent_id for a, prof, sim in delivery_ranked}
        self.assertEqual(agent_ids, {"a1", "a2"})

    def test_vector_floor_and_stage2_drops(self) -> None:
        # Setup 4 agents
        # Candidate 1: strong match (passes vector floor & inference)
        p1 = Profile(employee_id="emp_1", name="Alice", role="SE", items=[ProfileItem(key="current_work", body="生産管理システムの設計")])
        a1 = Agent(agent_id="a1", employee_id="emp_1", display_name="Alice", active=True)

        # Candidate 2: passes vector floor, but Stage 2 inference returns connection=None
        p2 = Profile(employee_id="emp_2", name="Bob", role="SE", items=[ProfileItem(key="current_work", body="生産管理システムの運用")])
        a2 = Agent(agent_id="a2", employee_id="emp_2", display_name="Bob", active=True)
        self.inferencer.set_override(
            "emp_2",
            ConnectionInferenceResult(
                connection=None,
                no_connection_reason="質問に対する具体的な知見が見当たらないため落選",
                cited_item_keys=["current_work"],
            ),
        )

        # Candidate 3: passes vector floor, but Stage 2 inference score < 0.50
        p3 = Profile(employee_id="emp_3", name="Charlie", role="SE", items=[ProfileItem(key="current_work", body="生産管理システムのテスト")])
        a3 = Agent(agent_id="a3", employee_id="emp_3", display_name="Charlie", active=True)
        self.inferencer.set_override(
            "emp_3",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="わずかな接点", score=0.35),
                cited_item_keys=["current_work"],
            ),
        )

        # Candidate 4: completely unrelated (accounting), falls below vector floor (0.20)
        p4 = Profile(employee_id="emp_4", name="David", role="Accounting", items=[ProfileItem(key="current_work", body="有価証券報告書作成と月次決算業務")])
        a4 = Agent(agent_id="a4", employee_id="emp_4", display_name="David", active=True)

        agents = [a1, a2, a3, a4]
        all_profiles = [p1, p2, p3, p4]

        result = self.engine.run_matching("生産管理の改善事例", agents, all_profiles)

        # Only Candidate 1 should qualify
        self.assertEqual(len(result.qualified_candidates), 1)
        self.assertEqual(result.qualified_candidates[0].agent.agent_id, "a1")

        # Candidates 2, 3, 4 should be dropped
        self.assertEqual(len(result.dropped_candidates), 3)
        dropped_agent_ids = {d.agent.agent_id for d in result.dropped_candidates}
        self.assertEqual(dropped_agent_ids, {"a2", "a3", "a4"})

        # Check drop reasons and stages
        for d in result.dropped_candidates:
            if d.agent.agent_id == "a4":
                self.assertEqual(d.drop_stage, "vector_floor")
            elif d.agent.agent_id == "a2":
                self.assertEqual(d.drop_stage, "stage2_null")
            elif d.agent.agent_id == "a3":
                self.assertEqual(d.drop_stage, "stage2_threshold")


if __name__ == "__main__":
    unittest.main()
