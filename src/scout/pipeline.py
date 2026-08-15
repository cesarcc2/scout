from __future__ import annotations

import logging
import re

from .alerts.notify import push_new_deals
from .collectors.base import mark_disappeared, persist
from .collectors.olx import OlxCollector
from .config import settings
from .normalize import catalog as catalog_mod
from .normalize.run import normalize_category
from .pricing.deals import find_deals
from .pricing.stats import percentile_rank, samples

log = logging.getLogger(__name__)

COLLECTORS = [OlxCollector]

# Words that identify a vendor, not a product. Dropped when deriving a search
# term from a catalog label, because nobody types "GeForce" in an OLX search.
_VENDOR_WORDS = re.compile(r"^(geforce|radeon|arc|nvidia|amd|intel)\s+", re.I)


def deep_terms(category: str) -> list[str]:
    """One search term per catalog product, plus the broad category terms.

    This is what makes the first run useful instead of the seventh. The broad
    terms alone ("rtx 5070") return whatever OLX decides is relevant; querying
    each product by name sweeps up the whole active market in one pass.
    """
    cat = catalog_mod.get(category)
    terms: list[str] = []
    for product in cat.products:
        term = _VENDOR_WORDS.sub("", product.label).strip().lower()
        # Drop capacity suffixes: "rtx 5060 ti 16gb" finds less than "rtx 5060 ti".
        term = re.sub(r"\s+\d{1,2}\s*gb$", "", term)
        if term and term not in terms:
            terms.append(term)
    for term in cat.query_terms:
        if term.lower() not in terms:
            terms.append(term)
    return terms


def collect(category: str, deep: bool = False,
            terms: list[str] | None = None,
            max_pages: int | None = None,
            progress=None) -> dict[str, int]:
    cat = catalog_mod.get(category)
    search_terms = terms or (deep_terms(category) if deep else cat.query_terms)
    totals = {"new": 0, "seen": 0, "price_changed": 0, "terms": len(search_terms)}

    for factory in COLLECTORS:
        collector = factory()
        for i, term in enumerate(search_terms, 1):
            if progress:
                progress(i, len(search_terms), term)
            try:
                batch = list(collector.search(term, category, max_pages=max_pages))
            except Exception as exc:
                log.exception("collector %s failed on %r: %s", collector.site, term, exc)
                continue
            if not batch:
                continue
            stats = persist(batch)
            for k in ("new", "seen", "price_changed"):
                totals[k] += stats[k]
            log.info("%s %-22s → %d listings (%d new)", collector.site, term,
                     len(batch), stats["new"])

    totals["disappeared"] = mark_disappeared(category)

    # The dashboard's "new since you last looked" badge is cached; a sweep is
    # exactly when it goes stale.
    from .web import uistate

    uistate.invalidate(category)
    return totals


def bootstrap(category: str, progress=None) -> dict:
    """First run: sweep the entire active market, then score it immediately.

    Takes roughly 10-20 minutes at the default polite request rate. When it
    finishes you have a real price distribution per product built from every
    listing currently live on the site — no waiting.
    """
    log.info("bootstrap: sweeping the full active market for %s", category)
    collected = collect(category, deep=True,
                        max_pages=max(settings.max_pages, 15), progress=progress)
    normalized = normalize_category(category, force=True)
    deals = find_deals(category)
    return {"collected": collected, "normalized": normalized, "deals": len(deals)}


def live_search(query: str, category: str = "gpu", pages: int = 3) -> list[dict]:
    """Fetch one query from the site right now and price it immediately.

    Everything it finds is persisted too, so an ad-hoc search also deepens the
    distributions for later.
    """
    collector = OlxCollector()
    batch = list(collector.search(query, category, max_pages=pages))
    if batch:
        persist(batch)
        normalize_category(category)

    site_ids = [b.site_listing_id for b in batch]
    if not site_ids:
        return []

    from .db import query as db_query

    rows = db_query(
        """
        SELECT l.id, l.url, l.title, l.location, l.price_cents, l.photo_count,
               EXTRACT(EPOCH FROM (now() - l.first_seen)) / 86400.0 AS days_listed,
               n.product_id, n.adjusted_cents, n.match_kind, n.excluded_by,
               n.modifiers
        FROM listing l JOIN normalized n ON n.listing_id = l.id
        WHERE l.site_listing_id = ANY(%s) AND l.category = %s
        ORDER BY n.adjusted_cents NULLS LAST
        """,
        (site_ids, category),
    )

    cat = catalog_mod.get(category)
    from .pricing.stats import all_distributions

    dists = all_distributions(category)
    sample_cache: dict[str, list] = {}
    out = []
    for r in rows:
        pid = r["product_id"]
        dist = dists.get(pid) if pid else None
        adjusted = (r["adjusted_cents"] or 0) / 100.0
        if pid and pid not in sample_cache:
            sample_cache[pid] = samples(category, pid)
        product = cat.product(pid) if pid else None
        out.append({
            "url": r["url"], "title": r["title"], "location": r["location"],
            "price_eur": (r["price_cents"] or 0) / 100.0,
            "adjusted_eur": adjusted,
            "days_listed": float(r["days_listed"] or 0),
            "photo_count": r["photo_count"] or 0,
            "modifiers": list(r["modifiers"] or []),
            "match_kind": r["match_kind"],
            "excluded_by": r["excluded_by"],
            "product_id": pid,
            "product_label": product.label if product else None,
            "median_eur": dist.p50 if dist else 0.0,
            "sample_size": dist.n if dist else 0,
            "confidence": dist.confidence if dist else "none",
            "pct_rank": percentile_rank(sample_cache.get(pid, []), adjusted) if pid else None,
            "vs_median": ((dist.p50 - adjusted) / dist.p50 * 100)
                         if dist and dist.p50 else None,
        })
    return out


def run_cycle(category: str, alert: bool = True) -> dict:
    log.info("=== cycle start: %s ===", category)
    collected = collect(category)
    normalized = normalize_category(category)
    deals = find_deals(category)
    pushed = push_new_deals(deals) if alert else 0
    result = {
        "collected": collected,
        "normalized": normalized,
        "deals": len(deals),
        "alerted": pushed,
    }
    log.info("=== cycle done: %s ===", result)
    return result


def run_all(alert: bool = True) -> dict[str, dict]:
    return {cat: run_cycle(cat, alert) for cat in catalog_mod.load_all()}
