#!/usr/bin/env python3
"""Create the next calendar month's partition for ``webhook_deliveries``.

Issue #522: ``webhook_deliveries`` is partitioned by RANGE on ``created_at``
with one partition per month. Run this script (e.g. from cron just before
the start of each month) to ensure the next month's partition exists before
traffic arrives. Partitions inherit the parent table's indexes, constraints
and foreign keys automatically.

Usage:
    python scripts/create_next_partition.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import create_engine, text


def compute_next_month_range(now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Return (start, end) of the calendar month after *now*."""
    now = now or datetime.utcnow()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = (this_month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    following_month_start = (next_month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month_start, following_month_start


def partition_name(start: datetime) -> str:
    """Return the partition table name for the month starting at *start*."""
    return f"webhook_deliveries_{start.year:04d}_{start.month:02d}"


def create_next_month_partition(
    engine,
    now: Optional[datetime] = None,
) -> str:
    """Create the next month's partition; returns the created table name."""
    start, end = compute_next_month_range(now)
    name = partition_name(start)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} "
                f"PARTITION OF webhook_deliveries "
                f"FOR VALUES FROM (:start) TO (:end)"
            ),
            {"start": start, "end": end},
        )
    return name


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    engine = create_engine(database_url)
    try:
        name = create_next_month_partition(engine)
        print(f"OK: partition {name} ready")
        return 0
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"ERROR: failed to create next month's partition: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
