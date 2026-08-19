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
import json
import os
import re
from typing import Any
import uuid

from knowledge_discovery.matching import MatchingEngine
from knowledge_discovery.models import (
    Card,
    MailSeed,
    PreviewCandidate,
    Profile,
    ProfileItem,
    Schedule,
    Task,
    utc_now_iso,
)
from knowledge_discovery.service import KnowledgeDiscoveryService
from knowledge_discovery.store import Store

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
            if cleaned and cleaned != "null":
                data = json.loads(cleaned)
                if isinstance(data, dict) and "body_draft" in data:
                    key = data.get("item_key", "current_work")
                    body = data.get("body_draft", "").strip()
                    if body:
                        return key, body
        except Exception:
            pass

    # Heuristic extraction fallback
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
    ) -> None:
        self.store = store
        self.kd_service = kd_service
        self.matching_engine = matching_engine or kd_service.matching_engine
        self.llm_client = llm_client
        self.t1 = t1
        self.t2 = t2
        assert self.t1 < self.t2, f"t1 ({self.t1}) must be < t2 ({self.t2})"

    def run_sweep(self, demo_today: str | None = None) -> dict[str, Any]:
        """Execute full secretary sweep across all tasks and mail seeds (§14.1, §14.2, §14.5).

        State Machine Rules (§14.2):
        1. Confirmed or Dismissed tasks: Do NOT recreate cards.
        2. Task status 'done' or score < T1: Transition open card to 'resolved'.
        3. T1 <= score < T2: Create or update 'notice' card.
        4. score >= T2: Run preview search (public only, embedding_public). If candidates found,
           create or promote card to 'request_draft'. If 0 candidates, keep/create 'notice'.
        5. Idempotent: Never creates duplicate open cards for the same (owner, task_id).
        """
        ref_today = get_today(demo_today)
        all_tasks = self.store.list_tasks()
        registered_agents = self.store.list_agents(active_only=True)
        profiles_list = self.store.list_profiles()
        profiles_map = {p.employee_id: p for p in profiles_list}

        # Group tasks by owner_employee_id
        tasks_by_owner: dict[str, list[Task]] = {}
        for t in all_tasks:
            tasks_by_owner.setdefault(t.owner_employee_id, []).append(t)

        tasks_evaluated = 0
        cards_created = 0
        cards_promoted = 0
        cards_updated = 0
        cards_resolved = 0
        diff_cards_created = 0

        for owner_id, owner_tasks in tasks_by_owner.items():
            # Resolve sender entity for audit recording
            owner_agent = self.store.get_agent_by_employee_id(owner_id)
            sender_entity = owner_agent.agent_id if owner_agent else owner_id

            for task in owner_tasks:
                tasks_evaluated += 1

                # Rule 1: Skip if already confirmed or dismissed
                existing_cards = self.store.find_cards_for_task(owner_id, task.task_id)
                has_terminal = any(c.status in ("confirmed", "dismissed") for c in existing_cards)
                if has_terminal:
                    continue

                open_card = self.store.find_open_card_for_task(owner_id, task.task_id)

                # Rule 2: If task is done -> resolve open card
                if task.status == "done":
                    if open_card is not None:
                        open_card.status = "resolved"
                        open_card.resolved_reason = "task_done"
                        open_card.updated_at = utc_now_iso()
                        self.store.save_card(open_card)
                        cards_resolved += 1
                    continue

                # Calculate score
                score, evidence_line, _ = calculate_stagnation_score(
                    task, owner_tasks, today=ref_today
                )

                # Rule 2b: If score < T1 -> resolve open card
                if score < self.t1:
                    if open_card is not None:
                        open_card.status = "resolved"
                        open_card.resolved_reason = "score_below_t1"
                        open_card.updated_at = utc_now_iso()
                        self.store.save_card(open_card)
                        cards_resolved += 1
                    continue

                # Rule 3: T1 <= score < T2 (Notice Tier)
                if self.t1 <= score < self.t2:
                    if open_card is None:
                        card_id = f"card_stag_{uuid.uuid4().hex[:10]}"
                        new_card = Card(
                            card_id=card_id,
                            owner_employee_id=owner_id,
                            type="stagnation",
                            tier="notice",
                            payload={
                                "task_id": task.task_id,
                                "task_title": task.title,
                                "score": score,
                                "evidence_line": evidence_line,
                            },
                            status="open",
                        )
                        self.store.save_card(new_card)
                        cards_created += 1

                        # Audit log (§14.6)
                        self.kd_service.transmission.send(
                            from_entity=sender_entity,
                            to_entity="system",
                            intent="stagnation_detected",
                            payload_type="stagnation_detected",
                            payload={"task_id": task.task_id, "tier": "notice", "score": score},
                        )
                    else:
                        # Update existing open card without downgrading tier
                        open_card.payload["score"] = score
                        open_card.payload["evidence_line"] = evidence_line
                        open_card.payload["task_title"] = task.title
                        open_card.updated_at = utc_now_iso()
                        self.store.save_card(open_card)
                        cards_updated += 1
                    continue

                # Rule 4: score >= T2 (Request Draft Tier or Notice fallback)
                if score >= self.t2:
                    # 1. Draft inquiry question
                    q_draft = generate_question_draft(task, llm_client=self.llm_client)

                    # 2. Pure preview search on embedding_public
                    preview_cands = self.matching_engine.preview_search(
                        question=q_draft,
                        registered_agents=registered_agents,
                        profiles=profiles_map,
                        exclude_employee_id=owner_id,
                    )

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

                    if open_card is None:
                        card_id = f"card_stag_{uuid.uuid4().hex[:10]}"
                        new_card = Card(
                            card_id=card_id,
                            owner_employee_id=owner_id,
                            type="stagnation",
                            tier=tier,
                            payload={
                                "task_id": task.task_id,
                                "task_title": task.title,
                                "score": score,
                                "evidence_line": evidence_line,
                                "question_draft": q_draft,
                                "preview": preview_dict,
                            },
                            status="open",
                        )
                        self.store.save_card(new_card)
                        cards_created += 1

                        # Audit log: stagnation_detected
                        self.kd_service.transmission.send(
                            from_entity=sender_entity,
                            to_entity="system",
                            intent="stagnation_detected",
                            payload_type="stagnation_detected",
                            payload={"task_id": task.task_id, "tier": tier, "score": score},
                        )

                        # Audit log: preview_search if candidates found
                        if preview_cands:
                            self.kd_service.transmission.send(
                                from_entity=sender_entity,
                                to_entity="system",
                                intent="preview_search",
                                payload_type="preview_search",
                                payload={
                                    "task_id": task.task_id,
                                    "candidates": [
                                        {"employee_id": c.employee_id, "score": c.score}
                                        for c in preview_cands
                                    ],
                                },
                            )
                    else:
                        # Existing card: promote notice -> request_draft or update
                        was_notice = (open_card.tier == "notice")
                        open_card.tier = tier
                        open_card.payload["score"] = score
                        open_card.payload["evidence_line"] = evidence_line
                        open_card.payload["task_title"] = task.title
                        open_card.payload["question_draft"] = q_draft
                        open_card.payload["preview"] = preview_dict
                        open_card.updated_at = utc_now_iso()
                        self.store.save_card(open_card)

                        if was_notice and tier == "request_draft":
                            cards_promoted += 1
                        else:
                            cards_updated += 1

                        # Record preview_search audit if preview executed
                        if preview_cands:
                            self.kd_service.transmission.send(
                                from_entity=sender_entity,
                                to_entity="system",
                                intent="preview_search",
                                payload_type="preview_search",
                                payload={
                                    "task_id": task.task_id,
                                    "candidates": [
                                        {"employee_id": c.employee_id, "score": c.score}
                                        for c in preview_cands
                                    ],
                                },
                            )

        # ---------------------------------------------------------------------
        # Sweep Mail Seeds for Profile Diff Proposals (§14.5)
        # ---------------------------------------------------------------------
        unprocessed_mails = self.store.list_mail_seeds(unprocessed_only=True)
        for mail in unprocessed_mails:
            owner_prof = self.store.get_profile(mail.owner_employee_id)
            diff_res = extract_profile_diff(mail, owner_prof, llm_client=self.llm_client)

            if diff_res is not None:
                item_key, body_draft = diff_res
                card_id = f"card_diff_{uuid.uuid4().hex[:10]}"
                diff_card = Card(
                    card_id=card_id,
                    owner_employee_id=mail.owner_employee_id,
                    type="profile_diff",
                    tier=None,
                    payload={
                        "item_key": item_key,
                        "body_draft": body_draft,
                        "source_mail_id": mail.mail_id,
                        "subject": mail.subject,
                    },
                    status="open",
                )
                self.store.save_card(diff_card)
                diff_cards_created += 1

                owner_agent = self.store.get_agent_by_employee_id(mail.owner_employee_id)
                sender_entity = owner_agent.agent_id if owner_agent else mail.owner_employee_id
                self.kd_service.transmission.send(
                    from_entity=sender_entity,
                    to_entity="system",
                    intent="profile_diff_proposed",
                    payload_type="profile_diff_proposed",
                    payload={"mail_id": mail.mail_id, "item_key": item_key},
                )

            # Mark processed
            mail.processed = True
            self.store.save_mail_seed(mail)

        return {
            "status": "ok",
            "date": ref_today.isoformat(),
            "tasks_evaluated": tasks_evaluated,
            "cards_created": cards_created,
            "cards_promoted": cards_promoted,
            "cards_updated": cards_updated,
            "cards_resolved": cards_resolved,
            "diff_cards_created": diff_cards_created,
        }

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

        return {
            "employee_id": employee_id,
            "date": ref_today.isoformat(),
            "reminders": reminders,
            "stagnation_cards": stagnation_cards,
            "profile_diff_cards": profile_diff_cards,
        }

    def confirm_stagnation_card(
        self,
        card_id: str,
        edited_question: str,
    ) -> dict[str, Any]:
        """Confirm stagnation card and dispatch query via standard query path (§14.4).

        Idempotency & CAS:
        - If already 'confirmed', returns existing linked_query_audit_id without resubmitting.
        - If not 'open', raises an error.
        - On query execution failure, rolls back card status to 'open'.
        """
        card = self.store.get_card(card_id)
        if card is None:
            raise ValueError(f"Secretary card '{card_id}' not found")

        # Idempotent double-POST / double-click check
        if card.status == "confirmed":
            return {
                "status": "already_confirmed",
                "card_id": card.card_id,
                "query_audit_id": card.linked_query_audit_id,
            }

        if card.status != "open":
            raise ValueError(f"Card '{card_id}' cannot be confirmed in status '{card.status}'")

        # Atomic CAS: mark confirmed
        card.status = "confirmed"
        self.store.save_card(card)

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
        item_key: str | None = None,
    ) -> dict[str, Any]:
        """Process employee review for a profile diff proposal card (§14.5).

        Actions:
        - 'apply': Add item with visibility='public', regenerate embeddings, card -> 'applied'.
        - 'edit_apply': Add edited body with visibility='public', regenerate embeddings, card -> 'applied'.
        - 'private_apply': Add item with visibility='private', regenerate embeddings, card -> 'applied'.
        - 'dismiss': Card -> 'dismissed' (no profile change).
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

        key = item_key or card.payload.get("item_key", "current_work")
        body = (
            edited_body.strip()
            if (action == "edit_apply" and edited_body and edited_body.strip())
            else str(card.payload.get("body_draft", "")).strip()
        )
        visibility = "private" if action == "private_apply" else "public"

        # Update profile
        prof = self.store.get_profile(card.owner_employee_id)
        if prof is None:
            prof = Profile(
                employee_id=card.owner_employee_id,
                name=card.owner_employee_id,
                role="Employee",
                items=[],
            )

        # Check if item with same key already exists
        existing_item = prof.get_item(key)
        if existing_item is not None:
            existing_item.body = body
            existing_item.source = "mail_seed"
            existing_item.visibility = visibility
            existing_item.reviewed = True
        else:
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
        """Dismiss a secretary card (§14.2)."""
        card = self.store.get_card(card_id)
        if card is None:
            raise ValueError(f"Card '{card_id}' not found")
        card.status = "dismissed"
        card.updated_at = utc_now_iso()
        self.store.save_card(card)
        return {"status": "dismissed", "card_id": card_id}
