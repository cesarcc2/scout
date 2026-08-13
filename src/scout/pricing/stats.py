"""The statistics that replace an AI 'is this a good price?' call.

Everything here is a weighted empirical distribution over your own scraped
history. No model can beat this, because no model has your data.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from ..config import settings
from ..db import query


CONFIDENCE_TIERS = (("high", 15), ("medium", 8), ("low", 4))


@dataclass(slots=True)
class Distribution:
    product_id: str
    n: int
    n_sold_proxy: int
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    mean: float
    values: list[float] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        """Graded, not binary.

        The original design refused to score anything below a sample threshold,
        which meant staring at an empty dashboard for a week. A snapshot of what
        is listed *right now* is already a real market — 6 comparable listings
        genuinely tell you something, they just tell you less than 40. So the
        answer is a confidence label, not silence.
        """
        for name, floor in CONFIDENCE_TIERS:
            if self.n >= floor:
                return name
        return "none"

    @property
    def trustworthy(self) -> bool:
        return self.n >= settings.min_samples

    @property
    def iqr(self) -> float:
        return self.p75 - self.p25


def weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float:
    """Linear-interpolated weighted percentile.

    `pairs` is (value, weight), unsorted. Weights let sold-proxy listings —
    prices the market actually accepted — count for more than asking prices
    that have been sitting untouched for six weeks.
    """
    if not pairs:
        return 0.0
    pairs = sorted(pairs, key=lambda x: x[0])
    values = [v for v, _ in pairs]
    total = sum(w for _, w in pairs)
    if total <= 0:
        return float(values[len(values) // 2])

    cum: list[float] = []
    running = 0.0
    for _, w in pairs:
        running += w
        cum.append(running)

    target = q * total
    idx = bisect_left(cum, target)
    if idx <= 0:
        return float(values[0])
    if idx >= len(values):
        return float(values[-1])

    lo_c, hi_c = cum[idx - 1], cum[idx]
    if hi_c == lo_c:
        return float(values[idx])
    frac = (target - lo_c) / (hi_c - lo_c)
    return float(values[idx - 1] + frac * (values[idx] - values[idx - 1]))


def percentile_rank(pairs: list[tuple[float, float]], value: float) -> float:
    """Where `value` sits in the distribution, 0.0 = cheapest."""
    if not pairs:
        return 0.5
    total = sum(w for _, w in pairs)
    if total <= 0:
        return 0.5
    below = sum(w for v, w in pairs if v < value)
    equal = sum(w for v, w in pairs if v == value)
    return (below + equal / 2) / total


_SAMPLES_SQL = """
SELECT n.adjusted_cents,
       (l.disappeared_at IS NOT NULL
        AND l.seen_count > 1
        AND l.disappeared_at - l.first_seen < make_interval(hours => %(sold_hours)s)
       ) AS sold_proxy
FROM normalized n
JOIN listing l ON l.id = n.listing_id
WHERE n.category = %(category)s
  AND n.product_id = %(product_id)s
  AND n.match_kind IN ('rule', 'fuzzy')
  AND n.adjusted_cents IS NOT NULL
  AND l.seller_is_pro = FALSE
  -- Window on last_seen, not first_seen: an ad posted three months ago that is
  -- still up today IS current market data. Filtering on first_seen threw away
  -- the entire existing market on day one and was the reason this used to need
  -- a week of history before saying anything.
  AND l.last_seen > now() - make_interval(days => %(window_days)s)
"""


def samples(category: str, product_id: str, window_days: int | None = None
            ) -> list[tuple[float, float]]:
    rows = query(
        _SAMPLES_SQL,
        {
            "category": category,
            "product_id": product_id,
            "window_days": window_days or settings.stats_window_days,
            "sold_hours": settings.sold_proxy_hours,
        },
    )
    return [
        (r["adjusted_cents"] / 100.0, settings.sold_weight if r["sold_proxy"] else 1.0)
        for r in rows
    ]


def distribution(category: str, product_id: str,
                 window_days: int | None = None) -> Distribution:
    pairs = samples(category, product_id, window_days)
    n_sold = sum(1 for _, w in pairs if w > 1.0)
    total_w = sum(w for _, w in pairs) or 1.0
    return Distribution(
        product_id=product_id,
        values=sorted(v for v, _ in pairs),
        n=len(pairs),
        n_sold_proxy=n_sold,
        p10=weighted_percentile(pairs, 0.10),
        p25=weighted_percentile(pairs, 0.25),
        p50=weighted_percentile(pairs, 0.50),
        p75=weighted_percentile(pairs, 0.75),
        p90=weighted_percentile(pairs, 0.90),
        mean=sum(v * w for v, w in pairs) / total_w,
    )


def all_distributions(category: str, window_days: int | None = None
                      ) -> dict[str, Distribution]:
    from ..normalize import catalog as catalog_mod

    cat = catalog_mod.get(category)
    return {p.id: distribution(category, p.id, window_days) for p in cat.products}
