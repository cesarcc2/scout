"""Cross-product comparison — the '5070 Ti vs 9070 XT vs 4080 Super' question.

This is a lookup table and a division, not a reasoning problem. Never ask a
language model to recall benchmark numbers: it will produce plausible ones,
and plausible-but-wrong is the worst possible input to a ranking.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import query
from ..normalize import catalog as catalog_mod
from . import retail
from .stats import all_distributions


@dataclass(slots=True)
class ValueRow:
    product_id: str
    label: str
    brand: str
    attributes: dict[str, float]
    used_p25: float
    used_median: float
    retail_eur: float | None
    best_active_eur: float | None
    best_active_url: str | None
    sample_size: int
    basis_price: float                 # the price this ranking was computed on
    cost_per_point: float | None       # € per unit of rank_by attribute
    value_index: float = 0.0           # 100 = best in the comparison set

    @property
    def used_vs_retail(self) -> float | None:
        if not self.retail_eur or self.retail_eur <= 0:
            return None
        return self.used_median / self.retail_eur


_BEST_ACTIVE_SQL = """
SELECT DISTINCT ON (n.product_id)
       n.product_id, n.adjusted_cents, l.url
FROM normalized n
JOIN listing l ON l.id = n.listing_id
WHERE n.category = %(category)s
  AND l.disappeared_at IS NULL
  AND l.seller_is_pro = FALSE
  AND n.match_kind IN ('rule', 'fuzzy')
  AND n.adjusted_cents IS NOT NULL
ORDER BY n.product_id, n.adjusted_cents ASC
"""


def compare(category: str,
            product_ids: list[str] | None = None,
            basis: str = "p25",
            min_attributes: dict[str, float] | None = None,
            max_attributes: dict[str, float] | None = None) -> list[ValueRow]:
    """Rank products by cost per unit of the catalog's `rank_by` attribute.

    `basis` picks what price to rank on:
      - "p25"    what a patient buyer actually pays used (default)
      - "median" what a typical buyer pays used
      - "best"   the cheapest thing listed right now
      - "retail" buying new instead
    """
    cat = catalog_mod.get(category)
    attr = cat.rank_by
    dists = all_distributions(category)
    retail_prices = retail.current_retail(category)
    best_active = {
        r["product_id"]: (r["adjusted_cents"] / 100.0, r["url"])
        for r in query(_BEST_ACTIVE_SQL, {"category": category})
    }

    wanted = set(product_ids) if product_ids else None
    rows: list[ValueRow] = []

    for product in cat.products:
        if wanted and product.id not in wanted:
            continue
        dist = dists.get(product.id)
        if dist is None:
            continue

        # Hard filters — a card that does not fit your PSU is not a deal at
        # any price.
        if min_attributes and any(
            product.attributes.get(k, 0) < v for k, v in min_attributes.items()
        ):
            continue
        if max_attributes and any(
            product.attributes.get(k, float("inf")) > v
            for k, v in max_attributes.items()
        ):
            continue

        active = best_active.get(product.id)
        retail_eur = retail_prices.get(product.id)

        price = {
            "p25": dist.p25,
            "median": dist.p50,
            "best": active[0] if active else 0.0,
            "retail": retail_eur or 0.0,
        }.get(basis, dist.p25)

        attr_value = product.attributes.get(attr, 0.0)
        cost_per_point = (price / attr_value) if (price > 0 and attr_value > 0) else None

        rows.append(
            ValueRow(
                product_id=product.id,
                label=product.label,
                brand=product.brand,
                attributes=product.attributes,
                used_p25=dist.p25,
                used_median=dist.p50,
                retail_eur=retail_eur,
                best_active_eur=active[0] if active else None,
                best_active_url=active[1] if active else None,
                sample_size=dist.n,
                basis_price=price,
                cost_per_point=cost_per_point,
            )
        )

    scored = [r for r in rows if r.cost_per_point]
    if scored:
        cheapest = min(r.cost_per_point for r in scored)  # type: ignore[type-var]
        for r in rows:
            if r.cost_per_point:
                r.value_index = round(cheapest / r.cost_per_point * 100, 1)

    rows.sort(key=lambda r: (r.cost_per_point is None, r.cost_per_point or 0))
    return rows
