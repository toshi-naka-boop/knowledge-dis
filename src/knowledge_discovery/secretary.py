"""Proactive Secretary Layer (Milestone 3, Phase A).

Follows design.md §14:
- Deterministic stagnation score with 5 signals & 2-tier thresholds (T1/T2, FR17-18).
- Assert T1 < T2 at startup.
- Sweep task state machine: notice -> request_draft promotion, done -> resolved, dismissed non-recreation (FR16).
- Public-only preview search using embedding_public without side effects (FR19).
- Atomic confirmation CAS routing to existing query flow (FR20).
- Morning digest dynamically aggregating schedule reminders and open cards (FR22).
- Proactive profile diff proposal from mail seeds and 4-way review (FR23).
- Dedicated audit log records for stagnation, preview search, and profile diff (FR21).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
import re
from typing import Any
import uuid

from knowledge_discovery.connectors import SeedConnector, SourceConnector, apply_fetch_result
from knowledge_discovery.matching import MatchingEngine
from knowledge_discovery.models import (
    Card,
    MailSeed,
    PreviewCandidate,
    Profile,
    ProfileItem,
    Schedule,
    Task,
    default_autonomy_policy,
    utc_now_iso,
)
from knowledge_discovery.schemas import SchemaRegistry
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import Store

logger = logging.getLogger(__name__)

# Mail retention window (§16.3 Gmail part D, fixed at 14 days per design).
# Applies regardless of source (SeedConnector never produces mail_seeds, so
# this is a no-op in demo mode).
MAIL_RETENTION_DAYS: int = 14

# Sweep run claim staleness window (autonomous-agent design §3). A "running"
# claim older than this is treated as abandoned and re-claimable.
SWEEP_CLAIM_TTL_SECONDS: int = int(os.environ.get("SWEEP_CLAIM_TTL_SECONDS", "300"))

# -----------------------------------------------------------------------------
# Configuration & Weights (§14.3)
# -----------------------------------------------------------------------------

W_OVERDUE: float = float(os.environ.get("W_OVERDUE", "1.0"))
W_STALE: float = float(os.environ.get("W_STALE", "1.0"))
W_RESCHED: float = float(os.environ.get("W_RESCHED", "2.0"))
W_NEGLECT: float = float(os.environ.get("W_NEGLECT", "3.0"))
W_UNTOUCHED: float = float(os.environ.get("W_UNTOUCHED", "2.0"))

STAGNATION_T1: float = float(os.environ.get("STAGNATION_T1", "3.0"))
STAGNATION_T2: float = float(os.environ.get("STAGNATION_T2", "7.0"))
STAGNATION_CAP: int = int(os.environ.get("STAGNATION_CAP", "10"))
STAGNATION_NEGLECT_WINDOW: int = int(os.environ.get("STAGNATION_NEGLECT_WINDOW", "3"))

# Startup assertion (§14.3)
assert STAGNATION_T1 < STAGNATION_T2, f"STAGNATION_T1 ({STAGNATION_T1}) must be strictly less than STAGNATION_T2 ({STAGNATION_T2})"


# -----------------------------------------------------------------------------
# Date Helpers (§14.7)
# -----------------------------------------------------------------------------

def get_today(demo_today: str | None = None) -> date:
    """Return the base date for stagnation and schedule calculations.

    Uses DEMO_TODAY environment variable (or argument) if set, otherwise UTC date.
    """
    raw = demo_today or os.environ.get("DEMO_TODAY")
    if raw:
        try:
            # Handle ISO date (YYYY-MM-DD) or timestamp
            if "T" in raw:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            return date.fromisoformat(raw.strip())
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).date()


def _parse_date_or_timestamp(val: str | None) -> date | None:
    """Safely parse a date or ISO timestamp string into a date object."""
    if not val or not str(val).strip():
        return None
    val_str = str(val).strip()
    try:
        if "T" in val_str:
            return datetime.fromisoformat(val_str.replace("Z", "+00:00")).date()
        return date.fromisoformat(val_str)
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------------------------------
# Stagnation Score Calculation (§14.3)
# -----------------------------------------------------------------------------

def calculate_stagnation_score(
    task: Task,
    all_owner_tasks: list[Task],
    today: date | None = None,
    w_overdue: float = W_OVERDUE,
    w_stale: float = W_STALE,
    w_resched: float = W_RESCHED,
    w_neglect: float = W_NEGLECT,
    w_untouched: float = W_UNTOUCHED,
    cap: int = STAGNATION_CAP,
    neglect_window: int = STAGNATION_NEGLECT_WINDOW,
) -> tuple[float, str, dict[str, Any]]:
    """Compute rule-based stagnation score and synthesized evidence line.

    Signals:
    1. Overdue days: days past due date (capped).
    2. Stale days: days since last update (capped).
    3. Reschedule count: count of reschedules.
    4. Relative neglect: task stale >= neglect_window while another task of owner updated within neglect_window.
    5. Untouched: status == 'todo' and created_at == status_changed_at.

    Returns:
        (score, evidence_line, signals_dict)
    """
    ref_today = today or get_today()

    # 1. Overdue days
    due_d = _parse_date_or_timestamp(task.due_date)
    overdue_days = max(0, (ref_today - due_d).days) if due_d is not None else 0
    capped_overdue = min(overdue_days, cap)

    # 2. Stale days
    upd_d = _parse_date_or_timestamp(task.last_updated_at) or _parse_date_or_timestamp(task.created_at)
    stale_days = max(0, (ref_today - upd_d).days) if upd_d is not None else 0
    capped_stale = min(stale_days, cap)

    # 3. Reschedule count
    resched_count = max(0, task.reschedule_count)

    # 4. Relative neglect
    relative_neglect = 0
    if stale_days >= neglect_window:
        for other in all_owner_tasks:
            if other.task_id == task.task_id:
                continue
            other_upd_d = _parse_date_or_timestamp(other.last_updated_at) or _parse_date_or_timestamp(other.created_at)
            if other_upd_d is not None:
                days_since_other = max(0, (ref_today - other_upd_d).days)
                if days_since_other < neglect_window:
                    relative_neglect = 1
                    break

    # 5. Untouched
    untouched = 1 if (task.status == "todo" and task.status_changed_at == task.created_at) else 0

    score = (
        w_overdue * capped_overdue
        + w_stale * capped_stale
        + w_resched * resched_count
        + w_neglect * relative_neglect
        + w_untouched * untouched
    )

    # Synthesize evidence line deterministically (NO LLM, §14.3)
    evidence_parts: list[str] = []
    if resched_count > 0:
        evidence_parts.append(f"Rescheduled {resched_count} time{'s' if resched_count > 1 else ''}")
    if overdue_days > 0:
        evidence_parts.append(f"overdue by {overdue_days} day{'s' if overdue_days > 1 else ''}")
    if stale_days > 0:
        evidence_parts.append(f"no updates for {stale_days} day{'s' if stale_days > 1 else ''}")
    if relative_neglect == 1:
        evidence_parts.append("inactive while other tasks are progressing")
    if untouched == 1:
        evidence_parts.append("not yet started")

    if evidence_parts:
        evidence_line = ", ".join(evidence_parts) + "."
        evidence_line = evidence_line[0].upper() + evidence_line[1:]
    else:
        evidence_line = "No active stagnation signals."

    signals = {
        "overdue_days": overdue_days,
        "capped_overdue": capped_overdue,
        "stale_days": stale_days,
        "capped_stale": capped_stale,
        "reschedule_count": resched_count,
        "relative_neglect": relative_neglect,
        "untouched": untouched,
        "score": score,
    }

    return score, evidence_line, signals


# -----------------------------------------------------------------------------
# Autonomy State Mapping (autonomous-agent design §4)
# -----------------------------------------------------------------------------

def derive_autonomy_state(
    task: Task, card: Card | None, effective_policy: dict[str, bool]
) -> str:
    """Map a task's persisted card to its concept-level autonomy state (design §4).

    | concept state                                              | representation |
    |---|---|
    | observing                                                  | no open card |
    | stalled                                                    | open card, tier 'notice' (or None) |
    | need_detected (also candidate_found / awaiting_human_approval) | open card, tier 'request_draft' |

    'searching' is a transient in-run state (never persisted) and is never
    returned here. No new state model/migration: this is purely a read-side
    mapping over the existing card status/tier. effective_policy is accepted
    for API symmetry with the design's stated signature and future UI overlay
    use (e.g. a 'paused' badge, §8 C-17) — it does not change which of the
    three names above is returned, since the card's own status/tier already
    reflects whatever policy allowed to happen when it was written.
    """
    del effective_policy  # reserved (see docstring); not needed to pick the state name
    del task  # the mapping only needs the card; task kept for signature symmetry with §4
    if card is None or card.status != "open":
        return "observing"
    if card.tier == "request_draft":
        return "need_detected"
    return "stalled"


# -----------------------------------------------------------------------------
# Question Draft & Profile Diff Helpers
# -----------------------------------------------------------------------------

def generate_question_draft(task: Task, llm_client: Any | None = None) -> str:
    """Generate an AI inquiry draft from task title and description (§14.4)."""
    # Deterministic fallback template
    desc_snippet = f": {task.description.strip()}" if task.description.strip() else ""
    fallback = f"Seeking expertise and advice regarding {task.title}{desc_snippet}."

    if llm_client is None:
        return fallback

    try:
        model_name = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
        prompt = (
            f"You are an AI assistant helping a colleague formulate a concise discovery inquiry.\n"
            f"Task Title: {task.title}\n"
            f"Task Description: {task.description}\n"
            f"Write a single natural question (1-2 sentences in English) asking colleagues for advice or tacit knowledge to complete this task."
        )
        response = llm_client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        if hasattr(response, "text") and response.text:
            cleaned = response.text.strip().strip('"').strip("'")
            return cleaned if cleaned else fallback
    except Exception:
        pass

    return fallback


# Sentinel: LLM was configured but unreachable / returned garbage. Distinct from
# None ("no diff") so the caller can leave the mail unconsumed and retry (R-2).
EXTRACTION_FAILED = object()


def extract_profile_diff(
    mail: MailSeed,
    current_profile: Profile | None = None,
    llm_client: Any | None = None,
) -> tuple[str, str] | None:
    """Extract candidate profile item (item_key, body_draft) from unprocessed email (§14.5)."""
    if not mail.body.strip() and not mail.subject.strip():
        return None

    if llm_client is not None:
        try:
            model_name = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
            existing_text = current_profile.get_full_text() if current_profile else ""
            prompt = f"""You are analyzing a work email to see if it reveals tacit skills, current projects, or specialized background to add to the employee's professional profile.

Current Profile:
{existing_text}

Email Subject: {mail.subject}
Email Body: {mail.body}

If this email contains meaningful professional experience or project context not yet in the profile, extract a suggested profile item in JSON format:
{{
  "item_key": "current_work" | "expertise" | "background",
  "body_draft": "1-2 sentence description in English"
}}
If no meaningful knowledge is found, return null."""
            response = llm_client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            resp_text = response.text.strip() if hasattr(response, "text") and response.text else ""
            cleaned = re.sub(r"^```(?:json)?\s*", "", resp_text)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
            if cleaned == "null":
                # Explicit "no diff": LLM was reachable and deliberately found
                # nothing worth proposing. Do NOT fall through to the heuristic
                # (design §14.5-1: null must be expressible; V-8/S-6).
                return None
            if not cleaned:
                # Empty response is an API failure, not a deliberate null.
                # Leave the mail unconsumed so the next sweep retries (R-2).
                return EXTRACTION_FAILED
            data = json.loads(cleaned)
            if isinstance(data, dict) and "body_draft" in data:
                key = data.get("item_key", "current_work")
                body = data.get("body_draft", "").strip()
                if body:
                    return key, body
            return None
        except Exception:
            # LLM call/parse failed. Do NOT copy mail text as a fallback (that
            # re-opens S-6) and do NOT consume the mail — retry next sweep (R-2).
            return EXTRACTION_FAILED

    # Heuristic extraction fallback (reached only when llm_client is None,
    # e.g. a local demo without GEMINI_API_KEY — never the default path, V-8/S-6).
    subj = mail.subject.strip()
    body = mail.body.strip()
    if len(body) > 10:
        return "current_work", f"{subj}: {body}"
    return None


# -----------------------------------------------------------------------------
# Secretary Service Coordinator
# -----------------------------------------------------------------------------

class SecretaryService:
    """Orchestrates secretary background sweep, morning digest, card CAS, and diff reviews."""

    def __init__(
        self,
        store: Store,
        kd_service: KnowledgeDiscoveryService,
        matching_engine: MatchingEngine | None = None,
        llm_client: Any | None = None,
        t1: float = STAGNATION_T1,
        t2: float = STAGNATION_T2,
        connector: SourceConnector | None = None,
        sweep_claim_ttl_seconds: int = SWEEP_CLAIM_TTL_SECONDS,
    ) -> None:
        self.store = store
        self.kd_service = kd_service
        self.matching_engine = matching_engine or kd_service.matching_engine
        self.llm_client = llm_client
        self.t1 = t1
        self.t2 = t2
        self.connector = connector or SeedConnector()
        self.sweep_claim_ttl_seconds = sweep_claim_ttl_seconds
        assert self.t1 < self.t2, f"t1 ({self.t1}) must be < t2 ({self.t2})"

    # -------------------------------------------------------------------------
    # Autonomy policy helpers (autonomous-agent design §5)
    # -------------------------------------------------------------------------

    def _effective_policy(self, employee_id: str) -> tuple[dict[str, bool], str]:
        """Read an employee's autonomy policy and derive its effective values + CAS token.

        Doc-absent default (§5.4): Monitor ON / Search OFF / Ask OFF / Prepare OFF.
        The token is policy.updated_at if a doc exists, else "" — both are stable,
        comparable strings for upsert_card_gated's expected_policy_updated_at (Z-2).
        """
        policy = self.store.get_autonomy_policy(employee_id)
        eff = (policy or default_autonomy_policy(employee_id)).effective()
        token = policy.updated_at if policy is not None else ""
        return eff, token

    # All-True effective policy for origin="manual" (round-5 ledger A: manual is
    # a full-permission override, never consults the stored autonomy_policies doc).
    _MANUAL_OVERRIDE_EFFECTIVE: dict[str, bool] = {
        "monitor_stalled_work": True,
        "search_organization": True,
        "ask_candidate_agents": True,
        "prepare_introduction": True,
    }

    def _effective_policy_for(self, employee_id: str, origin: str) -> tuple[dict[str, bool], str | None]:
        """origin-aware effective policy (round-5 ledger A).

        origin="manual": full-permission override — eff is always all-True and
        the returned token is None, which is Store.upsert_card_gated's documented
        "no policy gating" contract (§3): no policy_hold is ever written, no
        policy_limited audit is ever considered, and any existing hold recorded
        under a real (non-None) policy_updated_at is treated as stale and dropped
        (a manual sweep resolves whatever was held).

        origin="scheduled": delegates to the real per-employee policy (§5.4/§5.5).
        """
        if origin == "manual":
            return dict(self._MANUAL_OVERRIDE_EFFECTIVE), None
        return self._effective_policy(employee_id)

    def _send_internal_audit(
        self,
        from_entity: str,
        to_entity: str,
        intent: str,
        payload_type: str,
        payload: dict[str, Any],
        audit_id: str,
    ) -> None:
        """Send a sweep_run/policy_limited audit with self-validation (C-16).

        Validates the payload against SchemaRegistry BEFORE calling into
        TransmissionLayer at all: an invalid payload here is a programming bug,
        not user input, so it is logged and swallowed rather than routed
        through transmission's reject_unregistered_type path (which would draw
        a red reject row into Bridge Trace for something the operator never
        did). create_only=True enforces the create-only CAS (Z-4): a retried
        send with the same deterministic audit_id is a no-op, never a rewrite.
        """
        is_valid, err_msg = SchemaRegistry.validate_payload(payload_type, payload)
        if not is_valid:
            logger.error(
                "Refusing to send invalid internal audit intent=%s audit_id=%s: %s",
                intent, audit_id, err_msg,
            )
            return
        self.kd_service.transmission.send(
            from_entity=from_entity,
            to_entity=to_entity,
            intent=intent,
            payload_type=payload_type,
            payload=payload,
            audit_id=audit_id,
            create_only=True,
        )

    def _sync_owners(
        self, registered_agents: list[Any], profiles_list: list[Profile], ref_today: date
    ) -> dict[str, int]:
        """Sync-then-detect's "sync" half (§16.3): pull each tenant owner's
        external data through `self.connector` and reconcile it via
        `apply_fetch_result` before stagnation detection runs.

        `SeedConnector` (the default) is a no-op *by construction here*: it
        is never even asked to fetch, and `apply_fetch_result` is never
        called (round-14 V-12) — a bare `isinstance` check short-circuits
        before touching Store, so switching `SOURCE_CONNECTOR` back to
        `seed` (or leaving it unset) can never mark previously-synced `gws`
        data done/deleted, and demo mode pays zero extra Store queries.

        Single-owner mode (`GWS_SELF_EMPLOYEE_ID`): the sync target is that
        one owner *only* — not a filter over agents ∪ profiles, so it is
        synced even if that owner has no agent/profile registered yet
        (round-14 V-11; this is what makes the empty-`InMemoryStore` manual
        gate in design §10 goal 28 actually fetch anything). Every other
        registered owner is counted in `sync_skipped_owners` for
        visibility, without ever being fetched. A connector/reconciliation
        failure for one owner is caught and counted in `sync_errors` so it
        never halts the sweep (§16.3 failure handling) — and never logs
        task/mail titles or bodies.
        """
        if isinstance(self.connector, SeedConnector):
            return {
                "sync_tasks": 0,
                "sync_schedules": 0,
                "sync_mails": 0,
                "sync_skipped_owners": 0,
                "sync_skipped_mails": 0,
                "sync_errors": 0,
            }

        today_str = ref_today.isoformat()
        self_only = os.environ.get("GWS_SELF_EMPLOYEE_ID", "").strip()
        registered_owner_ids = {a.employee_id for a in registered_agents} | {
            p.employee_id for p in profiles_list
        }
        if self_only:
            target_owners = {self_only}
            skipped_owner_ids = registered_owner_ids - target_owners
        else:
            target_owners = registered_owner_ids
            skipped_owner_ids = set()

        stats = {
            "sync_tasks": 0,
            "sync_schedules": 0,
            "sync_mails": 0,
            "sync_skipped_owners": len(skipped_owner_ids),
            "sync_skipped_mails": 0,
            "sync_errors": 0,
            # round-15 R-3: in single-owner mode an unregistered employee_id is
            # still synced (goal 28 runs against an empty store), but a typo must
            # be visible rather than silently creating a ghost owner.
            "sync_self_owner_registered": (self_only in registered_owner_ids) if self_only else None,
        }
        # round-15 R-4: a misconfigured connector fails identically for every
        # owner; record it once instead of once per owner (400x on a full seed).
        if getattr(self.connector, "misconfigured", False):
            stats["sync_errors"] = 1
            return stats
        for owner_id in sorted(target_owners):
            try:
                fetch_result = self.connector.fetch(owner_id, today_str)
                summary = apply_fetch_result(self.store, owner_id, fetch_result, today_str)
            except Exception:
                stats["sync_errors"] += 1
                continue
            stats["sync_tasks"] += summary.tasks
            stats["sync_schedules"] += summary.schedules
            stats["sync_mails"] += summary.mails
            stats["sync_skipped_mails"] += summary.skipped
            stats["sync_errors"] += len(summary.errors)
        return stats

    def _apply_mail_retention(self, ref_today: date) -> None:
        """Enforce mail_seed retention (§16.3 Gmail part D).

        Runs every sweep regardless of connector: a mail whose body was
        already cleared has nothing left to clear, and SeedConnector never
        produces mail_seeds, so this is a no-op in demo mode.
        - Any mail already `processed=True` with a non-empty body has its
          body cleared (the diff proposal, if any, was already extracted
          and written to a card; the raw body has no further use).
        - Any mail (processed or not) older than MAIL_RETENTION_DAYS is
          deleted outright.
        """
        for mail in self.store.list_mail_seeds():
            received = _parse_date_or_timestamp(mail.received_at)
            if received is not None and (ref_today - received).days >= MAIL_RETENTION_DAYS:
                self.store.delete_mail_seed(mail.mail_id)
                continue
            if mail.processed and mail.body:
                mail.body = ""
                self.store.save_mail_seed(mail)

    def run_sweep(
        self,
        demo_today: str | None = None,
        origin: str = "manual",
        run_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute a secretary sweep (autonomous-agent design §1/§5.3, round-5 ledger A).

        Single pipeline for every origin (the former origin="manual" full
        duplicate of the state machine has been retired — round-5 V-6/V-1/V-5/K-4):
        both origins run `_run_scheduled_sweep()`'s claim -> execute -> finish
        lifecycle.

        origin="manual" (the default, matching every pre-existing caller/test):
        a full-permission human override (§5.3) — `_effective_policy_for()`
        returns all-True with no CAS policy gating, so no policy_hold is ever
        written and no policy_limited audit is ever sent — and run_key defaults
        to a fresh "manual-"+uuid4() (no dedup: a manual click always runs).
        card/audit generation, promotion, resolve, profile_diff, and mail
        retention behavior are equivalent to the legacy manual sweep; the only
        allowed differences are a sweep_run audit (origin="manual"), a
        deterministic card_id, and CAS-mediated writes.

        origin="scheduled": per-owner autonomy-policy gating applies (§5.3).
        Only HTTP callers acting as an unattended automatic trigger (Cloud
        Scheduler, Agent Engine) should ever pass "scheduled" — see design §1
        for why the domain API itself still defaults to "manual".
        """
        return self._run_scheduled_sweep(demo_today=demo_today, origin=origin, run_key=run_key)

    # -------------------------------------------------------------------------
    # Unified (claim/finish/fail + autonomy-policy-gated) sweep (autonomous-agent
    # design §3/§5.3, round-5 ledger A)
    # -------------------------------------------------------------------------

    def _run_scheduled_sweep(
        self, demo_today: str | None = None, origin: str = "scheduled", run_key: str | None = None
    ) -> dict[str, Any]:
        """Execute the unified sweep for either origin (design §3/§5.3, round-5 ledger A).

        Lifecycle: claim -> execute -> finish, or fail on exception (fail
        leaves the run immediately re-claimable, C-14/Z-1). A 'done' claim
        short-circuits into a dedup response that reuses the already-finished
        summary and, if the sweep_run audit is somehow missing, reconstructs
        it from that confirmed summary (R4-H4). An 'in_progress' claim
        short-circuits without doing any work. origin="manual"'s default
        run_key ("manual-"+uuid4()) is always fresh, so in practice it always
        claims and never dedups/in-progresses (round-5 ledger A).
        """
        ref_today = get_today(demo_today)
        if run_key is None:
            prefix = "manual" if origin == "manual" else "scheduled"
            run_key = f"{prefix}-{uuid.uuid4().hex}"
        effective_run_key = run_key
        date_str = ref_today.isoformat()

        claim_token, claim_state = self.store.claim_sweep_run(
            effective_run_key,
            origin=origin,
            date=date_str,
            ttl_seconds=self.sweep_claim_ttl_seconds,
        )

        if claim_state == "done":
            run = self.store.get_sweep_run(effective_run_key) or {}
            summary = dict(run.get("summary") or {})
            if summary:
                audit_payload = {k: summary[k] for k in SchemaRegistry.SWEEP_RUN_KEYS if k in summary}
                self._send_internal_audit(
                    from_entity="system",
                    to_entity="system",
                    intent="sweep_run",
                    payload_type="sweep_run",
                    payload=audit_payload,
                    audit_id=f"msg_sweep_{effective_run_key}",
                )
            return {"status": "deduplicated", "run_key": effective_run_key, **summary}

        if claim_state == "in_progress":
            return {"status": "in_progress", "run_key": effective_run_key}

        assert claim_token is not None  # claim_state == "claimed"

        try:
            summary = self._execute_scheduled_sweep(ref_today, effective_run_key, origin)
        except Exception as exc:
            self.store.fail_sweep_run(effective_run_key, claim_token, error=str(exc))
            raise

        finished = self.store.finish_sweep_run(effective_run_key, claim_token, summary)
        if finished:
            # R4-H4: emit only AFTER the done+summary CAS has been confirmed.
            audit_payload = {k: summary[k] for k in SchemaRegistry.SWEEP_RUN_KEYS}
            self._send_internal_audit(
                from_entity="system",
                to_entity="system",
                intent="sweep_run",
                payload_type="sweep_run",
                payload=audit_payload,
                audit_id=f"msg_sweep_{effective_run_key}",
            )
        return {"status": "ok" if finished else "lost_claim", "run_key": effective_run_key, **summary}

    def _execute_scheduled_sweep(self, ref_today: date, run_key: str, origin: str) -> dict[str, Any]:
        """Run the (optionally gated) sweep body and return the full counts summary.

        origin="scheduled": every gate in §5.3 applies (per-owner autonomy policy).
        origin="manual": `_effective_policy_for` returns an all-True override with
        no CAS policy gating (round-5 ledger A) — no policy_hold is ever written
        and policy_limited_counts stays empty.

        The returned dict is a superset of the sweep_run audit's counts-only
        schema (SchemaRegistry.SWEEP_RUN_KEYS): it also carries cards_updated,
        diff_cards_created, and the sync stats, which the legacy manual sweep's
        callers/tests depend on but the audit payload deliberately omits (§6).
        """
        registered_agents = self.store.list_agents(active_only=True)
        profiles_list = self.store.list_profiles()
        profiles_map = {p.employee_id: p for p in profiles_list}

        # Connector sync is NOT gated (§5.3 C-15): it is the user's own data
        # refreshing, not an autonomous "action".
        sync_stats = self._sync_owners(registered_agents, profiles_list, ref_today)

        all_tasks = self.store.list_tasks()
        tasks_by_owner: dict[str, list[Task]] = {}
        for t in all_tasks:
            tasks_by_owner.setdefault(t.owner_employee_id, []).append(t)

        counters: dict[str, int] = {
            "tasks_evaluated": 0,
            "cards_created": 0,
            "cards_promoted": 0,
            "cards_updated": 0,
            "cards_resolved": 0,
            "diff_cards_created": 0,
            "needs_detected": 0,
            "candidates_explored": 0,
        }
        policy_limited_counts: dict[str, int] = {}

        for owner_id, owner_tasks in tasks_by_owner.items():
            eff, policy_token = self._effective_policy_for(owner_id, origin)
            owner_agent = self.store.get_agent_by_employee_id(owner_id)
            sender_entity = owner_agent.agent_id if owner_agent else owner_id

            for task in owner_tasks:
                counters["tasks_evaluated"] += 1
                self._scheduled_process_task(
                    task=task,
                    owner_id=owner_id,
                    owner_tasks=owner_tasks,
                    ref_today=ref_today,
                    eff=eff,
                    policy_token=policy_token,
                    registered_agents=registered_agents,
                    profiles_map=profiles_map,
                    sender_entity=sender_entity,
                    counters=counters,
                    policy_limited_counts=policy_limited_counts,
                    run_key=run_key,
                )

        self._scheduled_process_mail(counters=counters, origin=origin, run_key=run_key)
        self._apply_mail_retention(ref_today)

        # E2 (round-5 ledger): one anonymous policy_limited audit per stage for
        # the whole run (never per-owner — from_entity/payload never carry an
        # owner identifier), task_count summed across every held owner.
        for stage, task_count in policy_limited_counts.items():
            self._send_internal_audit(
                from_entity="secretary",
                to_entity="system",
                intent="policy_limited",
                payload_type="policy_limited",
                payload={"stage": stage, "run_key": run_key, "task_count": task_count},
                audit_id="msg_pol_" + hashlib.sha1(f"{run_key}:{stage}".encode()).hexdigest()[:12],
            )

        return {
            "origin": origin,
            "run_key": run_key,
            "date": ref_today.isoformat(),
            "tasks_evaluated": counters["tasks_evaluated"],
            "cards_created": counters["cards_created"],
            "cards_promoted": counters["cards_promoted"],
            "cards_updated": counters["cards_updated"],
            "cards_resolved": counters["cards_resolved"],
            "diff_cards_created": counters["diff_cards_created"],
            "needs_detected": counters["needs_detected"],
            "candidates_explored": counters["candidates_explored"],
            "policy_held": sum(policy_limited_counts.values()),
            "schema_version": 1,
            **sync_stats,
        }

    def _send_stagnation_audit(
        self,
        card_id: str,
        tier: str,
        task: Task,
        score: float,
        sender_entity: str,
        epoch: str = "",
    ) -> None:
        """Deterministic create-only stagnation_detected (R4-H2, round-6 ledger C-28,
        round-7 C-33): id keys on (card_id, band, audit_epoch) so a re-run that
        lands on the SAME band within the SAME lifecycle is a harmless no-op,
        while a genuine band change gets its own fresh audit id.

        The epoch is the card's payload["audit_epoch"], stamped by the store's
        CAS at each reopen (re-detection after resolve, C-13/C-28) and carried
        forward on every later write of that lifecycle. A reopen therefore gets
        a fresh audit row even on a band the card saw in a PRIOR lifecycle,
        while every later write of the same lifecycle — including a crash-retry
        in a different run — reuses the stored epoch and dedups via create-only
        (round-7 C-33: no salt-less duplicate row in the run after a reopen).

        round-5 ledger E1: this is a pre-existing/registered intent (not one of
        the 2 internal-only sweep_run/policy_limited types), so it goes through
        the normal transmission.send() path — a validation failure here (a
        programming bug) leaves a visible reject_unregistered_type row instead
        of being silently dropped. create_only=True keeps the deterministic-id
        idempotency (a same-band re-sweep is a harmless no-op).
        """
        id_key = f"{card_id}:{tier}:{epoch}" if epoch else f"{card_id}:{tier}"
        self.kd_service.transmission.send(
            from_entity=sender_entity,
            to_entity="system",
            intent="stagnation_detected",
            payload_type="stagnation_detected",
            payload={"task_id": task.task_id, "tier": tier, "score": score},
            audit_id="msg_stag_" + hashlib.sha1(id_key.encode()).hexdigest()[:12],
            create_only=True,
        )

    def _scheduled_process_task(
        self,
        task: Task,
        owner_id: str,
        owner_tasks: list[Task],
        ref_today: date,
        eff: dict[str, bool],
        policy_token: str | None,
        registered_agents: list[Any],
        profiles_map: dict[str, Profile],
        sender_entity: str,
        counters: dict[str, int],
        policy_limited_counts: dict[str, int],
        run_key: str,
    ) -> None:
        """Gated per-task state machine for the unified sweep (design §5.3, round-5 ledger A/B/C).

        Every card write goes through Store.upsert_card_gated() (R4-H1); every
        side-effect (audit emission, counters) is driven strictly by the
        returned outcome (Z-3) — a rejected_* write never counts or emits.
        Promotion/needs_detected decisions use the prev_status/prev_tier
        RETURNED by the CAS (round-5 ledger B), not the pre-read `open_card`
        below, which only decides which write to attempt and how to build its
        payload — never what got promoted (V-2/K-5: that decision now lives
        inside the same transaction as the write it describes).

        round-6 ledger W-1/W-3: the never-downgrade guard (C-19) and the
        policy_hold carry-forward (round-5 ledger C) are enforced INSIDE
        Store.upsert_card_gated's own transaction now, not here — the payload
        this method builds below deliberately never copies a stale
        `policy_hold` from `open_card` (a pre-read); it is stripped before the
        CAS call so the store's own fresh read decides whether to carry an
        existing hold forward (default) or drop it (only when this method
        passes clear_policy_hold=True). This method's remaining `open_card`
        reads only decide which write to attempt/how to build evidence
        fields — never the downgrade/hold outcome itself.
        """
        # Rule 1 (unchanged from manual): confirmed/dismissed tasks are frozen.
        existing_cards = self.store.find_cards_for_task(owner_id, task.task_id)
        if any(c.status in ("confirmed", "dismissed") for c in existing_cards):
            return

        existing = self.store.find_card_by_domain_key(owner_id, "stagnation", task.task_id)
        open_card = existing if existing is not None and existing.status == "open" else None

        def _resolve(reason: str) -> None:
            base_payload = dict(open_card.payload) if open_card is not None else {}
            base_payload["task_id"] = task.task_id
            resolve_card = Card(
                card_id=open_card.card_id,
                owner_employee_id=owner_id,
                type="stagnation",
                tier=open_card.tier,
                payload=base_payload,
                status="resolved",
                resolved_reason=reason,
            )
            _, outcome, _, _ = self.store.upsert_card_gated(resolve_card, expected_policy_updated_at=policy_token)
            if outcome in ("created", "updated"):
                counters["cards_resolved"] += 1

        if task.status == "done":
            if open_card is not None:
                _resolve("task_done")
            return

        score, evidence_line, _ = calculate_stagnation_score(task, owner_tasks, today=ref_today)

        if score < self.t1:
            if open_card is not None:
                _resolve("score_below_t1")
            return

        # score >= T1 from here: a card is warranted at least at 'notice'.
        if not eff["monitor_stalled_work"]:
            # Monitor OFF (§5.3): no new scoring-driven card creation. An
            # already-open card still gets its evidence refreshed — "don't
            # start new things, fold up what's already started" (C-10/C-15).
            if open_card is not None:
                payload = dict(open_card.payload)
                payload.pop("policy_hold", None)  # round-6 ledger W-1: let the store carry it forward
                payload.update(
                    {"task_id": task.task_id, "task_title": task.title, "score": score, "evidence_line": evidence_line}
                )
                refresh_card = Card(
                    card_id=open_card.card_id, owner_employee_id=owner_id, type="stagnation",
                    tier=open_card.tier, payload=payload, status="open",
                )
                self.store.upsert_card_gated(refresh_card, expected_policy_updated_at=policy_token)
            return

        prev_tier = open_card.tier if open_card is not None else None
        card_id = (
            open_card.card_id
            if open_card is not None
            else "card_stag_" + hashlib.sha1(f"{owner_id}:{task.task_id}".encode()).hexdigest()[:12]
        )

        # C-19: a card already at request_draft is never downgraded — neither
        # by a later drop in score nor by a later policy restriction. This
        # generalizes the manual sweep's pre-existing "no downgrade by score"
        # invariant to policy as well.
        if prev_tier == "request_draft":
            payload = dict(open_card.payload)
            payload.pop("policy_hold", None)  # round-6 ledger W-1: let the store carry it forward
            payload.update(
                {"task_id": task.task_id, "task_title": task.title, "score": score, "evidence_line": evidence_line}
            )
            refresh_card = Card(
                card_id=card_id, owner_employee_id=owner_id, type="stagnation",
                tier="request_draft", payload=payload, status="open",
            )
            _, outcome, _, _ = self.store.upsert_card_gated(refresh_card, expected_policy_updated_at=policy_token)
            if outcome in ("updated", "reopened"):
                counters["cards_updated"] += 1
            return

        existing_hold = open_card.payload.get("policy_hold") if open_card is not None else None
        if (
            existing_hold is not None
            and existing_hold.get("policy_updated_at") == policy_token
            and score >= self.t2
        ):
            # R4-H5: the hold's reason (score qualifies, policy still
            # restricts) is unchanged since it was last recorded — refresh
            # evidence only, skip explore/LLM/related audits entirely.
            payload = dict(open_card.payload)
            payload.update(
                {"task_id": task.task_id, "task_title": task.title, "score": score, "evidence_line": evidence_line}
            )
            refresh_card = Card(
                card_id=card_id, owner_employee_id=owner_id, type="stagnation",
                tier="notice", payload=payload, status="open",
            )
            self.store.upsert_card_gated(refresh_card, expected_policy_updated_at=policy_token)
            return

        if score < self.t2:
            # Notice band: no exploration is ever attempted here, regardless
            # of policy (this mirrors the full-policy path too). Payload is
            # MERGED from the existing card (not replaced) so an unrelated
            # stale key (e.g. a policy_hold recorded under the current token,
            # round-5 ledger C) survives a band dip, matching the legacy
            # manual sweep's in-place field update too.
            payload = dict(open_card.payload) if open_card is not None else {}
            payload.pop("policy_hold", None)  # round-6 ledger W-1/W-3: let the store carry it forward
            payload.update(
                {"task_id": task.task_id, "task_title": task.title, "score": score, "evidence_line": evidence_line}
            )
            new_card = Card(
                card_id=card_id, owner_employee_id=owner_id, type="stagnation",
                tier="notice", payload=payload, status="open",
            )
            written, outcome, _, _ = self.store.upsert_card_gated(new_card, expected_policy_updated_at=policy_token)
            if outcome in ("created", "reopened"):
                counters["cards_created"] += 1
            elif outcome == "updated":
                counters["cards_updated"] += 1
            if outcome in ("created", "updated", "reopened"):
                self._send_stagnation_audit(
                    card_id, "notice", task, score, sender_entity,
                    epoch=written.payload.get("audit_epoch", ""),
                )
            return

        # score >= T2: this task qualifies for exploration under full policy.
        # eff.effective() already enforces search<=monitor<=... / ask<=search /
        # prepare<=ask, so the first False in this order is the single gate
        # actually in effect (monitor OFF was already handled/returned above).
        if not eff["search_organization"]:
            self._apply_policy_hold(
                stage="search", card_id=card_id, owner_id=owner_id, task=task, score=score,
                evidence_line=evidence_line, policy_token=policy_token, sender_entity=sender_entity,
                counters=counters, policy_limited_counts=policy_limited_counts, run_key=run_key,
            )
            return

        task_query = f"{task.title} {task.description}".strip()

        if not eff["ask_candidate_agents"]:
            shortlist = self.matching_engine.preview_shortlist(
                question=task_query, registered_agents=registered_agents,
                profiles=profiles_map, exclude_employee_id=owner_id,
            )
            counters["candidates_explored"] += len(shortlist)  # counts-only; shortlist itself is discarded
            self._apply_policy_hold(
                stage="ask", card_id=card_id, owner_id=owner_id, task=task, score=score,
                evidence_line=evidence_line, policy_token=policy_token, sender_entity=sender_entity,
                counters=counters, policy_limited_counts=policy_limited_counts, run_key=run_key,
            )
            return

        if not eff["prepare_introduction"]:
            shortlist = self.matching_engine.preview_shortlist(
                question=task_query, registered_agents=registered_agents,
                profiles=profiles_map, exclude_employee_id=owner_id,
            )
            counters["candidates_explored"] += len(shortlist)
            self.matching_engine.preview_evaluate(task_query, shortlist)  # counts-only; result discarded
            self._apply_policy_hold(
                stage="prepare", card_id=card_id, owner_id=owner_id, task=task, score=score,
                evidence_line=evidence_line, policy_token=policy_token, sender_entity=sender_entity,
                counters=counters, policy_limited_counts=policy_limited_counts, run_key=run_key,
            )
            return

        # Full path (all 4 permissions ON): identical semantics to manual sweep
        # — draft the question, then preview with THAT question (§5.3 "current
        # implementation, unchanged"). shortlist is computed once and reused
        # for both the candidates_explored count and Stage-2 evaluation.
        q_draft = generate_question_draft(task, llm_client=self.llm_client)
        shortlist = self.matching_engine.preview_shortlist(
            question=q_draft, registered_agents=registered_agents,
            profiles=profiles_map, exclude_employee_id=owner_id,
        )
        counters["candidates_explored"] += len(shortlist)
        preview_cands = self.matching_engine.preview_evaluate(q_draft, shortlist)

        if len(preview_cands) == 0:
            tier = "notice"
            preview_dict: dict[str, Any] = {
                "candidates": [],
                "note": "No matching candidates found across public profiles.",
            }
        else:
            tier = "request_draft"
            preview_dict = {
                "candidates": [
                    {
                        "employee_id": c.employee_id,
                        "name": c.name,
                        "reason_text": c.reason_text,
                        "score": c.score,
                        "cited_item_keys": c.cited_item_keys,
                    }
                    for c in preview_cands
                ]
            }

        payload = {
            "task_id": task.task_id,
            "task_title": task.title,
            "score": score,
            "evidence_line": evidence_line,
            "question_draft": q_draft,
            "preview": preview_dict,
        }
        full_card = Card(
            card_id=card_id, owner_employee_id=owner_id, type="stagnation",
            tier=tier, payload=payload, status="open",
        )
        # clear_policy_hold=True (round-6 ledger W-1/W-3): this write reflects
        # a full evaluation that actually ran under the CURRENT policy (all 4
        # permissions ON) — any stale policy_hold marker left over from before
        # a policy change no longer applies and must not linger.
        card, outcome, prev_status_txn, prev_tier_txn = self.store.upsert_card_gated(
            full_card, expected_policy_updated_at=policy_token, clear_policy_hold=True
        )

        # round-5 ledger B: promotion/needs_detected are driven by prev_tier_txn
        # (read inside the CAS transaction), not the pre-read `prev_tier` above,
        # which may be stale under concurrent execution (V-2/K-5).
        if outcome in ("created", "reopened"):
            counters["cards_created"] += 1
        elif outcome == "updated" and prev_tier_txn == "notice" and tier == "request_draft":
            counters["cards_promoted"] += 1
        elif outcome == "updated":
            counters["cards_updated"] += 1

        if outcome in ("created", "updated", "reopened"):
            reopened = outcome == "reopened"
            self._send_stagnation_audit(
                card_id, tier, task, score, sender_entity,
                epoch=card.payload.get("audit_epoch", ""),
            )
            if tier == "request_draft":
                # round-6 ledger C-28: a reopen landing directly on request_draft
                # is a genuine new detection event even when prev_tier_txn (the
                # RESOLVED doc's tier before this write) already reads
                # "request_draft" — it was promoted in a PRIOR lifecycle that
                # has since been resolved, so needs_detected must still count it.
                if reopened or prev_tier_txn != "request_draft":
                    counters["needs_detected"] += 1
                if preview_cands:
                    # round-5 ledger E1: preview_search is a pre-existing/registered
                    # intent, not one of the 2 internal-only types — normal
                    # transmission path (validation failure -> visible reject row).
                    prev_id_key = f"{card_id}:{tier}:{run_key}" if reopened else f"{card_id}:{tier}"
                    self.kd_service.transmission.send(
                        from_entity=sender_entity, to_entity="system", intent="preview_search",
                        payload_type="preview_search",
                        payload={
                            "task_id": task.task_id,
                            "candidates": [{"employee_id": c.employee_id, "score": c.score} for c in preview_cands],
                        },
                        audit_id="msg_prev_" + hashlib.sha1(prev_id_key.encode()).hexdigest()[:12],
                        create_only=True,
                    )

    def _apply_policy_hold(
        self,
        stage: str,
        card_id: str,
        owner_id: str,
        task: Task,
        score: float,
        evidence_line: str,
        policy_token: str | None,
        sender_entity: str,
        counters: dict[str, int],
        policy_limited_counts: dict[str, int],
        run_key: str,
    ) -> None:
        """Write/refresh a policy_hold notice card (design §5.3) and record the hold for C-20/E2 aggregation.

        The held candidate content itself is NEVER written into the card
        (§5.3 "held path の探索結果はどこにも保存せず counts のみ") — only the
        stage/policy_updated_at marker that lets a later run recognize the
        hold as unchanged (R4-H5) and skip re-exploring. policy_limited_counts
        is keyed by stage only (round-5 ledger E2: never per-owner — the later
        policy_limited audit must stay anonymous, no owner identifier in
        from_entity or payload).
        """
        payload = {
            "task_id": task.task_id,
            "task_title": task.title,
            "score": score,
            "evidence_line": evidence_line,
            "policy_hold": {"stage": stage, "policy_updated_at": policy_token},
        }
        hold_card = Card(
            card_id=card_id, owner_employee_id=owner_id, type="stagnation",
            tier="notice", payload=payload, status="open",
        )
        written, outcome, _, _ = self.store.upsert_card_gated(hold_card, expected_policy_updated_at=policy_token)
        if outcome in ("created", "reopened"):
            counters["cards_created"] += 1
        if outcome in ("created", "updated", "reopened"):
            self._send_stagnation_audit(
                card_id, "notice", task, score, sender_entity,
                epoch=written.payload.get("audit_epoch", ""),
            )
            policy_limited_counts[stage] = policy_limited_counts.get(stage, 0) + 1

    def _scheduled_process_mail(self, counters: dict[str, int], origin: str, run_key: str) -> None:
        """Gated mail->profile_diff pipeline: Monitor OFF means unread, unconsumed mail (§5.3 C-15).

        R4-H3/round-5 ledger D outcome matrix: `mail.processed` flips to True
        for a card write outcome of 'created', 'updated', 'reopened', or
        'rejected_terminal' (a duplicate mail whose proposal was already
        confirmed/dismissed/applied — already adjudicated, so the mail is
        consumed to stop endless LLM re-reads), or a deliberate "no diff" LLM
        verdict (diff_res is None, which never touches a card at all) — never
        for rejected_policy_changed or an LLM/extraction failure, so a stalled
        write can never make the secretary "forget" to look at a mail again.
        """
        for mail in self.store.list_mail_seeds(unprocessed_only=True):
            eff, policy_token = self._effective_policy_for(mail.owner_employee_id, origin)
            if not eff["monitor_stalled_work"]:
                continue

            owner_prof = self.store.get_profile(mail.owner_employee_id)
            diff_res = extract_profile_diff(mail, owner_prof, llm_client=self.llm_client)

            if diff_res is EXTRACTION_FAILED:
                continue  # leave processed=False, retried next sweep (R-2)

            outcome: str | None = None
            if diff_res is not None:
                item_key, body_draft = diff_res
                card_id = "card_diff_" + hashlib.sha1(mail.mail_id.encode()).hexdigest()[:12]
                diff_card = Card(
                    card_id=card_id, owner_employee_id=mail.owner_employee_id, type="profile_diff", tier=None,
                    payload={
                        "item_key": item_key, "body_draft": body_draft,
                        "source_mail_id": mail.mail_id, "subject": mail.subject,
                    },
                    status="open",
                )
                _, outcome, _, _ = self.store.upsert_card_gated(diff_card, expected_policy_updated_at=policy_token)
                if outcome in ("created", "reopened"):
                    counters["diff_cards_created"] += 1
                if outcome in ("created", "updated", "reopened"):
                    owner_agent = self.store.get_agent_by_employee_id(mail.owner_employee_id)
                    sender_entity = owner_agent.agent_id if owner_agent else mail.owner_employee_id
                    # round-5 ledger E1: normal transmission path (see
                    # _send_stagnation_audit's docstring for the rationale).
                    # round-6 ledger C-28: run_key is mixed into the id (mail_id
                    # is not enough alone) so a genuine re-detection in a LATER
                    # run — e.g. after a reopen — gets its own audit row, while
                    # a same-run crash-retry still dedups via create-only.
                    self.kd_service.transmission.send(
                        from_entity=sender_entity, to_entity="system", intent="profile_diff_proposed",
                        payload_type="profile_diff_proposed",
                        payload={"mail_id": mail.mail_id, "item_key": item_key},
                        audit_id="msg_diff_"
                        + hashlib.sha1(mail.mail_id.encode()).hexdigest()[:12],
                        create_only=True,
                    )

            # round-5 ledger D: processed=True for {created, updated, reopened,
            # rejected_terminal} (or the diff_res is None "no diff" verdict,
            # which never wrote a card) — NOT for rejected_policy_changed or an
            # LLM/extraction failure (handled by the `continue` above), which
            # must stay unprocessed so the next sweep retries.
            if diff_res is None or outcome in ("created", "updated", "reopened", "rejected_terminal"):
                mail.processed = True
                self.store.save_mail_seed(mail)

    def get_morning_digest(
        self,
        employee_id: str,
        demo_today: str | None = None,
    ) -> dict[str, Any]:
        """Assemble dynamic morning digest for employee (§14.2, §14.8).

        Schedule rule order: Overdue -> Today -> Tomorrow -> Upcoming.
        Followed by open stagnation and profile_diff cards.
        """
        ref_today = get_today(demo_today)
        all_schedules = self.store.list_schedules(owner_employee_id=employee_id)

        # Categorize and order schedule reminders
        reminders: list[dict[str, Any]] = []
        for s in all_schedules:
            due_d = _parse_date_or_timestamp(s.due_date)
            if due_d is None:
                category = "upcoming"
                priority = 4
            elif due_d < ref_today:
                category = "overdue"
                priority = 1
            elif due_d == ref_today:
                category = "today"
                priority = 2
            elif (due_d - ref_today).days == 1:
                category = "tomorrow"
                priority = 3
            else:
                category = "upcoming"
                priority = 4

            reminders.append({
                "item_id": s.item_id,
                "kind": s.kind,
                "title": s.title,
                "due_date": s.due_date,
                "due_category": category,
                "_priority": priority,
            })

        # Sort: priority ascending, then due_date ascending
        reminders.sort(key=lambda r: (r["_priority"], r["due_date"]))
        for r in reminders:
            r.pop("_priority", None)

        # Retrieve open cards
        open_cards = self.store.list_cards(owner_employee_id=employee_id, status="open")
        stagnation_cards = [
            c.to_dict() for c in open_cards if c.type == "stagnation"
        ]
        profile_diff_cards = [
            c.to_dict() for c in open_cards if c.type == "profile_diff"
        ]

        # last_sweep / autonomy.effective (autonomous-agent design §8, C-17):
        # lets the UI show "Last sweep …" and switch Watching rows to
        # "· Paused" when the employee's effective Monitor is OFF.
        latest_run = self.store.get_latest_sweep_run()
        last_sweep = (
            {"at": latest_run.get("finished_at"), "origin": latest_run.get("origin")}
            if latest_run is not None
            else None
        )
        effective_policy, _ = self._effective_policy(employee_id)

        return {
            "employee_id": employee_id,
            "date": ref_today.isoformat(),
            "reminders": reminders,
            "stagnation_cards": stagnation_cards,
            "profile_diff_cards": profile_diff_cards,
            "last_sweep": last_sweep,
            "autonomy": {"effective": effective_policy},
        }

    def confirm_stagnation_card(
        self,
        card_id: str,
        edited_question: str,
    ) -> dict[str, Any]:
        """Confirm stagnation card and dispatch query via standard query path (§14.4).

        Atomic CAS (X-3, V-6/S-8):
        - Store.try_confirm_card() atomically transitions open->confirmed; only the
          caller that wins the race proceeds to submit the query.
        - A losing concurrent call (card already 'confirmed') returns the existing
          linked_query_audit_id without resubmitting.
        - If not 'open' and not 'confirmed', raises an error.
        - On query execution failure, rolls back card status to 'open'.
        """
        card, won = self.store.try_confirm_card(card_id)
        if card is None:
            raise ValueError(f"Secretary card '{card_id}' not found")

        if not won:
            if card.status == "confirmed":
                return {
                    "status": "already_confirmed",
                    "card_id": card.card_id,
                    "query_audit_id": card.linked_query_audit_id,
                }
            raise ValueError(f"Card '{card_id}' cannot be confirmed in status '{card.status}'")

        try:
            # Pass edited question to existing query pipeline
            result = self.kd_service.submit_query(
                requester_id=card.owner_employee_id,
                question_text=edited_question,
            )
            query_audit_id = result.query_message.audit_id
            card.linked_query_audit_id = query_audit_id
            card.updated_at = utc_now_iso()
            self.store.save_card(card)

            return {
                "status": "confirmed",
                "card_id": card.card_id,
                "query_audit_id": query_audit_id,
                "dispatched_count": len(result.dispatched_asks),
            }
        except Exception as exc:
            # Rollback on submission failure
            card.status = "open"
            card.linked_query_audit_id = None
            card.updated_at = utc_now_iso()
            self.store.save_card(card)
            raise RuntimeError(f"Failed to submit discovery query: {exc}") from exc

    def review_profile_diff(
        self,
        card_id: str,
        action: str,  # 'apply' | 'edit_apply' | 'private_apply' | 'dismiss'
        edited_body: str | None = None,
    ) -> dict[str, Any]:
        """Process employee review for a profile diff proposal card (§14.5).

        Actions:
        - 'apply': Add a NEW item with visibility='public', regenerate embeddings, card -> 'applied'.
        - 'edit_apply': Add a NEW item with the edited body (public), regenerate embeddings, card -> 'applied'.
        - 'private_apply': Add a NEW item with visibility='private', regenerate embeddings, card -> 'applied'.
        - 'dismiss': Card -> 'dismissed' (no profile change).

        Reflection is always additive (design §14.5-3, V-7/S-7): existing profile
        items are never overwritten and their visibility is never flipped. If the
        proposed item_key collides with an existing item, a unique suffixed key is
        used instead. The item_key itself always comes from the card's own payload
        (never from the caller) so a request cannot retarget which item gets edited.
        """
        card = self.store.get_card(card_id)
        if card is None or card.type != "profile_diff":
            raise ValueError(f"Profile diff card '{card_id}' not found")
        if card.status != "open":
            raise ValueError(f"Card '{card_id}' is already {card.status}")

        if action == "dismiss":
            card.status = "dismissed"
            card.updated_at = utc_now_iso()
            self.store.save_card(card)
            return {"status": "dismissed", "card_id": card_id}

        if action not in ("apply", "edit_apply", "private_apply"):
            raise ValueError(f"Unsupported review action '{action}'")

        key = card.payload.get("item_key", "current_work")
        body = (
            edited_body.strip()
            if (action == "edit_apply" and edited_body and edited_body.strip())
            else str(card.payload.get("body_draft", "")).strip()
        )
        visibility = "private" if action == "private_apply" else "public"

        # Reflection assumes the profile already exists (V-10): the secretary
        # never fabricates a profile for a mail owner who isn't a registered
        # employee.
        prof = self.store.get_profile(card.owner_employee_id)
        if prof is None:
            raise LookupError(
                f"Profile for employee '{card.owner_employee_id}' not found; cannot apply diff"
            )

        # Additive only: never overwrite an existing key/visibility. On
        # collision, append a uniquely suffixed key instead (design §14.5-3).
        if prof.get_item(key) is not None:
            suffix = 1
            candidate_key = f"{key}_mail_{suffix}"
            while prof.get_item(candidate_key) is not None:
                suffix += 1
                candidate_key = f"{key}_mail_{suffix}"
            key = candidate_key

        new_item = ProfileItem(
            key=key,
            body=body,
            source="mail_seed",
            visibility=visibility,
            reviewed=True,
        )
        prof.items.append(new_item)

        # Regenerate BOTH full embedding and public embedding (§14.5, §9.3)
        self.matching_engine.compute_profile_embedding(prof)
        self.store.save_profile(prof)

        # Update card status
        card.status = "applied"
        card.updated_at = utc_now_iso()
        self.store.save_card(card)

        return {
            "status": "applied",
            "card_id": card_id,
            "item_key": key,
            "visibility": visibility,
            "reviewed": True,
        }

    def dismiss_card(self, card_id: str) -> dict[str, Any]:
        """Dismiss an open stagnation card (§14.2, E-8).

        Restricted to type='stagnation' and status='open': profile_diff cards
        have their own dedicated 'see later' path via review_profile_diff(action='dismiss'),
        and confirmed/applied/resolved cards must not be reachable through this
        generic endpoint.
        """
        card = self.store.get_card(card_id)
        if card is None or card.type != "stagnation":
            raise ValueError(f"Stagnation card '{card_id}' not found")
        if card.status != "open":
            raise ValueError(f"Card '{card_id}' cannot be dismissed in status '{card.status}'")
        card.status = "dismissed"
        card.updated_at = utc_now_iso()
        self.store.save_card(card)
        return {"status": "dismissed", "card_id": card_id}
