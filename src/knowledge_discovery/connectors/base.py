"""Source connector interface (design.md §16.3, FR28〜30).

`SourceConnector.fetch()` is the data-source differentiation point: it takes an
owner and today's date and returns a `FetchResult` describing what that owner's
external tools currently look like. It has no side effects — it never touches
`Store`. `apply_fetch_result` reconciles a `FetchResult` into `Store` (upsert,
destructive delete under the completeness barrier, `reschedule_count`
bookkeeping, etc.) per §16.3「フィールドの所有」「取得と reconciliation」.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from knowledge_discovery.models import MailSeed, Schedule, Task, utc_now_iso


@dataclass
class TaskRecord:
    """One task as reported by a connector, pre-write to the `Task` model.

    Only the fields a connector owns (§16.3「フィールドの所有」) live here:
    title / description / due_date / status / last_updated_at / source_id.
    `reschedule_count`, `created_at`, and `status_changed_at` are the
    secretary's own bookkeeping and are computed by `apply_fetch_result`
    against the previously stored record — a connector never sees them.

    Attributes:
        source_id: Stable external key (e.g. `gws_task_<listId>_<taskId>`),
            used as the `Task.task_id` when applied.
        title: Task title.
        description: Task notes/description.
        due_date: Due date normalized to UTC `YYYY-MM-DD`, or None if unset.
        status: `"todo"` or `"done"` (connector-side; `"in_progress"` is a
            secretary/user-only state and never produced by a connector).
        last_updated_at: ISO8601 timestamp the source reports as its own
            last-modified time (e.g. Tasks API `updated`).
    """

    source_id: str
    title: str
    description: str = ""
    due_date: str | None = None
    status: str = "todo"
    last_updated_at: str = ""


@dataclass
class ScheduleRecord:
    """One schedule reminder as reported by a connector.

    Attributes:
        source_id: Stable external key (e.g. `gws_cal_<eventId>_<kind>`),
            used as the `Schedule.item_id` when applied.
        kind: `"meeting_prep"` | `"meeting_review"` (the only kinds a
            connector produces; `expense_deadline` / `weekly_report` /
            `monthly_report` / `journal` stay seed/rule-driven per §16.3).
        title: Schedule title (e.g. the calendar event summary).
        due_date: Due date in `YYYY-MM-DD`.
    """

    source_id: str
    kind: str
    title: str
    due_date: str


@dataclass
class MailRecord:
    """One mail seed as reported by a connector (Gmail adapter, part D).

    Attributes:
        source_id: Stable external key (e.g. `gws_mail_<msgId>`), used as
            the `MailSeed.mail_id` when applied.
        subject: Mail subject, already truncated to 80 chars by the connector.
        body: Mail body, already truncated to `GWS_MAIL_BODY_CHARS` chars.
        received_at: ISO8601 timestamp the source reports.
    """

    source_id: str
    subject: str
    body: str
    received_at: str = ""


@dataclass
class SyncSummary:
    """Aggregate sync outcome for logs / `scripts/gws_probe.py` output.

    Counts only — never carries titles, subjects, or bodies (§16.3 failure
    handling: credentials and content must not appear in error text either).
    """

    tasks: int = 0
    schedules: int = 0
    mails: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    complete: bool = True


@dataclass
class FetchResult:
    """Return value of `SourceConnector.fetch()`.

    Attributes:
        tasks: Tasks fetched from the source.
        schedules: Schedule reminders fetched from the source.
        mails: Mail seeds fetched from the source (empty unless the Gmail
            adapter is enabled).
        complete: True only if every page of every underlying resource
            (task lists, calendar events, gmail messages) was fetched
            successfully. Callers must only perform destructive
            reconciliation (marking missing `source="gws"` tasks done,
            deleting out-of-window/cancelled `source="gws"` schedules) when
            `complete` is True; otherwise apply upserts only (§16.3
            completeness barrier).
        errors: Human-readable failure descriptions (counts/HTTP status/
            resource name only — never credentials or content).
        cancelled_ids: Calendar event ids the source reports as
            `status == "cancelled"` within the fetched window, for callers
            to reconcile away the corresponding `source="gws"` schedule.
    """

    tasks: list[TaskRecord] = field(default_factory=list)
    schedules: list[ScheduleRecord] = field(default_factory=list)
    mails: list[MailRecord] = field(default_factory=list)
    complete: bool = True
    errors: list[str] = field(default_factory=list)
    cancelled_ids: list[str] = field(default_factory=list)


class SourceConnector(abc.ABC):
    """The data-source differentiation point (§16.3).

    Implementations fetch one owner's external data and return it as a
    `FetchResult`. They must not write to `Store` — reconciliation is the
    caller's responsibility (`apply_fetch_result`, in `secretary.run_sweep`'s
    sync-then-detect step).
    """

    @abc.abstractmethod
    def fetch(self, owner_employee_id: str, today: str) -> FetchResult:
        """Fetch `owner_employee_id`'s current external data.

        Args:
            owner_employee_id: The employee whose data to fetch.
            today: Reference date as `YYYY-MM-DD` (from `DEMO_TODAY` / the
                sweep's `demo_today`), used for the Calendar window.

        Returns:
            A `FetchResult`. On partial failure (e.g. one task list's
            pages fail), still returns whatever was fetched successfully,
            with `complete=False` and the failure reasons appended to
            `errors`.
        """
        raise NotImplementedError


def apply_fetch_result(
    store: Any, owner_employee_id: str, result: FetchResult, today: str
) -> SyncSummary:
    """Reconcile a `FetchResult` into `Store` (§16.3「フィールドの所有」「取得と reconciliation」).

    Field ownership (C-39/C-41/C-44):
        - Connector-owned (overwritten verbatim from the fetched record every
          sync): title / description / due_date / status / last_updated_at /
          source="gws" / last_seen_due.
        - Secretary-owned (computed here against the previously stored
          record, never touched by the connector): reschedule_count,
          created_at, status_changed_at.
        - On first sync: created_at = this sync's timestamp, status_changed_at
          = the source's own last_updated_at, last_seen_due is set (not
          compared), reschedule_count stays 0.
        - On a resync: reschedule_count += 1 only if the fetched due_date
          differs from the stored last_seen_due; status_changed_at is bumped
          to this sync's timestamp only if status actually changed.

    Completeness barrier (W-3): destructive reconciliation — marking a
    missing `source="gws"` task `"done"`, deleting an out-of-window or
    `cancelled_ids` `source="gws"` schedule — runs only when `result.complete`
    is True. Otherwise only upserts are applied.

    Mail seeds (part D): existing `mail_id`s are never re-inserted (append-
    only; no destructive reconciliation for messages that vanish from
    Gmail — "メールは消滅を同期しない").

    Args:
        store: The `Store` implementation to write into.
        owner_employee_id: The employee these records belong to.
        result: The `FetchResult` to reconcile.
        today: Reference date as `YYYY-MM-DD` (unused directly here beyond
            documenting the sync's reference date; the actual sync timestamp
            used for created_at/status_changed_at is captured once below so
            every record touched by this call agrees on "now").

    Returns:
        A `SyncSummary` describing what was written (counts only — never
        titles, subjects, or bodies).
    """
    sync_time = utc_now_iso()
    summary = SyncSummary()

    # -- Tasks ---------------------------------------------------------
    fetched_task_ids: set[str] = set()
    for record in result.tasks:
        fetched_task_ids.add(record.source_id)
        existing = store.get_task(record.source_id)
        if existing is None or existing.owner_employee_id != owner_employee_id:
            task = Task(
                task_id=record.source_id,
                owner_employee_id=owner_employee_id,
                title=record.title,
                description=record.description,
                status=record.status,
                due_date=record.due_date or "",
                created_at=sync_time,
                last_updated_at=record.last_updated_at,
                reschedule_count=0,
                status_changed_at=record.last_updated_at,
                source="gws",
                last_seen_due=record.due_date,
            )
        else:
            task = existing
            if task.last_seen_due != record.due_date:
                task.reschedule_count += 1
            if task.status != record.status:
                task.status_changed_at = sync_time
            task.title = record.title
            task.description = record.description
            task.due_date = record.due_date or ""
            task.status = record.status
            task.last_updated_at = record.last_updated_at
            task.source = "gws"
            task.last_seen_due = record.due_date
        store.save_task(task)
        summary.tasks += 1

    if result.complete:
        for existing_task in store.list_tasks(owner_employee_id=owner_employee_id, source="gws"):
            if existing_task.task_id in fetched_task_ids or existing_task.status == "done":
                continue
            existing_task.status = "done"
            existing_task.status_changed_at = sync_time
            store.save_task(existing_task)

    # -- Schedules -------------------------------------------------------
    fetched_schedule_ids: set[str] = set()
    for srecord in result.schedules:
        fetched_schedule_ids.add(srecord.source_id)
        schedule = Schedule(
            item_id=srecord.source_id,
            owner_employee_id=owner_employee_id,
            kind=srecord.kind,
            title=srecord.title,
            due_date=srecord.due_date,
            source="gws",
        )
        store.save_schedule(schedule)
        summary.schedules += 1

    if result.complete:
        # A source="gws" schedule this sync didn't return is either
        # out-of-window or explicitly reported cancelled (cancelled events
        # never appear in result.schedules) — either way, delete it.
        for existing_schedule in store.list_schedules(
            owner_employee_id=owner_employee_id, source="gws"
        ):
            if existing_schedule.item_id not in fetched_schedule_ids:
                store.delete_schedule(existing_schedule.item_id)

    # -- Mail (part D) -----------------------------------------------------
    for mrecord in result.mails:
        if store.get_mail_seed(mrecord.source_id) is not None:
            summary.skipped += 1
            continue
        mail = MailSeed(
            mail_id=mrecord.source_id,
            owner_employee_id=owner_employee_id,
            subject=mrecord.subject,
            body=mrecord.body,
            received_at=mrecord.received_at or sync_time,
            processed=False,
        )
        store.save_mail_seed(mail)
        summary.mails += 1

    summary.errors = list(result.errors)
    summary.complete = result.complete
    return summary
