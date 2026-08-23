"""CLI probe for GoogleWorkspaceConnector using the author's own ADC (design.md §16.3, ゴール28).

Usage (author ADC already logged in with tasks.readonly/calendar.readonly[/gmail.readonly]):

    PYTHONPATH=src .venv/bin/python scripts/gws_probe.py --owner emp_jordan_lee

Prints counts, per-source completeness, and error strings only. Titles,
descriptions, subjects, and bodies are never fetched-and-shown here — the
connector's `fetch()` return value already withholds nothing but counts from
this script by construction, but this script additionally never prints the
record contents even though they are present in memory.

`--apply-to-memory` additionally reconciles the fetch into a throwaway,
empty `InMemoryStore` (via `apply_fetch_result`) and prints the resulting
morning digest's counts by kind/tier only — still no titles or bodies.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from knowledge_discovery.connectors.base import apply_fetch_result
from knowledge_discovery.connectors.google_workspace import (
    GoogleWorkspaceConnector,
    default_session,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Probe target's employee_id")
    parser.add_argument(
        "--today",
        default=None,
        help="Reference date YYYY-MM-DD (default: current UTC date)",
    )
    parser.add_argument(
        "--apply-to-memory",
        action="store_true",
        help="Reconcile the fetch into an empty InMemoryStore and print digest counts by kind (no titles/bodies).",
    )
    args = parser.parse_args()

    today = args.today or datetime.now(timezone.utc).date().isoformat()

    session = default_session()
    connector = GoogleWorkspaceConnector(session)
    result = connector.fetch(args.owner, today)

    print(f"owner: {args.owner}")
    print(f"today: {today}")
    print(f"tasks: {len(result.tasks)}")
    print(f"schedules: {len(result.schedules)} (cancelled: {len(result.cancelled_ids)})")
    print(f"mails: {len(result.mails)}")
    print(f"complete: {result.complete}")
    print(f"errors: {len(result.errors)}")
    for error in result.errors:
        print(f"  - {error}")

    if args.apply_to_memory:
        from knowledge_discovery.store import InMemoryStore

        store = InMemoryStore()
        summary = apply_fetch_result(store, args.owner, result, today)
        kinds: dict[str, int] = {}
        for schedule in store.list_schedules(owner_employee_id=args.owner):
            kinds[schedule.kind] = kinds.get(schedule.kind, 0) + 1
        statuses: dict[str, int] = {}
        for task in store.list_tasks(owner_employee_id=args.owner):
            statuses[task.status] = statuses.get(task.status, 0) + 1
        print("--- applied to empty InMemoryStore ---")
        print(f"synced tasks: {summary.tasks} by status: {statuses}")
        print(f"synced schedules: {summary.schedules} by kind: {kinds}")
        print(f"synced mails: {summary.mails} (skipped: {summary.skipped})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
