"""Unit tests for FirestoreStore in knowledge_discovery."""

import os
import sys
import unittest
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.firestore_store import FirestoreStore
from knowledge_discovery.models import Agent, Message, Profile, ProfileItem


class MockDocumentSnapshot:
    """Mock for Firestore DocumentSnapshot."""

    def __init__(self, doc_id: str, data: dict[str, Any] | None) -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        # Fixed attribute, not a per-call property: stream() configures
        # reference.delete side effects that the store must actually invoke
        self.reference = MagicMock()

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class MockCollectionReference:
    """Mock for Firestore CollectionReference."""

    def __init__(self, name: str, storage: dict[str, dict[str, Any]]) -> None:
        self.name = name
        self._storage = storage

    def document(self, doc_id: str) -> Any:
        col_storage = self._storage.setdefault(self.name, {})

        mock_doc = MagicMock()
        mock_doc.id = doc_id

        def _set(data: dict[str, Any]) -> None:
            col_storage[doc_id] = dict(data)

        def _get() -> MockDocumentSnapshot:
            data = col_storage.get(doc_id)
            return MockDocumentSnapshot(doc_id, data)

        def _delete() -> None:
            col_storage.pop(doc_id, None)

        mock_doc.set.side_effect = _set
        mock_doc.get.side_effect = _get
        mock_doc.delete.side_effect = _delete
        return mock_doc

    def stream(self) -> list[MockDocumentSnapshot]:
        col_storage = self._storage.setdefault(self.name, {})
        snapshots = []
        for doc_id, data in col_storage.items():
            snap = MockDocumentSnapshot(doc_id, data)
            # Add reference with delete()
            snap.reference.delete.side_effect = lambda did=doc_id: col_storage.pop(did, None)
            snapshots.append(snap)
        return snapshots

    def limit(self, n: int) -> Any:
        mock_query = MagicMock()
        mock_query.get.side_effect = lambda: self.stream()[:n]
        return mock_query

    def where(self, field_path: str, op_string: str, value: Any) -> Any:
        mock_query = MagicMock()

        def _stream() -> list[MockDocumentSnapshot]:
            col_storage = self._storage.setdefault(self.name, {})
            results = []
            for doc_id, data in col_storage.items():
                if op_string == "==" and data.get(field_path) == value:
                    results.append(MockDocumentSnapshot(doc_id, data))
            return results

        def _limit(n: int) -> Any:
            limited_query = MagicMock()
            limited_query.stream.side_effect = lambda: _stream()[:n]
            return limited_query

        mock_query.stream.side_effect = _stream
        mock_query.limit.side_effect = _limit
        return mock_query


class MockFirestoreClient:
    """Mock Firestore client for testing FirestoreStore without network."""

    def __init__(self) -> None:
        self.storage: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> MockCollectionReference:
        return MockCollectionReference(name, self.storage)


class TestFirestoreStore(unittest.TestCase):
    """Tests for FirestoreStore CRUD and query operations."""

    def setUp(self) -> None:
        self.mock_client = MockFirestoreClient()
        self.store = FirestoreStore(client=self.mock_client)

    def test_save_and_get_agent(self) -> None:
        agent = Agent(
            agent_id="agent_rachel",
            employee_id="emp_rachel",
            display_name="Rachel Kim",
            supported_intents=["connect_ask", "connect_ask_private"],
            endpoint="agent://rachel",
            active=True,
        )
        self.store.save_agent(agent)

        fetched = self.store.get_agent("agent_rachel")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.agent_id, "agent_rachel")
        self.assertEqual(fetched.display_name, "Rachel Kim")
        self.assertEqual(fetched.supported_intents, ["connect_ask", "connect_ask_private"])
        self.assertTrue(fetched.active)

    def test_get_agent_by_employee_id(self) -> None:
        agent = Agent(
            agent_id="agent_marcus",
            employee_id="emp_marcus",
            display_name="Marcus Delgado",
        )
        self.store.save_agent(agent)

        fetched = self.store.get_agent_by_employee_id("emp_marcus")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.agent_id, "agent_marcus")

        non_existent = self.store.get_agent_by_employee_id("emp_unknown")
        self.assertIsNone(non_existent)

    def test_list_agents(self) -> None:
        a1 = Agent(agent_id="a1", employee_id="e1", display_name="A1", active=True)
        a2 = Agent(agent_id="a2", employee_id="e2", display_name="A2", active=False)
        self.store.save_agent(a1)
        self.store.save_agent(a2)

        all_agents = self.store.list_agents(active_only=False)
        self.assertEqual(len(all_agents), 2)

        active_agents = self.store.list_agents(active_only=True)
        self.assertEqual(len(active_agents), 1)
        self.assertEqual(active_agents[0].agent_id, "a1")

    def test_save_and_get_profile(self) -> None:
        profile = Profile(
            employee_id="emp_elena",
            name="Elena Vasquez",
            role="Transition Advisor",
            items=[
                ProfileItem(key="current_work", body="Advises independent practices", visibility="public"),
                ProfileItem(key="transition_pipeline", body="Secret unannounced deals", visibility="private"),
            ],
            embedding=[0.1, 0.2, 0.3],
        )
        self.store.save_profile(profile)

        fetched = self.store.get_profile("emp_elena")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Elena Vasquez")
        self.assertEqual(len(fetched.items), 2)
        self.assertTrue(fetched.has_any_private(["transition_pipeline"]))
        self.assertFalse(fetched.has_any_private(["current_work"]))

    def test_save_and_list_messages(self) -> None:
        m1 = Message(
            audit_id="msg_001",
            from_entity="user1",
            to_entity="system",
            intent="query",
            payload_type="query",
            payload={"question_text": "Need real estate expert"},
            timestamp="2026-08-18T10:00:00Z",
        )
        m2 = Message(
            audit_id="msg_002",
            from_entity="system",
            to_entity="agent_marcus",
            intent="connect_ask",
            payload_type="connect_ask",
            payload={"reason_text": "Marcus has zoning experience"},
            timestamp="2026-08-18T10:00:01Z",
        )
        self.store.save_message(m1)
        self.store.save_message(m2)

        messages = self.store.list_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].audit_id, "msg_001")
        self.assertEqual(messages[1].audit_id, "msg_002")

        entity_msgs = self.store.get_messages_for_entity("agent_marcus")
        self.assertEqual(len(entity_msgs), 1)
        self.assertEqual(entity_msgs[0].audit_id, "msg_002")

    def test_clear(self) -> None:
        self.store.save_agent(Agent(agent_id="a1", employee_id="e1", display_name="A1"))
        self.store.save_profile(Profile(employee_id="e1", name="E1", role="Role"))
        self.store.save_message(Message(audit_id="m1", from_entity="u1", to_entity="u2", intent="query", payload_type="query"))

        self.store.clear()
        self.assertEqual(len(self.store.list_agents()), 0)
        self.assertEqual(len(self.store.list_profiles()), 0)
        self.assertEqual(len(self.store.list_messages()), 0)


if __name__ == "__main__":
    unittest.main()
