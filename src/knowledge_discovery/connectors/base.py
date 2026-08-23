"""Source connector interface (design.md §16.3, FR28〜30).

`SourceConnector.fetch()` is the data-source differentiation point: it takes an
owner and today's date and returns a `FetchResult` describing what that owner's
external tools currently look like. It has no side effects — it never touches
`Store`. Reconciling a `FetchResult` against `Store` (upsert, destructive
delete under the completeness barrier, `reschedule_count` bookkeeping, etc.) is
`apply_fetch_result`'s job, implemented in the follow-up integration step (C-2)
once `models.py` gains `Task.source` / `Task.last_seen_due` / `Schedule.source`
and `Store` gains identity/deletion/filter APIs. This module only fixes that
function's signature and contract so C-2 has a stable target to implement
against.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


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
    """Reconcile a `FetchResult` into `Store` (§16.3). Implemented in C-2.

    This is deliberately unimplemented in the current step (part C, pure
    connector layer only). It is wired up once `models.py` gains
    `Task.source` / `Task.last_seen_due` / `Schedule.source` and `Store`
    gains identities / deletion / `list_tasks(source=)` (§16.3「モデル拡張」).

    Intended contract for C-2:
        - Upsert every `TaskRecord` / `ScheduleRecord` / `MailRecord` by its
          `source_id`, writing only the connector-owned fields verbatim.
        - `reschedule_count` increments only when the fetched `due_date`
          differs from the stored `last_seen_due` on a record that already
          existed before this sync (never on first sync — first sync only
          sets `last_seen_due`).
        - `created_at` is set once, at first sync. `status_changed_at` is
          set to the source's `last_updated_at` at first sync, and to the
          sync time whenever `status` changes thereafter.
        - Destructive reconciliation (marking missing `source="gws"` tasks
          `"done"`; deleting out-of-window or `cancelled_ids` `source="gws"`
          schedules) happens only when `result.complete` is True. Otherwise
          only upserts are applied.
        - Mail seeds: append-only (no destructive reconciliation); existing
          `mail_id`s are never re-inserted.

        Args:
            store: The `Store` implementation to write into.
            owner_employee_id: The employee these records belong to.
            result: The `FetchResult` to reconcile.
            today: Reference date as `YYYY-MM-DD`.

        Returns:
            A `SyncSummary` describing what was written.
    """
    raise NotImplementedError(
        "apply_fetch_result is implemented in C-2, after models.py gains "
        "Task.source/Task.last_seen_due/Schedule.source and Store gains "
        "identities/deletion/list_tasks(source=) per design.md §16.3"
    )
