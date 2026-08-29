"""Unit tests for the HTTP layer of the Autonomous Agent phase (Phase 3+4).

Covers what Phase 2's tests/test_autonomy.py does not: the FastAPI routes
themselves (design.md v4 §1, §2, §5.5, §8, §9 #16) --

- POST /api/secretary/sweep: origin body acceptance, HTTP-layer default
  "scheduled", invalid-origin rejection, Cloud Scheduler header -> deterministic
  run_key (design §2/§3).
- POST /internal/autonomous-sweep: OIDC verification (#16: no token / aud
  mismatch / email mismatch / iss mismatch / valid token / env unset -> 404),
  per-tenant iteration, the 200-iff-all-done-or-deduplicated / else 500
  response contract (design §2, Y-4), and that a per-tenant exception still
  reaches the domain layer's fail_sweep_run.
- GET/PUT /api/secretary/autonomy: default persisted:false, round-trip,
  save-time normalization, contact_mode enum enforcement.
- GET /api/secretary/digest: last_sweep and autonomy.effective are present
  (design §8, C-17).

No network, no real LLM, no real Firestore: InMemoryStore + DeterministicEmbedder
+ FakeConnectionInferencer only, and real ES256-signed OIDC-shaped JWTs verified
through the actual google-auth code path (same discipline as test_auth.py's
TestIapJwtVerification).
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.matching import DeterministicEmbedder, FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import Task
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from google.auth import jwt as google_jwt
    from google.auth.crypt import es256

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    from fastapi.testclient import TestClient

    from knowledge_discovery.auth import GOOGLE_OIDC_ISSUERS, _CachingCertsRequest, verify_autonomous_sweep_token
    from knowledge_discovery.secretary import SecretaryService
    from knowledge_discovery.server import create_app
    from knowledge_discovery.tenancy import TenantConfig, TenantRegistry

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


AUDIENCE = "kd-autonomous-sweep"
INVOKER_EMAIL = "kd-sweeper@my-project.iam.gserviceaccount.com"


def _build_service(store: InMemoryStore) -> KnowledgeDiscoveryService:
    matching_engine = MatchingEngine(
        embedder=DeterministicEmbedder(),
        inferencer=FakeConnectionInferencer(),
        vector_floor=0.20,
        connection_threshold=0.50,
        max_dispatch_k=3,
        funnel_limit=20,
    )
    return KnowledgeDiscoveryService(store=store, matching_engine=matching_engine)


class _FakeResponse:
    def __init__(self, status: int, data: bytes) -> None:
        self.status = status
        self.data = data
        self.headers: dict[str, str] = {}


class _FakeCertsTransport:
    """Stands in for google.auth.transport.Request (same pattern as test_auth.py)."""

    def __init__(self, certs: dict) -> None:
        self.certs = certs
        self.call_count = 0

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> _FakeResponse:
        self.call_count += 1
        import json

        return _FakeResponse(200, json.dumps(self.certs).encode("utf-8"))


def _generate_ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv, pub_pem


def _make_oidc_jwt(priv, kid: str = "test-kid-1", **claim_overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "email": INVOKER_EMAIL,
        "email_verified": True,
        "sub": "1234567890",
    }
    payload.update(claim_overrides)
    signer = es256.ES256Signer(priv, key_id=kid)
    token = google_jwt.encode(signer, payload, header={"alg": "ES256", "kid": kid})
    return token.decode("utf-8") if isinstance(token, bytes) else token


class _FakeRequest:
    """Minimal stand-in for fastapi.Request: only .headers is used by verify_autonomous_sweep_token."""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


# =============================================================================
# 1. verify_autonomous_sweep_token (function-level, real crypto)
# =============================================================================


@unittest.skipUnless(HAS_CRYPTO and HAS_FASTAPI, "cryptography/google-auth/fastapi not available")
class TestVerifyAutonomousSweepToken(unittest.TestCase):
    def setUp(self) -> None:
        self.priv, self.pub_pem = _generate_ec_keypair()
        self.other_priv, self.other_pub_pem = _generate_ec_keypair()
        self.certs = {"test-kid-1": self.pub_pem}
        self.transport = _FakeCertsTransport(self.certs)
        self.cert_cache = _CachingCertsRequest()
        self.cert_cache._transport = self.transport

    def _verify(self, token: str) -> None:
        request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
        verify_autonomous_sweep_token(
            request, audience=AUDIENCE, invoker_email=INVOKER_EMAIL, cert_cache=self.cert_cache
        )

    def test_no_token_is_401(self) -> None:
        request = _FakeRequest(headers={})
        with self.assertRaises(Exception) as ctx:
            verify_autonomous_sweep_token(
                request, audience=AUDIENCE, invoker_email=INVOKER_EMAIL, cert_cache=self.cert_cache
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_valid_token_passes(self) -> None:
        token = _make_oidc_jwt(self.priv)
        self._verify(token)  # must not raise

    def test_audience_mismatch_is_403(self) -> None:
        token = _make_oidc_jwt(self.priv, aud="some-other-audience")
        with self.assertRaises(Exception) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_email_mismatch_is_403(self) -> None:
        token = _make_oidc_jwt(self.priv, email="someone-else@example.com")
        with self.assertRaises(Exception) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_issuer_mismatch_is_403(self) -> None:
        token = _make_oidc_jwt(self.priv, iss="https://not-google.example.com")
        with self.assertRaises(Exception) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_email_not_verified_is_403(self) -> None:
        token = _make_oidc_jwt(self.priv, email_verified=False)
        with self.assertRaises(Exception) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_bad_signature_is_403(self) -> None:
        bad_certs = {"test-kid-1": self.other_pub_pem}
        self.cert_cache._cached_response = None
        self.transport.certs = bad_certs
        token = _make_oidc_jwt(self.priv)
        with self.assertRaises(Exception) as ctx:
            self._verify(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_both_issuer_forms_accepted(self) -> None:
        self.assertIn("accounts.google.com", GOOGLE_OIDC_ISSUERS)
        self.assertIn("https://accounts.google.com", GOOGLE_OIDC_ISSUERS)
        token = _make_oidc_jwt(self.priv, iss="accounts.google.com")
        self._verify(token)  # bare-form issuer must also pass


# =============================================================================
# 2. POST /internal/autonomous-sweep (HTTP layer, multi-tenant)
# =============================================================================


@unittest.skipUnless(HAS_CRYPTO and HAS_FASTAPI, "cryptography/google-auth/fastapi not available")
class TestAutonomousSweepEndpoint(unittest.TestCase):
    def setUp(self) -> None:
        self.priv, self.pub_pem = _generate_ec_keypair()
        self.certs = {"test-kid-1": self.pub_pem}
        self.transport = _FakeCertsTransport(self.certs)

        # Patch the transport used by every _CachingCertsRequest built inside
        # server.py (there is exactly one per app -- the dedicated instance).
        self._transport_patcher = patch(
            "knowledge_discovery.auth._CachingCertsRequest._get_transport",
            return_value=self.transport,
        )
        self._transport_patcher.start()
        self.addCleanup(self._transport_patcher.stop)

        self.store_a = InMemoryStore()
        self.store_b = InMemoryStore()
        self.registry = TenantRegistry(
            [
                TenantConfig(tenant_id="tenant_a", database="db_a", email_domains=("a.example",), api_key="key-a"),
                TenantConfig(tenant_id="tenant_b", database="db_b", email_domains=("b.example",), api_key="key-b"),
            ]
        )
        stores_by_tenant = {"tenant_a": self.store_a, "tenant_b": self.store_b}

        def store_factory(tenant: TenantConfig):  # type: ignore[no-untyped-def]
            return stores_by_tenant[tenant.tenant_id]

        env_patch = {"AUTONOMOUS_SWEEP_AUDIENCE": AUDIENCE, "AUTONOMOUS_SWEEP_INVOKER": INVOKER_EMAIL}
        self._env_patcher = patch.dict(os.environ, env_patch)
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)

        self.app = create_app(registry=self.registry, store_factory=store_factory)
        self.client = TestClient(self.app)

    def _token(self, **overrides) -> str:
        return _make_oidc_jwt(self.priv, **overrides)

    def _auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_env_unset_is_404(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            del os.environ["AUTONOMOUS_SWEEP_AUDIENCE"]
            resp = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))
        self.assertEqual(resp.status_code, 404)

    def test_no_token_is_401(self) -> None:
        resp = self.client.post("/internal/autonomous-sweep")
        self.assertEqual(resp.status_code, 401)

    def test_bad_token_is_403(self) -> None:
        resp = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token(aud="wrong")))
        self.assertEqual(resp.status_code, 403)

    def test_api_key_alone_is_not_accepted(self) -> None:
        """No API key / query param path exists for this route -- only the bearer token."""
        resp = self.client.post("/internal/autonomous-sweep", headers={"X-API-Key": "key-a"})
        self.assertEqual(resp.status_code, 401)

    def test_valid_token_sweeps_every_tenant_and_returns_200(self) -> None:
        resp = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body["tenants"].keys()), {"tenant_a", "tenant_b"})
        for tenant_id, result in body["tenants"].items():
            self.assertIn(result["status"], ("ok", "deduplicated"), msg=tenant_id)

    def test_second_call_within_same_window_deduplicates(self) -> None:
        first = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))
        second = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        for tenant_id, result in second.json()["tenants"].items():
            self.assertEqual(result["status"], "deduplicated", msg=tenant_id)

    def test_partial_tenant_failure_is_500_and_calls_fail_sweep_run(self) -> None:
        """One tenant's _execute_scheduled_sweep raises; the other tenant still
        succeeds. Overall response must be 500 (Y-4), and the domain layer's
        fail_sweep_run must have transitioned the failing tenant's run to
        'failed' (immediately re-claimable, C-14/Z-1) rather than leaving it
        stuck 'running'."""
        real_execute = SecretaryService._execute_scheduled_sweep
        store_b = self.store_b

        def fake_execute(secretary_self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if secretary_self.store is store_b:
                raise RuntimeError("boom")
            return real_execute(secretary_self, *args, **kwargs)

        with patch.object(SecretaryService, "_execute_scheduled_sweep", fake_execute):
            resp = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))

        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["tenants"]["tenant_a"]["status"], "ok")
        self.assertEqual(body["tenants"]["tenant_b"]["status"], "error")
        self.assertNotIn("Traceback", body["tenants"]["tenant_b"]["error"])

        run_key_b = body["tenants"]["tenant_b"]["run_key"]
        raw_run = store_b.get_sweep_run(run_key_b)
        self.assertIsNotNone(raw_run)
        self.assertEqual(raw_run["status"], "failed")

        run_key_a = body["tenants"]["tenant_a"]["run_key"]
        raw_run_a = self.store_a.get_sweep_run(run_key_a)
        self.assertEqual(raw_run_a["status"], "done")

    def test_retry_after_partial_failure_only_reruns_the_failed_tenant(self) -> None:
        store_b = self.store_b
        real_execute = SecretaryService._execute_scheduled_sweep
        call_counts: dict[str, int] = {"tenant_b": 0}

        def flaky_execute(secretary_self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if secretary_self.store is store_b:
                call_counts["tenant_b"] += 1
                if call_counts["tenant_b"] == 1:
                    raise RuntimeError("boom")
            return real_execute(secretary_self, *args, **kwargs)

        with patch.object(SecretaryService, "_execute_scheduled_sweep", flaky_execute):
            first = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))
            self.assertEqual(first.status_code, 500)
            second = self.client.post("/internal/autonomous-sweep", headers=self._auth(self._token()))

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["tenants"]["tenant_a"]["status"], "deduplicated")
        self.assertEqual(second.json()["tenants"]["tenant_b"]["status"], "ok")
        self.assertEqual(call_counts["tenant_b"], 2)


# =============================================================================
# 3. POST /api/secretary/sweep origin handling (HTTP layer)
# =============================================================================


@unittest.skipUnless(HAS_FASTAPI, "fastapi/TestClient not available")
class TestSweepOriginHandling(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.service = _build_service(self.store)
        self.app = create_app(store=self.store, service=self.service, api_key="test-key")
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": "test-key"}

    def test_no_body_defaults_to_scheduled_with_run_key(self) -> None:
        resp = self.client.post("/api/secretary/sweep", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("origin"), "scheduled")
        self.assertIn("run_key", body)

    def test_empty_body_defaults_to_scheduled(self) -> None:
        resp = self.client.post("/api/secretary/sweep", headers=self.headers, json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("origin"), "scheduled")

    def test_explicit_manual_origin(self) -> None:
        resp = self.client.post("/api/secretary/sweep", headers=self.headers, json={"origin": "manual"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # round-5 ledger A: manual now runs the same unified pipeline as
        # scheduled, so its summary also carries origin/run_key — a fresh
        # "manual-"+uuid4() run_key with no dedup (§1).
        self.assertEqual(body.get("origin"), "manual")
        self.assertIn("run_key", body)
        self.assertTrue(body["run_key"].startswith("manual-"))

    def test_invalid_origin_is_4xx(self) -> None:
        resp = self.client.post("/api/secretary/sweep", headers=self.headers, json={"origin": "bogus"})
        self.assertIn(resp.status_code, (400, 422))

    def test_cloud_scheduler_headers_give_deterministic_run_key_and_dedup(self) -> None:
        headers = dict(self.headers)
        headers["X-CloudScheduler-JobName"] = "kd-sweep-job"
        headers["X-CloudScheduler-ScheduleTime"] = "2026-08-28T08:00:00Z"

        first = self.client.post("/api/secretary/sweep", headers=headers)
        second = self.client.post("/api/secretary/sweep", headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["run_key"], second.json()["run_key"])
        self.assertEqual(first.json()["status"], "ok")
        self.assertEqual(second.json()["status"], "deduplicated")

    def test_handler_invokes_secretary_run_sweep(self) -> None:
        with patch.object(SecretaryService, "run_sweep", return_value={"status": "ok"}) as spy:
            resp = self.client.post("/api/secretary/sweep", headers=self.headers, json={"origin": "manual"})
        self.assertEqual(resp.status_code, 200)
        spy.assert_called_once()
        _, kwargs = spy.call_args
        self.assertEqual(kwargs.get("origin"), "manual")

    def test_handler_invokes_secretary_run_sweep_scheduled_with_run_key(self) -> None:
        with patch.object(SecretaryService, "run_sweep", return_value={"status": "ok"}) as spy:
            resp = self.client.post("/api/secretary/sweep", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        spy.assert_called_once()
        _, kwargs = spy.call_args
        self.assertEqual(kwargs.get("origin"), "scheduled")
        self.assertIsNotNone(kwargs.get("run_key"))


# =============================================================================
# 4. Autonomy Policy API (GET/PUT /api/secretary/autonomy)
# =============================================================================


@unittest.skipUnless(HAS_FASTAPI, "fastapi/TestClient not available")
class TestAutonomyPolicyApi(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.service = _build_service(self.store)
        self.app = create_app(store=self.store, service=self.service, api_key="test-key")
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": "test-key"}

    def test_get_default_is_not_persisted(self) -> None:
        resp = self.client.get(
            "/api/secretary/autonomy", headers=self.headers, params={"employee_id": "emp_new"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["persisted"])
        self.assertTrue(body["monitor_stalled_work"])
        self.assertFalse(body["search_organization"])
        self.assertEqual(body["effective"]["monitor_stalled_work"], True)
        self.assertEqual(body["effective"]["search_organization"], False)

    def test_put_then_get_round_trips_and_is_persisted(self) -> None:
        payload = {
            "employee_id": "emp_rt",
            "monitor_stalled_work": True,
            "search_organization": True,
            "ask_candidate_agents": True,
            "prepare_introduction": True,
            "contact_mode": "always_ask",
        }
        put_resp = self.client.put("/api/secretary/autonomy", headers=self.headers, json=payload)
        self.assertEqual(put_resp.status_code, 200)
        self.assertTrue(put_resp.json()["persisted"])

        get_resp = self.client.get(
            "/api/secretary/autonomy", headers=self.headers, params={"employee_id": "emp_rt"}
        )
        self.assertTrue(get_resp.json()["persisted"])
        self.assertTrue(get_resp.json()["effective"]["prepare_introduction"])

    def test_normalization_search_on_monitor_off_forces_effective_search_off(self) -> None:
        payload = {
            "employee_id": "emp_norm",
            "monitor_stalled_work": False,
            "search_organization": True,
            "ask_candidate_agents": False,
            "prepare_introduction": False,
            "contact_mode": "always_ask",
        }
        resp = self.client.put("/api/secretary/autonomy", headers=self.headers, json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # normalized() replaces the raw booleans with their effective values before saving.
        self.assertFalse(body["search_organization"])
        self.assertFalse(body["effective"]["search_organization"])

    def test_contact_mode_other_than_always_ask_is_400(self) -> None:
        payload = {
            "employee_id": "emp_bad",
            "monitor_stalled_work": True,
            "search_organization": False,
            "ask_candidate_agents": False,
            "prepare_introduction": False,
            "contact_mode": "auto_contact",
        }
        resp = self.client.put("/api/secretary/autonomy", headers=self.headers, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_get_malformed_employee_id_is_400(self) -> None:
        """round-5 ledger E5: employee_id must match ^[A-Za-z0-9_-]{1,64}$."""
        resp = self.client.get(
            "/api/secretary/autonomy", headers=self.headers, params={"employee_id": "emp/../etc"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_malformed_employee_id_is_400(self) -> None:
        payload = {
            "employee_id": "emp owner; DROP TABLE",
            "monitor_stalled_work": True,
            "search_organization": False,
            "ask_candidate_agents": False,
            "prepare_introduction": False,
            "contact_mode": "always_ask",
        }
        resp = self.client.put("/api/secretary/autonomy", headers=self.headers, json=payload)
        self.assertEqual(resp.status_code, 400)

    def test_human_other_employee_is_403(self) -> None:
        from knowledge_discovery.auth import Principal

        class _Fixed:
            def resolve(self, request):  # type: ignore[no-untyped-def]
                return Principal(mode="human", tenant_id="meridian", employee_id="emp_someone_else")

        app = create_app(store=self.store, service=self.service, principal_resolver=_Fixed())
        client = TestClient(app)
        resp = client.get("/api/secretary/autonomy", params={"employee_id": "emp_target"})
        self.assertEqual(resp.status_code, 403)
        resp2 = client.put(
            "/api/secretary/autonomy",
            json={
                "employee_id": "emp_target",
                "monitor_stalled_work": True,
                "search_organization": False,
                "ask_candidate_agents": False,
                "prepare_introduction": False,
                "contact_mode": "always_ask",
            },
        )
        self.assertEqual(resp2.status_code, 403)

    def test_system_principal_is_403(self) -> None:
        from knowledge_discovery.auth import Principal

        class _Fixed:
            def resolve(self, request):  # type: ignore[no-untyped-def]
                return Principal(mode="system", tenant_id="meridian", employee_id=None)

        app = create_app(store=self.store, service=self.service, principal_resolver=_Fixed())
        client = TestClient(app)
        resp = client.get("/api/secretary/autonomy", params={"employee_id": "emp_target"})
        self.assertEqual(resp.status_code, 403)
        resp2 = client.put(
            "/api/secretary/autonomy",
            json={
                "employee_id": "emp_target",
                "monitor_stalled_work": True,
                "search_organization": False,
                "ask_candidate_agents": False,
                "prepare_introduction": False,
                "contact_mode": "always_ask",
            },
        )
        self.assertEqual(resp2.status_code, 403)


# =============================================================================
# 5. Digest extension: last_sweep + autonomy.effective (design §8, C-17)
# =============================================================================


@unittest.skipUnless(HAS_FASTAPI, "fastapi/TestClient not available")
class TestDigestAutonomyExtension(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.store.save_task(
            Task(task_id="task_1", owner_employee_id="emp_x", title="Task", status="todo")
        )
        self.service = _build_service(self.store)
        self.app = create_app(store=self.store, service=self.service, api_key="test-key")
        self.client = TestClient(self.app)
        self.headers = {"X-API-Key": "test-key"}

    def test_digest_has_null_last_sweep_before_any_scheduled_sweep(self) -> None:
        resp = self.client.get(
            "/api/secretary/digest", headers=self.headers, params={"employee_id": "emp_x"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["last_sweep"])
        self.assertIn("autonomy", body)
        self.assertIn("effective", body["autonomy"])
        self.assertEqual(body["autonomy"]["effective"]["monitor_stalled_work"], True)

    def test_digest_reports_last_sweep_after_scheduled_sweep(self) -> None:
        sweep_resp = self.client.post("/api/secretary/sweep", headers=self.headers, json={"origin": "scheduled"})
        self.assertEqual(sweep_resp.status_code, 200)

        resp = self.client.get(
            "/api/secretary/digest", headers=self.headers, params={"employee_id": "emp_x"}
        )
        body = resp.json()
        self.assertIsNotNone(body["last_sweep"])
        self.assertEqual(body["last_sweep"]["origin"], "scheduled")
        self.assertIsNotNone(body["last_sweep"]["at"])

    def test_digest_reflects_monitor_off_effective_policy(self) -> None:
        self.client.put(
            "/api/secretary/autonomy",
            headers=self.headers,
            json={
                "employee_id": "emp_x",
                "monitor_stalled_work": False,
                "search_organization": False,
                "ask_candidate_agents": False,
                "prepare_introduction": False,
                "contact_mode": "always_ask",
            },
        )
        resp = self.client.get(
            "/api/secretary/digest", headers=self.headers, params={"employee_id": "emp_x"}
        )
        self.assertFalse(resp.json()["autonomy"]["effective"]["monitor_stalled_work"])

    def test_digest_malformed_employee_id_is_400(self) -> None:
        """round-6 ledger C-30: the digest route must reject a malformed
        employee_id with 400 before it reaches Store, same as the Autonomy
        API (round-5 ledger E5) — previously it passed employee_id straight
        through to Firestore as a document id."""
        resp = self.client.get(
            "/api/secretary/digest", headers=self.headers, params={"employee_id": "emp/../etc"}
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
