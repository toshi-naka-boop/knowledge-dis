"""Unit tests for seed generation script (generate_seeds.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.store import InMemoryStore
from scripts.generate_seeds import (
    build_fixed_personas,
    generate_all_seeds,
    generate_synthetic_profiles,
    populate_store,
)


class TestSeedGeneration(unittest.TestCase):
    """Tests for 4 fixed personas, 396 synthetic profiles, and store population."""

    def test_fixed_personas(self) -> None:
        agents, profiles = build_fixed_personas()
        self.assertEqual(len(agents), 4)
        self.assertEqual(len(profiles), 4)

        agent_ids = {a.agent_id for a in agents}
        self.assertEqual(
            agent_ids,
            {
                "agent_rachel_kim",
                "agent_marcus_delgado",
                "agent_elena_vasquez",
                "agent_tom_whitfield",
            },
        )

        profiles_by_id = {p.employee_id: p for p in profiles}

        # 1. Rachel Kim (Healthcare Staffing)
        rachel = profiles_by_id["emp_rachel_kim"]
        self.assertIn("Healthcare Staffing", rachel.role)
        self.assertFalse(rachel.has_any_private(["current_work", "expertise", "background"]))

        # 2. Marcus Delgado (Real Estate)
        marcus = profiles_by_id["emp_marcus_delgado"]
        self.assertIn("Healthcare Real Estate", marcus.role)
        self.assertIn("zoning", marcus.get_item("current_work").body.lower())

        # 3. Elena Vasquez (Transition / Private Item)
        elena = profiles_by_id["emp_elena_vasquez"]
        self.assertIn("Transition Advisor", elena.role)
        self.assertTrue(elena.has_any_private(["transition_pipeline"]))
        self.assertEqual(elena.get_item("transition_pipeline").visibility, "private")

        # 4. Tom Whitfield (Accounting / Drop candidate)
        tom = profiles_by_id["emp_tom_whitfield"]
        self.assertIn("Accountant", tom.role)
        self.assertIn("GAAP", tom.get_item("expertise").body)

    def test_synthetic_profiles(self) -> None:
        synthetic = generate_synthetic_profiles(count=396)
        self.assertEqual(len(synthetic), 396)

        # Verify all synthetic profiles have visibility='public', reviewed=False, source='seed_synth'
        for p in synthetic:
            self.assertTrue(p.employee_id.startswith("emp_synth_"))
            self.assertGreater(len(p.items), 0)
            for item in p.items:
                self.assertEqual(item.visibility, "public")
                self.assertFalse(item.reviewed)
                self.assertEqual(item.source, "seed_synth")

    def test_generate_all_seeds_and_embeddings(self) -> None:
        agents, profiles = generate_all_seeds()
        self.assertEqual(len(agents), 4)
        self.assertEqual(len(profiles), 400)

        # Check all profiles have embeddings generated
        for p in profiles:
            self.assertIsNotNone(p.embedding)
            self.assertGreater(len(p.embedding), 0)

    def test_populate_store_dry_run_and_live(self) -> None:
        store = InMemoryStore()

        # Dry run does not write to store
        agent_cnt, prof_cnt = populate_store(store, dry_run=True)
        self.assertEqual(agent_cnt, 4)
        self.assertEqual(prof_cnt, 400)
        self.assertEqual(len(store.list_agents()), 0)
        self.assertEqual(len(store.list_profiles()), 0)

        # Live population writes to store
        populate_store(store, dry_run=False)
        self.assertEqual(len(store.list_agents()), 4)
        self.assertEqual(len(store.list_profiles()), 400)


if __name__ == "__main__":
    unittest.main()
