"""Google Workspace connector: Tasks / Calendar / Gmail over REST (§16.3).

Read-only scopes only. `session` is injected (an `AuthorizedSession`-compatible
object exposing `.get(url, params=...)` returning a `requests.Response`-like
object with `.status_code` / `.json()`); tests inject a fake session so this
module needs no network access. `default_session()` builds the real thing from
`google.auth.default()` for `scripts/gws_probe.py` and production use.

Field mapping and the completeness barrier follow design.md §16.3 exactly:
- Tasks: all task lists, all pages (`nextPageToken`), `showCompleted=true&
  showHidden=true`. `complete=True` only if every list's every page succeeded.
- Calendar: all pages of `primary`'s events in [yesterday, today+N]. Events
  whose date falls in [today, today+N] map to `meeting_prep`; events in
  [yesterday, today) map to `meeting_review`; an event dated today that has
  already ended (its `end.dateTime` is in the past) also maps to
  `meeting_review`. `status == "cancelled"` events are reported via
  `cancelled_ids` instead of `schedules`.
- Gmail (part D, default off via `GWS_GMAIL_ENABLED=false`): only messages
  under the `kd-secretary` label (opt-in), most recent `GWS_GMAIL_DAYS` days,
  up to `GWS_GMAIL_MAX_RESULTS` messages, body truncated to
  `GWS_MAIL_BODY_CHARS` chars (text/plain part preferred), subject truncated
  to 80 chars. Subjects and bodies are never written to `errors` or logs —
  only counts are logged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import os
from typing import Any, Protocol

from .base import FetchResult, MailRecord, ScheduleRecord, SourceConnector, TaskRecord

logger = logging.getLogger(__name__)

TASKS_BASE = "https://tasks.googleapis.com/tasks/v1"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"

GMAIL_LABEL_NAME = "kd-secretary"

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class HttpSession(Protocol):
    """The subset of `AuthorizedSession` / `requests.Session` this module needs."""

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any: ...


def default_session(scopes: tuple[str, ...] = DEFAULT_SCOPES) -> HttpSession:
    """Build a real `AuthorizedSession` from `google.auth.default()` (author ADC).

    Imports `google.auth` lazily so importing this module (e.g. for
    `SourceConnector` typing) never requires ADC to be configured — only
    calling this function does.
    """
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=list(scopes))
    return AuthorizedSession(credentials)


def _rfc3339_to_utc_date(value: str) -> str | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _rfc3339_to_utc_datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _extract_plain_text(payload: dict[str, Any]) -> str:
    """Depth-first search for a `text/plain` MIME part; empty string if none."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    if mime_type == "text/plain" and body.get("data"):
        return _b64url_decode(body["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def _internal_date_to_iso(value: Any) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


@dataclass
class _PageResult:
    items: list[dict[str, Any]]
    complete: bool


class GoogleWorkspaceConnector(SourceConnector):
    """Tasks / Calendar / Gmail connector, driven by an injected HTTP session."""

    def __init__(self, session: HttpSession) -> None:
        self.session = session

    # -- SourceConnector -----------------------------------------------

    def fetch(self, owner_employee_id: str, today: str) -> FetchResult:
        errors: list[str] = []

        tasks, tasks_complete = self._fetch_tasks(errors)
        schedules, cancelled_ids, calendar_complete = self._fetch_calendar(today, errors)
        mails, gmail_complete = self._fetch_gmail(errors)

        complete = tasks_complete and calendar_complete and gmail_complete
        logger.info(
            "gws_connector: owner=%s tasks=%d schedules=%d cancelled=%d mails=%d "
            "complete=%s error_count=%d",
            owner_employee_id,
            len(tasks),
            len(schedules),
            len(cancelled_ids),
            len(mails),
            complete,
            len(errors),
        )
        return FetchResult(
            tasks=tasks,
            schedules=schedules,
            mails=mails,
            complete=complete,
            errors=errors,
            cancelled_ids=cancelled_ids,
        )

    # -- generic paging --------------------------------------------------

    def _paginate(
        self,
        url: str,
        params: dict[str, Any],
        items_key: str,
        context: str,
        errors: list[str],
    ) -> _PageResult:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            request_params = dict(params)
            if page_token:
                request_params["pageToken"] = page_token
            try:
                response = self.session.get(url, params=request_params)
            except Exception as exc:  # noqa: BLE001 - network/transport errors vary by client
                errors.append(f"{context}: request failed ({type(exc).__name__})")
                return _PageResult(items, False)
            status_code = getattr(response, "status_code", None)
            if status_code != 200:
                errors.append(f"{context}: HTTP {status_code}")
                return _PageResult(items, False)
            try:
                data = response.json()
            except Exception:  # noqa: BLE001 - malformed body from a real API
                errors.append(f"{context}: invalid JSON response")
                return _PageResult(items, False)
            items.extend(data.get(items_key) or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                return _PageResult(items, True)

    # -- Tasks -------------------------------------------------------------

    def _fetch_tasks(self, errors: list[str]) -> tuple[list[TaskRecord], bool]:
        lists_result = self._paginate(
            f"{TASKS_BASE}/users/@me/lists", {}, "items", "tasks.lists", errors
        )
        if not lists_result.complete:
            return [], False

        records: list[TaskRecord] = []
        complete = True
        for task_list in lists_result.items:
            list_id = task_list.get("id")
            if not list_id:
                continue
            tasks_result = self._paginate(
                f"{TASKS_BASE}/lists/{list_id}/tasks",
                {"showCompleted": "true", "showHidden": "true", "maxResults": 100},
                "items",
                f"tasks.list[{list_id}]",
                errors,
            )
            if not tasks_result.complete:
                complete = False
            for raw in tasks_result.items:
                records.append(self._map_task(list_id, raw))
        return records, complete

    def _map_task(self, list_id: str, raw: dict[str, Any]) -> TaskRecord:
        due = raw.get("due")
        return TaskRecord(
            source_id=f"gws_task_{list_id}_{raw.get('id', '')}",
            title=raw.get("title", ""),
            description=raw.get("notes", ""),
            due_date=_rfc3339_to_utc_date(due) if due else None,
            status="done" if raw.get("status") == "completed" else "todo",
            last_updated_at=raw.get("updated", ""),
        )

    # -- Calendar ------------------------------------------------------

    def _fetch_calendar(
        self, today: str, errors: list[str]
    ) -> tuple[list[ScheduleRecord], list[str], bool]:
        days_ahead = int(os.environ.get("GWS_CAL_DAYS_AHEAD", "3"))
        today_date = date.fromisoformat(today)
        yesterday = today_date - timedelta(days=1)
        horizon = today_date + timedelta(days=days_ahead)
        now = datetime.now(timezone.utc)

        time_min = datetime.combine(yesterday, datetime.min.time(), tzinfo=timezone.utc)
        time_max = datetime.combine(
            horizon + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        )
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "maxResults": 250,
        }
        events_result = self._paginate(
            f"{CALENDAR_BASE}/calendars/primary/events",
            params,
            "items",
            "calendar.events",
            errors,
        )

        schedules: list[ScheduleRecord] = []
        cancelled_ids: list[str] = []
        for event in events_result.items:
            event_id = event.get("id", "")
            if event.get("status") == "cancelled":
                if event_id:
                    cancelled_ids.append(event_id)
                continue
            record = self._map_calendar_event(event, today_date, yesterday, horizon, now)
            if record is not None:
                schedules.append(record)
        return schedules, cancelled_ids, events_result.complete

    def _map_calendar_event(
        self,
        event: dict[str, Any],
        today_date: date,
        yesterday: date,
        horizon: date,
        now: datetime,
    ) -> ScheduleRecord | None:
        start = event.get("start", {}) or {}
        end = event.get("end", {}) or {}
        is_all_day = "dateTime" not in start
        start_raw = start.get("dateTime") or start.get("date")
        if not start_raw:
            return None
        start_date_str = start_raw if is_all_day else _rfc3339_to_utc_date(start_raw)
        if start_date_str is None:
            return None
        try:
            event_date = date.fromisoformat(start_date_str)
        except ValueError:
            return None

        if yesterday <= event_date < today_date:
            kind = "meeting_review"
        elif today_date <= event_date <= horizon:
            kind = "meeting_prep"
            if event_date == today_date and not is_all_day:
                end_raw = end.get("dateTime")
                end_dt = _rfc3339_to_utc_datetime(end_raw) if end_raw else None
                if end_dt is not None and end_dt <= now:
                    kind = "meeting_review"
        else:
            return None

        event_id = event.get("id", "")
        return ScheduleRecord(
            source_id=f"gws_cal_{event_id}_{kind}",
            kind=kind,
            title=event.get("summary", ""),
            due_date=start_date_str,
        )

    # -- Gmail (part D) --------------------------------------------------

    def _fetch_gmail(self, errors: list[str]) -> tuple[list[MailRecord], bool]:
        if os.environ.get("GWS_GMAIL_ENABLED", "false").lower() != "true":
            return [], True

        label_id, label_ok = self._resolve_gmail_label(errors)
        if not label_ok:
            return [], False
        if label_id is None:
            return [], True  # opt-in label not created yet: zero mails, not an error

        days = int(os.environ.get("GWS_GMAIL_DAYS", "7"))
        max_results = int(os.environ.get("GWS_GMAIL_MAX_RESULTS", "20"))
        body_chars = int(os.environ.get("GWS_MAIL_BODY_CHARS", "2000"))

        message_ids, list_complete = self._list_gmail_message_ids(
            label_id, days, max_results, errors
        )

        records: list[MailRecord] = []
        complete = list_complete
        for msg_id in message_ids:
            try:
                response = self.session.get(
                    f"{GMAIL_BASE}/users/me/messages/{msg_id}", params={"format": "full"}
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"gmail.messages.get: request failed ({type(exc).__name__})")
                complete = False
                continue
            status_code = getattr(response, "status_code", None)
            if status_code != 200:
                errors.append(f"gmail.messages.get: HTTP {status_code}")
                complete = False
                continue
            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                errors.append("gmail.messages.get: invalid JSON response")
                complete = False
                continue
            records.append(self._map_gmail_message(msg_id, data, body_chars))

        logger.info("gws_connector: gmail messages fetched=%d complete=%s", len(records), complete)
        return records, complete

    def _resolve_gmail_label(self, errors: list[str]) -> tuple[str | None, bool]:
        try:
            response = self.session.get(f"{GMAIL_BASE}/users/me/labels")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gmail.labels.list: request failed ({type(exc).__name__})")
            return None, False
        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            errors.append(f"gmail.labels.list: HTTP {status_code}")
            return None, False
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            errors.append("gmail.labels.list: invalid JSON response")
            return None, False
        for label in data.get("labels") or []:
            if label.get("name") == GMAIL_LABEL_NAME:
                return label.get("id"), True
        return None, True

    def _list_gmail_message_ids(
        self, label_id: str, days: int, max_results: int, errors: list[str]
    ) -> tuple[list[str], bool]:
        ids: list[str] = []
        page_token: str | None = None
        query = f"newer_than:{days}d"
        while len(ids) < max_results:
            params: dict[str, Any] = {
                "labelIds": label_id,
                "q": query,
                "maxResults": min(100, max_results - len(ids)),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self.session.get(f"{GMAIL_BASE}/users/me/messages", params=params)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"gmail.messages.list: request failed ({type(exc).__name__})")
                return ids, False
            status_code = getattr(response, "status_code", None)
            if status_code != 200:
                errors.append(f"gmail.messages.list: HTTP {status_code}")
                return ids, False
            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                errors.append("gmail.messages.list: invalid JSON response")
                return ids, False
            for item in data.get("messages") or []:
                msg_id = item.get("id")
                if msg_id:
                    ids.append(msg_id)
                if len(ids) >= max_results:
                    break
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids[:max_results], True

    def _map_gmail_message(self, msg_id: str, data: dict[str, Any], body_chars: int) -> MailRecord:
        payload = data.get("payload", {}) or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers") or []
        }
        subject = headers.get("subject", "")[:80]
        body = _extract_plain_text(payload)[:body_chars]
        received_at = _internal_date_to_iso(data.get("internalDate"))
        return MailRecord(
            source_id=f"gws_mail_{msg_id}",
            subject=subject,
            body=body,
            received_at=received_at,
        )
