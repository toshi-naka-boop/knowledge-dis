"""Principal resolution and authentication (design.md §16.1, FR25-26, Part A).

This module is the single differentiation point between the demo API-key
world and IAP-authenticated production. All server.py routes depend on
`PrincipalResolver.resolve(request) -> Principal`; Part B (tenancy) is meant
to slot in underneath this same interface without touching server.py's
routing logic.

- `Principal`: the resolved caller identity (mode/tenant_id/employee_id/email).
- `DemoKeyResolver`: AUTH_MODE=demo_key. Reproduces the pre-existing
  X-API-Key / api_key check byte-for-byte; tenant_id is a fixed constant
  (Part B will replace this with a tenant ledger lookup).
- `IapResolver`: AUTH_MODE=iap. Verifies the `X-Goog-IAP-JWT-Assertion`
  header with `google.oauth2.id_token.verify_token`, then resolves the
  verified email to a tenant-scoped Principal via system_accounts /
  IAP_ALLOWED_DOMAINS / Store.get_identity.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from fastapi import HTTPException, Request, status
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "knowledge_discovery.auth requires fastapi (see scripts/requirements.txt)"
    ) from exc

from knowledge_discovery.store import Store

# Cloud Run IAP audience format: /projects/<PROJECT_NUMBER>/locations/<REGION>/services/<SERVICE>
IAP_AUDIENCE_PATTERN = re.compile(r"^/projects/\d+/locations/[a-z0-9-]+/services/[a-z0-9-]+$")

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CLOCK_SKEW_SECONDS = 30

# Default cert cache freshness window (Cache-Control max-age overrides this
# when present on the certs response). "取得失敗時は期限内の旧鍵で継続、期限切れは401":
# once a fetch fails, the previous certs keep being served for one more
# window of the same length before the cache is considered truly expired.
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


class DemoKeyResolver(PrincipalResolver):
    """AUTH_MODE=demo_key: single shared key, single tenant (current behavior).

    Every caller who presents the right key gets mode="demo" with no
    employee_id -- each API's own request payload supplies the acting
    identity (unchanged from the pre-Part-A behavior).
    """

    def __init__(self, expected_api_key: str, tenant_id: str = "meridian") -> None:
        self.expected_api_key = expected_api_key
        self.tenant_id = tenant_id

    def resolve(self, request: Request) -> Principal:
        provided_key = request.headers.get("x-api-key") or request.query_params.get("api_key")
        if not provided_key or provided_key != self.expected_api_key:
            raise _unauthorized(
                "Invalid or missing API key. Provide header 'X-API-Key' or query parameter 'api_key'."
            )
        return Principal(mode="demo", tenant_id=self.tenant_id, employee_id=None, email=None)


class _CachingCertsRequest:
    """A `google.auth.transport.Request`-compatible callable that caches the
    IAP public key response in-process.

    While the cached response is within its freshness window, no network
    call is made. Once the window elapses, a fresh fetch is attempted; if
    that fetch fails, the stale response is still served for one further
    window (grace period) before being treated as truly expired -- at which
    point callers see a normal exception, which IapResolver turns into a
    fail-closed 401 (not 503).
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
                # Fetch failed: serve the stale response through one extra
                # grace window (2x ttl total since the last good fetch)
                # before giving up and letting the caller fail-closed.
                if self._cached_response is not None and (now - self._fetched_at) < (self._ttl * 2):
                    return self._cached_response
                raise


class IapResolver(PrincipalResolver):
    """AUTH_MODE=iap: verify the IAP-signed JWT and resolve email -> Principal.

    Part A ships with a single tenant (tenant_id fixed), matching the
    demo-mode default; Part B will replace this with a per-domain tenant
    lookup without changing this class's external contract.
    """

    def __init__(
        self,
        store: Store,
        audience: str,
        allowed_domains: list[str],
        system_accounts: list[str],
        tenant_id: str = "meridian",
        cert_cache: _CachingCertsRequest | None = None,
    ) -> None:
        self.store = store
        self.audience = audience
        self.allowed_domains = {d.strip().lower() for d in allowed_domains if d.strip()}
        self.system_accounts = {s.strip().lower() for s in system_accounts if s.strip()}
        self.tenant_id = tenant_id
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

        if email in self.system_accounts:
            return Principal(mode="system", tenant_id=self.tenant_id, employee_id=None, email=email)

        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if domain not in self.allowed_domains:
            raise _forbidden("Email domain is not registered for any tenant.")

        employee_id = self.store.get_identity(email)
        if employee_id is None:
            raise _forbidden("No employee identity is registered for this email.")

        return Principal(mode="human", tenant_id=self.tenant_id, employee_id=employee_id, email=email)


def validate_iap_audience_format(audience: str) -> None:
    """Startup-time format check, called only when AUTH_MODE=iap (§16.1)."""
    if not IAP_AUDIENCE_PATTERN.match(audience or ""):
        raise RuntimeError(
            "IAP_AUDIENCE must match '/projects/<PROJECT_NUMBER>/locations/<REGION>/services/<SERVICE>' "
            f"when AUTH_MODE=iap (got: {audience!r})"
        )


def build_principal_resolver(
    store: Store,
    expected_api_key: str,
    auth_mode: str | None = None,
    tenant_id: str = "meridian",
) -> PrincipalResolver:
    """Build the configured PrincipalResolver from AUTH_MODE / IAP_* env vars."""
    mode = auth_mode or os.environ.get("AUTH_MODE", "demo_key")
    if mode == "iap":
        audience = os.environ.get("IAP_AUDIENCE", "")
        validate_iap_audience_format(audience)
        allowed_domains = [d for d in os.environ.get("IAP_ALLOWED_DOMAINS", "").split(",") if d.strip()]
        system_accounts = [s for s in os.environ.get("IAP_SYSTEM_ACCOUNTS", "").split(",") if s.strip()]
        return IapResolver(
            store=store,
            audience=audience,
            allowed_domains=allowed_domains,
            system_accounts=system_accounts,
            tenant_id=tenant_id,
        )
    return DemoKeyResolver(expected_api_key=expected_api_key, tenant_id=tenant_id)
