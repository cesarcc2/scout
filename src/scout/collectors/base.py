from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from psycopg.types.json import Jsonb

from ..config import settings
from ..db import connect

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ScrapedListing:
    site: str
    site_listing_id: str
    category: str
    url: str
    title: str
    description: str = ""
    price_cents: int | None = None
    currency: str = "EUR"
    location: str | None = None
    seller_id: str | None = None
    seller_is_pro: bool = False
    photo_count: int = 0
    posted_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Collector(Protocol):
    site: str

    def search(self, term: str, category: str) -> Iterable[ScrapedListing]: ...


def polite_sleep() -> None:
    """The single most important function in this codebase.

    Getting IP-banned costs you days of data. Being slow costs you nothing —
    second-hand listings do not move in seconds.
    """
    time.sleep(settings.request_delay + random.uniform(0, settings.request_jitter))


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

# The `prev` CTE snapshots the old price before the upsert overwrites it —
# RETURNING cannot see EXCLUDED, and the price trajectory is a signal we care
# about (a seller who has already cut twice will cut again).
_UPSERT = """
WITH prev AS (
    SELECT price_cents FROM listing
    WHERE site = %(site)s AND site_listing_id = %(site_listing_id)s
), up AS (
    INSERT INTO listing (site, site_listing_id, category, url, title, description,
                         price_cents, currency, location, seller_id, seller_is_pro,
                         photo_count, posted_at, raw, first_seen)
    VALUES (%(site)s, %(site_listing_id)s, %(category)s, %(url)s, %(title)s,
            %(description)s, %(price_cents)s, %(currency)s, %(location)s,
            %(seller_id)s, %(seller_is_pro)s, %(photo_count)s, %(posted_at)s, %(raw)s,
            -- Trust the site's own posting date over "when we first saw it".
            -- This is what makes listing age real from the very first sweep
            -- instead of after a week of watching.
            LEAST(COALESCE(%(posted_at)s, now()), now()))
    ON CONFLICT (site, site_listing_id) DO UPDATE SET
        last_seen      = now(),
        first_seen     = LEAST(listing.first_seen,
                               COALESCE(EXCLUDED.posted_at, listing.first_seen)),
        seen_count     = listing.seen_count + 1,
        disappeared_at = NULL,
        title          = EXCLUDED.title,
        description    = CASE WHEN EXCLUDED.description <> ''
                              THEN EXCLUDED.description ELSE listing.description END,
        price_cents    = EXCLUDED.price_cents,
        photo_count    = EXCLUDED.photo_count
    RETURNING id, price_cents, (xmax = 0) AS inserted
)
SELECT up.id,
       up.inserted,
       (prev.price_cents IS DISTINCT FROM up.price_cents) AS price_changed
FROM up LEFT JOIN prev ON TRUE
"""


def persist(listings: Iterable[ScrapedListing]) -> dict[str, int]:
    """Upsert a batch. Returns counts of new / updated / price-changed."""
    stats = {"new": 0, "seen": 0, "price_changed": 0}
    with connect() as conn:
        for item in listings:
            row = conn.execute(
                _UPSERT,
                {
                    "site": item.site,
                    "site_listing_id": item.site_listing_id,
                    "category": item.category,
                    "url": item.url,
                    "title": item.title,
                    "description": item.description,
                    "price_cents": item.price_cents,
                    "currency": item.currency,
                    "location": item.location,
                    "seller_id": item.seller_id,
                    "seller_is_pro": item.seller_is_pro,
                    "photo_count": item.photo_count,
                    "posted_at": item.posted_at,
                    "raw": Jsonb(item.raw),
                },
            ).fetchone()

            if row["inserted"]:
                stats["new"] += 1
            else:
                stats["seen"] += 1
                if row["price_changed"]:
                    stats["price_changed"] += 1

            # Append a price point only when it differs from the most recent
            # observation, so a 700 -> 650 -> 700 round trip is fully recorded
            # but an unchanged listing does not grow the table every cycle.
            if item.price_cents is not None:
                conn.execute(
                    """
                    INSERT INTO price_point (listing_id, price_cents)
                    SELECT %(id)s, %(p)s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM (
                            SELECT price_cents FROM price_point
                            WHERE listing_id = %(id)s
                            ORDER BY observed_at DESC LIMIT 1
                        ) last WHERE last.price_cents = %(p)s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    {"id": row["id"], "p": item.price_cents},
                )
    return stats


def mark_disappeared(category: str) -> int:
    """Close out listings we have stopped seeing.

    Heuristic, not truth: a listing can also vanish because the seller edited
    the title out of our search terms, or because a sweep failed. We require
    several missed sweeps before believing it. Anything that disappears fast
    after appearing is treated downstream as 'probably sold', which is the
    closest thing to real transaction data you can get without an API.
    """
    grace_hours = max(6, (settings.cycle_minutes * 4) // 60)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE listing SET disappeared_at = last_seen
            WHERE category = %s AND disappeared_at IS NULL AND last_seen < %s
            """,
            (category, cutoff),
        )
        return cur.rowcount
