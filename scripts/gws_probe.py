"""CLI probe for GoogleWorkspaceConnector using the author's own ADC (design.md §16.3, ゴール28).

Usage (author ADC already logged in with tasks.readonly/calendar.readonly[/gmail.readonly]):

    PYTHONPATH=src .venv/bin/python scripts/gws_probe.py --owner emp_jordan_lee

Prints counts, per-source completeness, and error strings only. Titles,
descriptions, subjects, and bodies are never fetched-and-shown here — the
connector's `fetch()` return value already withholds nothing but counts from
this script by construction, but this script additionally never prints the
record contents even though they are present in memory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
