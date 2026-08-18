"""FastAPI server for knowledge-discovery (Milestone 2).

Follows design.md §3 - §7:
- Protected by DEMO_API_KEY (X-API-Key header or query parameter).
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
from typing import Any

from knowledge_discovery.matching import DeterministicEmbedder, FakeConnectionInferencer, MatchingEngine
from knowledge_discovery.models import Attachment
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import InMemoryStore, Store

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, Security, status
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


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------

def create_app_from_env() -> Any:
    """Production factory: wire FirestoreStore / Gemini adapters from environment.

    Env vars:
    - USE_FIRESTORE=1        -> FirestoreStore (project from GOOGLE_CLOUD_PROJECT)
    - GEMINI_API_KEY set     -> GeminiEmbedder + GeminiConnectionInferencer
    - DEMO_API_KEY           -> API key protection (create_app default)

    Run with: uvicorn 'knowledge_discovery.server:create_app_from_env' --factory
    """
    store: Store | None = None
    if os.environ.get("USE_FIRESTORE") == "1":
        from knowledge_discovery.firestore_store import FirestoreStore

        store = FirestoreStore(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
    service: KnowledgeDiscoveryService | None = None
    if os.environ.get("GEMINI_API_KEY") or use_vertex:
        from knowledge_discovery.gemini_adapters import (
            GeminiConnectionInferencer,
            GeminiEmbedder,
        )

        if store is None:
            store = InMemoryStore()
            from scripts.generate_seeds import populate_store

            populate_store(store, dry_run=False)
        matching_engine = MatchingEngine(
            embedder=GeminiEmbedder(),
            inferencer=GeminiConnectionInferencer(),
            vector_floor=float(os.environ.get("VECTOR_FLOOR", "0.20")),
            connection_threshold=float(os.environ.get("CONNECTION_THRESHOLD", "0.50")),
            max_dispatch_k=3,
            funnel_limit=20,
        )
        service = KnowledgeDiscoveryService(store=store, matching_engine=matching_engine)

    return create_app(store=store, service=service)


def create_app(
    store: Store | None = None,
    service: KnowledgeDiscoveryService | None = None,
    api_key: str | None = None,
) -> Any:
    """Create and configure the FastAPI application."""
    if FastAPI is None:
        raise RuntimeError("fastapi is not installed. Please install requirements.txt.")

    expected_api_key = api_key or os.environ.get("DEMO_API_KEY", DEFAULT_DEMO_API_KEY)

    # Initialize store and service if not provided
    if store is None:
        store = InMemoryStore()
        # Seed the in-memory store with default personas and profiles
        from scripts.generate_seeds import populate_store
        populate_store(store, dry_run=False)

    if service is None:
        matching_engine = MatchingEngine(
            embedder=DeterministicEmbedder(),
            inferencer=FakeConnectionInferencer(),
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
            funnel_limit=20,
        )
        service = KnowledgeDiscoveryService(store=store, matching_engine=matching_engine)

    app = FastAPI(
        title="Knowledge Discovery API",
        description="Tacit knowledge discovery and synergy matching engine (Milestone 2)",
        version="0.2.0",
    )

    web_dir = Path(__file__).parent / "web"

    # API Key verification dependency
    def verify_api_key(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        api_key_query: str | None = Query(default=None, alias="api_key"),
    ) -> str:
        provided_key = x_api_key or api_key_query
        if not provided_key or provided_key != expected_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key. Provide header 'X-API-Key' or query parameter 'api_key'.",
            )
        return provided_key

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

    @app.get("/api/agents", dependencies=[Depends(verify_api_key)])
    def list_registered_agents() -> dict[str, Any]:
        """List active registered agents for UI dropdowns."""
        agents = store.list_agents(active_only=True)
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

    @app.post("/api/query", dependencies=[Depends(verify_api_key)])
    def submit_query(req: QueryRequest) -> dict[str, Any]:
        """Submit a question, run 2-track 2-stage matching, and dispatch asks."""
        result = service.submit_query(
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

    @app.get("/api/requester/{requester_id}/status", dependencies=[Depends(verify_api_key)])
    def get_requester_status(requester_id: str) -> dict[str, Any]:
        """Return requester-facing status projection (design.md §3, §6.4).

        Strict Privacy Rules:
        - When pending: Candidate employee_id and identity are NOT exposed.
        - When resolved (matched or declined): Respondent ID/name and reason/attachment are exposed.
        - NEVER exposes connect_ask vs connect_ask_private distinction or internal consent events.
        """
        raw_statuses = service.get_requester_status(requester_id=requester_id)

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

    @app.get("/api/candidate/{agent_id}/asks", dependencies=[Depends(verify_api_key)])
    def get_candidate_asks(agent_id: str) -> dict[str, Any]:
        """Retrieve synergy requests dispatched to the specified candidate agent."""
        all_messages = store.list_messages()
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
                    "question_summary": m.payload.get("question_summary", ""),
                    "reason_text": m.payload.get("reason_text", ""),
                    "score": m.payload.get("score", 0.0),
                    "consent_state": m.consent_state,
                    "timestamp": m.timestamp,
                }
                for m in candidate_asks
            ],
        }

    @app.post("/api/candidate/{agent_id}/consent", dependencies=[Depends(verify_api_key)])
    def submit_candidate_consent(agent_id: str, req: ConsentRequest) -> dict[str, Any]:
        """Submit candidate consent reply (granted or declined with optional attachment)."""
        att = None
        if req.attachment is not None:
            att = Attachment(type=req.attachment.type, content=req.attachment.content)

        try:
            consent_res = service.respond_consent(
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

    @app.get("/api/audit/messages", dependencies=[Depends(verify_api_key)])
    def get_audit_messages() -> dict[str, Any]:
        """Retrieve audit dashboard records with fail-closed masked payloads and funnel stats."""
        records = service.get_audit_dashboard_records()
        profiles = store.list_profiles()
        agents = store.list_agents(active_only=True)

        dispatched_count = sum(1 for r in records if r["intent"] in ("connect_ask", "connect_ask_private"))
        dropped_count = sum(1 for r in records if r["intent"] == "no_connection")

        return {
            "funnel_stats": {
                "total_profiles": len(profiles),
                "funnel_limit": 20,
                "registered_agents_count": len(agents),
                "dispatched_count": dispatched_count,
                "dropped_count": dropped_count,
            },
            "records": records,
        }

    return app


# Default app instance for running via `uvicorn knowledge_discovery.server:app`
app = None
if FastAPI is not None:
    app = create_app()
