"""Transmission layer and message dispatcher for knowledge discovery.

Follows design.md §3:
- Schema validation and reject_unregistered_type.
- Destination supported_intents validation and reject_unsupported_intent (C-25).
- Single message-wide private mask rule and audit_payload generation (C-18, C-21).
- Audit trail recording for all dispatched and rejected messages.
"""

from __future__ import annotations

import uuid
from typing import Any

from knowledge_discovery.models import Message, Profile
from knowledge_discovery.schemas import SchemaRegistry
from knowledge_discovery.store import Store


class TransmissionError(Exception):
    """Exception raised when a transmission fails or is rejected."""

    def __init__(self, message: Message) -> None:
        super().__init__(f"Transmission rejected: intent={message.intent}, reason={message.payload.get('reason')}")
        self.message = message


class TransmissionLayer:
    """Dispatches messages, validates schemas/intents, applies private masks, and logs audits."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _reason_leaks_private(profile: Any, reason_text: str) -> bool:
        """Return True if reason_text contains fragments of any private item body.

        Defense beyond LLM self-report (V-1/S-1): even if the model cites only
        public keys, quoting private content in the reason forces the mask.
        Comparison is whitespace-normalized, case-insensitive, over sliding
        fragments so partial quotes are caught.
        """
        if not reason_text:
            return False
        norm_reason = "".join(reason_text.lower().split())
        for item in profile.items:
            if getattr(item, "visibility", "public") != "private":
                continue
            norm_body = "".join(item.body.lower().split())
            if len(norm_body) < 12:
                if norm_body and norm_body in norm_reason:
                    return True
                continue
            step = 8
            frag_len = 15
            for i in range(0, len(norm_body) - frag_len + 1, step):
                if norm_body[i : i + frag_len] in norm_reason:
                    return True
        return False

    def _resolve_profile(
        self,
        from_entity: str,
        to_entity: str,
        candidate_profile: Profile | None = None,
    ) -> Profile | None:
        """Resolve candidate profile to check item visibility."""
        if candidate_profile is not None:
            return candidate_profile

        # Try to resolve to_entity
        agent_to = self.store.get_agent(to_entity)
        if agent_to is not None:
            prof = self.store.get_profile(agent_to.employee_id)
            if prof is not None:
                return prof

        prof_to = self.store.get_profile(to_entity)
        if prof_to is not None:
            return prof_to

        # Try to resolve from_entity
        agent_from = self.store.get_agent(from_entity)
        if agent_from is not None:
            prof = self.store.get_profile(agent_from.employee_id)
            if prof is not None:
                return prof

        prof_from = self.store.get_profile(from_entity)
        if prof_from is not None:
            return prof_from

        return None

    def send(
        self,
        from_entity: str,
        to_entity: str,
        intent: str,
        payload_type: str,
        payload: dict[str, Any],
        consent_state: str = "n/a",
        audit_id: str | None = None,
        candidate_profile: Profile | None = None,
        create_only: bool = False,
    ) -> Message:
        """Process and send a message through the transmission pipeline.

        Pipeline steps:
        1. Validate payload schema against SchemaRegistry.
           If invalid -> record and return reject_unregistered_type (rejected=True).
        2. Check destination agent supported_intents if recipient is a registered agent.
           If unsupported -> record and return reject_unsupported_intent (rejected=True).
        3. Enforce private mask rule (C-18/C-21):
           If cited_item_keys contains any private item:
           - connect_ask -> connect_ask_private
           - generate audit_payload for masked audit view
        4. Save final message to store audit log.
        5. Return the dispatched Message.

        create_only (autonomous-agent design §3 Z-4): when True, the final
        message is saved via Store.save_message_if_absent() instead of the
        unconditional save_message(). A doc already present at `audit_id` is
        left untouched and its stored content is returned instead — the first
        writer wins, so a retried/duplicate send can never rewind a summary
        or forge a fresher timestamp. Intended only for deterministic,
        decision-content-free audit_ids (sweep_run / policy_limited) supplied
        by the caller; do not combine with server-generated random audit_ids.
        """
        msg_id = audit_id or f"msg_{uuid.uuid4().hex[:12]}"

        # Step 1: Schema validation
        is_valid, err_msg = SchemaRegistry.validate_payload(payload_type, payload)
        if not is_valid:
            reject_msg = Message(
                audit_id=msg_id,
                from_entity=from_entity,
                to_entity=to_entity,
                intent="reject_unregistered_type",
                payload_type="reject_unregistered_type",
                payload={
                    "raw_payload_type": payload_type,
                    "reason": err_msg or "Unregistered or invalid payload structure",
                },
                audit_payload=None,
                consent_state="n/a",
                rejected=True,
            )
            self.store.save_message(reject_msg)
            return reject_msg

        # Step 2: Destination supported_intents validation (C-25)
        dest_agent = self.store.get_agent(to_entity)
        if dest_agent is not None:
            if dest_agent.supported_intents and intent not in dest_agent.supported_intents:
                reject_msg = Message(
                    audit_id=msg_id,
                    from_entity=from_entity,
                    to_entity=to_entity,
                    intent="reject_unsupported_intent",
                    payload_type="reject_unsupported_intent",
                    payload={
                        "target_agent_id": dest_agent.agent_id,
                        "attempted_intent": intent,
                        "supported_intents": list(dest_agent.supported_intents),
                        "reason": f"Destination agent '{dest_agent.agent_id}' does not support intent '{intent}'",
                    },
                    audit_payload=None,
                    consent_state="n/a",
                    rejected=True,
                )
                self.store.save_message(reject_msg)
                return reject_msg

        # Step 3: Private Mask Rule (C-18, C-21, V-1/S-1)
        # The mask decision must not trust the LLM's self-reported cited_item_keys
        # alone: keys are validated against the actual profile, and the reason
        # text is scanned for private-body fragments. Any uncertainty (unknown
        # keys, leaked fragments) fails CLOSED into the masked/private path.
        final_intent = intent
        final_payload_type = payload_type
        audit_payload: dict[str, Any] | None = None

        cited_keys = payload.get("cited_item_keys")
        cited_list = cited_keys if isinstance(cited_keys, list) else []
        reason_text = str(payload.get("reason_text") or "")

        if cited_list or reason_text:
            profile = self._resolve_profile(from_entity, to_entity, candidate_profile)
            if profile is not None:
                known_keys = {item.key for item in profile.items}
                has_unknown_key = any(k not in known_keys for k in cited_list)
                must_mask = (
                    profile.has_any_private(cited_list)
                    or has_unknown_key
                    or self._reason_leaks_private(profile, reason_text)
                )
                if must_mask:
                    # Promote connect_ask to connect_ask_private mechanically
                    if final_intent == "connect_ask":
                        final_intent = "connect_ask_private"
                        final_payload_type = "connect_ask_private"

                    # Generate audit_payload for masked audit view
                    audit_payload = {
                        "masked": True,
                        "note": f"非公開項目に基づく{final_intent}（内容非表示）",
                        "score": payload.get("score"),
                        "cited_count": len(cited_list),
                    }

        # Step 4: Construct and store operational message
        message = Message(
            audit_id=msg_id,
            from_entity=from_entity,
            to_entity=to_entity,
            intent=final_intent,
            payload_type=final_payload_type,
            payload=payload,
            audit_payload=audit_payload,
            consent_state=consent_state,
            rejected=False,
        )
        if create_only:
            created = self.store.save_message_if_absent(message)
            if not created:
                existing = self.store.get_message(msg_id)
                return existing if existing is not None else message
            return message

        self.store.save_message(message)
        return message
