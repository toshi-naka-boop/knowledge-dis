"""Unit tests for store.py in knowledge_discovery."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.models import Agent, Message, Profile, ProfileItem
from knowledge_discovery.store import InMemoryStore


class TestStore(unittest.TestCase):
    """Tests for InMemoryStore implementation."""

    def setUp(self) -> None:
        self.store = InMemoryStore()

    def test_agent_crud(self) -> None:
        agent1 = Agent(
            agent_id="agent_1",
            employee_id="emp_1",
            display_name="Alice",
            supported_intents=["connect_ask"],
            active=True,
        )
        agent2 = Agent(
            agent_id="agent_2",
            employee_id="emp_2",
            display_name="Bob",
            supported_intents=["connect_ask"],
            active=False,
        )
        self.store.save_agent(agent1)
        self.store.save_agent(agent2)

        retrieved1 = self.store.get_agent("agent_1")
        self.assertIsNotNone(retrieved1)
        self.assertEqual(retrieved1.display_name, "Alice")

        by_emp = self.store.get_agent_by_employee_id("emp_1")
        self.assertIsNotNone(by_emp)
        self.assertEqual(by_emp.agent_id, "agent_1")

        all_agents = self.store.list_agents(active_only=False)
        self.assertEqual(len(all_agents), 2)

        active_agents = self.store.list_agents(active_only=True)
        self.assertEqual(len(active_agents), 1)
        self.assertEqual(active_agents[0].agent_id, "agent_1")

    def test_profile_crud(self) -> None:
        prof = Profile(
            employee_id="emp_100",
            name="Charlie",
            role="Data Scientist",
            items=[ProfileItem(key="expertise", body="Machine Learning")],
        )
        self.store.save_profile(prof)

        retrieved = self.store.get_profile("emp_100")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Charlie")
        self.assertEqual(len(retrieved.items), 1)

        profiles = self.store.list_profiles()
        self.assertEqual(len(profiles), 1)

    def test_message_crud_and_queries(self) -> None:
        msg1 = Message(
            audit_id="msg_1",
            from_entity="user_1",
            to_entity="system",
            intent="query",
            payload_type="query",
            payload={"question_text": "生産管理について", "requester_id": "user_1"},
        )
        msg2 = Message(
            audit_id="msg_2",
            from_entity="system",
            to_entity="agent_1",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={"reason_text": "知見あり", "cited_item_keys": ["current_work"], "score": 0.9},
        )
        msg3 = Message(
            audit_id="msg_3",
            from_entity="system",
            to_entity="system",
            intent="match_proposal",
            payload_type="match_proposal",
            payload={"meeting_duration": 15, "proposed_by": "system", "participants": ["user_1", "emp_1"]},
        )
        self.store.save_message(msg1)
        self.store.save_message(msg2)
        self.store.save_message(msg3)

        retrieved = self.store.get_message("msg_1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.intent, "query")

        all_msgs = self.store.list_messages()
        self.assertEqual(len(all_msgs), 3)

        user1_msgs = self.store.get_messages_for_entity("user_1")
        # user_1 is in msg1 (from_entity) and msg3 (participants)
        self.assertEqual(len(user1_msgs), 2)
        self.assertEqual({m.audit_id for m in user1_msgs}, {"msg_1", "msg_3"})

    def test_clear(self) -> None:
        self.store.save_agent(Agent(agent_id="a", employee_id="e", display_name="D"))
        self.store.clear()
        self.assertEqual(len(self.store.list_agents()), 0)


if __name__ == "__main__":
    unittest.main()
