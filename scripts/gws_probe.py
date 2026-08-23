"""CLI probe for GoogleWorkspaceConnector using the author's own ADC (design.md §16.3, ゴール28).

Usage (author ADC already logged in with tasks.readonly/calendar.readonly[/gmail.readonly]):

    PYTHONPATH=src .venv/bin/python scripts/gws_probe.py --owner emp_jordan_lee

Prints counts, per-source completeness, and error strings only. Titles,
descriptions, subjects, and bodies are never fetched-and-shown here — the
connector's `fetch()` return value already withholds nothing but counts from
this script by construction, but this script additionally never prints the
record contents even though they are present in memory.

`--apply-to-memory` additionally builds a real SecretaryService (this
connector injected, `GWS_SELF_EMPLOYEE_ID` forced to `--owner` so single-owner
mode's sync target is exactly this probe, per §16.3) around a throwaway,
empty `InMemoryStore`, and runs `run_sweep()` followed by
`get_morning_digest()` — the same path design §10 goal 28's manual gate
exercises — printing only reminder kind/due_category counts and stagnation/
profile_diff card type/tier counts. Never titles, subjects, or bodies.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from knowledge_discovery.connectors.google_workspace import (
    GoogleWorkspaceConnector,
    default_session,
)


def main(argv: list[str] | None = None) -> int:
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
        help=(
            "Run a real run_sweep()/get_morning_digest() against an empty "
            "InMemoryStore and print digest counts by kind/tier (no titles/bodies)."
        ),
    )
    args = parser.parse_args(argv)

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
        from knowledge_discovery.matching import (
            DeterministicEmbedder,
            FakeConnectionInferencer,
            MatchingEngine,
        )
        from knowledge_discovery.secretary import SecretaryService
        from knowledge_discovery.service import KnowledgeDiscoveryService
        from knowledge_discovery.store import InMemoryStore
        from knowledge_discovery.transmission import TransmissionLayer

        # Single-owner mode (§16.3): the sync target is GWS_SELF_EMPLOYEE_ID
        # itself, not a filter over agents ∪ profiles, so it is fetched even
        # against a completely empty Store. Forced here rather than merely
        # documented, so this probe can never accidentally run in the
        # multi-owner (unsupported, fail-closed) mode.
        os.environ["GWS_SELF_EMPLOYEE_ID"] = args.owner

        store = InMemoryStore()
        matching_engine = MatchingEngine(
            embedder=DeterministicEmbedder(),
            inferencer=FakeConnectionInferencer(),
            vector_floor=0.20,
            connection_threshold=0.50,
            max_dispatch_k=3,
        )
        kd_service = KnowledgeDiscoveryService(
            store=store,
            transmission=TransmissionLayer(store),
            matching_engine=matching_engine,
        )
        secretary = SecretaryService(
            store=store,
            kd_service=kd_service,
            matching_engine=matching_engine,
            connector=connector,
        )

        sweep_result = secretary.run_sweep(demo_today=today)
        digest = secretary.get_morning_digest(args.owner, demo_today=today)

        reminder_counts: dict[str, int] = {}
        for reminder in digest["reminders"]:
            key = f"{reminder['kind']}/{reminder['due_category']}"
            reminder_counts[key] = reminder_counts.get(key, 0) + 1

        card_counts: dict[str, int] = {}
        for card in digest["stagnation_cards"]:
            key = f"stagnation/{card['tier']}"
            card_counts[key] = card_counts.get(key, 0) + 1
        if digest["profile_diff_cards"]:
            card_counts["profile_diff"] = len(digest["profile_diff_cards"])

        print("--- run_sweep -> get_morning_digest (empty InMemoryStore) ---")
        print(f"sweep: {sweep_result}")
        print(f"reminders by kind/due_category: {reminder_counts}")
        print(f"cards by type/tier: {card_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
