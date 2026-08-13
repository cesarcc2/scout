"""Retail baselines for Portugal.

Design note: rather than write a bespoke parser per shop (which breaks every
time someone redesigns a page), this pulls JSON-LD `Product`/`Offer` blocks,
which most PT/ES electronics shops emit for Google Shopping and therefore have
a strong incentive to keep valid. Product names from the shop are then run
through the *same* catalog matcher used for classifieds — one matching
implementation, two data sources.

`shops.yaml` holds the search URL templates. If a shop stops returning JSON-LD,
you get zero rows for it (logged), not garbage prices.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterator

from ..collectors.base import polite_sleep
from ..config import settings
from ..db import connect, query
from ..normalize import catalog as catalog_mod
from ..normalize.matcher import match

log = logging.getLogger(__name__)

_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# Verify these before trusting them — shops change URL schemes. Each entry only
# needs a search URL with {q} where the query goes.
DEFAULT_SHOPS: dict[str, str] = {
    "pcdiga": "https://www.pcdiga.com/catalogsearch/result/?q={q}",
    "globaldata": "https://www.globaldata.pt/pesquisa?controller=search&s={q}",
    "versus": "https://www.versusgamers.com/busqueda?controller=search&s={q}",
    "pccomponentes": "https://www.pccomponentes.pt/buscar/?query={q}",
}


@dataclass(slots=True)
class RetailOffer:
    source: str
    name: str
    price_eur: float
    url: str
    in_stock: bool


def _client():
    try:
        from curl_cffi import requests as creq

        return creq.Session(impersonate=settings.user_agent_profile)
    except Exception:  # pragma: no cover
        import httpx

        return httpx.Client(follow_redirects=True, timeout=settings.request_timeout)


def _walk(node, out: list[dict]) -> None:
    if isinstance(node, dict):
        if node.get("@type") in ("Product", "ProductGroup") or "offers" in node:
            out.append(node)
        for value in node.values():
            _walk(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, out)


def _offer_price(offers) -> tuple[float | None, bool]:
    if isinstance(offers, list):
        for o in offers:
            price, stock = _offer_price(o)
            if price:
                return price, stock
        return None, False
    if not isinstance(offers, dict):
        return None, False
    raw = offers.get("price") or offers.get("lowPrice")
    availability = str(offers.get("availability", "")).lower()
    in_stock = "instock" in availability or availability == ""
    if raw is None:
        return None, in_stock
    try:
        return float(str(raw).replace(",", ".")), in_stock
    except ValueError:
        return None, in_stock


def parse_jsonld(html: str, source: str) -> Iterator[RetailOffer]:
    for block in _JSONLD.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        products: list[dict] = []
        _walk(data, products)
        for prod in products:
            name = prod.get("name")
            if not name:
                continue
            price, in_stock = _offer_price(prod.get("offers"))
            if not price or price <= 0:
                continue
            yield RetailOffer(
                source=source,
                name=str(name),
                price_eur=price,
                url=str(prod.get("url") or prod.get("@id") or ""),
                in_stock=in_stock,
            )


def collect_retail(category: str, shops: dict[str, str] | None = None) -> int:
    """One pass over every shop for every catalog query term. Run daily —
    retail prices do not move hourly, and this is the politeness-sensitive part.
    """
    cat = catalog_mod.get(category)
    shops = shops or DEFAULT_SHOPS
    client = _client()
    today = date.today()
    written = 0

    # Best price per (product, shop) for today.
    best: dict[tuple[str, str], RetailOffer] = {}

    for shop, template in shops.items():
        for term in cat.query_terms:
            url = template.format(q=term.replace(" ", "+"))
            try:
                resp = client.get(url, timeout=settings.request_timeout)
                html = resp.text if resp.status_code == 200 else ""
            except Exception as exc:
                log.warning("retail %s failed for %r: %s", shop, term, exc)
                html = ""
            polite_sleep()
            if not html:
                continue

            found = 0
            for offer in parse_jsonld(html, shop):
                res = match(cat, offer.name)
                if not res.usable or res.product_id is None:
                    continue
                found += 1
                key = (res.product_id, shop)
                if key not in best or offer.price_eur < best[key].price_eur:
                    best[key] = offer
            if found == 0:
                log.debug("no JSON-LD products parsed from %s for %r", shop, term)

    with connect() as conn:
        for (product_id, shop), offer in best.items():
            conn.execute(
                """
                INSERT INTO retail_price (category, product_id, source, observed_on,
                                          price_cents, url, in_stock)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (category, product_id, source, observed_on)
                DO UPDATE SET price_cents = LEAST(retail_price.price_cents,
                                                  EXCLUDED.price_cents),
                              url = EXCLUDED.url,
                              in_stock = EXCLUDED.in_stock
                """,
                (category, product_id, shop, today,
                 int(round(offer.price_eur * 100)), offer.url, offer.in_stock),
            )
            written += 1

    log.info("retail: wrote %d product/shop prices for %s", written, category)
    return written


def current_retail(category: str) -> dict[str, float]:
    """Cheapest in-stock retail price per product, from the last 7 days,
    falling back to the catalog's hint where we have no scraped data."""
    rows = query(
        """
        SELECT product_id, MIN(price_cents) AS cents
        FROM retail_price
        WHERE category = %s AND in_stock AND observed_on > current_date - 7
        GROUP BY product_id
        """,
        (category,),
    )
    out = {r["product_id"]: r["cents"] / 100.0 for r in rows}
    for product in catalog_mod.get(category).products:
        if product.id not in out and product.retail_fallback_eur > 0:
            out[product.id] = product.retail_fallback_eur
    return out
