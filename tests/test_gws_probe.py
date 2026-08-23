"""Unit tests for scripts/gws_probe.py's --apply-to-memory path (design.md
§16.3, ゴール28; round-14 E-14).

`--apply-to-memory` must exercise the real `run_sweep()` -> `get_morning_digest()`
path against an empty `InMemoryStore` (not a hand-rolled `apply_fetch_result`
+ manual count, which used to bypass `run_sweep` entirely and could report a
green probe while the actual sweep stayed empty — see round-14 V-11). No
network access: `default_session()` is monkeypatched to return a fake HTTP
session, same technique as tests/test_connectors.py.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_discovery.connectors.google_workspace import CALENDAR_BASE, TASKS_BASE  # noqa: E402

from scripts import gws_probe  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, data: dict) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> dict:
        return self._data


class _FakeSession:
    """Routes `.get(url, params)` to a per-URL handler; no network access."""

    def __init__(self, handlers: dict) -> None:
        self._handlers = handlers

    def get(self, url: str, params=None):
        handler = self._handlers.get(url)
        if handler is None:
            raise AssertionError(f"no handler registered for {url}")
        return handler(dict(params or {}))


def _no_next_page(items: list, items_key: str):
    def handler(params: dict):
        return _FakeResponse(200, {items_key: items})

    return handler


class TestApplyToMemory(unittest.TestCase):
    """--apply-to-memory: real run_sweep()/get_morning_digest(), counts only."""

    def setUp(self) -> None:
        self._env_backup = dict(os.environ)
        os.environ.pop("GWS_GMAIL_ENABLED", None)
        os.environ.pop("GWS_SELF_EMPLOYEE_ID", None)
        os.environ.pop("DEMO_TODAY", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _fake_session(self) -> _FakeSession:
        secret_title = "CONFIDENTIAL Q4 restructuring notes"
        handlers = {
            f"{TASKS_BASE}/users/@me/lists": _no_next_page([{"id": "list1"}], "items"),
            f"{TASKS_BASE}/lists/list1/tasks": _no_next_page(
                [
                    {
                        "id": "t1",
                        "title": secret_title,
                        "notes": "should never be printed",
                        "due": "2026-07-25T00:00:00.000Z",
                        "status": "needsAction",
                        "updated": "2026-07-25T00:00:00.000Z",
                    }
                ],
                "items",
            ),
            f"{CALENDAR_BASE}/calendars/primary/events": _no_next_page(
                [
                    {
                        "id": "ev1",
                        "summary": secret_title,
                        "status": "confirmed",
                        # Deliberately tomorrow, not today: same-day events are
                        # additionally classified by whether they've already
                        # ended relative to the real wall clock, which this
                        # offline test must not depend on.
                        "start": {"dateTime": "2026-08-25T09:00:00Z"},
                        "end": {"dateTime": "2026-08-25T10:00:00Z"},
                    }
                ],
                "items",
            ),
        }
        return _FakeSession(handlers)

    def test_apply_to_memory_runs_real_sweep_and_digest_counts_only(self) -> None:
        with mock.patch.object(gws_probe, "default_session", return_value=self._fake_session()):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = gws_probe.main(
                    ["--owner", "emp_probe", "--today", "2026-08-24", "--apply-to-memory"]
                )
        self.assertEqual(rc, 0)
        output = buf.getvalue()

        # Single-owner mode was forced to the probed owner (§16.3), so the
        # sweep actually fetched and reconciled data into the empty store.
        self.assertEqual(os.environ["GWS_SELF_EMPLOYEE_ID"], "emp_probe")

        # Counts by kind/due_category and by type/tier are present...
        self.assertIn("reminders by kind/due_category:", output)
        self.assertIn("'meeting_prep/tomorrow': 1", output)
        self.assertIn("cards by type/tier:", output)
        self.assertIn("'stagnation/notice': 1", output)

        # ...but no title/subject/body content ever reaches stdout.
        self.assertNotIn("CONFIDENTIAL", output)
        self.assertNotIn("restructuring", output)
        self.assertNotIn("should never be printed", output)

    def test_apply_to_memory_absent_leaves_gws_self_employee_id_unset(self) -> None:
        with mock.patch.object(gws_probe, "default_session", return_value=self._fake_session()):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = gws_probe.main(["--owner", "emp_probe", "--today", "2026-08-24"])
        self.assertEqual(rc, 0)
        self.assertNotIn("GWS_SELF_EMPLOYEE_ID", os.environ)
        self.assertNotIn("run_sweep", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
