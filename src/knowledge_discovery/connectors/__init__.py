"""Data source connectors — the differentiable "source" side of §16.3.

`SourceConnector` is the interface; `SeedConnector` (no-op) and
`GoogleWorkspaceConnector` (Tasks / Calendar / Gmail) are the two
implementations selected via env `SOURCE_CONNECTOR`.
"""

from __future__ import annotations

import os

from .base import (
    FetchResult,
    MailRecord,
    ScheduleRecord,
    SourceConnector,
    SyncSummary,
    TaskRecord,
    apply_fetch_result,
)
from .seed import SeedConnector

__all__ = [
    "FetchResult",
    "MailRecord",
    "ScheduleRecord",
    "SourceConnector",
    "SyncSummary",
    "TaskRecord",
    "apply_fetch_result",
    "SeedConnector",
    "build_connector_from_env",
]


class _MisconfiguredGwsConnector(SourceConnector):
    """Stand-in returned by `build_connector_from_env()` when
    `SOURCE_CONNECTOR=google_workspace` but `GWS_SELF_EMPLOYEE_ID` is unset
    (round-14 V-11/V-13/S-11).

    `GoogleWorkspaceConnector.fetch()` ignores `owner_employee_id` (single
    ADC account, no per-owner credentials), so without a single-owner target
    there is no defined semantics for "which owner does this data belong
    to" -- every owner in the tenant would otherwise be synced with the same
    author's data. `fetch()` always raises instead of ever calling the real
    API, so `SecretaryService._sync_owners`'s existing per-owner
    try/except records this in `sync_errors` and detection continues over
    whatever is already in `Store` -- fail-closed without a server crash.
    """

    misconfigured = True  # round-15 R-4: lets the sweep record the error once

    def fetch(self, owner_employee_id: str, today: str) -> FetchResult:
        raise RuntimeError(
            "SOURCE_CONNECTOR=google_workspace requires GWS_SELF_EMPLOYEE_ID "
            "to be set (single-owner mode only; no per-owner credentials exist)."
        )


def build_connector_from_env() -> SourceConnector:
    """Select a `SourceConnector` from env `SOURCE_CONNECTOR=seed|google_workspace`.

    Default is `seed` (no-op). `google_workspace` requires
    `GWS_SELF_EMPLOYEE_ID` to be set (single-owner mode; §16.3) -- if it is
    unset, a `_MisconfiguredGwsConnector` is returned instead of the real
    connector so no owner is ever synced with another owner's data
    (round-14 V-11/V-13/S-11). Otherwise a `GoogleWorkspaceConnector` is
    built from the author's ADC (`default_session()`); Gmail's own opt-in
    (`GWS_GMAIL_ENABLED`) and other `GWS_*` knobs are read directly by
    `GoogleWorkspaceConnector` at fetch time, not here.

    Importing `google_workspace`'s ADC dependency is deferred to this
    function (only reached when `SOURCE_CONNECTOR=google_workspace`) so
    importing this package never requires ADC to be configured.
    """
    kind = os.environ.get("SOURCE_CONNECTOR", "seed").strip().lower()
    if kind == "google_workspace":
        if not os.environ.get("GWS_SELF_EMPLOYEE_ID", "").strip():
            return _MisconfiguredGwsConnector()
        from .google_workspace import GoogleWorkspaceConnector, default_session

        return GoogleWorkspaceConnector(default_session())
    return SeedConnector()
