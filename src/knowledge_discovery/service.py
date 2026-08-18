"""High-level knowledge discovery coordinator service (Milestone 1).

Follows design.md §1 - §7:
- Orchestrates query submission, 2-track 2-stage matching, and asks dispatch.
- Handles consent replies (granted -> match_proposal without reason_text; declined -> decline_with_reason).
- Provides requester-facing status view (never distinguishing private from public asks).
- Provides audit dashboard view with fail-closed payload masking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from knowledge_discovery.matching import (
    DroppedCandidate,
    FunnelCandidate,
    MatchingEngine,
)
from knowledge_discovery.models import (
    Attachment,
    Message,
    Profile,
    RequesterCandidateStatus,
)
from knowledge_discovery.schemas import SchemaRegistry
from knowledge_discovery.store import Store
from knowledge_discovery.transmission import TransmissionLayer


@dataclass
class QuerySubmissionResult:
    """Output of a submitted knowledge discovery query."""

    query_message: Message
    funnel_candidates: list[FunnelCandidate]
    dispatched_asks: list[Message]
    dropped_candidates: list[DroppedCandidate]


@dataclass
class ConsentResult:
    """Output of processing a candidate consent reply."""

    reply_message: Message
    outcome_message: Message  # match_proposal or decline_with_reason


class KnowledgeDiscoveryService:
    """Core service coordinating matching, transmission, consent, and audit."""

    def __init__(
        self,
        store: Store,
        transmission: TransmissionLayer | None = None,
        matching_engine: MatchingEngine | None = None,
    ) -> None:
        self.store = store
        self.transmission = transmission or TransmissionLayer(store)
        self.matching_engine = matching_engine or MatchingEngine()
        # No in-process requester/ask maps (V-2): Cloud Run scales to zero and
        # restarts between requests, so every link lives in message payloads
        # (requester_id / query_id / ask_audit_id) and is derived from the store.

    def submit_query(self, requester_id: str, question_text: str) -> QuerySubmissionResult:
        """Submit a question, run 2-track matching, and dispatch asks/no_connection records."""
        # 1. Record query message
        query_msg = self.transmission.send(
            from_entity=requester_id,
            to_entity="system",
            intent="query",
            payload_type="query",
            payload={"question_text": question_text, "requester_id": requester_id},
        )

        # 2. Retrieve registered agents and all employee profiles
        agents = self.store.list_agents(active_only=True)
        profiles = self.store.list_profiles()

        # 3. Run exploratory matching (2 tracks × 2 stages)
        matching_res = self.matching_engine.run_matching(question_text, agents, profiles)

        # 4. Record dropped candidates as no_connection in audit log
        for dropped in matching_res.dropped_candidates:
            self.transmission.send(
                from_entity="system",
                to_entity=dropped.agent.agent_id,
                intent="no_connection",
                payload_type="no_connection",
                payload={
                    "reason_text": dropped.reason_text,
                    "cited_item_keys": list(dropped.cited_item_keys),
                    "score": dropped.score,
                },
                candidate_profile=dropped.profile,
            )

        # 5. Dispatch asks to qualified candidates (up to max k=3)
        dispatched_asks: list[Message] = []
        for qual in matching_res.qualified_candidates:
            ask_msg = self.transmission.send(
                from_entity="system",
                to_entity=qual.agent.agent_id,
                intent="connect_ask",
                payload_type="connect_ask",
                payload={
                    "question_summary": question_text,
                    "reason_text": qual.reason_text,
                    "cited_item_keys": list(qual.cited_item_keys),
                    "score": qual.score,
                    # Durable links (V-2): survive instance restarts
                    "requester_id": requester_id,
                    "query_id": query_msg.audit_id,
                },
                consent_state="pending",
                candidate_profile=qual.profile,
            )
            dispatched_asks.append(ask_msg)

        return QuerySubmissionResult(
            query_message=query_msg,
            funnel_candidates=matching_res.funnel_candidates,
            dispatched_asks=dispatched_asks,
            dropped_candidates=matching_res.dropped_candidates,
        )

    def respond_consent(
        self,
        candidate_entity_id: str,
        ask_audit_id: str,
        decision: str,  # 'granted' | 'declined'
        reason_text: str = "",
        attachment: Attachment | dict[str, Any] | None = None,
    ) -> ConsentResult:
        """Process a candidate's consent decision (granted or declined)."""
        original_ask = self.store.get_message(ask_audit_id)
        if original_ask is None:
            raise ValueError(f"Ask message with ID '{ask_audit_id}' not found")

        # Update original ask message consent state
        original_ask.consent_state = decision
        self.store.save_message(original_ask)

        # 1. Record consent_reply message
        reply_msg = self.transmission.send(
            from_entity=candidate_entity_id,
            to_entity="system",
            intent="consent_reply",
            payload_type="consent_reply",
            payload={"decision": decision, "ask_audit_id": ask_audit_id},
            consent_state=decision,
        )

        # Requester is derived from the durable link in the ask payload (V-2)
        requester_id = str(original_ask.payload.get("requester_id") or "requester")

        # 2. Outcome handling based on decision
        if decision == "granted":
            # Match established -> match_proposal delivered to BOTH parties
            # (design §6 / V-5: one message per recipient, no reason_text)
            candidate_employee_id = candidate_entity_id
            agent = self.store.get_agent(candidate_entity_id)
            if agent is not None:
                candidate_employee_id = agent.employee_id

            proposal_payload = {
                "meeting_duration": 15,
                "proposed_by": "system",
                "participants": [requester_id, candidate_employee_id],
                "ask_audit_id": ask_audit_id,
            }
            outcome_msg = self.transmission.send(
                from_entity="system",
                to_entity=requester_id,
                intent="match_proposal",
                payload_type="match_proposal",
                payload=dict(proposal_payload),
                consent_state="granted",
            )
            self.transmission.send(
                from_entity="system",
                to_entity=candidate_employee_id,
                intent="match_proposal",
                payload_type="match_proposal",
                payload=dict(proposal_payload),
                consent_state="granted",
            )
        else:
            # Declined -> return decline_with_reason to requester with reason and optional attachment
            att_dict = None
            if attachment is not None:
                att_dict = attachment.to_dict() if isinstance(attachment, Attachment) else dict(attachment)

            outcome_msg = self.transmission.send(
                from_entity=candidate_entity_id,
                to_entity=requester_id,
                intent="decline_with_reason",
                payload_type="decline_with_reason",
                payload={
                    "reason_text": reason_text,
                    "attachment": att_dict,
                    "ask_audit_id": ask_audit_id,
                },
                consent_state="declined",
            )

        return ConsentResult(reply_message=reply_msg, outcome_message=outcome_msg)

    def get_requester_status(self, requester_id: str, query_audit_id: str | None = None) -> list[RequesterCandidateStatus]:
        """Return candidate status list from the requester viewpoint (§6.4).

        States:
        - pending: '返答待ち'
        - matched: 'つながりました（MTG提案あり）'
        - declined: '今回は難しいそうです（理由+添付表示）'

        Strict requirement: Does NOT distinguish connect_ask vs connect_ask_private.
        """
        # Store-derived, stateless projection (V-2). Scoped to ONE query so
        # candidates from past questions do not mix into the view (V-4):
        # the given query_audit_id, or the requester's latest query by default.
        messages = self.store.list_messages()

        if query_audit_id is None:
            own_queries = [
                m for m in messages
                if m.intent == "query" and m.payload.get("requester_id") == requester_id
            ]
            if not own_queries:
                return []
            query_audit_id = max(own_queries, key=lambda m: m.timestamp).audit_id

        asks = [
            m for m in messages
            if m.intent in ("connect_ask", "connect_ask_private")
            and m.payload.get("query_id") == query_audit_id
        ]

        statuses: list[RequesterCandidateStatus] = []
        for ask_msg in asks:
            # Candidate name lookup
            agent = self.store.get_agent(ask_msg.to_entity)
            cand_name = agent.display_name if agent else ask_msg.to_entity
            cand_id = agent.employee_id if agent else ask_msg.to_entity

            if ask_msg.consent_state == "pending":
                statuses.append(
                    RequesterCandidateStatus(
                        candidate_id=cand_id,
                        candidate_name=cand_name,
                        state="pending",
                    )
                )
            elif ask_msg.consent_state == "granted":
                statuses.append(
                    RequesterCandidateStatus(
                        candidate_id=cand_id,
                        candidate_name=cand_name,
                        state="matched",
                        meeting_duration=15,
                    )
                )
            elif ask_msg.consent_state == "declined":
                # Join the decline to THIS ask via its durable ask_audit_id (V-4:
                # matching by sender would surface a past decline's reason here)
                decline_msg = next(
                    (
                        m for m in messages
                        if m.intent == "decline_with_reason"
                        and m.payload.get("ask_audit_id") == ask_msg.audit_id
                    ),
                    None,
                )

                reason = decline_msg.payload.get("reason_text", "") if decline_msg else ""
                att = decline_msg.payload.get("attachment") if decline_msg else None

                statuses.append(
                    RequesterCandidateStatus(
                        candidate_id=cand_id,
                        candidate_name=cand_name,
                        state="declined",
                        decline_reason=reason,
                        attachment=att,
                    )
                )

        return statuses

    def get_audit_dashboard_records(self) -> list[dict[str, Any]]:
        """Retrieve all messages formatted for audit dashboard display (§7)."""
        messages = self.store.list_messages()
        records: list[dict[str, Any]] = []

        for msg in messages:
            display_payload = SchemaRegistry.get_audit_view(msg)
            records.append(
                {
                    "audit_id": msg.audit_id,
                    "from": msg.from_entity,
                    "to": msg.to_entity,
                    "intent": msg.intent,
                    "payload_type": msg.payload_type,
                    "display_payload": display_payload,
                    "consent_state": msg.consent_state,
                    "timestamp": msg.timestamp,
                    "rejected": msg.rejected,
                    "is_red_alert": msg.rejected,
                    "is_no_connection": msg.intent == "no_connection",
                }
            )

        return records
