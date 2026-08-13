"""Deal scoring.

Deliberately interpretable: every deal carries the reasons that produced its
score and the cautions that argue against it, so when the system pings you at
23:40 about an RTX 5070 Ti you can decide in five seconds whether to drive
across Lisbon for it.

Everything here works from a single collection sweep. Nothing requires history.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings
from ..db import query
from ..normalize import catalog as catalog_mod
from . import retail
from .stats import CONFIDENCE_TIERS, Distribution, all_distributions, percentile_rank, samples

_CONF_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(slots=True)
class Deal:
    listing_id: int
    site: str
    url: str
    title: str
    location: str | None
    product_id: str
    product_label: str
    price_eur: float
    adjusted_eur: float
    modifiers: list[str]
    median_eur: float
    p25_eur: float
    p10_eur: float
    p75_eur: float
    p90_eur: float
    pct_rank: float
    retail_eur: float | None
    retail_ratio: float | None
    price_drop_pct: float
    days_listed: float
    photo_count: int
    sample_size: int
    confidence: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    @property
    def below_median_pct(self) -> float:
        if self.median_eur <= 0:
            return 0.0
        return (self.median_eur - self.adjusted_eur) / self.median_eur * 100

    @property
    def saving_eur(self) -> float:
        return max(0.0, self.median_eur - self.adjusted_eur)


_ACTIVE_SQL = """
SELECT l.id, l.site, l.url, l.title, l.location, l.price_cents, l.first_seen,
       l.photo_count,
       n.product_id, n.adjusted_cents, n.modifiers,
       EXTRACT(EPOCH FROM (now() - l.first_seen)) / 86400.0 AS days_listed,
       (SELECT p.price_cents FROM price_point p
         WHERE p.listing_id = l.id ORDER BY p.observed_at ASC LIMIT 1) AS first_price
FROM listing l
JOIN normalized n ON n.listing_id = l.id
WHERE l.category = %(category)s
  AND l.disappeared_at IS NULL
  AND l.seller_is_pro = FALSE
  AND n.match_kind IN ('rule', 'fuzzy')
  AND n.adjusted_cents IS NOT NULL
"""


def _score(pct_rank: float, retail_ratio: float | None, price_drop_pct: float,
           days_listed: float, dist: Distribution
           ) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    cautions: list[str] = []

    # Market position dominates: 0 points at the median, 55 at the floor.
    market = max(0.0, (0.5 - pct_rank) / 0.5) * 55
    if pct_rank <= 0.10:
        reasons.append(f"cheapest {pct_rank * 100:.0f}% of what's listed")
    elif pct_rank <= 0.25:
        reasons.append(f"bottom {pct_rank * 100:.0f}% of the market")

    # Retail gap: a used card at 55% of new is interesting even if several
    # other people are also asking silly-low prices this week.
    retail_pts = 0.0
    if retail_ratio is not None:
        retail_pts = max(0.0, min(1.0, (0.85 - retail_ratio) / 0.35)) * 30
        if retail_ratio < 0.7:
            reasons.append(f"{retail_ratio * 100:.0f}% of retail")
        elif retail_ratio > 0.95:
            cautions.append("barely cheaper than buying new")

    # A seller who has already cut the price twice will cut it again.
    drop_pts = max(0.0, min(1.0, price_drop_pct / 20.0)) * 10
    if price_drop_pct >= 5:
        reasons.append(f"price cut {price_drop_pct:.0f}% since listed")

    # Freshness. On a first sweep everything looks equally new unless the site
    # tells you when it was posted — which OLX does, so we use it. A cheap ad
    # posted today is the one worth driving for; a cheap ad still up after two
    # months is cheap for a reason nobody has written down.
    fresh_pts = 0.0
    if days_listed <= 2:
        fresh_pts = 5.0
        reasons.append("posted in the last 48h")
    elif days_listed >= 45:
        cautions.append(f"listed {days_listed:.0f} days and still unsold")

    if dist.n_sold_proxy >= 3:
        reasons.append(f"{dist.n_sold_proxy} comparable sales observed")

    if dist.confidence in ("low", "none"):
        cautions.append(f"thin data — only {dist.n} comparable listings")

    return round(market + retail_pts + drop_pts + fresh_pts, 1), reasons, cautions


def find_deals(
    category: str,
    limit: int = 50,
    min_score: float = 0.0,
    max_percentile: float | None = None,
    product_ids: list[str] | None = None,
    max_price: float | None = None,
    location: str | None = None,
    min_confidence: str = "low",
) -> list[Deal]:
    cat = catalog_mod.get(category)
    dists = all_distributions(category)
    retail_prices = retail.current_retail(category)
    sample_cache = {p.id: samples(category, p.id) for p in cat.products}
    ceiling = settings.deal_percentile if max_percentile is None else max_percentile
    conf_floor = _CONF_ORDER.get(min_confidence, 1)
    wanted = set(product_ids) if product_ids else None

    deals: list[Deal] = []
    for row in query(_ACTIVE_SQL, {"category": category}):
        pid = row["product_id"]
        if wanted and pid not in wanted:
            continue
        dist = dists.get(pid)
        if dist is None or _CONF_ORDER[dist.confidence] < conf_floor:
            continue

        adjusted = row["adjusted_cents"] / 100.0
        price = (row["price_cents"] or 0) / 100.0
        if max_price and price > max_price:
            continue
        if location and location.lower() not in (row["location"] or "").lower():
            continue

        pct = percentile_rank(sample_cache.get(pid, []), adjusted)
        if pct > ceiling:
            continue

        retail_eur = retail_prices.get(pid)
        retail_ratio = (adjusted / retail_eur) if retail_eur else None

        first_price = (row["first_price"] or row["price_cents"] or 0) / 100.0
        drop = ((first_price - price) / first_price * 100) if first_price > 0 else 0.0
        days = float(row["days_listed"] or 0)

        score, reasons, cautions = _score(pct, retail_ratio, drop, days, dist)
        if score < min_score:
            continue

        # Too-good-to-be-true guard. Well below the observed floor usually means
        # a typo, a deposit-only ad, a parts card, or a scam — not a bargain.
        if dist.p10 > 0 and adjusted < dist.p10 * 0.6:
            cautions.append("far below every comparable listing — verify carefully")
        if (row["photo_count"] or 0) <= 1:
            cautions.append("one photo or fewer")

        product = cat.product(pid)
        deals.append(
            Deal(
                listing_id=row["id"],
                site=row["site"],
                url=row["url"],
                title=row["title"],
                location=row["location"],
                product_id=pid,
                product_label=product.label if product else pid,
                price_eur=price,
                adjusted_eur=adjusted,
                modifiers=list(row["modifiers"] or []),
                median_eur=dist.p50,
                p25_eur=dist.p25,
                p10_eur=dist.p10,
                p75_eur=dist.p75,
                p90_eur=dist.p90,
                pct_rank=pct,
                retail_eur=retail_eur,
                retail_ratio=retail_ratio,
                price_drop_pct=drop,
                days_listed=days,
                photo_count=row["photo_count"] or 0,
                sample_size=dist.n,
                confidence=dist.confidence,
                score=score,
                reasons=reasons,
                cautions=cautions,
            )
        )

    deals.sort(key=lambda d: d.score, reverse=True)
    return deals[:limit]


def active_listings(category: str, product_id: str) -> list[dict]:
    """Every live listing for one product, cheapest first — the per-product view."""
    rows = query(
        _ACTIVE_SQL + " AND n.product_id = %(pid)s ORDER BY n.adjusted_cents ASC",
        {"category": category, "pid": product_id},
    )
    dist = all_distributions(category).get(product_id)
    pairs = samples(category, product_id)
    out = []
    for r in rows:
        adjusted = r["adjusted_cents"] / 100.0
        out.append({
            "id": r["id"], "url": r["url"], "title": r["title"],
            "location": r["location"], "price_eur": (r["price_cents"] or 0) / 100.0,
            "adjusted_eur": adjusted, "modifiers": list(r["modifiers"] or []),
            "days_listed": float(r["days_listed"] or 0),
            "photo_count": r["photo_count"] or 0,
            "pct_rank": percentile_rank(pairs, adjusted),
            "vs_median": ((dist.p50 - adjusted) / dist.p50 * 100) if dist and dist.p50 else 0.0,
        })
    return out
