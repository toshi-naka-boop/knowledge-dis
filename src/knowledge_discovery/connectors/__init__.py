"""Data source connectors — the differentiable "source" side of §16.3.

`SourceConnector` is the interface; `SeedConnector` (no-op) and
`GoogleWorkspaceConnector` (Tasks / Calendar / Gmail) are the two
implementations selected via env `SOURCE_CONNECTOR`.
"""

from __future__ import annotations

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
]
