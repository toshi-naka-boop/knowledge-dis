"""Tenant registry and per-tenant context routing (design.md §16.2, FR27, Part B).

The isolation boundary is the Firestore *database*, not the process: every
request is confined to the `TenantContext` of `principal.tenant_id`, and
there is no code path that queries across tenants (no `X-KD-Tenant` header,
no cross-tenant sweep, no shared API key). A leaked tenant key exposes that
tenant only -- other tenants are untouched (design §16.2 "分離の主張は正確に").

- `TenantConfig`: one row of the tenant ledger (`env TENANTS_JSON`, or the
  single-tenant default below when unset).
- `TenantRegistry`: parses/validates the ledger at startup (duplicate
  tenant_id / database / email_domain / api_key all fail startup) and
  answers "which tenant does this credential belong to".
- `TenantContext` / `ContextRouter`: `store` (and the services built on it)
  are built once per tenant_id, lazily, and cached for the life of the
  process. A ledger edit only takes effect on the next process restart --
  there is no cache invalidation (documented tradeoff, not a bug).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_TENANT_ID = "meridian"
DEFAULT_DATABASE = "(default)"
DEFAULT_EMAIL_DOMAINS: tuple[str, ...] = ("meridian-care.example",)
DEFAULT_API_KEY_ENV = "DEMO_API_KEY"
DEFAULT_API_KEY_FALLBACK = "demo-key-2026"


def _normalize_strings(values: list[str]) -> tuple[str, ...]:
    """Lowercase + strip email_domains / system_accounts before they become
    dict keys (round-14 V-14/S-13): matches `IapResolver.resolve()`, which
    already lowercases the verified JWT email before looking it up here, so
    a hand-authored `TENANTS_JSON` entry can't silently evade the startup
    duplicate-domain/account check or the runtime lookup by case alone."""
    return tuple(v.strip().lower() for v in values)


@dataclass(frozen=True)
class TenantConfig:
    """One row of the tenant ledger."""

    tenant_id: str
    database: str
    email_domains: tuple[str, ...]
    api_key: str
    system_accounts: tuple[str, ...] = ()


class TenantRegistry:
    """Startup-validated tenant ledger (`env TENANTS_JSON`, design §16.2)."""

    def __init__(self, tenants: list[TenantConfig]) -> None:
        if not tenants:
            raise RuntimeError("TenantRegistry requires at least one tenant.")
        self._by_id: dict[str, TenantConfig] = {}
        self._by_api_key: dict[str, TenantConfig] = {}
        self._by_domain: dict[str, TenantConfig] = {}
        self._by_system_account: dict[str, TenantConfig] = {}
        seen_databases: dict[str, str] = {}
        for tenant in tenants:
            if tenant.tenant_id in self._by_id:
                raise RuntimeError(f"Duplicate tenant_id in tenant ledger: {tenant.tenant_id!r}")
            if tenant.database in seen_databases:
                raise RuntimeError(
                    f"Duplicate database in tenant ledger: {tenant.database!r} "
                    f"(tenants {seen_databases[tenant.database]!r} and {tenant.tenant_id!r})"
                )
            if not tenant.api_key:
                raise RuntimeError(f"Tenant {tenant.tenant_id!r} has no api_key configured.")
            if tenant.api_key in self._by_api_key:
                raise RuntimeError(f"Duplicate api_key in tenant ledger (tenant {tenant.tenant_id!r}).")
            if not tenant.email_domains:
                raise RuntimeError(f"Tenant {tenant.tenant_id!r} has no email_domains configured.")
            for domain in tenant.email_domains:
                if domain in self._by_domain:
                    raise RuntimeError(f"Duplicate email_domain in tenant ledger: {domain!r}")
            for account in tenant.system_accounts:
                if account in self._by_system_account:
                    raise RuntimeError(f"Duplicate system_account in tenant ledger: {account!r}")

            seen_databases[tenant.database] = tenant.tenant_id
            self._by_id[tenant.tenant_id] = tenant
            self._by_api_key[tenant.api_key] = tenant
            for domain in tenant.email_domains:
                self._by_domain[domain] = tenant
            for account in tenant.system_accounts:
                self._by_system_account[account] = tenant

        self.tenants: list[TenantConfig] = list(tenants)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "TenantRegistry":
        """Build the registry from `env TENANTS_JSON`, or the single-tenant
        default (`meridian` / `(default)` / `meridian-care.example` / `DEMO_API_KEY`)
        when it is unset -- current behavior is exactly this one-tenant case."""
        env = env if env is not None else os.environ
        raw = env.get("TENANTS_JSON")
        if not raw:
            return cls.single(
                tenant_id=DEFAULT_TENANT_ID,
                database=DEFAULT_DATABASE,
                email_domains=list(DEFAULT_EMAIL_DOMAINS),
                api_key=env.get(DEFAULT_API_KEY_ENV, DEFAULT_API_KEY_FALLBACK),
            )
        entries = json.loads(raw)
        return cls([cls._config_from_entry(entry, env) for entry in entries])

    @staticmethod
    def _config_from_entry(entry: dict[str, Any], env: dict[str, str]) -> TenantConfig:
        tenant_id = entry["tenant_id"]
        api_key_env = entry.get("api_key_env")
        if not api_key_env:
            # Plain-text `api_key` in the ledger is not accepted (round-14
            # E-13): keys live in env vars / Secret Manager, never in
            # TENANTS_JSON itself.
            raise RuntimeError(f"Tenant {tenant_id!r} must set 'api_key_env'.")
        api_key = env.get(api_key_env, "")
        return TenantConfig(
            tenant_id=tenant_id,
            database=entry.get("database", DEFAULT_DATABASE),
            email_domains=_normalize_strings(entry.get("email_domains", [])),
            api_key=api_key,
            system_accounts=_normalize_strings(entry.get("system_accounts", [])),
        )

    @classmethod
    def single(
        cls,
        tenant_id: str = DEFAULT_TENANT_ID,
        database: str = DEFAULT_DATABASE,
        email_domains: list[str] | None = None,
        api_key: str = DEFAULT_API_KEY_FALLBACK,
        system_accounts: list[str] | None = None,
    ) -> "TenantRegistry":
        """Build a one-tenant registry. Used by `server.create_app`'s
        test-friendly signature (explicit `store=`/`api_key=`) to stay a drop-in
        single-tenant setup without requiring a TENANTS_JSON ledger."""
        return cls(
            [
                TenantConfig(
                    tenant_id=tenant_id,
                    database=database,
                    email_domains=_normalize_strings(list(email_domains or DEFAULT_EMAIL_DOMAINS)),
                    api_key=api_key,
                    system_accounts=_normalize_strings(list(system_accounts or ())),
                )
            ]
        )

    def resolve_by_api_key(self, key: str) -> TenantConfig | None:
        return self._by_api_key.get(key)

    def resolve_by_email_domain(self, domain: str) -> TenantConfig | None:
        return self._by_domain.get(domain)

    def resolve_by_system_account(self, email: str) -> TenantConfig | None:
        return self._by_system_account.get(email)

    def get(self, tenant_id: str) -> TenantConfig | None:
        return self._by_id.get(tenant_id)


@dataclass
class TenantContext:
    """The per-tenant object graph (design §16.2): one Store (bound to the
    tenant's database) plus the services built on top of it. `matching` may be
    the same shared instance across tenants (embedder/inferencer are
    stateless); `store`/`service`/`secretary` must not be shared."""

    tenant: TenantConfig
    store: Any
    service: Any = None
    secretary: Any = None
    matching: Any = None
    static_counts: dict[str, int] = field(default_factory=dict)


class ContextRouter:
    """`for_tenant(tenant_id) -> TenantContext`, built lazily and cached for
    the life of the process (design §16.2: "遅延生成・プロセス内キャッシュ
    （台帳変更は再起動で反映）"). There is deliberately no cross-tenant
    lookup and no cache-busting entry point."""

    def __init__(
        self,
        registry: TenantRegistry,
        context_factory: Callable[[TenantConfig], TenantContext],
    ) -> None:
        self.registry = registry
        self._context_factory = context_factory
        self._cache: dict[str, TenantContext] = {}
        self._lock = threading.Lock()

    def for_tenant(self, tenant_id: str) -> TenantContext:
        cached = self._cache.get(tenant_id)
        if cached is not None:
            return cached
        tenant = self.registry.get(tenant_id)
        if tenant is None:
            raise KeyError(f"Unknown tenant_id: {tenant_id!r}")
        with self._lock:
            cached = self._cache.get(tenant_id)
            if cached is not None:
                return cached
            context = self._context_factory(tenant)
            self._cache[tenant_id] = context
            return context
