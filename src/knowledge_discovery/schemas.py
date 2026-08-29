"""Schema registry and payload validation for knowledge discovery.

Follows design.md §3:
- JSON Schema-style validation for all operational payload types.
- Strict payload type registry and rejection of unregistered types.
- Fail-closed audit display whitelist (C-21).
"""

from __future__ import annotations

from typing import Any

from knowledge_discovery.models import Message


class SchemaRegistry:
    """Registry for message payload schemas and audit display policies."""

    # All registered payload types in the system
    REGISTERED_TYPES: frozenset[str] = frozenset(
        {
            "query",
            "connect_ask",
            "connect_ask_private",
            "no_connection",
            "consent_reply",
            "match_proposal",
            "decline_with_reason",
            "reject_unregistered_type",
            "reject_unsupported_intent",
            "stagnation_detected",
            "preview_search",
            "profile_diff_proposed",
            "sweep_run",
            "policy_limited",
        }
    )

    # Whitelist of payload types allowed for unmasked display when audit_payload is None.
    # Note: connect_ask_private, stagnation_detected, preview_search, and profile_diff_proposed
    # are intentionally excluded so they are fail-closed masked in audit view (§14.6, C-21).
    # sweep_run and policy_limited ARE whitelisted (their payload is counts/enum-only by
    # construction, autonomous-agent design §6) but get_audit_view() still projects them
    # to their exact allowed key set as a second, independent line of defense (C-16/C-21).
    AUDIT_WHITELIST: frozenset[str] = frozenset(
        {
            "query",
            "connect_ask",
            "no_connection",
            "consent_reply",
            "match_proposal",
            "decline_with_reason",
            "reject_unregistered_type",
            "reject_unsupported_intent",
            "sweep_run",
            "policy_limited",
        }
    )

    # Exact allowed-key sets for the 2 autonomous-agent intents (autonomous-agent
    # design §6, Z-5). validate_payload() rejects any payload with extra/missing
    # keys; get_audit_view() re-projects to these keys as defense-in-depth.
    SWEEP_RUN_KEYS: frozenset[str] = frozenset(
        {
            "origin",
            "run_key",
            "date",
            "tasks_evaluated",
            "cards_created",
            "cards_promoted",
            "cards_resolved",
            "needs_detected",
            "candidates_explored",
            "policy_held",
            "schema_version",
        }
    )
    SWEEP_RUN_NUMERIC_KEYS: frozenset[str] = frozenset(
        {
            "tasks_evaluated",
            "cards_created",
            "cards_promoted",
            "cards_resolved",
            "needs_detected",
            "candidates_explored",
            "policy_held",
            "schema_version",
        }
    )
    POLICY_LIMITED_KEYS: frozenset[str] = frozenset({"stage", "run_key", "task_count"})
    POLICY_LIMITED_STAGES: frozenset[str] = frozenset({"search", "ask", "prepare"})

    @classmethod
    def is_registered_type(cls, payload_type: str) -> bool:
        """Check whether a payload_type is recognized by the schema registry."""
        return payload_type in cls.REGISTERED_TYPES

    @classmethod
    def validate_payload(
        cls, payload_type: str, payload: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Validate payload structure against registered schemas.

        Returns:
            (True, None) if valid.
            (False, error_message) if invalid or unregistered.
        """
        if not isinstance(payload, dict):
            return False, f"Payload must be a dictionary, got {type(payload).__name__}"

        if not cls.is_registered_type(payload_type):
            return False, f"Unregistered payload_type: '{payload_type}'"

        if payload_type == "query":
            if not payload.get("question_text") or not isinstance(payload["question_text"], str):
                return False, "query payload requires non-empty string 'question_text'"
            if not payload.get("requester_id") or not isinstance(payload["requester_id"], str):
                return False, "query payload requires non-empty string 'requester_id'"
            return True, None

        elif payload_type in ("connect_ask", "connect_ask_private"):
            if "reason_text" not in payload or not isinstance(payload["reason_text"], str):
                return False, f"{payload_type} requires string 'reason_text'"
            if "cited_item_keys" not in payload or not isinstance(payload["cited_item_keys"], list):
                return False, f"{payload_type} requires list 'cited_item_keys'"
            if "score" not in payload or not isinstance(payload["score"], (int, float)):
                return False, f"{payload_type} requires numeric 'score'"
            return True, None

        elif payload_type == "no_connection":
            if "reason_text" not in payload or not isinstance(payload["reason_text"], str):
                return False, "no_connection requires string 'reason_text'"
            if "cited_item_keys" not in payload or not isinstance(payload["cited_item_keys"], list):
                return False, "no_connection requires list 'cited_item_keys'"
            if "score" in payload and payload["score"] is not None and not isinstance(payload["score"], (int, float)):
                return False, "no_connection score must be numeric or None"
            return True, None

        elif payload_type == "consent_reply":
            decision = payload.get("decision")
            if decision not in ("granted", "declined"):
                return False, "consent_reply requires 'decision' to be 'granted' or 'declined'"
            if not payload.get("ask_audit_id") or not isinstance(payload["ask_audit_id"], str):
                return False, "consent_reply requires non-empty string 'ask_audit_id'"
            return True, None

        elif payload_type == "match_proposal":
            # C-19: reason_text must NOT be included in match_proposal
            if "reason_text" in payload:
                return False, "match_proposal MUST NOT contain 'reason_text' (C-19 privacy requirement)"
            if not isinstance(payload.get("meeting_duration"), int) or payload["meeting_duration"] <= 0:
                return False, "match_proposal requires positive integer 'meeting_duration'"
            if not payload.get("proposed_by") or not isinstance(payload["proposed_by"], str):
                return False, "match_proposal requires string 'proposed_by'"
            participants = payload.get("participants")
            if not isinstance(participants, list) or len(participants) < 2:
                return False, "match_proposal requires list 'participants' with at least 2 entries"
            return True, None

        elif payload_type == "decline_with_reason":
            if "reason_text" not in payload or not isinstance(payload["reason_text"], str):
                return False, "decline_with_reason requires string 'reason_text'"
            if "attachment" in payload and payload["attachment"] is not None:
                att = payload["attachment"]
                if not isinstance(att, dict):
                    return False, "attachment must be a dictionary or None"
                if att.get("type") not in ("link", "text", "doc"):
                    return False, "attachment type must be 'link', 'text', or 'doc'"
                if "content" not in att or not isinstance(att["content"], str):
                    return False, "attachment requires string 'content'"
            return True, None

        elif payload_type == "reject_unregistered_type":
            if not payload.get("raw_payload_type") or not isinstance(payload["raw_payload_type"], str):
                return False, "reject_unregistered_type requires 'raw_payload_type'"
            if not payload.get("reason") or not isinstance(payload["reason"], str):
                return False, "reject_unregistered_type requires 'reason'"
            return True, None

        elif payload_type == "reject_unsupported_intent":
            if not payload.get("target_agent_id") or not isinstance(payload["target_agent_id"], str):
                return False, "reject_unsupported_intent requires 'target_agent_id'"
            if not payload.get("attempted_intent") or not isinstance(payload["attempted_intent"], str):
                return False, "reject_unsupported_intent requires 'attempted_intent'"
            if not isinstance(payload.get("supported_intents"), list):
                return False, "reject_unsupported_intent requires list 'supported_intents'"
            return True, None

        elif payload_type == "stagnation_detected":
            if not payload.get("task_id") or not isinstance(payload["task_id"], str):
                return False, "stagnation_detected requires non-empty string 'task_id'"
            if "score" not in payload or not isinstance(payload["score"], (int, float)):
                return False, "stagnation_detected requires numeric 'score'"
            return True, None

        elif payload_type == "preview_search":
            if not payload.get("task_id") or not isinstance(payload["task_id"], str):
                return False, "preview_search requires non-empty string 'task_id'"
            if "candidates" not in payload or not isinstance(payload["candidates"], list):
                return False, "preview_search requires list 'candidates'"
            return True, None

        elif payload_type == "profile_diff_proposed":
            if not payload.get("mail_id") or not isinstance(payload["mail_id"], str):
                return False, "profile_diff_proposed requires non-empty string 'mail_id'"
            if not payload.get("item_key") or not isinstance(payload["item_key"], str):
                return False, "profile_diff_proposed requires non-empty string 'item_key'"
            return True, None

        elif payload_type == "sweep_run":
            # Fail-closed whitelist: exact key match (no unknown keys, none missing).
            if set(payload.keys()) != cls.SWEEP_RUN_KEYS:
                return False, "sweep_run requires exactly the counts-only key set (no unknown/missing keys)"
            if not payload.get("origin") or not isinstance(payload["origin"], str):
                return False, "sweep_run requires non-empty string 'origin'"
            if not payload.get("run_key") or not isinstance(payload["run_key"], str):
                return False, "sweep_run requires non-empty string 'run_key'"
            if not payload.get("date") or not isinstance(payload["date"], str):
                return False, "sweep_run requires non-empty string 'date'"
            for key in cls.SWEEP_RUN_NUMERIC_KEYS:
                value = payload[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return False, f"sweep_run requires non-negative int '{key}'"
            if payload["schema_version"] != 1:
                return False, "sweep_run requires 'schema_version' == 1"
            return True, None

        elif payload_type == "policy_limited":
            # Fail-closed whitelist: exact key match, stage enum, no free-text note (Z-5).
            if set(payload.keys()) != cls.POLICY_LIMITED_KEYS:
                return False, "policy_limited requires exactly {stage, run_key, task_count} (no unknown/missing keys)"
            if payload.get("stage") not in cls.POLICY_LIMITED_STAGES:
                return False, "policy_limited requires 'stage' in {'search', 'ask', 'prepare'}"
            if not payload.get("run_key") or not isinstance(payload["run_key"], str):
                return False, "policy_limited requires non-empty string 'run_key'"
            task_count = payload.get("task_count")
            if isinstance(task_count, bool) or not isinstance(task_count, int) or task_count < 1:
                return False, "policy_limited requires positive int 'task_count'"
            return True, None

        return True, None

    # Fact-line notes for fail-closed masked rows (content never shown). UI language is English.
    MASKED_NOTES: dict[str, str] = {
        "stagnation_detected": "Secretary: stagnation detected (content hidden)",
        "preview_search": "Secretary: preview search run, nothing delivered (content hidden)",
        "profile_diff_proposed": "Secretary: profile addition proposed to owner (content hidden)",
    }

    @classmethod
    def get_audit_view(cls, message: Message) -> dict[str, Any]:
        """Compute the fail-closed audit view payload for dashboard presentation (C-21).

        Rules:
        1. If audit_payload is present, display audit_payload (masked content).
        2. If audit_payload is None and payload_type is in AUDIT_WHITELIST, display payload.
        3. Otherwise (unregistered type, omitted audit_payload, or not in whitelist),
           fall back to masked view (fail-closed).
        """
        if message.audit_payload is not None:
            return dict(message.audit_payload)

        if message.payload_type == "sweep_run":
            return {k: v for k, v in message.payload.items() if k in cls.SWEEP_RUN_KEYS}

        if message.payload_type == "policy_limited":
            return {k: v for k, v in message.payload.items() if k in cls.POLICY_LIMITED_KEYS}

        if message.payload_type in cls.AUDIT_WHITELIST:
            return dict(message.payload)

        # Fail-closed fallback: never display unverified/unwhitelisted payload in plain text.
        # Secretary intents (design §14.6) get an explicit English fact-line; anything
        # else gets the generic masked note. Either way the content stays hidden.
        return {
            "masked": True,
            "note": cls.MASKED_NOTES.get(message.payload_type, "Not displayable (masked by default)"),
        }
