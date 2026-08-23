"""Unit tests for knowledge_discovery.auth (design.md §16.1, Part A: FR25-26).

Verifies:
- IapResolver: real ES256-signed IAP-format JWTs are verified through the
  actual google-auth verification code path (not hand-rolled crypto), with
  negative cases for bad signature / aud / iss / expiry / future iat /
  missing email, and the +/-30s clock-skew boundary.
- The in-process cert cache: normal cache hits avoid network calls, a fetch
  failure while the cache is still fresh is a non-event, and a fetch failure
  once the cache is fully stale fails closed (raises, not silently 503s).
- validate_iap_audience_format's Cloud Run IAP audience format check.
- The full §16.1 permission table (demo/human/system x every route) and the
  require_self ownership checks, driven over HTTP via TestClient with the
  demo-key path unchanged and human/system driven by an injectable
  PrincipalResolver test double.
- The consent CAS (W-1): ask.to_entity/intent/pending verified atomically,
  duplicate POST -> 409, mismatched ask -> 403.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.matching import FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import Agent, Card, ConnectionDetails, ConnectionInferenceResult, Profile, ProfileItem
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

    from knowledge_discovery.auth import (
        IAP_ISSUER,
        IapResolver,
        Principal,
        _CachingCertsRequest,
        validate_iap_audience_format,
    )
    from knowledge_discovery.server import create_app

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


TEST_AUDIENCE = "/projects/123456789012/locations/us-central1/services/knowledge-discovery"


# -----------------------------------------------------------------------------
# Test doubles
# -----------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, data: bytes, headers: dict | None = None) -> None:
        self.status = status
        self.data = data
        self.headers = headers or {}


class _FakeCertsTransport:
    """Stands in for google.auth.transport.Request: a callable(url, method=...) -> response."""

    def __init__(self, certs: dict, status: int = 200, headers: dict | None = None, raise_exc: Exception | None = None) -> None:
        self.certs = certs
        self.status = status
        self.headers = headers or {}
        self.raise_exc = raise_exc
        self.call_count = 0

    def __call__(self, url: str, method: str = "GET", **kwargs) -> _FakeResponse:
        self.call_count += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        import json

        return _FakeResponse(self.status, json.dumps(self.certs).encode("utf-8"), self.headers)


class _FakeRequest:
    """Minimal stand-in for fastapi.Request: only .headers is used by resolvers."""

    def __init__(self, headers: dict | None = None, query_params: dict | None = None) -> None:
        self.headers = headers or {}
        self.query_params = query_params or {}


class _FixedPrincipalResolver:
    """Test double: always resolves to whatever Principal is currently set,
    regardless of the incoming request. Lets HTTP-level permission-table
    tests flip principal.mode/employee_id per assertion without needing to
    forge distinct credentials for demo/human/system on every call."""

    def __init__(self, principal: "Principal") -> None:
        self.principal = principal

    def resolve(self, request) -> "Principal":
        return self.principal


def _generate_ec_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv, pub_pem


def _make_iap_jwt(priv, kid: str = "test-kid-1", **claim_overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": IAP_ISSUER,
        "aud": TEST_AUDIENCE,
        "iat": now,
        "exp": now + 600,
        "email": "rachel.kim@meridian-care.example",
        "sub": "accounts.google.com:1234567890",
    }
    payload.update(claim_overrides)
    signer = es256.ES256Signer(priv, key_id=kid)
    return google_jwt.encode(signer, payload, header={"alg": "ES256", "kid": kid})


@unittest.skipUnless(HAS_CRYPTO and HAS_FASTAPI, "cryptography/google-auth/fastapi not available")
class TestIapJwtVerification(unittest.TestCase):
    """IAP assertion verification through the real google-auth code path."""

    def setUp(self) -> None:
        self.priv, self.pub_pem = _generate_ec_keypair()
        self.other_priv, self.other_pub_pem = _generate_ec_keypair()
        self.certs = {"test-kid-1": self.pub_pem}
        self.transport = _FakeCertsTransport(self.certs)
        self.cert_cache = _CachingCertsRequest()
        self.cert_cache._transport = self.transport
        self.store = InMemoryStore()
        self.store.save_identity("rachel.kim@meridian-care.example", "emp_rachel_kim")
        self.resolver = IapResolver(
            store=self.store,
            audience=TEST_AUDIENCE,
            allowed_domains=["meridian-care.example"],
            system_accounts=["kd-sweeper@my-project.iam.gserviceaccount.com"],
            cert_cache=self.cert_cache,
        )

    def _resolve(self, token: str) -> Principal:
        request = _FakeRequest(headers={"x-goog-iap-jwt-assertion": token})
        return self.resolver.resolve(request)

    def test_valid_human_assertion_resolves_employee_id(self) -> None:
        token = _make_iap_jwt(self.priv)
        principal = self._resolve(token)
        self.assertEqual(principal.mode, "human")
        self.assertEqual(principal.employee_id, "emp_rachel_kim")
        self.assertEqual(principal.email, "rachel.kim@meridian-care.example")
        self.assertEqual(principal.tenant_id, "meridian")

    def test_email_case_is_normalized(self) -> None:
        token = _make_iap_jwt(self.priv, email="Rachel.Kim@Meridian-Care.EXAMPLE")
        principal = self._resolve(token)
        self.assertEqual(principal.mode, "human")
        self.assertEqual(principal.employee_id, "emp_rachel_kim")

    def test_system_account_resolves_system_mode(self) -> None:
        token = _make_iap_jwt(self.priv, email="kd-sweeper@my-project.iam.gserviceaccount.com")
        principal = self._resolve(token)
        self.assertEqual(principal.mode, "system")
        self.assertIsNone(principal.employee_id)

    def test_missing_header_is_401(self) -> None:
        request = _FakeRequest(headers={})
        with self.assertRaises(Exception) as ctx:
            self.resolver.resolve(request)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_bad_signature_is_401(self) -> None:
        # kid matches but the certs mapping holds a DIFFERENT key's public cert.
        bad_certs = {"test-kid-1": self.other_pub_pem}
        self.cert_cache._cached_response = None  # force refetch with bad certs
        self.transport.certs = bad_certs
        token = _make_iap_jwt(self.priv)
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_audience_mismatch_is_401(self) -> None:
        token = _make_iap_jwt(self.priv, aud="/projects/999/locations/us-central1/services/other-svc")
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_issuer_mismatch_is_401(self) -> None:
        token = _make_iap_jwt(self.priv, iss="https://not-iap.example.com")
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_expired_token_is_401(self) -> None:
        now = int(time.time())
        token = _make_iap_jwt(self.priv, iat=now - 3600, exp=now - 1800)
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_future_iat_is_401(self) -> None:
        now = int(time.time())
        token = _make_iap_jwt(self.priv, iat=now + 3600, exp=now + 4200)
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_missing_email_claim_is_401(self) -> None:
        now = int(time.time())
        payload_overrides = {"iat": now, "exp": now + 600}
        signer = es256.ES256Signer(self.priv, key_id="test-kid-1")
        payload = {
            "iss": IAP_ISSUER,
            "aud": TEST_AUDIENCE,
            "sub": "accounts.google.com:1",
            **payload_overrides,
        }
        token = google_jwt.encode(signer, payload, header={"alg": "ES256", "kid": "test-kid-1"})
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_clock_skew_boundary_accepts_within_30s(self) -> None:
        now = int(time.time())
        # exp exactly 30s in the past: latest = exp + skew(30) == now -> accepted.
        token = _make_iap_jwt(self.priv, iat=now - 630, exp=now - 30)
        principal = self._resolve(token)
        self.assertEqual(principal.mode, "human")

    def test_clock_skew_boundary_rejects_beyond_30s(self) -> None:
        now = int(time.time())
        # exp 31s in the past: latest = exp + skew(30) == now - 1 < now -> rejected.
        token = _make_iap_jwt(self.priv, iat=now - 631, exp=now - 31)
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_unregistered_domain_is_403(self) -> None:
        token = _make_iap_jwt(self.priv, email="someone@not-meridian.example")
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_domain_registered_but_no_identity_is_403(self) -> None:
        token = _make_iap_jwt(self.priv, email="ghost.person@meridian-care.example")
        with self.assertRaises(Exception) as ctx:
            self._resolve(token)
        self.assertEqual(ctx.exception.status_code, 403)


@unittest.skipUnless(HAS_CRYPTO and HAS_FASTAPI, "cryptography/google-auth/fastapi not available")
class TestCertCache(unittest.TestCase):
    """Cert cache resilience: cache hits skip the network; a failed refetch
    serves stale certs through a grace window, then fails closed (401)."""

    def setUp(self) -> None:
        self.priv, self.pub_pem = _generate_ec_keypair()
        self.certs = {"test-kid-1": self.pub_pem}

    def test_cache_hit_skips_network(self) -> None:
        transport = _FakeCertsTransport(self.certs)
        cache = _CachingCertsRequest(default_ttl_seconds=60)
        cache._transport = transport
        cache(url="https://www.gstatic.com/iap/verify/public_key")
        cache(url="https://www.gstatic.com/iap/verify/public_key")
        self.assertEqual(transport.call_count, 1)

    def test_fetch_failure_within_grace_serves_stale(self) -> None:
        transport = _FakeCertsTransport(self.certs)
        cache = _CachingCertsRequest(default_ttl_seconds=0.05)
        cache._transport = transport
        first = cache(url="u")
        time.sleep(0.08)  # past ttl, still within the 2x grace window
        transport.raise_exc = RuntimeError("network down")
        second = cache(url="u")
        self.assertIs(first, second)

    def test_fetch_failure_beyond_grace_raises(self) -> None:
        transport = _FakeCertsTransport(self.certs)
        cache = _CachingCertsRequest(default_ttl_seconds=0.05)
        cache._transport = transport
        cache(url="u")
        transport.raise_exc = RuntimeError("network down")
        time.sleep(0.15)  # past ttl*2 grace window entirely
        with self.assertRaises(RuntimeError):
            cache(url="u")


@unittest.skipUnless(HAS_FASTAPI, "fastapi not available")
class TestIapAudienceFormat(unittest.TestCase):
    def test_valid_format_accepted(self) -> None:
        validate_iap_audience_format("/projects/123456789/locations/us-central1/services/kd")

    def test_missing_audience_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_iap_audience_format("")

    def test_malformed_audience_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_iap_audience_format("projects/123/locations/us-central1/services/kd")


@unittest.skipUnless(HAS_FASTAPI, "fastapi/TestClient not available")
class TestPermissionTable(unittest.TestCase):
    """§16.1 permission table across demo / human / system, and require_self."""

    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.inferencer = FakeConnectionInferencer()
        self.matching_engine = MatchingEngine(
            inferencer=self.inferencer,
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )
        self.service = KnowledgeDiscoveryService(store=self.store, matching_engine=self.matching_engine)

        self.agent_marcus = Agent(
            agent_id="agent_marcus_delgado",
            employee_id="emp_marcus_delgado",
            display_name="Marcus Delgado",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_marcus = Profile(
            employee_id="emp_marcus_delgado",
            name="Marcus Delgado",
            role="Commercial Broker",
            items=[ProfileItem(key="current_work", body="Brokers medical office buildings and tracks zoning, ADA and clinic relocation requirements.", visibility="public", reviewed=True)],
        )
        self.agent_rachel = Agent(
            agent_id="agent_rachel_kim",
            employee_id="emp_rachel_kim",
            display_name="Rachel Kim",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_rachel = Profile(
            employee_id="emp_rachel_kim",
            name="Rachel Kim",
            role="Account Manager",
            items=[ProfileItem(key="current_work", body="Manages staffing contracts for hospital clients and handles clinic relocations.", visibility="public", reviewed=True)],
        )
        self.store.save_agent(self.agent_marcus)
        self.store.save_agent(self.agent_rachel)
        self.store.save_profile(self.profile_marcus)
        self.store.save_profile(self.profile_rachel)

        self.principal_resolver = _FixedPrincipalResolver(Principal(mode="demo", tenant_id="meridian"))
        self.app = create_app(
            store=self.store,
            service=self.service,
            principal_resolver=self.principal_resolver,
        )
        self.client = TestClient(self.app)

    def _as(self, mode: str, employee_id: str | None = None) -> None:
        self.principal_resolver.principal = Principal(
            mode=mode,
            tenant_id="meridian",
            employee_id=employee_id,
            email=f"{employee_id}@meridian-care.example" if employee_id else None,
        )

    def _make_stagnation_card(self, owner_employee_id: str) -> str:
        card = Card(
            card_id=f"card_test_{owner_employee_id}_{int(time.time() * 1000)}",
            owner_employee_id=owner_employee_id,
            type="stagnation",
            tier="request_draft",
            payload={"task_id": "task_x", "task_title": "Test task"},
            status="open",
        )
        self.store.save_card(card)
        return card.card_id

    # -- /api/me -------------------------------------------------------------

    def test_get_me(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.get("/api/me")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["mode"], "human")
        self.assertEqual(data["tenant_id"], "meridian")
        self.assertEqual(data["employee_id"], "emp_marcus_delgado")

    # -- POST /api/query -------------------------------------------------------

    def test_query_demo_allowed(self) -> None:
        self._as("demo")
        res = self.client.post("/api/query", json={"requester_id": "emp_marcus_delgado", "question_text": "hi"})
        self.assertEqual(res.status_code, 200)

    def test_query_human_self_allowed(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.post("/api/query", json={"requester_id": "emp_marcus_delgado", "question_text": "hi"})
        self.assertEqual(res.status_code, 200)

    def test_query_human_other_forbidden(self) -> None:
        self._as("human", "emp_rachel_kim")
        res = self.client.post("/api/query", json={"requester_id": "emp_marcus_delgado", "question_text": "hi"})
        self.assertEqual(res.status_code, 403)

    def test_query_system_forbidden(self) -> None:
        self._as("system")
        res = self.client.post("/api/query", json={"requester_id": "emp_marcus_delgado", "question_text": "hi"})
        self.assertEqual(res.status_code, 403)

    # -- GET /api/requester/{id}/status ----------------------------------------

    def test_requester_status_human_self_allowed(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.get("/api/requester/emp_marcus_delgado/status")
        self.assertEqual(res.status_code, 200)

    def test_requester_status_human_other_forbidden(self) -> None:
        self._as("human", "emp_rachel_kim")
        res = self.client.get("/api/requester/emp_marcus_delgado/status")
        self.assertEqual(res.status_code, 403)

    def test_requester_status_system_forbidden(self) -> None:
        self._as("system")
        res = self.client.get("/api/requester/emp_marcus_delgado/status")
        self.assertEqual(res.status_code, 403)

    # -- GET /api/candidate/{agent_id}/asks ------------------------------------

    def test_candidate_asks_human_self_allowed(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.get("/api/candidate/agent_marcus_delgado/asks")
        self.assertEqual(res.status_code, 200)

    def test_candidate_asks_human_other_forbidden(self) -> None:
        self._as("human", "emp_rachel_kim")
        res = self.client.get("/api/candidate/agent_marcus_delgado/asks")
        self.assertEqual(res.status_code, 403)

    def test_candidate_asks_system_forbidden(self) -> None:
        self._as("system")
        res = self.client.get("/api/candidate/agent_marcus_delgado/asks")
        self.assertEqual(res.status_code, 403)

    # -- POST /api/candidate/{agent_id}/consent (route-level ownership) -------

    def test_consent_human_self_allowed(self) -> None:
        self._as("demo")
        self.inferencer.set_override(
            "emp_marcus_delgado",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="Knows medical office zoning.", score=0.9),
                cited_item_keys=["current_work"],
            ),
        )
        q = self.client.post(
            "/api/query", json={"requester_id": "emp_jordan_lee", "question_text": "Looking for medical facility zoning and clinic site acquisition expertise."}
        )
        self.assertEqual(q.status_code, 200)
        asks = self.client.get("/api/candidate/agent_marcus_delgado/asks").json()["asks"]
        self.assertGreater(len(asks), 0)
        ask_id = asks[0]["ask_audit_id"]

        self._as("human", "emp_marcus_delgado")
        res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": ask_id, "decision": "granted"},
        )
        self.assertEqual(res.status_code, 200)

    def test_consent_human_other_forbidden(self) -> None:
        self._as("human", "emp_rachel_kim")
        res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": "ask_does_not_exist", "decision": "granted"},
        )
        self.assertEqual(res.status_code, 403)

    def test_consent_system_forbidden(self) -> None:
        self._as("system")
        res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": "ask_does_not_exist", "decision": "granted"},
        )
        self.assertEqual(res.status_code, 403)

    # -- GET /api/audit/messages (tenant-wide read, all 3 modes) --------------

    def test_audit_messages_all_modes_allowed(self) -> None:
        for mode, emp in (("demo", None), ("human", "emp_marcus_delgado"), ("system", None)):
            self._as(mode, emp)
            res = self.client.get("/api/audit/messages")
            self.assertEqual(res.status_code, 200, msg=f"mode={mode}")

    # -- POST /api/secretary/sweep ----------------------------------------------

    def test_sweep_demo_allowed(self) -> None:
        self._as("demo")
        res = self.client.post("/api/secretary/sweep", json={})
        self.assertEqual(res.status_code, 200)

    def test_sweep_human_forbidden(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.post("/api/secretary/sweep", json={})
        self.assertEqual(res.status_code, 403)

    def test_sweep_system_allowed(self) -> None:
        self._as("system")
        res = self.client.post("/api/secretary/sweep", json={})
        self.assertEqual(res.status_code, 200)

    # -- GET /api/secretary/digest -----------------------------------------------

    def test_digest_human_self_allowed(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.get("/api/secretary/digest", params={"employee_id": "emp_marcus_delgado"})
        self.assertEqual(res.status_code, 200)

    def test_digest_human_other_forbidden(self) -> None:
        self._as("human", "emp_rachel_kim")
        res = self.client.get("/api/secretary/digest", params={"employee_id": "emp_marcus_delgado"})
        self.assertEqual(res.status_code, 403)

    def test_digest_system_allowed_explicit_employee(self) -> None:
        self._as("system")
        res = self.client.get("/api/secretary/digest", params={"employee_id": "emp_marcus_delgado"})
        self.assertEqual(res.status_code, 200)

    # -- POST /api/secretary/confirm (card owner) --------------------------------

    def test_confirm_human_self_allowed(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("human", "emp_marcus_delgado")
        res = self.client.post("/api/secretary/confirm", json={"card_id": card_id, "edited_question": "q?"})
        self.assertEqual(res.status_code, 200)

    def test_confirm_human_other_forbidden(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("human", "emp_rachel_kim")
        res = self.client.post("/api/secretary/confirm", json={"card_id": card_id, "edited_question": "q?"})
        self.assertEqual(res.status_code, 403)

    def test_confirm_system_forbidden(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("system")
        res = self.client.post("/api/secretary/confirm", json={"card_id": card_id, "edited_question": "q?"})
        self.assertEqual(res.status_code, 403)

    # -- POST /api/secretary/cards/{card_id}/dismiss (card owner) ----------------

    def test_dismiss_human_self_allowed(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("human", "emp_marcus_delgado")
        res = self.client.post(f"/api/secretary/cards/{card_id}/dismiss")
        self.assertEqual(res.status_code, 200)

    def test_dismiss_human_other_forbidden(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("human", "emp_rachel_kim")
        res = self.client.post(f"/api/secretary/cards/{card_id}/dismiss")
        self.assertEqual(res.status_code, 403)

    def test_dismiss_system_forbidden(self) -> None:
        card_id = self._make_stagnation_card("emp_marcus_delgado")
        self._as("system")
        res = self.client.post(f"/api/secretary/cards/{card_id}/dismiss")
        self.assertEqual(res.status_code, 403)

    # -- POST /api/secretary/profile-diff/{card_id}/review (card owner) ----------

    def _make_profile_diff_card(self, owner_employee_id: str) -> str:
        card = Card(
            card_id=f"card_diff_{owner_employee_id}_{int(time.time() * 1000)}",
            owner_employee_id=owner_employee_id,
            type="profile_diff",
            tier=None,
            payload={"item_key": "new_skill", "body_draft": "Draft body.", "source_mail_id": "mail_x", "subject": "s"},
            status="open",
        )
        self.store.save_card(card)
        return card.card_id

    def test_review_human_self_allowed(self) -> None:
        card_id = self._make_profile_diff_card("emp_marcus_delgado")
        self._as("human", "emp_marcus_delgado")
        res = self.client.post(f"/api/secretary/profile-diff/{card_id}/review", json={"action": "dismiss"})
        self.assertEqual(res.status_code, 200)

    def test_review_human_other_forbidden(self) -> None:
        card_id = self._make_profile_diff_card("emp_marcus_delgado")
        self._as("human", "emp_rachel_kim")
        res = self.client.post(f"/api/secretary/profile-diff/{card_id}/review", json={"action": "dismiss"})
        self.assertEqual(res.status_code, 403)

    def test_review_system_forbidden(self) -> None:
        card_id = self._make_profile_diff_card("emp_marcus_delgado")
        self._as("system")
        res = self.client.post(f"/api/secretary/profile-diff/{card_id}/review", json={"action": "dismiss"})
        self.assertEqual(res.status_code, 403)

    # -- POST /api/probe/unregistered-intent -------------------------------------

    def test_probe_demo_allowed(self) -> None:
        self._as("demo")
        res = self.client.post("/api/probe/unregistered-intent")
        self.assertEqual(res.status_code, 200)

    def test_probe_human_forbidden(self) -> None:
        self._as("human", "emp_marcus_delgado")
        res = self.client.post("/api/probe/unregistered-intent")
        self.assertEqual(res.status_code, 403)

    def test_probe_system_allowed(self) -> None:
        self._as("system")
        res = self.client.post("/api/probe/unregistered-intent")
        self.assertEqual(res.status_code, 200)

    # -- /attachments and static pages stay unauthenticated -----------------------

    def test_attachments_and_static_pages_need_no_principal(self) -> None:
        self._as("human", "emp_marcus_delgado")  # irrelevant: these ignore the resolver entirely
        res = self.client.get("/attachments/doc_clinic_relocation_guide")
        self.assertEqual(res.status_code, 200)
        res2 = self.client.get("/requester")
        self.assertEqual(res2.status_code, 200)


@unittest.skipUnless(HAS_FASTAPI, "fastapi/TestClient not available")
class TestConsentCas(unittest.TestCase):
    """W-1: ask.to_entity/intent/pending verified atomically; duplicate POST -> 409."""

    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.inferencer = FakeConnectionInferencer()
        self.matching_engine = MatchingEngine(
            inferencer=self.inferencer,
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )
        self.service = KnowledgeDiscoveryService(store=self.store, matching_engine=self.matching_engine)
        self.agent_marcus = Agent(
            agent_id="agent_marcus_delgado",
            employee_id="emp_marcus_delgado",
            display_name="Marcus Delgado",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.agent_rachel = Agent(
            agent_id="agent_rachel_kim",
            employee_id="emp_rachel_kim",
            display_name="Rachel Kim",
            supported_intents=["connect_ask", "connect_ask_private", "no_connection"],
            active=True,
        )
        self.profile_marcus = Profile(
            employee_id="emp_marcus_delgado",
            name="Marcus Delgado",
            role="Broker",
            items=[ProfileItem(key="current_work", body="Brokers medical office buildings and tracks zoning, ADA and clinic relocation requirements.", visibility="public", reviewed=True)],
        )
        self.profile_rachel = Profile(
            employee_id="emp_rachel_kim",
            name="Rachel Kim",
            role="Account Manager",
            items=[ProfileItem(key="current_work", body="Manages staffing contracts for hospital clients and handles clinic relocations.", visibility="public", reviewed=True)],
        )
        self.store.save_agent(self.agent_marcus)
        self.store.save_agent(self.agent_rachel)
        self.store.save_profile(self.profile_marcus)
        self.store.save_profile(self.profile_rachel)

        self.inferencer.set_override(
            "emp_marcus_delgado",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="Knows medical office zoning.", score=0.9),
                cited_item_keys=["current_work"],
            ),
        )
        self.inferencer.set_override(
            "emp_rachel_kim",
            ConnectionInferenceResult(
                connection=ConnectionDetails(reason_text="Manages relevant staffing contracts.", score=0.85),
                cited_item_keys=["current_work"],
            ),
        )

        self.principal_resolver = _FixedPrincipalResolver(Principal(mode="demo", tenant_id="meridian"))
        self.app = create_app(store=self.store, service=self.service, principal_resolver=self.principal_resolver)
        self.client = TestClient(self.app)

        q = self.client.post(
            "/api/query", json={"requester_id": "emp_jordan_lee", "question_text": "Looking for medical facility zoning and clinic site acquisition expertise."}
        )
        self.assertEqual(q.status_code, 200)
        self.marcus_ask_id = self.client.get("/api/candidate/agent_marcus_delgado/asks").json()["asks"][0]["ask_audit_id"]
        self.rachel_ask_id = self.client.get("/api/candidate/agent_rachel_kim/asks").json()["asks"][0]["ask_audit_id"]

    def test_double_post_is_409(self) -> None:
        first = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": self.marcus_ask_id, "decision": "granted"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": self.marcus_ask_id, "decision": "granted"},
        )
        self.assertEqual(second.status_code, 409)

    def test_ask_addressed_to_other_agent_is_403(self) -> None:
        # Rachel's ask_audit_id used against Marcus's agent_id endpoint.
        res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": self.rachel_ask_id, "decision": "granted"},
        )
        self.assertEqual(res.status_code, 403)

    def test_unknown_ask_is_404(self) -> None:
        res = self.client.post(
            "/api/candidate/agent_marcus_delgado/consent",
            json={"ask_audit_id": "ask_totally_unknown", "decision": "granted"},
        )
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
