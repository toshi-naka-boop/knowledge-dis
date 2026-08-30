"""Principal resolution and authentication (design.md §16.1/§16.2, FR25-27).

This module is the single differentiation point between the demo API-key
world and IAP-authenticated production, and (Part B) the point where a
credential is bound to a tenant. All server.py routes depend on
`PrincipalResolver.resolve(request) -> Principal`.

- `Principal`: the resolved caller identity (mode/tenant_id/employee_id/email).
- `DemoKeyResolver`: AUTH_MODE=demo_key. Reproduces the pre-existing
  X-API-Key / api_key check byte-for-byte, except the tenant_id is now
  whichever tenant's key matched (design §16.2 C-42/W-2: "どの鍵と一致した
  かでテナントに束縛される", no key spans multiple tenants).
- `IapResolver`: AUTH_MODE=iap. Verifies the `X-Goog-IAP-JWT-Assertion`
  header with `google.oauth2.id_token.verify_token`, then resolves the
  verified email to a tenant-scoped Principal via the TenantRegistry's
  system_accounts / email_domains and that tenant's Store.get_identity.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import HTTPException, Request, status
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "knowledge_discovery.auth requires fastapi (see scripts/requirements.txt)"
    ) from exc

from knowledge_discovery.tenancy import ContextRouter, TenantRegistry

# Cloud Run IAP audience format: /projects/<PROJECT_NUMBER>/locations/<REGION>/services/<SERVICE>
IAP_AUDIENCE_PATTERN = re.compile(r"^/projects/\d+/locations/[a-z0-9-]+/services/[a-z0-9-]+$")

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CLOCK_SKEW_SECONDS = 30

# Default cert cache freshness window (Cache-Control max-age overrides this
# when present on the certs response). "取得失敗時は期限内の旧鍵で継続、期限切れは401":
# while the cache is within this window, no network call is made at all; once
# it elapses, a fresh fetch is required, and a failed fetch fails closed
# immediately (no extra grace window past ttl — round-14 E-12/S-12).
DEFAULT_CERT_CACHE_TTL_SECONDS = 3600


@dataclass
class Principal:
    """Resolved caller identity for a single request (design §16.1)."""

    mode: str  # "demo" | "human" | "system"
    tenant_id: str
    employee_id: str | None = None
    email: str | None = None


class PrincipalResolver:
    """Abstract resolver: request -> Principal. Failure must raise HTTPException(401)."""

    def resolve(self, request: Request) -> Principal:
        raise NotImplementedError


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


#: HttpOnly cookie set by POST /login: carries the same demo key as the
#: X-API-Key header, so it never has to ride in the URL (demo_key mode only).
DEMO_ACCESS_COOKIE = "kd_access"


class DemoKeyResolver(PrincipalResolver):
    """AUTH_MODE=demo_key: the key presented determines the tenant (§16.2).

    Every caller who presents a key registered in the tenant ledger gets
    mode="demo" bound to whichever tenant that key belongs to, with no
    employee_id -- each API's own request payload supplies the acting
    identity (unchanged from the pre-Part-A behavior). There is no key that
    spans multiple tenants and no way to address another tenant from this
    resolver (design §16.2: "全テナント横断の鍵...は置かない").

    The key may arrive three equivalent ways: X-API-Key header, api_key query
    parameter, or the DEMO_ACCESS_COOKIE set by POST /login (the browser
    sign-in path, so judges never carry the key in a URL).
    """

    def __init__(self, registry: TenantRegistry) -> None:
        self.registry = registry

    def resolve(self, request: Request) -> Principal:
        provided_key = (
            request.headers.get("x-api-key")
            or request.query_params.get("api_key")
            or request.cookies.get(DEMO_ACCESS_COOKIE)
        )
        tenant = self.registry.resolve_by_api_key(provided_key) if provided_key else None
        if tenant is None:
            raise _unauthorized(
                "Invalid or missing API key. Provide header 'X-API-Key' or query parameter 'api_key'."
            )
        return Principal(mode="demo", tenant_id=tenant.tenant_id, employee_id=None, email=None)


class _CachingCertsRequest:
    """A `google.auth.transport.Request`-compatible callable that caches the
    IAP public key response in-process.

    While the cached response is within its freshness window, no network
    call is made. Once the window elapses, a fresh fetch is attempted; if
    that fetch fails, the stale response is no longer served -- the
    exception propagates immediately, which IapResolver turns into a
    fail-closed 401 (not 503). Design §16.1: "取得失敗時は期限内の旧鍵で継続、
    期限切れは401" -- there is no grace period beyond the freshness window
    (round-14 E-12/S-12).
    """

    def __init__(self, default_ttl_seconds: int = DEFAULT_CERT_CACHE_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl_seconds
        self._transport: Any | None = None
        self._lock = threading.Lock()
        self._cached_response: Any | None = None
        self._fetched_at: float = 0.0
        self._ttl: float = default_ttl_seconds

    def _get_transport(self) -> Any:
        if self._transport is None:
            from google.auth.transport.requests import Request as GoogleAuthRequest

            self._transport = GoogleAuthRequest()
        return self._transport

    @staticmethod
    def _max_age_from_headers(headers: Any) -> float | None:
        if headers is None:
            return None
        cache_control = headers.get("Cache-Control") or headers.get("cache-control")
        if not cache_control:
            return None
        match = re.search(r"max-age=(\d+)", cache_control)
        if match:
            return float(match.group(1))
        return None

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        now = time.monotonic()
        with self._lock:
            if self._cached_response is not None and (now - self._fetched_at) < self._ttl:
                return self._cached_response
            try:
                response = self._get_transport()(url, method=method, **kwargs)
                if response.status != 200:
                    raise RuntimeError(f"IAP certs endpoint returned HTTP {response.status}")
                self._cached_response = response
                self._fetched_at = now
                self._ttl = self._max_age_from_headers(getattr(response, "headers", None)) or self._default_ttl
                return response
            except Exception:
                # Fetch failed past the freshness window: no grace period.
                # Propagate so IapResolver.resolve() fails closed with 401.
                raise


class IapResolver(PrincipalResolver):
    """AUTH_MODE=iap: verify the IAP-signed JWT and resolve email -> Principal.

    The tenant is resolved from the TenantRegistry (§16.2): a system_accounts
    match binds mode=system to that account's tenant; otherwise the email
    domain resolves a tenant and that tenant's own Store.get_identity (via
    ContextRouter) resolves the employee_id for mode=human. IAP_ALLOWED_DOMAINS
    / IAP_SYSTEM_ACCOUNTS envs are absorbed into the ledger (no separate
    single-tenant fallback list; not backward compatible with Part A's env vars).
    """

    def __init__(
        self,
        registry: TenantRegistry,
        router: ContextRouter,
        audience: str,
        cert_cache: _CachingCertsRequest | None = None,
    ) -> None:
        self.registry = registry
        self.router = router
        self.audience = audience
        self._cert_request = cert_cache or _CachingCertsRequest()

    def resolve(self, request: Request) -> Principal:
        assertion = request.headers.get("x-goog-iap-jwt-assertion")
        if not assertion:
            raise _unauthorized("Missing X-Goog-IAP-JWT-Assertion header.")

        try:
            from google.oauth2 import id_token as google_id_token
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("google-auth is required for AUTH_MODE=iap (see scripts/requirements.txt)") from exc

        try:
            payload = google_id_token.verify_token(
                assertion,
                request=self._cert_request,
                audience=self.audience,
                certs_url=IAP_CERTS_URL,
                clock_skew_in_seconds=IAP_CLOCK_SKEW_SECONDS,
            )
        except Exception as exc:
            raise _unauthorized(f"Invalid IAP assertion: {exc}") from exc

        if payload.get("iss") != IAP_ISSUER:
            raise _unauthorized("IAP assertion has an unexpected issuer.")

        email = payload.get("email")
        if not email:
            raise _unauthorized("IAP assertion is missing the required 'email' claim.")
        email = str(email).strip().lower()

        system_tenant = self.registry.resolve_by_system_account(email)
        if system_tenant is not None:
            return Principal(mode="system", tenant_id=system_tenant.tenant_id, employee_id=None, email=email)

        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        tenant = self.registry.resolve_by_email_domain(domain)
        if tenant is None:
            raise _forbidden("Email domain is not registered for any tenant.")

        tenant_store = self.router.for_tenant(tenant.tenant_id).store
        employee_id = tenant_store.get_identity(email)
        if employee_id is None:
            raise _forbidden("No employee identity is registered for this email.")

        return Principal(mode="human", tenant_id=tenant.tenant_id, employee_id=employee_id, email=email)


GOOGLE_OIDC_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
AUTONOMOUS_SWEEP_CLOCK_SKEW_SECONDS = 30


def verify_autonomous_sweep_token(
    request: Request,
    audience: str,
    invoker_email: str,
    cert_cache: "_CachingCertsRequest",
) -> None:
    """Verify the OIDC ID token on `POST /internal/autonomous-sweep` (autonomous-agent
    design v4 §2). `cert_cache` must be a `_CachingCertsRequest` instance dedicated to
    this route (not shared with `IapResolver`'s IAP-certs cache -- separate cache,
    separate certs endpoint). Uses `google.oauth2.id_token.verify_token`'s default
    certs_url (Google's general OIDC certs, not IAP's).

    Conditions (all required): Authorization: Bearer <id_token> present / signature
    and aud verify via google-auth / iss in {accounts.google.com,
    https://accounts.google.com} / email == invoker_email / email_verified is True.
    No token -> 401. Any verification failure (bad signature, aud/iss/email mismatch,
    expired, unverified email) -> 403. Never inspects API keys or query params.

    round-5 ledger E3: every 401/403 detail is one of two fixed generic strings
    ("authentication required" / "invalid token") — the specific failure reason
    (bad signature, which claim mismatched, google-auth's own exception text)
    is never put in the response body, only logged server-side, so the caller
    can never use the response to distinguish *why* a token was rejected.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise _unauthorized("authentication required")
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise _unauthorized("authentication required")

    try:
        from google.oauth2 import id_token as google_id_token
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "google-auth is required for /internal/autonomous-sweep (see scripts/requirements.txt)"
        ) from exc

    try:
        payload = google_id_token.verify_token(
            token,
            request=cert_cache,
            audience=audience,
            clock_skew_in_seconds=AUTONOMOUS_SWEEP_CLOCK_SKEW_SECONDS,
        )
    except Exception as exc:
        logger.warning("autonomous-sweep OIDC token verification failed: %s", exc)
        raise _forbidden("invalid token") from exc

    if payload.get("iss") not in GOOGLE_OIDC_ISSUERS:
        logger.warning("autonomous-sweep OIDC token has an unexpected issuer: %r", payload.get("iss"))
        raise _forbidden("invalid token")

    email = str(payload.get("email") or "").strip().lower()
    if email != invoker_email.strip().lower():
        logger.warning("autonomous-sweep OIDC token email does not match the configured invoker identity")
        raise _forbidden("invalid token")

    if payload.get("email_verified") is not True:
        logger.warning("autonomous-sweep OIDC token email is not verified")
        raise _forbidden("invalid token")


def validate_iap_audience_format(audience: str) -> None:
    """Startup-time format check, called only when AUTH_MODE=iap (§16.1)."""
    if not IAP_AUDIENCE_PATTERN.match(audience or ""):
        raise RuntimeError(
            "IAP_AUDIENCE must match '/projects/<PROJECT_NUMBER>/locations/<REGION>/services/<SERVICE>' "
            f"when AUTH_MODE=iap (got: {audience!r})"
        )


def build_principal_resolver(
    registry: TenantRegistry,
    router: ContextRouter,
    auth_mode: str | None = None,
) -> PrincipalResolver:
    """Build the configured PrincipalResolver from AUTH_MODE / IAP_AUDIENCE env vars.

    The tenant ledger (registry) is the single source of truth for API keys,
    email domains and system_accounts in both modes (§16.2); there is no
    per-call tenant_id parameter to keep in sync with it.
    """
    mode = auth_mode or os.environ.get("AUTH_MODE", "demo_key")
    if mode == "iap":
        audience = os.environ.get("IAP_AUDIENCE", "")
        validate_iap_audience_format(audience)
        return IapResolver(registry=registry, router=router, audience=audience)
    return DemoKeyResolver(registry=registry)
