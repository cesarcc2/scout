"""Dashboard read-state.

Notifications are optional in Scout, which means the dashboard has to answer
"what's new since I last looked?" on its own. That is all this module does:
remember when you last acknowledged the deals page, and cache the resulting
count cheaply enough that every page render can show a badge.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from ..db import connect, query

# The nav badge renders on every page, and computing it means scoring the whole
# market. A few seconds of staleness is invisible to a human and turns an
# O(listings) recount into a dictionary lookup.
_CACHE_TTL = 20.0
_cache: dict[str, tuple[float, int]] = {}


def seen_at(category: str) -> datetime:
    """When the deals page was last acknowledged for this category.

    First ever call seeds the row at 'now', so a fresh install does not
    announce its entire first sweep as new — that would be 200 unread items
    and no signal at all.
    """
    rows = query("SELECT deals_seen_at FROM ui_state WHERE category = %s", (category,))
    if rows:
        return rows[0]["deals_seen_at"]
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO ui_state (category) VALUES (%s) "
            "ON CONFLICT (category) DO UPDATE SET category = EXCLUDED.category "
            "RETURNING deals_seen_at",
            (category,),
        ).fetchone()
    return row["deals_seen_at"]


def mark_seen(category: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO ui_state (category, deals_seen_at) VALUES (%s, now()) "
            "ON CONFLICT (category) DO UPDATE SET deals_seen_at = now()",
            (category,),
        )
    _cache.pop(category, None)


def invalidate(category: str | None = None) -> None:
    """Called after a collection run so the badge reflects it immediately."""
    if category:
        _cache.pop(category, None)
    else:
        _cache.clear()


def new_deal_count(category: str) -> int:
    """How many scored deals arrived since the last acknowledgement."""
    now = time.monotonic()
    hit = _cache.get(category)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    from ..pricing.deals import find_deals

    try:
        since = seen_at(category)
        count = sum(
            1 for d in find_deals(category, limit=500, seen_at=since) if d.is_new
        )
    except Exception:
        count = 0
    _cache[category] = (now, count)
    return count


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
