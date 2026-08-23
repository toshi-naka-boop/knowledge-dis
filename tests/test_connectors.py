"""Unit tests for knowledge_discovery.connectors (design.md §16.3, part C/D).

No network access: GoogleWorkspaceConnector is driven by a fake HTTP session
that maps (url, params) to canned JSON responses, including paginated
sequences and injected failures. Covers:
- Tasks: list/page mapping, idempotent field mapping, completeness barrier
  on a mid-pagination failure.
- Calendar: meeting_prep / meeting_review window classification, cancelled
  event reporting, completeness barrier.
- Gmail (part D): disabled by default, label-not-found -> zero mails (not an
  error), pagination up to the configured cap, subject/body truncation, and
  that logs never carry subject/body content (assertLogs on counts only).
- apply_fetch_result (C-2): field ownership (connector- vs secretary-owned),
  first-sync non-accrual, reschedule accrual on due change, completeness
  barrier for both Tasks (done) and Calendar (delete), and mail_id dedup.
"""

from __future__ import annotations

import base64
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from knowledge_discovery.connectors.base import (
    FetchResult,
    MailRecord,
    ScheduleRecord,
    SourceConnector,
    TaskRecord,
    apply_fetch_result,
)
from knowledge_discovery.connectors.google_workspace import (
    CALENDAR_BASE,
    GMAIL_BASE,
    TASKS_BASE,
    GoogleWorkspaceConnector,
)
from knowledge_discovery.connectors.seed import SeedConnector
from knowledge_discovery.models import Schedule
from knowledge_discovery.store import InMemoryStore


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class FakeResponse:
    def __init__(self, status_code: int, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    """Routes `.get(url, params)` to a per-URL handler; records every call."""

    def __init__(self, handlers: dict):
        self._handlers = handlers
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params=None):
        params = dict(params or {})
        self.calls.append((url, params))
        handler = self._handlers.get(url)
        if handler is None:
            raise AssertionError(f"FakeSession: no handler registered for {url}")
        return handler(params)


def paged_handler(pages: list[tuple[list, bool]], items_key: str):
    """pages[i] = (items, has_next_page). Pages are selected by sequential pageToken."""

    def handler(params: dict):
        token = params.get("pageToken")
        idx = 0 if token is None else int(token)
        items, has_next = pages[idx]
        data = {items_key: items}
        if has_next:
            data["nextPageToken"] = str(idx + 1)
        return FakeResponse(200, data)

    return handler


def failing_handler(status_code: int = 500):
    def handler(params: dict):
        return FakeResponse(status_code, {})

    return handler


class TestSeedConnector(unittest.TestCase):
    def test_fetch_is_empty_and_complete(self) -> None:
        connector = SeedConnector()
        self.assertIsInstance(connector, SourceConnector)
        result = connector.fetch("emp_anyone", "2026-08-24")
        self.assertEqual(result, FetchResult(complete=True))
        self.assertEqual(result.tasks, [])
        self.assertEqual(result.schedules, [])
        self.assertEqual(result.mails, [])
        self.assertTrue(result.complete)
        self.assertEqual(result.errors, [])


class GwsConnectorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)
        # Gmail is opt-in and off by default in most tests; individual tests turn it on.
        os.environ.pop("GWS_GMAIL_ENABLED", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def build_session(self, tasks_handlers=None, calendar_handler=None, gmail_handlers=None):
        handlers = {}
        handlers[f"{TASKS_BASE}/users/@me/lists"] = (tasks_handlers or {}).get(
            "lists", paged_handler([([], False)], "items")
        )
        for list_id, handler in (tasks_handlers or {}).get("per_list", {}).items():
            handlers[f"{TASKS_BASE}/lists/{list_id}/tasks"] = handler
        handlers[f"{CALENDAR_BASE}/calendars/primary/events"] = calendar_handler or paged_handler(
            [([], False)], "items"
        )
        if gmail_handlers:
            if "labels" in gmail_handlers:
                handlers[f"{GMAIL_BASE}/users/me/labels"] = gmail_handlers["labels"]
            if "messages_list" in gmail_handlers:
                handlers[f"{GMAIL_BASE}/users/me/messages"] = gmail_handlers["messages_list"]
            for msg_id, handler in gmail_handlers.get("messages_get", {}).items():
                handlers[f"{GMAIL_BASE}/users/me/messages/{msg_id}"] = handler
        return FakeSession(handlers)


class TestTasksMapping(GwsConnectorTestBase):
    def test_maps_fields_and_paginates_lists_and_tasks(self) -> None:
        lists_handler = paged_handler(
            [([{"id": "list1"}, {"id": "list2"}], False)], "items"
        )
        list1_tasks_handler = paged_handler(
            [
                (
                    [
                        {
                            "id": "t1",
                            "title": "Write report",
                            "notes": "quarterly numbers",
                            "due": "2026-08-25T00:00:00.000Z",
                            "status": "needsAction",
                            "updated": "2026-08-20T10:00:00.000Z",
                        }
                    ],
                    True,
                ),
                (
                    [
                        {
                            "id": "t2",
                            "title": "Done task",
                            "status": "completed",
                            "updated": "2026-08-19T09:00:00.000Z",
                        }
                    ],
                    False,
                ),
            ],
            "items",
        )
        list2_tasks_handler = paged_handler([([], False)], "items")
        session = self.build_session(
            tasks_handlers={
                "lists": lists_handler,
                "per_list": {"list1": list1_tasks_handler, "list2": list2_tasks_handler},
            }
        )
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_jordan_lee", "2026-08-24")

        self.assertTrue(result.complete)
        self.assertEqual(result.errors, [])
        by_id = {t.source_id: t for t in result.tasks}
        self.assertEqual(
            by_id["gws_task_list1_t1"].title, "Write report"
        )
        self.assertEqual(by_id["gws_task_list1_t1"].description, "quarterly numbers")
        self.assertEqual(by_id["gws_task_list1_t1"].due_date, "2026-08-25")
        self.assertEqual(by_id["gws_task_list1_t1"].status, "todo")
        self.assertEqual(by_id["gws_task_list1_t1"].last_updated_at, "2026-08-20T10:00:00.000Z")
        self.assertEqual(by_id["gws_task_list1_t2"].status, "done")
        self.assertIsNone(by_id["gws_task_list1_t2"].due_date)

    def test_no_due_date_maps_to_none(self) -> None:
        lists_handler = paged_handler([([{"id": "list1"}], False)], "items")
        tasks_handler = paged_handler(
            [([{"id": "t1", "title": "No due", "status": "needsAction", "updated": "x"}], False)],
            "items",
        )
        session = self.build_session(
            tasks_handlers={"lists": lists_handler, "per_list": {"list1": tasks_handler}}
        )
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertIsNone(result.tasks[0].due_date)

    def test_mid_pagination_failure_sets_complete_false_but_keeps_partial_results(self) -> None:
        lists_handler = paged_handler([([{"id": "list1"}, {"id": "list2"}], False)], "items")

        def list1_handler(params):
            token = params.get("pageToken")
            if token is None:
                return FakeResponse(
                    200,
                    {
                        "items": [
                            {"id": "t1", "title": "Page one task", "status": "needsAction", "updated": "u1"}
                        ],
                        "nextPageToken": "1",
                    },
                )
            return FakeResponse(500, {})

        list2_handler = paged_handler(
            [([{"id": "t2", "title": "List two task", "status": "needsAction", "updated": "u2"}], False)],
            "items",
        )
        session = self.build_session(
            tasks_handlers={
                "lists": lists_handler,
                "per_list": {"list1": list1_handler, "list2": list2_handler},
            }
        )
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")

        self.assertFalse(result.complete)
        self.assertTrue(any("tasks.list[list1]" in e for e in result.errors))
        source_ids = {t.source_id for t in result.tasks}
        # Partial data from the failed list's first page, plus the other list's full data.
        self.assertIn("gws_task_list1_t1", source_ids)
        self.assertIn("gws_task_list2_t2", source_ids)


class TestCalendarMapping(GwsConnectorTestBase):
    def test_window_classification_and_cancelled(self) -> None:
        today = "2026-08-24"
        events = [
            {
                "id": "ev_future",
                "summary": "Future meeting",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-25T09:00:00Z"},
                "end": {"dateTime": "2026-08-25T10:00:00Z"},
            },
            {
                "id": "ev_yesterday",
                "summary": "Yesterday meeting",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-23T09:00:00Z"},
                "end": {"dateTime": "2026-08-23T10:00:00Z"},
            },
            {
                "id": "ev_today_ended",
                "summary": "Already-ended today meeting",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-24T01:00:00Z"},
                "end": {"dateTime": "2000-01-01T00:00:00Z"},  # far in the past -> ended
            },
            {
                "id": "ev_today_upcoming",
                "summary": "Not-yet-ended today meeting",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-24T01:00:00Z"},
                "end": {"dateTime": "2999-01-01T00:00:00Z"},  # far future -> not ended
            },
            {
                "id": "ev_cancelled",
                "summary": "Cancelled meeting",
                "status": "cancelled",
                "start": {"dateTime": "2026-08-25T09:00:00Z"},
                "end": {"dateTime": "2026-08-25T10:00:00Z"},
            },
        ]
        calendar_handler = paged_handler([(events, False)], "items")
        session = self.build_session(calendar_handler=calendar_handler)
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", today)

        self.assertTrue(result.complete)
        by_id = {s.source_id: s for s in result.schedules}
        self.assertEqual(by_id["gws_cal_ev_future_meeting_prep"].kind, "meeting_prep")
        self.assertEqual(by_id["gws_cal_ev_yesterday_meeting_review"].kind, "meeting_review")
        self.assertEqual(by_id["gws_cal_ev_today_ended_meeting_review"].kind, "meeting_review")
        self.assertEqual(by_id["gws_cal_ev_today_upcoming_meeting_prep"].kind, "meeting_prep")
        self.assertEqual(result.cancelled_ids, ["ev_cancelled"])
        self.assertEqual(len(result.schedules), 4)

    def test_days_ahead_env_extends_window(self) -> None:
        os.environ["GWS_CAL_DAYS_AHEAD"] = "1"
        events = [
            {
                "id": "ev_out_of_window",
                "summary": "Two days out",
                "status": "confirmed",
                "start": {"dateTime": "2026-08-26T09:00:00Z"},
                "end": {"dateTime": "2026-08-26T10:00:00Z"},
            }
        ]
        calendar_handler = paged_handler([(events, False)], "items")
        session = self.build_session(calendar_handler=calendar_handler)
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertEqual(result.schedules, [])

    def test_pagination_failure_sets_complete_false(self) -> None:
        def handler(params):
            if params.get("pageToken") is None:
                return FakeResponse(200, {"items": [], "nextPageToken": "1"})
            return FakeResponse(503, {})

        session = self.build_session(calendar_handler=handler)
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertFalse(result.complete)
        self.assertTrue(any("calendar.events" in e for e in result.errors))


class TestGmailConnector(GwsConnectorTestBase):
    def test_disabled_by_default_makes_no_gmail_calls(self) -> None:
        session = self.build_session()
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertEqual(result.mails, [])
        self.assertTrue(result.complete)
        gmail_calls = [c for c in session.calls if GMAIL_BASE in c[0]]
        self.assertEqual(gmail_calls, [])

    def test_label_not_found_returns_zero_mails_not_an_error(self) -> None:
        os.environ["GWS_GMAIL_ENABLED"] = "true"
        labels_handler = paged_handler(
            [([{"id": "Label_1", "name": "some-other-label"}], False)], "labels"
        )
        session = self.build_session(gmail_handlers={"labels": labels_handler})
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertEqual(result.mails, [])
        self.assertTrue(result.complete)
        self.assertEqual(result.errors, [])

    def test_mapping_pagination_cap_and_truncation(self) -> None:
        os.environ["GWS_GMAIL_ENABLED"] = "true"
        os.environ["GWS_GMAIL_MAX_RESULTS"] = "3"
        os.environ["GWS_MAIL_BODY_CHARS"] = "10"

        labels_handler = paged_handler(
            [([{"id": "Label_kd", "name": "kd-secretary"}], False)], "labels"
        )

        def messages_list_handler(params):
            token = params.get("pageToken")
            if token is None:
                return FakeResponse(
                    200, {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "1"}
                )
            # A 4th message exists upstream, but max_results=3 should cap us at m3.
            return FakeResponse(200, {"messages": [{"id": "m3"}, {"id": "m4"}]})

        long_subject = "S" * 120
        long_body_plain = "This body is definitely longer than ten characters."

        def make_message_handler(subject: str, body_text: str):
            def handler(params):
                return FakeResponse(
                    200,
                    {
                        "internalDate": "1690000000000",
                        "payload": {
                            "mimeType": "multipart/alternative",
                            "headers": [{"name": "Subject", "value": subject}],
                            "parts": [
                                {
                                    "mimeType": "text/plain",
                                    "body": {"data": _b64url(body_text)},
                                },
                                {
                                    "mimeType": "text/html",
                                    "body": {"data": _b64url(f"<p>{body_text}</p>")},
                                },
                            ],
                        },
                    },
                )

            return handler

        session = self.build_session(
            gmail_handlers={
                "labels": labels_handler,
                "messages_list": messages_list_handler,
                "messages_get": {
                    "m1": make_message_handler(long_subject, long_body_plain),
                    "m2": make_message_handler("short", long_body_plain),
                    "m3": make_message_handler("m3 subject", long_body_plain),
                },
            }
        )
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")

        self.assertTrue(result.complete)
        self.assertEqual(len(result.mails), 3)  # capped at GWS_GMAIL_MAX_RESULTS
        by_id = {m.source_id: m for m in result.mails}
        self.assertIn("gws_mail_m1", by_id)
        self.assertIn("gws_mail_m2", by_id)
        self.assertIn("gws_mail_m3", by_id)
        self.assertNotIn("gws_mail_m4", by_id)
        self.assertEqual(len(by_id["gws_mail_m1"].subject), 80)
        self.assertEqual(by_id["gws_mail_m1"].subject, long_subject[:80])
        self.assertEqual(by_id["gws_mail_m1"].body, long_body_plain[:10])
        self.assertEqual(len(by_id["gws_mail_m1"].body), 10)

    def test_messages_get_failure_sets_complete_false(self) -> None:
        os.environ["GWS_GMAIL_ENABLED"] = "true"
        labels_handler = paged_handler(
            [([{"id": "Label_kd", "name": "kd-secretary"}], False)], "labels"
        )
        messages_list_handler = paged_handler([([{"id": "m1"}], False)], "messages")
        session = self.build_session(
            gmail_handlers={
                "labels": labels_handler,
                "messages_list": messages_list_handler,
                "messages_get": {"m1": failing_handler(500)},
            }
        )
        connector = GoogleWorkspaceConnector(session)
        result = connector.fetch("emp_x", "2026-08-24")
        self.assertFalse(result.complete)
        self.assertTrue(any("gmail.messages.get" in e for e in result.errors))

    def test_logs_never_contain_subject_or_body(self) -> None:
        os.environ["GWS_GMAIL_ENABLED"] = "true"
        secret_subject = "CONFIDENTIAL SUBJECT MARKER"
        secret_body = "CONFIDENTIAL BODY MARKER"
        labels_handler = paged_handler(
            [([{"id": "Label_kd", "name": "kd-secretary"}], False)], "labels"
        )
        messages_list_handler = paged_handler([([{"id": "m1"}], False)], "messages")

        def message_handler(params):
            return FakeResponse(
                200,
                {
                    "internalDate": "1690000000000",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [{"name": "Subject", "value": secret_subject}],
                        "body": {"data": _b64url(secret_body)},
                    },
                },
            )

        session = self.build_session(
            gmail_handlers={
                "labels": labels_handler,
                "messages_list": messages_list_handler,
                "messages_get": {"m1": message_handler},
            }
        )
        connector = GoogleWorkspaceConnector(session)
        with self.assertLogs("knowledge_discovery.connectors.google_workspace", level="INFO") as cm:
            result = connector.fetch("emp_x", "2026-08-24")
        self.assertEqual(len(result.mails), 1)
        combined_log_output = "\n".join(cm.output)
        self.assertNotIn(secret_subject, combined_log_output)
        self.assertNotIn(secret_body, combined_log_output)


class TestDefaultSession(unittest.TestCase):
    def test_default_session_uses_google_auth_default(self) -> None:
        from knowledge_discovery.connectors import google_workspace

        fake_credentials = object()
        with mock.patch("google.auth.default", return_value=(fake_credentials, "proj")) as m_default, \
            mock.patch("google.auth.transport.requests.AuthorizedSession") as m_session:
            google_workspace.default_session()
            m_default.assert_called_once()
            called_scopes = m_default.call_args.kwargs.get("scopes")
            self.assertIn(
                "https://www.googleapis.com/auth/tasks.readonly", called_scopes
            )
            self.assertIn(
                "https://www.googleapis.com/auth/calendar.readonly", called_scopes
            )
            self.assertIn(
                "https://www.googleapis.com/auth/gmail.readonly", called_scopes
            )
            m_session.assert_called_once_with(fake_credentials)


class TestApplyFetchResult(unittest.TestCase):
    """apply_fetch_result (C-2): field ownership, reconciliation, mail dedup."""

    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.owner = "emp_x"
        self.today = "2026-08-24"

    def test_first_sync_upserts_task_and_does_not_accrue_reschedule(self) -> None:
        result = FetchResult(
            tasks=[
                TaskRecord(
                    source_id="gws_task_l1_t1",
                    title="Write report",
                    description="notes",
                    due_date="2026-08-25",
                    status="todo",
                    last_updated_at="2026-08-20T10:00:00Z",
                )
            ],
            complete=True,
        )
        summary = apply_fetch_result(self.store, self.owner, result, self.today)
        self.assertEqual(summary.tasks, 1)
        self.assertEqual(summary.errors, [])
        self.assertTrue(summary.complete)

        task = self.store.get_task("gws_task_l1_t1")
        self.assertIsNotNone(task)
        self.assertEqual(task.owner_employee_id, self.owner)
        self.assertEqual(task.title, "Write report")
        self.assertEqual(task.due_date, "2026-08-25")
        self.assertEqual(task.status, "todo")
        self.assertEqual(task.source, "gws")
        self.assertEqual(task.last_seen_due, "2026-08-25")
        # First sync: no reschedule to accrue, only bookkeeping is set.
        self.assertEqual(task.reschedule_count, 0)
        self.assertEqual(task.last_updated_at, "2026-08-20T10:00:00Z")
        self.assertEqual(task.status_changed_at, "2026-08-20T10:00:00Z")

    def test_resync_with_same_due_date_does_not_increment_reschedule(self) -> None:
        record = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="todo",
            last_updated_at="2026-08-20T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[record], complete=True), self.today)
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[record], complete=True), self.today)
        task = self.store.get_task("gws_task_l1_t1")
        self.assertEqual(task.reschedule_count, 0)

    def test_resync_with_changed_due_date_increments_reschedule_once(self) -> None:
        first = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="todo",
            last_updated_at="2026-08-20T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[first], complete=True), self.today)

        rescheduled = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-28",
            status="todo",
            last_updated_at="2026-08-21T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[rescheduled], complete=True), self.today)
        task = self.store.get_task("gws_task_l1_t1")
        self.assertEqual(task.reschedule_count, 1)
        self.assertEqual(task.last_seen_due, "2026-08-28")
        self.assertEqual(task.last_updated_at, "2026-08-21T10:00:00Z")

        # A further resync with the same (new) due date must not increment again.
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[rescheduled], complete=True), self.today)
        self.assertEqual(self.store.get_task("gws_task_l1_t1").reschedule_count, 1)

    def test_status_change_updates_status_changed_at(self) -> None:
        todo = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="todo",
            last_updated_at="2026-08-20T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[todo], complete=True), self.today)
        original_status_changed_at = self.store.get_task("gws_task_l1_t1").status_changed_at

        done = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="done",
            last_updated_at="2026-08-22T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[done], complete=True), self.today)
        task = self.store.get_task("gws_task_l1_t1")
        self.assertEqual(task.status, "done")
        self.assertNotEqual(task.status_changed_at, original_status_changed_at)

    def test_task_missing_from_complete_fetch_is_marked_done(self) -> None:
        record = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="todo",
            last_updated_at="2026-08-20T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[record], complete=True), self.today)
        # Next sync no longer reports this task, but is complete.
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[], complete=True), self.today)
        task = self.store.get_task("gws_task_l1_t1")
        self.assertEqual(task.status, "done")

    def test_task_missing_from_incomplete_fetch_is_not_marked_done(self) -> None:
        record = TaskRecord(
            source_id="gws_task_l1_t1",
            title="Write report",
            due_date="2026-08-25",
            status="todo",
            last_updated_at="2026-08-20T10:00:00Z",
        )
        apply_fetch_result(self.store, self.owner, FetchResult(tasks=[record], complete=True), self.today)
        # Next sync fails partway through: no destructive reconciliation.
        apply_fetch_result(
            self.store,
            self.owner,
            FetchResult(tasks=[], complete=False, errors=["tasks.list: HTTP 500"]),
            self.today,
        )
        task = self.store.get_task("gws_task_l1_t1")
        self.assertEqual(task.status, "todo")

    def test_calendar_out_of_window_schedule_deleted_on_complete_sync(self) -> None:
        first = ScheduleRecord(
            source_id="gws_cal_ev1_meeting_prep", kind="meeting_prep", title="Kickoff", due_date="2026-08-25"
        )
        apply_fetch_result(self.store, self.owner, FetchResult(schedules=[first], complete=True), self.today)
        self.assertIsNotNone(self._find_schedule("gws_cal_ev1_meeting_prep"))

        # The event has scrolled out of the fetch window this sync.
        apply_fetch_result(self.store, self.owner, FetchResult(schedules=[], complete=True), self.today)
        self.assertIsNone(self._find_schedule("gws_cal_ev1_meeting_prep"))

    def test_calendar_cancelled_event_schedule_deleted_on_complete_sync(self) -> None:
        first = ScheduleRecord(
            source_id="gws_cal_ev1_meeting_prep", kind="meeting_prep", title="Kickoff", due_date="2026-08-25"
        )
        apply_fetch_result(self.store, self.owner, FetchResult(schedules=[first], complete=True), self.today)

        apply_fetch_result(
            self.store,
            self.owner,
            FetchResult(schedules=[], cancelled_ids=["ev1"], complete=True),
            self.today,
        )
        self.assertIsNone(self._find_schedule("gws_cal_ev1_meeting_prep"))

    def test_calendar_reconciliation_skipped_on_incomplete_sync(self) -> None:
        first = ScheduleRecord(
            source_id="gws_cal_ev1_meeting_prep", kind="meeting_prep", title="Kickoff", due_date="2026-08-25"
        )
        apply_fetch_result(self.store, self.owner, FetchResult(schedules=[first], complete=True), self.today)
        apply_fetch_result(
            self.store,
            self.owner,
            FetchResult(schedules=[], complete=False, errors=["calendar.events: HTTP 503"]),
            self.today,
        )
        self.assertIsNotNone(self._find_schedule("gws_cal_ev1_meeting_prep"))

    def test_existing_mail_id_is_not_reinserted(self) -> None:
        record = MailRecord(source_id="gws_mail_m1", subject="Hi", body="Body text", received_at="2026-08-20T00:00:00Z")
        summary1 = apply_fetch_result(self.store, self.owner, FetchResult(mails=[record], complete=True), self.today)
        self.assertEqual(summary1.mails, 1)
        self.assertEqual(summary1.skipped, 0)

        summary2 = apply_fetch_result(self.store, self.owner, FetchResult(mails=[record], complete=True), self.today)
        self.assertEqual(summary2.mails, 0)
        self.assertEqual(summary2.skipped, 1)
        # Only one mail_seed exists in the store, not two.
        self.assertEqual(len(self.store.list_mail_seeds(owner_employee_id=self.owner)), 1)

    def test_sync_summary_reflects_errors_and_completeness(self) -> None:
        result = FetchResult(complete=False, errors=["tasks.list: HTTP 500"])
        summary = apply_fetch_result(self.store, self.owner, result, self.today)
        self.assertFalse(summary.complete)
        self.assertEqual(summary.errors, ["tasks.list: HTTP 500"])

    def _find_schedule(self, item_id: str) -> Schedule | None:
        for s in self.store.list_schedules(owner_employee_id=self.owner):
            if s.item_id == item_id:
                return s
        return None


if __name__ == "__main__":
    unittest.main()
