"""End-to-end smoke test against a real Postgres, using fabricated listings.

Not a unit test — this exists to prove the SQL, the sold-proxy logic and the
scoring actually run together before you point the collector at a live site.

    SCOUT_DSN=postgresql://scout:scout@localhost:5432/scout \
        python tests/e2e_synthetic.py
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from scout.collectors.base import ScrapedListing, persist
from scout.db import connect, init_db, query
from scout.normalize.run import export_unmatched, normalize_category
from scout.pricing.compare import compare
from scout.pricing.deals import find_deals
from scout.pricing.stats import all_distributions

random.seed(7)

TEMPLATES = [
    "Placa Gráfica {model} {brand} {suffix}",
    "{model} {brand} como nova",
    "{model} com garantia e fatura",
    "Vendo {model} {brand}",
    "{model} selada, nunca usada",
    "{model} usada para mineração",
]
BRANDS = ["Gigabyte", "MSI Ventus", "Asus TUF", "Sapphire Pulse", "Zotac"]
SUFFIXES = ["OC 16GB", "Gaming", "", "excelente estado"]

# (model text, catalog id, realistic used street price in EUR)
MARKET = [
    ("RTX 5070 Ti", "rtx_5070_ti", 720),
    ("RTX 5070", "rtx_5070", 540),
    ("RX 9070 XT", "rx_9070_xt", 610),
    ("RTX 4080 Super", "rtx_4080_super", 800),
    ("RTX 4070 Super", "rtx_4070_super", 470),
    ("RX 7900 XTX", "rx_7900_xtx", 720),
]

NOISE = [
    "Procuro RTX 5070 Ti bom preço",
    "PC Gaming completo i5 + RTX 4070",
    "RTX 3080 para peças não liga",
    "GTX 1080 Ti 11GB EVGA",
    "Cadeira gaming preta",
    "Suporte anti sag para placa gráfica",
]


def seed(n_per_model: int = 30, day_one: bool = False) -> None:
    now = datetime.now(timezone.utc)
    batch: list[ScrapedListing] = []
    lid = 0

    for model, _pid, street in MARKET:
        for i in range(n_per_model):
            lid += 1
            price = max(120, random.gauss(street, street * 0.11))
            title = random.choice(TEMPLATES).format(
                model=model, brand=random.choice(BRANDS), suffix=random.choice(SUFFIXES)
            )
            batch.append(ScrapedListing(
                site="synthetic", site_listing_id=f"s{lid}", category="gpu",
                url=f"https://example.invalid/{lid}", title=title,
                description="Entrega em mão em Lisboa.",
                price_cents=int(price * 100), location="Lisboa, Lisboa",
                seller_id=f"u{lid % 40}", photo_count=3,
                posted_at=now - timedelta(days=random.uniform(0, 40)), raw={},
            ))

        # One deliberate bargain per model: 25% under street price.
        lid += 1
        batch.append(ScrapedListing(
            site="synthetic", site_listing_id=f"s{lid}", category="gpu",
            url=f"https://example.invalid/{lid}",
            title=f"{model} {random.choice(BRANDS)} - venda rápida",
            description="Preço não negociável, entrega imediata.",
            price_cents=int(street * 0.75 * 100), location="Porto, Porto",
            seller_id=f"u{lid}", photo_count=5, posted_at=now, raw={},
        ))

    for i, title in enumerate(NOISE):
        lid += 1
        batch.append(ScrapedListing(
            site="synthetic", site_listing_id=f"s{lid}", category="gpu",
            url=f"https://example.invalid/{lid}", title=title,
            price_cents=50000, posted_at=now, raw={},
        ))

    print("persist:", persist(batch))

    # first_seen already comes from posted_at (the site's own posting date), so
    # listing age is real even on a first sweep. In day-one mode we stop here:
    # nothing has disappeared yet, there is no price history, and no listing has
    # been seen twice. If deals still come out, the "no waiting" claim holds.
    if day_one:
        return

    with connect() as conn:
        conn.execute("""
            UPDATE listing
            SET disappeared_at = first_seen + interval '11 hours', seen_count = 3
            WHERE category = 'gpu' AND id % 5 = 0
        """)


def main(day_one: bool = False) -> None:
    init_db()
    with connect() as conn:
        conn.execute("TRUNCATE listing, retail_price, alerted, unmatched_title RESTART IDENTITY CASCADE")
    seed(day_one=day_one)

    print("MODE:", "DAY ONE (single sweep, zero history)" if day_one else "with history")
    print("normalize:", normalize_category("gpu", force=True))

    print("\n--- distributions ---")
    for pid, d in all_distributions("gpu").items():
        if d.n:
            print(f"{pid:<20} n={d.n:>3} sold={d.n_sold_proxy:>2} conf={d.confidence:<6} "
                  f"p25=€{d.p25:>7.0f} median=€{d.p50:>7.0f} p75=€{d.p75:>7.0f}")

    print("\n--- deals ---")
    deals = find_deals("gpu", limit=10)
    for d in deals:
        print(f"{d.score:>5.1f} {d.product_label:<24} €{d.price_eur:>6.0f} "
              f"(adj €{d.adjusted_eur:>6.0f}, median €{d.median_eur:>6.0f}, "
              f"pct={d.pct_rank:.2f}) {', '.join(d.reasons)}")

    print("\n--- value ranking (used p25) ---")
    for r in compare("gpu"):
        if r.cost_per_point:
            print(f"{r.label:<26} perf={r.attributes.get('perf_index'):>5.0f} "
                  f"p25=€{r.used_p25:>6.0f} €/pt={r.cost_per_point:>6.2f} "
                  f"value={r.value_index:>5.1f}")

    print("\n--- filtered: 16GB minimum, 320W ceiling ---")
    for r in compare("gpu", min_attributes={"vram_gb": 16}, max_attributes={"tdp_w": 320}):
        if r.cost_per_point:
            print(f"{r.label:<26} €/pt={r.cost_per_point:>6.2f}")

    print("\n--- coverage ---")
    for row in query("SELECT match_kind, COUNT(*) n FROM normalized GROUP BY match_kind ORDER BY n DESC"):
        print(f"{row['match_kind']:<10} {row['n']}")
    print("unmatched export ->", export_unmatched("gpu"))

    assert deals, "expected the planted bargains to surface as deals"
    print("\nOK")


if __name__ == "__main__":
    import sys

    main(day_one="--day-one" in sys.argv)
