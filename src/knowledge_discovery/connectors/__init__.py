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


def build_connector_from_env() -> SourceConnector:
    """Select a `SourceConnector` from env `SOURCE_CONNECTOR=seed|google_workspace`.

    Default is `seed` (no-op). `google_workspace` builds a
    `GoogleWorkspaceConnector` from the author's ADC (`default_session()`);
    Gmail's own opt-in (`GWS_GMAIL_ENABLED`) and other `GWS_*` knobs are read
    directly by `GoogleWorkspaceConnector` at fetch time, not here.

    Importing `google_workspace`'s ADC dependency is deferred to this
    function (only reached when `SOURCE_CONNECTOR=google_workspace`) so
    importing this package never requires ADC to be configured.
    """
    kind = os.environ.get("SOURCE_CONNECTOR", "seed").strip().lower()
    if kind == "google_workspace":
        from .google_workspace import GoogleWorkspaceConnector, default_session

        return GoogleWorkspaceConnector(default_session())
    return SeedConnector()
