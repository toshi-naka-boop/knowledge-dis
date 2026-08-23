"""Seed data source connector (§16.3). The default (`SOURCE_CONNECTOR=seed`).

The demo/offline-test data path is seeded directly into `Store` by
`scripts/generate_seeds.py`; there is nothing for `run_sweep`'s sync step to
fetch. This connector exists only so `SOURCE_CONNECTOR` has a real "no
external source" option with the same interface as `GoogleWorkspaceConnector`.
"""

from __future__ import annotations

from .base import FetchResult, SourceConnector


class SeedConnector(SourceConnector):
    """No-op connector: always returns an empty, complete `FetchResult`."""

    def fetch(self, owner_employee_id: str, today: str) -> FetchResult:
        return FetchResult(complete=True)
