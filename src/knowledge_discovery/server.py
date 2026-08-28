"""FastAPI server for knowledge-discovery (Milestone 2).

Follows design.md §3 - §7, §16.1, §16.2:
- Every route resolves a Principal (demo/human/system, see auth.py) and is
  gated by the §16.1 permission table. AUTH_MODE=demo_key (default) checks
  X-API-Key/api_key against the tenant ledger; AUTH_MODE=iap verifies the
  IAP-signed assertion instead.
- Every route then looks up `ContextRouter.for_tenant(principal.tenant_id)`
  (§16.2) and operates only on that TenantContext's store/service/secretary --
  there is no code path that reaches another tenant's data.
- POST /api/query: Submit question, run 2-track matching, dispatch asks.
- GET /api/requester/{requester_id}/status: Requester projection (strict candidate ID & consent isolation rules).
- GET /api/candidate/{agent_id}/asks: Candidate inbox for received asks.
- POST /api/candidate/{agent_id}/consent: Submit consent decision (granted / declined + reason & attachment).
- GET /api/audit/messages: Audit view with fail-closed masked payloads and funnel metrics.
- GET /attachments/{id}: Static document delivery (C-24).
- Serves web UI at /requester, /candidate, /audit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from knowledge_discovery.auth import Principal, PrincipalResolver, build_principal_resolver
from knowledge_discovery.matching import DeterministicEmbedder, FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import Attachment
from knowledge_discovery.secretary import SecretaryService
from knowledge_discovery.connectors import build_connector_from_env
from knowledge_discovery.service import (
    ConsentConflictError,
    ConsentForbiddenError,
    KnowledgeDiscoveryService,
)
from knowledge_discovery.store import InMemoryStore, Store
from knowledge_discovery.tenancy import (
    DEFAULT_DATABASE,
    DEFAULT_EMAIL_DOMAINS,
    DEFAULT_TENANT_ID,
    ContextRouter,
    TenantConfig,
    TenantContext,
    TenantRegistry,
)

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
    from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
    from pydantic import BaseModel, Field
except ImportError as exc:
    # This module is meaningless without fastapi/pydantic. Fail with a clean
    # ImportError so package __init__ and test guards can catch and skip it.
    raise ImportError(
        "knowledge_discovery.server requires fastapi and pydantic (see scripts/requirements.txt)"
    ) from exc


# Default API key for demo environment
DEFAULT_DEMO_API_KEY = "demo-key-2026"


# -----------------------------------------------------------------------------
# Static Attachment Store (C-24: Static delivery within Cloud Run)
# -----------------------------------------------------------------------------

SAMPLE_ATTACHMENTS: dict[str, str] = {
    "doc_clinic_relocation_guide": """# Meridian Care Partners: Medical Clinic Relocation & Zoning Guide (2026)

## 1. Zoning & Permitting
- Medical office buildings require specific municipal zoning classifications (e.g., C-2 or O-M).
- Ensure required parking ratios (typically 5:1000 sq ft for ambulatory medical vs 3:1000 for standard retail).

## 2. Infrastructure & Plumbing
- Clinical suites require dedicated backflow prevention for clinical sinks and sterilization units.
- Radiation shielding requirements apply for on-site diagnostic X-ray facilities.

## 3. ADA & Accessibility
- Clear door widths minimum 36 inches for stretcher / gurney access.
- Level threshold entrances and dedicated patient drop-off lanes.
""",
    "doc_practice_transition_handbook": """# Meridian Care Partners: Practice Transition & Succession Planning Overview

## 1. Valuation Preparation
- Normalize 3-year EBITDA with adjustments for owner compensation and discretionary expenses.
- Patient chart transfer protocols under HIPAA and state medical board rules.

## 2. Deal Structures
- Equity asset purchase vs stock sale.
- Typical post-sale retention: 6 to 18 months for founding physician handover continuity.

## 3. Staffing & Credentialing Continuity
- Maintain payer credentialing timelines (90-120 days lead time for new owner entity).
""",
    "doc_factory_automation_guide_2026": """# Manufacturing Systems & Quality Automation Guidelines

## 1. MES Integration Standards
- SCADA and MES integration protocols for high-throughput production lines.
- Traceability and lot tracking compliance guidelines.
""",
}


# -----------------------------------------------------------------------------
# Pydantic Schemas for Requests
# -----------------------------------------------------------------------------

class QueryRequest(BaseModel):  # type: ignore[misc]
    requester_id: str = Field(..., description="ID of the employee submitting the inquiry")
    question_text: str = Field(..., description="Natural language question text")


class AttachmentModel(BaseModel):  # type: ignore[misc]
    type: str = Field(..., description="Attachment type: 'link', 'text', or 'doc'")
    content: str = Field(..., description="URL, text snippet, or doc identifier")


class ConsentRequest(BaseModel):  # type: ignore[misc]
    ask_audit_id: str = Field(..., description="Audit ID of the original connect_ask message")
    decision: str = Field(..., description="Consent decision: 'granted' or 'declined'")
    reason_text: str = Field(default="", description="Optional note or decline reason")
    attachment: AttachmentModel | None = Field(default=None, description="Optional attachment")


class ConfirmCardRequest(BaseModel):  # type: ignore[misc]
    card_id: str = Field(..., description="ID of the stagnation card to confirm")
    edited_question: str = Field(..., description="Inquiry question (AI draft or user edited)")


class ProfileDiffReviewRequest(BaseModel):  # type: ignore[misc]
    action: str = Field(..., description="'apply' | 'edit_apply' | 'private_apply' | 'dismiss'")
    edited_body: str | None = Field(default=None, description="Optional edited body text for edit_apply")



# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def create_app_from_env() -> Any:
    """Production factory: wire FirestoreStore / Gemini adapters from environment.

    Env vars:
    - TENANTS_JSON           -> tenant ledger (default: 1 tenant, see tenancy.py)
    - USE_FIRESTORE=1        -> FirestoreStore per tenant, database=<tenant.database> (§16.2)
    - GEMINI_API_KEY set     -> GeminiEmbedder + GeminiConnectionInferencer (shared across tenants)
    - DEMO_API_KEY           -> default tenant's API key when TENANTS_JSON is unset

    Run with: uvicorn 'knowledge_discovery.server:create_app_from_env' --factory
    """
    registry = TenantRegistry.from_env()
    use_firestore = os.environ.get("USE_FIRESTORE") == "1"
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")

    def store_factory(tenant: TenantConfig) -> Store:
        if use_firestore:
            from knowledge_discovery.firestore_store import FirestoreStore

            return FirestoreStore(project=project, database=tenant.database)
        new_store = InMemoryStore()
        from scripts.generate_seeds import populate_store

        populate_store(new_store, dry_run=False)
        return new_store

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    matching_engine: MatchingEngine | None = None
    llm_client: Any | None = None
    if os.environ.get("GEMINI_API_KEY") or use_vertex:
        from knowledge_discovery.gemini_adapters import (
            GeminiConnectionInferencer,
            GeminiEmbedder,
            _build_genai_client,
        )

        # Gemini/embedding clients are stateless (§16.2), so one instance is
        # shared across every tenant's MatchingEngine; only the Store differs.
        matching_engine = MatchingEngine(
            embedder=GeminiEmbedder(),
            inferencer=GeminiConnectionInferencer(),
            vector_floor=float(os.environ.get("VECTOR_FLOOR", "0.20")),
            connection_threshold=float(os.environ.get("CONNECTION_THRESHOLD", "0.50")),
            max_dispatch_k=3,
            funnel_limit=20,
        )
        # Secretary question drafts and profile-diff extraction use the same
        # Gemini client as connection inference (§14.4/§14.5, V-8/E-7/S-6).
        llm_client = _build_genai_client(os.environ.get("GEMINI_API_KEY", ""))

    return create_app(
        registry=registry,
        store_factory=store_factory,
        matching_engine=matching_engine,
        llm_client=llm_client,
    )


def create_app(
    store: Store | None = None,
    service: KnowledgeDiscoveryService | None = None,
    api_key: str | None = None,
    llm_client: Any | None = None,
    principal_resolver: PrincipalResolver | None = None,
    registry: TenantRegistry | None = None,
    store_factory: Callable[[TenantConfig], Store] | None = None,
    matching_engine: MatchingEngine | None = None,
) -> Any:
    """Create and configure the FastAPI application.

    Tenancy (design §16.2): every tenant gets its own Store + KnowledgeDiscoveryService
    + SecretaryService, built lazily and cached per-tenant by a ContextRouter; routes
    below resolve `principal.tenant_id` and operate only on that tenant's context.

    Test-friendly single-tenant compatibility: when `registry` is omitted, a
    single-tenant registry (tenant_id="meridian") is built from `api_key`/
    `DEMO_API_KEY`, and an explicitly passed `store=`/`service=` is used as
    that one tenant's context (current single-tenant behavior, unchanged).
    For a real multi-tenant ledger, pass `registry=` (e.g. TenantRegistry.from_env())
    and `store_factory=` (how to build a fresh Store for a given tenant); `store=`/
    `service=` are ignored in that case.
    """
    if FastAPI is None:
        raise RuntimeError("fastapi is not installed. Please install requirements.txt.")

    expected_api_key = api_key or os.environ.get("DEMO_API_KEY", DEFAULT_DEMO_API_KEY)

    preset_store: Store | None = None
    preset_service: KnowledgeDiscoveryService | None = None
    if registry is None:
        registry = TenantRegistry.single(
            tenant_id=DEFAULT_TENANT_ID,
            database=DEFAULT_DATABASE,
            email_domains=list(DEFAULT_EMAIL_DOMAINS),
            api_key=expected_api_key,
        )
        preset_store = store
        preset_service = service

    def _default_store_factory(_tenant: TenantConfig) -> Store:
        new_store = InMemoryStore()
        from scripts.generate_seeds import populate_store

        populate_store(new_store, dry_run=False)
        return new_store

    effective_store_factory = store_factory or _default_store_factory

    # Gemini/embedding clients are stateless, so one MatchingEngine instance
    # is shared across every tenant's context (§16.2); only the Store differs
    # per tenant.
    shared_matching_engine = matching_engine
    if shared_matching_engine is None and preset_service is not None:
        shared_matching_engine = preset_service.matching_engine
    if shared_matching_engine is None:
        shared_matching_engine = MatchingEngine(
            embedder=DeterministicEmbedder(),
            inferencer=FakeConnectionInferencer(),
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )

    def _build_tenant_context(tenant: TenantConfig) -> TenantContext:
        if preset_store is not None and tenant.tenant_id == DEFAULT_TENANT_ID:
            tenant_store = preset_store
        else:
            tenant_store = effective_store_factory(tenant)

        if preset_service is not None and tenant.tenant_id == DEFAULT_TENANT_ID:
            tenant_service = preset_service
        else:
            tenant_service = KnowledgeDiscoveryService(store=tenant_store, matching_engine=shared_matching_engine)

        tenant_secretary = SecretaryService(
            store=tenant_store,
            kd_service=tenant_service,
            matching_engine=shared_matching_engine,
            llm_client=llm_client,
            # §16.3 data-source seam: SOURCE_CONNECTOR=seed|google_workspace (default seed).
            # One connector instance per tenant context; SeedConnector is a no-op.
            connector=build_connector_from_env(),
        )
        return TenantContext(
            tenant=tenant,
            store=tenant_store,
            service=tenant_service,
            secretary=tenant_secretary,
            matching=shared_matching_engine,
            static_counts={},
        )

    router = ContextRouter(registry, context_factory=_build_tenant_context)

    app = FastAPI(
        title="Knowledge Discovery API",
        description="Tacit knowledge discovery and synergy matching engine (Milestone 2)",
        version="0.2.0",
        # No unauthenticated surface beyond the UI shells (S-2): API schema
        # endpoints are disabled rather than left open on the public demo URL
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    web_dir = Path(__file__).parent / "web"

    # Principal resolution dependency (design §16.1/§16.2): AUTH_MODE=demo_key
    # (default) reproduces the old X-API-Key/api_key check, now resolving the
    # tenant from whichever tenant's key matched; AUTH_MODE=iap verifies the
    # IAP-signed assertion instead. Every route below depends on this single
    # function and then applies the §16.1 permission table.
    resolver = principal_resolver or build_principal_resolver(registry, router)

    def get_principal(request: Request) -> Principal:
        return resolver.resolve(request)

    def get_context(principal: Principal = Depends(get_principal)) -> TenantContext:
        """Resolve the caller's own TenantContext (§16.2). This is the only
        place a route obtains a store/service/secretary -- there is no
        function that returns another tenant's context."""
        try:
            return router.for_tenant(principal.tenant_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unknown tenant.")

    # -------------------------------------------------------------------------
    # §16.1 permission-table helpers (default-deny: every route below states
    # explicitly what each of demo/human/system may do; nothing is implicit)
    # -------------------------------------------------------------------------

    def _deny_system(principal: Principal) -> None:
        if principal.mode == "system":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted for system principals.")

    def _deny_human(principal: Principal) -> None:
        if principal.mode == "human":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted for human principals.")

    def _require_self_employee(principal: Principal, employee_id: str) -> None:
        """human must act only as themselves; demo/system are unaffected here."""
        if principal.mode == "human" and principal.employee_id != employee_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this employee_id.")

    def _require_self_agent(principal: Principal, agent_id: str, tenant_store: Store) -> None:
        """human may only act through the agent bound to their own employee_id."""
        if principal.mode != "human":
            return
        agent = tenant_store.get_agent(agent_id)
        if agent is None or agent.employee_id != principal.employee_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this agent_id.")

    def _require_card_owner(principal: Principal, card_id: str, tenant_store: Store) -> None:
        """human may only act on secretary cards they own; missing cards fall
        through to the service layer's own 404 handling."""
        if principal.mode != "human":
            return
        card = tenant_store.get_card(card_id)
        if card is None:
            return
        if card.owner_employee_id != principal.employee_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this card.")

    # -------------------------------------------------------------------------
    # Web UI Routes (HTML Static Pages)
    # -------------------------------------------------------------------------

    @app.get("/", response_class=RedirectResponse, include_in_schema=False)
    def index() -> str:
        return "/requester"

    @app.get("/requester", response_class=HTMLResponse, include_in_schema=False)
    def requester_ui() -> HTMLResponse:
        html_file = web_dir / "requester.html"
        if html_file.exists():
            return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Requester UI</h1><p>HTML file not found.</p>")

    @app.get("/candidate", response_class=HTMLResponse, include_in_schema=False)
    def candidate_ui() -> HTMLResponse:
        html_file = web_dir / "candidate.html"
        if html_file.exists():
            return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Candidate UI</h1><p>HTML file not found.</p>")

    @app.get("/audit", response_class=HTMLResponse, include_in_schema=False)
    def audit_ui() -> HTMLResponse:
        html_file = web_dir / "audit.html"
        if html_file.exists():
            return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Audit UI</h1><p>HTML file not found.</p>")

    # -------------------------------------------------------------------------
    # Static Document Delivery (C-24)
    # -------------------------------------------------------------------------

    @app.get("/attachments/{doc_id}", response_class=PlainTextResponse)
    def get_attachment(doc_id: str) -> PlainTextResponse:
        """Serve static document attachment by ID."""
        doc = SAMPLE_ATTACHMENTS.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Document attachment '{doc_id}' not found")
        return PlainTextResponse(content=doc, media_type="text/markdown")

    # -------------------------------------------------------------------------
    # API Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/me")
    def get_me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
        """Return the resolved caller identity (design §16.1). UI shells use
        this to hide the demo persona switcher once mode != 'demo'."""
        return {
            "mode": principal.mode,
            "tenant_id": principal.tenant_id,
            "employee_id": principal.employee_id,
        }

    @app.get("/api/agents")
    def list_registered_agents(
        principal: Principal = Depends(get_principal), ctx: TenantContext = Depends(get_context)
    ) -> dict[str, Any]:
        """List active registered agents for UI dropdowns."""
        agents = ctx.store.list_agents(active_only=True)
        return {
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "employee_id": a.employee_id,
                    "display_name": a.display_name,
                    "endpoint": a.endpoint,
                }
                for a in agents
            ]
        }

    @app.post("/api/query")
    def submit_query(
        req: QueryRequest,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Submit a question, run 2-track 2-stage matching, and dispatch asks."""
        _deny_system(principal)
        _require_self_employee(principal, req.requester_id)
        result = ctx.service.submit_query(
            requester_id=req.requester_id,
            question_text=req.question_text,
        )

        return {
            "query_id": result.query_message.audit_id,
            "requester_id": req.requester_id,
            "question_text": req.question_text,
            "dispatched_count": len(result.dispatched_asks),
            "funnel_count": len(result.funnel_candidates),
            "dropped_count": len(result.dropped_candidates),
            "funnel_candidates": [
                {
                    "employee_id": c.employee_id,
                    "name": c.name,
                    "role": c.role,
                    "similarity": round(c.similarity, 4),
                }
                for c in result.funnel_candidates
            ],
            "dispatched_asks": [m.audit_id for m in result.dispatched_asks],
        }

    @app.get("/api/requester/{requester_id}/status")
    def get_requester_status(
        requester_id: str,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Return requester-facing status projection (design.md §3, §6.4).

        Strict Privacy Rules:
        - When pending: Candidate employee_id and identity are NOT exposed.
        - When resolved (matched or declined): Respondent ID/name and reason/attachment are exposed.
        - NEVER exposes connect_ask vs connect_ask_private distinction or internal consent events.
        """
        _deny_system(principal)
        _require_self_employee(principal, requester_id)
        raw_statuses = ctx.service.get_requester_status(requester_id=requester_id)

        formatted_statuses: list[dict[str, Any]] = []
        for idx, s in enumerate(raw_statuses, start=1):
            if s.state == "pending":
                formatted_statuses.append({
                    "status_id": f"status_{idx}",
                    "state": "pending",
                    "display_state": "Waiting for response",
                })
            elif s.state == "matched":
                formatted_statuses.append({
                    "status_id": f"status_{idx}",
                    "state": "matched",
                    "display_state": "Connected (15-min Meeting Proposed)",
                    "respondent_id": s.candidate_id,
                    "respondent_name": s.candidate_name,
                    "meeting_duration": s.meeting_duration or 15,
                })
            elif s.state == "declined":
                formatted_statuses.append({
                    "status_id": f"status_{idx}",
                    "state": "declined",
                    "display_state": "Unavailable this time",
                    "respondent_id": s.candidate_id,
                    "respondent_name": s.candidate_name,
                    "decline_reason": s.decline_reason,
                    "attachment": s.attachment,
                })

        return {
            "requester_id": requester_id,
            "statuses": formatted_statuses,
        }

    @app.get("/api/candidate/{agent_id}/asks")
    def get_candidate_asks(
        agent_id: str,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Retrieve synergy requests dispatched to the specified candidate agent."""
        _deny_system(principal)
        _require_self_agent(principal, agent_id, ctx.store)
        all_messages = ctx.store.list_messages()
        candidate_asks = [
            m for m in all_messages
            if m.to_entity == agent_id and m.intent in ("connect_ask", "connect_ask_private")
        ]

        return {
            "agent_id": agent_id,
            "asks": [
                {
                    "ask_audit_id": m.audit_id,
                    "intent": m.intent,
                    "is_private": (m.intent == "connect_ask_private"),
                    "private_notice": (
                        "🔒 This request relates to one of your private profile items."
                        if m.intent == "connect_ask_private"
                        else ""
                    ),
                    "requester_id": m.payload.get("requester_id", ""),
                    "question_summary": m.payload.get("question_summary", ""),
                    "reason_text": m.payload.get("reason_text", ""),
                    "score": m.payload.get("score", 0.0),
                    "consent_state": m.consent_state,
                    "timestamp": m.timestamp,
                }
                for m in candidate_asks
            ],
        }

    @app.post("/api/candidate/{agent_id}/consent")
    def submit_candidate_consent(
        agent_id: str,
        req: ConsentRequest,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Submit candidate consent reply (granted or declined with optional attachment)."""
        _deny_system(principal)
        _require_self_agent(principal, agent_id, ctx.store)
        att = None
        if req.attachment is not None:
            att = Attachment(type=req.attachment.type, content=req.attachment.content)

        try:
            consent_res = ctx.service.respond_consent(
                candidate_entity_id=agent_id,
                ask_audit_id=req.ask_audit_id,
                decision=req.decision,
                reason_text=req.reason_text,
                attachment=att,
            )
            return {
                "status": "ok",
                "decision": req.decision,
                "reply_audit_id": consent_res.reply_message.audit_id,
                "outcome_intent": consent_res.outcome_message.intent,
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ConsentForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ConsentConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    # Counting profiles per poll would pull all 400 documents (with 3072-dim
    # embeddings, ~10MB) from Firestore every 3 seconds (E-4). The counts are
    # static for the demo, so compute them once on first request -- per
    # tenant (ctx.static_counts), since tenants have independent profile/agent
    # counts (§16.2).
    @app.get("/api/audit/messages")
    def get_audit_messages(
        principal: Principal = Depends(get_principal), ctx: TenantContext = Depends(get_context)
    ) -> dict[str, Any]:
        """Retrieve audit dashboard records with fail-closed masked payloads and funnel stats."""
        records = ctx.service.get_audit_dashboard_records()
        if not ctx.static_counts:
            ctx.static_counts["profiles"] = len(ctx.store.list_profiles())
            ctx.static_counts["agents"] = len(ctx.store.list_agents(active_only=True))

        dispatched_count = sum(1 for r in records if r["intent"] in ("connect_ask", "connect_ask_private"))
        dropped_count = sum(1 for r in records if r["intent"] == "no_connection")

        return {
            "funnel_stats": {
                "total_profiles": ctx.static_counts["profiles"],
                "funnel_limit": 20,
                "registered_agents_count": ctx.static_counts["agents"],
                "dispatched_count": dispatched_count,
                "dropped_count": dropped_count,
            },
            "records": records,
        }

    # -------------------------------------------------------------------------
    # Secretary Endpoints (§14)
    # -------------------------------------------------------------------------
    # Note: "today" is controlled exclusively by the DEMO_TODAY env var (§14.7).
    # There is no query/body override here (E-9): tests exercise date behavior
    # by calling SecretaryService directly with demo_today=, or by setting
    # DEMO_TODAY in the environment.

    @app.post("/api/secretary/sweep")
    def run_secretary_sweep(
        principal: Principal = Depends(get_principal), ctx: TenantContext = Depends(get_context)
    ) -> dict[str, Any]:
        """Execute proactive secretary sweep across the caller's own tenant only
        (§16.2: no all-tenant sweep exists)."""
        _deny_human(principal)
        return ctx.secretary.run_sweep()

    @app.get("/api/secretary/digest")
    def get_morning_digest(
        employee_id: str = Query(..., description="Employee ID"),
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Retrieve dynamic morning digest for employee (§14.2, §14.8)."""
        _require_self_employee(principal, employee_id)
        return ctx.secretary.get_morning_digest(employee_id=employee_id)

    @app.post("/api/secretary/confirm")
    def confirm_stagnation_card(
        req: ConfirmCardRequest,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Confirm a stagnation card and dispatch discovery query (§14.4)."""
        _deny_system(principal)
        _require_card_owner(principal, req.card_id, ctx.store)
        try:
            return ctx.secretary.confirm_stagnation_card(
                card_id=req.card_id,
                edited_question=req.edited_question,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    @app.post("/api/secretary/profile-diff/{card_id}/review")
    def review_profile_diff(
        card_id: str,
        req: ProfileDiffReviewRequest,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Review profile diff proposal with 4 choices (§14.5)."""
        _deny_system(principal)
        _require_card_owner(principal, card_id, ctx.store)
        try:
            return ctx.secretary.review_profile_diff(
                card_id=card_id,
                action=req.action,
                edited_body=req.edited_body,
            )
        except LookupError as exc:
            # Owning employee has no profile: the secretary never fabricates one (V-10).
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    @app.post("/api/secretary/cards/{card_id}/dismiss")
    def dismiss_secretary_card(
        card_id: str,
        principal: Principal = Depends(get_principal),
        ctx: TenantContext = Depends(get_context),
    ) -> dict[str, Any]:
        """Dismiss a secretary card (§14.2)."""
        _deny_system(principal)
        _require_card_owner(principal, card_id, ctx.store)
        try:
            return ctx.secretary.dismiss_card(card_id=card_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.post("/api/probe/unregistered-intent")
    def probe_unregistered_intent(
        principal: Principal = Depends(get_principal), ctx: TenantContext = Depends(get_context)
    ) -> dict[str, Any]:
        """Demo probe (E-2): push an unregistered payload_type through the
        transmission layer so the schema-registry rejection (red row) can be
        shown live on the deployed system during the demo's governance act."""
        _deny_human(principal)
        msg = ctx.service.transmission.send(
            from_entity="demo_probe",
            to_entity="agent_marcus_delgado" if ctx.store.get_agent("agent_marcus_delgado") else "system",
            intent="exfiltrate_profile",
            payload_type="exfiltrate_profile",
            payload={"note": "demo probe: unregistered payload type"},
        )
        return {"rejected": msg.rejected, "intent": msg.intent, "audit_id": msg.audit_id}

    return app


# Run with the env-driven factory (module-level eager creation would seed an
# unused in-memory store at import time and slow down cold starts):
#   uvicorn 'knowledge_discovery.server:create_app_from_env' --factory
