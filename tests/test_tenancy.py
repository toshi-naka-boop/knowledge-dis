"""Unit tests for knowledge_discovery.tenancy (design.md §16.2, FR27, Part B).

Verifies:
- TenantRegistry startup validation: duplicate tenant_id / database /
  email_domain / api_key all fail to construct (§16.2 "起動時検証").
- TenantRegistry.from_env() defaults to the single current-compatible tenant
  ("meridian" / "(default)" / "meridian-care.example" / DEMO_API_KEY) when
  TENANTS_JSON is unset.
- End-to-end, over HTTP (TestClient), that two tenants driven by two
  InMemoryStores through server.create_app are fully isolated: tenant A's
  agents/profiles/messages/cards never appear through tenant B's API key,
  across every route (agents / query / requester status / candidate asks /
  secretary digest / audit / sweep), including when the same employee_id and
  agent_id happen to exist in both tenants.
- A tenant-scoped API key resolves system-mode digest requests to its own
  tenant only (no cross-tenant employee_id lookup).
- A tenant-scoped sweep processes only that tenant's tasks.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.models import Agent, Card, Profile, ProfileItem, Task
from knowledge_discovery.store import InMemoryStore
from knowledge_discovery.tenancy import (
    DEFAULT_API_KEY_FALLBACK,
    DEFAULT_DATABASE,
    DEFAULT_EMAIL_DOMAINS,
    DEFAULT_TENANT_ID,
    TenantConfig,
    TenantRegistry,
)

try:
    from fastapi import HTTPException, status
    from fastapi.testclient import TestClient

    from knowledge_discovery.auth import Principal
    from knowledge_discovery.server import create_app

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


# -----------------------------------------------------------------------------
# TenantRegistry: startup validation and env defaults
# -----------------------------------------------------------------------------


class TestTenantRegistryValidation(unittest.TestCase):
    def test_duplicate_tenant_id_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            TenantRegistry(
                [
                    TenantConfig("t1", "db1", ("a.example",), "key1"),
                    TenantConfig("t1", "db2", ("b.example",), "key2"),
                ]
            )

    def test_duplicate_database_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            TenantRegistry(
                [
                    TenantConfig("t1", "shared-db", ("a.example",), "key1"),
                    TenantConfig("t2", "shared-db", ("b.example",), "key2"),
                ]
            )

    def test_duplicate_email_domain_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            TenantRegistry(
                [
                    TenantConfig("t1", "db1", ("shared.example",), "key1"),
                    TenantConfig("t2", "db2", ("shared.example",), "key2"),
                ]
            )

    def test_duplicate_api_key_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            TenantRegistry(
                [
                    TenantConfig("t1", "db1", ("a.example",), "shared-key"),
                    TenantConfig("t2", "db2", ("b.example",), "shared-key"),
                ]
            )

    def test_empty_registry_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            TenantRegistry([])

    def test_valid_two_tenant_registry_resolves(self) -> None:
        registry = TenantRegistry(
            [
                TenantConfig("t1", "db1", ("a.example",), "key1", system_accounts=("sa1@x.example",)),
                TenantConfig("t2", "db2", ("b.example",), "key2", system_accounts=("sa2@x.example",)),
            ]
        )
        self.assertEqual(registry.resolve_by_api_key("key1").tenant_id, "t1")
        self.assertEqual(registry.resolve_by_api_key("key2").tenant_id, "t2")
        self.assertIsNone(registry.resolve_by_api_key("no-such-key"))
        self.assertEqual(registry.resolve_by_email_domain("a.example").tenant_id, "t1")
        self.assertEqual(registry.resolve_by_system_account("sa2@x.example").tenant_id, "t2")


class TestTenantRegistryFromEnv(unittest.TestCase):
    def test_default_is_single_current_compatible_tenant(self) -> None:
        """TENANTS_JSON unset -> exactly today's behavior (§16.2 e)."""
        registry = TenantRegistry.from_env(env={})
        self.assertEqual(len(registry.tenants), 1)
        tenant = registry.tenants[0]
        self.assertEqual(tenant.tenant_id, DEFAULT_TENANT_ID)
        self.assertEqual(tenant.database, DEFAULT_DATABASE)
        self.assertEqual(tenant.email_domains, DEFAULT_EMAIL_DOMAINS)
        self.assertEqual(tenant.api_key, DEFAULT_API_KEY_FALLBACK)

    def test_default_honors_demo_api_key_env(self) -> None:
        registry = TenantRegistry.from_env(env={"DEMO_API_KEY": "my-custom-key"})
        self.assertEqual(registry.tenants[0].api_key, "my-custom-key")

    def test_tenants_json_builds_multi_tenant_registry(self) -> None:
        env = {
            "TENANTS_JSON": (
                '[{"tenant_id":"acme","database":"acme-db","email_domains":["acme.example"],'
                '"api_key_env":"ACME_KEY","system_accounts":["sa@acme.example"]},'
                '{"tenant_id":"globex","database":"globex-db","email_domains":["globex.example"],'
                '"api_key_env":"GLOBEX_KEY"}]'
            ),
            "ACME_KEY": "acme-key-from-env",
            "GLOBEX_KEY": "globex-key-from-env",
        }
        registry = TenantRegistry.from_env(env=env)
        self.assertEqual(len(registry.tenants), 2)
        self.assertEqual(registry.resolve_by_api_key("acme-key-from-env").tenant_id, "acme")
        self.assertEqual(registry.resolve_by_api_key("globex-key-from-env").tenant_id, "globex")

    def test_tenants_json_plaintext_api_key_is_rejected(self) -> None:
        """round-14 E-13: `TENANTS_JSON` accepts only `api_key_env`; a literal
        `api_key` field (even alongside a missing `api_key_env`) fails startup
        rather than silently working as a second, less safe config path."""
        env = {
            "TENANTS_JSON": (
                '[{"tenant_id":"acme","database":"acme-db","email_domains":["acme.example"],'
                '"api_key":"acme-key-in-plaintext"}]'
            ),
        }
        with self.assertRaises(RuntimeError):
            TenantRegistry.from_env(env=env)

    def test_email_domains_and_system_accounts_normalized_to_lowercase(self) -> None:
        """round-14 V-14/S-13: ledger-side domains/system_accounts are
        lowercased so they match the already-lowercased verified JWT email
        IapResolver looks them up with."""
        env = {
            "TENANTS_JSON": (
                '[{"tenant_id":"acme","database":"acme-db",'
                '"email_domains":["Acme.EXAMPLE"],"api_key_env":"ACME_KEY",'
                '"system_accounts":["SA@Acme.EXAMPLE"]}]'
            ),
            "ACME_KEY": "acme-key",
        }
        registry = TenantRegistry.from_env(env=env)
        self.assertEqual(registry.resolve_by_email_domain("acme.example").tenant_id, "acme")
        self.assertIsNone(registry.resolve_by_email_domain("Acme.EXAMPLE"))
        self.assertEqual(registry.resolve_by_system_account("sa@acme.example").tenant_id, "acme")
        self.assertIsNone(registry.resolve_by_system_account("SA@Acme.EXAMPLE"))

    def test_duplicate_email_domain_differing_only_by_case_fails(self) -> None:
        """Normalization must happen before the startup duplicate check, or a
        hand-authored ledger could evade it by case alone (round-14 S-13)."""
        env = {
            "TENANTS_JSON": (
                '[{"tenant_id":"t1","database":"db1","email_domains":["Shared.Example"],'
                '"api_key_env":"K1"},'
                '{"tenant_id":"t2","database":"db2","email_domains":["shared.example"],'
                '"api_key_env":"K2"}]'
            ),
            "K1": "key1",
            "K2": "key2",
        }
        with self.assertRaises(RuntimeError):
            TenantRegistry.from_env(env=env)


# -----------------------------------------------------------------------------
# End-to-end tenant isolation over HTTP
# -----------------------------------------------------------------------------


class _KeyedSystemPrincipalResolver:
    """Test double: same API-key -> tenant binding as DemoKeyResolver, but
    resolves to mode='system' instead of mode='demo'. Used to check
    system-mode digest isolation without re-exercising IapResolver's JWT
    verification machinery (already covered end-to-end by test_auth.py)."""

    def __init__(self, registry: TenantRegistry) -> None:
        self.registry = registry

    def resolve(self, request):  # type: ignore[no-untyped-def]
        key = request.headers.get("x-api-key") or request.query_params.get("api_key")
        tenant = self.registry.resolve_by_api_key(key) if key else None
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad key")
        return Principal(mode="system", tenant_id=tenant.tenant_id, employee_id=None, email=None)


@unittest.skipUnless(HAS_FASTAPI, "fastapi is not installed")
class TestTenantIsolation(unittest.TestCase):
    """Two tenants, both InMemory, sharing the SAME agent_id/employee_id, to
    make sure isolation holds even when IDs collide across tenants -- a
    routing bug would leak tenant A's data through tenant B's identical IDs."""

    def setUp(self) -> None:
        self.store_a = InMemoryStore()
        self.store_b = InMemoryStore()

        for label, store in (("A", self.store_a), ("B", self.store_b)):
            store.save_agent(
                Agent(
                    agent_id="agent_shared",
                    employee_id="emp_shared",
                    display_name=f"Tenant {label} Agent",
                    supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
                    active=True,
                )
            )
            store.save_profile(
                Profile(
                    employee_id="emp_shared",
                    name=f"Tenant {label} Person",
                    role="Specialist",
                    items=[
                        ProfileItem(
                            key="current_work",
                            body=(
                                f"Tenant {label} distinctive expertise about "
                                f"{'clinic zoning permits' if label == 'A' else 'payroll tax compliance'} "
                                "and related casework."
                            ),
                            visibility="public",
                            reviewed=True,
                        )
                    ],
                )
            )
            store.save_task(
                Task(
                    task_id=f"task_{label.lower()}",
                    owner_employee_id="emp_shared",
                    title=f"Tenant {label} Task",
                    status="todo",
                )
            )
            store.save_card(
                Card(
                    card_id=f"card_{label.lower()}",
                    owner_employee_id="emp_shared",
                    type="stagnation",
                    tier="notice",
                    payload={"task_title": f"Tenant {label} Stagnation Card", "evidence_line": "stub"},
                    status="open",
                )
            )

        self.registry = TenantRegistry(
            [
                TenantConfig(
                    tenant_id="tenant_a",
                    database="tenant-a-db",
                    email_domains=("tenant-a.example",),
                    api_key="key-a",
                    system_accounts=("sa-a@x.iam.gserviceaccount.com",),
                ),
                TenantConfig(
                    tenant_id="tenant_b",
                    database="tenant-b-db",
                    email_domains=("tenant-b.example",),
                    api_key="key-b",
                    system_accounts=("sa-b@x.iam.gserviceaccount.com",),
                ),
            ]
        )
        self.stores_by_tenant = {"tenant_a": self.store_a, "tenant_b": self.store_b}

        def store_factory(tenant: TenantConfig):  # type: ignore[no-untyped-def]
            return self.stores_by_tenant[tenant.tenant_id]

        self.app = create_app(registry=self.registry, store_factory=store_factory)
        self.client = TestClient(self.app)

        self.system_app = create_app(
            registry=self.registry,
            store_factory=store_factory,
            principal_resolver=_KeyedSystemPrincipalResolver(self.registry),
        )
        self.system_client = TestClient(self.system_app)

        self.headers_a = {"X-API-Key": "key-a"}
        self.headers_b = {"X-API-Key": "key-b"}

    # -- (a) agents ----------------------------------------------------------

    def test_agents_list_isolated(self) -> None:
        resp_a = self.client.get("/api/agents", headers=self.headers_a).json()
        resp_b = self.client.get("/api/agents", headers=self.headers_b).json()
        self.assertEqual([a["display_name"] for a in resp_a["agents"]], ["Tenant A Agent"])
        self.assertEqual([a["display_name"] for a in resp_b["agents"]], ["Tenant B Agent"])

    # -- (a) query / requester status / candidate asks / audit --------------

    def test_query_requester_status_candidate_asks_audit_isolated(self) -> None:
        # A question crafted to overlap with tenant A's profile body only.
        query_a = self.client.post(
            "/api/query",
            json={"requester_id": "emp_shared", "question_text": "Who knows about clinic zoning permits?"},
            headers=self.headers_a,
        )
        self.assertEqual(query_a.status_code, 200)

        # Tenant B has not been queried at all yet.
        status_b = self.client.get("/api/requester/emp_shared/status", headers=self.headers_b).json()
        self.assertEqual(status_b["statuses"], [])

        status_a = self.client.get("/api/requester/emp_shared/status", headers=self.headers_a).json()
        self.assertGreaterEqual(len(status_a["statuses"]), 1)

        # Candidate asks: tenant B's inbox for the SAME agent_id is empty;
        # nothing from tenant A's dispatch (if any) leaked across.
        asks_b = self.client.get("/api/candidate/agent_shared/asks", headers=self.headers_b).json()
        self.assertEqual(asks_b["asks"], [])
        asks_a = self.client.get("/api/candidate/agent_shared/asks", headers=self.headers_a).json()
        for ask in asks_a["asks"]:
            self.assertNotIn("payroll", ask.get("question_summary", "") + ask.get("reason_text", ""))

        # Audit: tenant A now has at least the query record; tenant B's
        # audit log (and its static profile/agent counts) reflect only B.
        audit_a = self.client.get("/api/audit/messages", headers=self.headers_a).json()
        audit_b = self.client.get("/api/audit/messages", headers=self.headers_b).json()
        self.assertGreaterEqual(len(audit_a["records"]), 1)
        self.assertEqual(audit_b["records"], [])
        self.assertEqual(audit_a["funnel_stats"]["total_profiles"], 1)
        self.assertEqual(audit_b["funnel_stats"]["total_profiles"], 1)
        for record in audit_b["records"]:
            self.assertNotIn("clinic zoning", str(record.get("display_payload", "")))

    # -- (b) secretary digest: same employee_id, demo AND system mode -------

    def test_digest_isolated_across_identical_employee_id_demo_mode(self) -> None:
        digest_a = self.client.get(
            "/api/secretary/digest", params={"employee_id": "emp_shared"}, headers=self.headers_a
        ).json()
        digest_b = self.client.get(
            "/api/secretary/digest", params={"employee_id": "emp_shared"}, headers=self.headers_b
        ).json()
        titles_a = [c["payload"]["task_title"] for c in digest_a["stagnation_cards"]]
        titles_b = [c["payload"]["task_title"] for c in digest_b["stagnation_cards"]]
        self.assertEqual(titles_a, ["Tenant A Stagnation Card"])
        self.assertEqual(titles_b, ["Tenant B Stagnation Card"])

    def test_digest_isolated_across_identical_employee_id_system_mode(self) -> None:
        digest_a = self.system_client.get(
            "/api/secretary/digest", params={"employee_id": "emp_shared"}, headers=self.headers_a
        ).json()
        digest_b = self.system_client.get(
            "/api/secretary/digest", params={"employee_id": "emp_shared"}, headers=self.headers_b
        ).json()
        titles_a = [c["payload"]["task_title"] for c in digest_a["stagnation_cards"]]
        titles_b = [c["payload"]["task_title"] for c in digest_b["stagnation_cards"]]
        self.assertEqual(titles_a, ["Tenant A Stagnation Card"])
        self.assertEqual(titles_b, ["Tenant B Stagnation Card"])

    # -- (c) sweep processes only its own tenant -----------------------------

    def test_sweep_processes_only_its_own_tenant(self) -> None:
        # Only tenant A's store has a task (seeded in setUp for both -- add
        # one extra task to tenant A only, so the counts differ observably).
        self.store_a.save_task(Task(task_id="task_a_extra", owner_employee_id="emp_shared", title="Extra", status="todo"))

        result_b = self.client.post("/api/secretary/sweep", headers=self.headers_b).json()
        self.assertEqual(result_b["tasks_evaluated"], 1)  # only task_b

        result_a = self.client.post("/api/secretary/sweep", headers=self.headers_a).json()
        self.assertEqual(result_a["tasks_evaluated"], 2)  # task_a + task_a_extra

        # Tenant B's card set is untouched by tenant A's sweep.
        cards_b_after = self.store_b.list_cards(owner_employee_id="emp_shared")
        self.assertEqual([c.card_id for c in cards_b_after], ["card_b"])


if __name__ == "__main__":
    unittest.main()
