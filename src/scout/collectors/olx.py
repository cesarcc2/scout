"""OLX.pt collector.

OLX's own web frontend talks to a public-ish JSON API at /api/v1/offers/.
Using it instead of parsing HTML means: stable field names, descriptions
included, no browser needed, and roughly a tenth of the bandwidth.

It sits behind Cloudflare. At the request rates configured here (one request
every few seconds, single IP) a TLS-fingerprint-impersonating client is enough;
no proxies, no headless browser. If you start getting 403s, slow down before
you reach for anything cleverer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterator

from dateutil import parser as dateparser

from ..config import settings
from .base import ScrapedListing, polite_sleep

log = logging.getLogger(__name__)

SITE = "olx.pt"
BASE = "https://www.olx.pt/api/v1/offers/"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.olx.pt/",
    "Origin": "https://www.olx.pt",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _session():
    """Prefer curl_cffi (impersonates a real Chrome TLS handshake)."""
    try:
        from curl_cffi import requests as creq

        return creq.Session(impersonate=settings.user_agent_profile), True
    except Exception:  # pragma: no cover - fallback path
        import httpx

        log.warning("curl_cffi unavailable, falling back to httpx (expect more 403s)")
        return httpx.Client(follow_redirects=True, timeout=settings.request_timeout), False


def _get(session, is_curl: bool, params: dict[str, Any]) -> dict | None:
    for attempt in range(settings.max_retries):
        try:
            if is_curl:
                resp = session.get(
                    BASE, params=params, headers=HEADERS, timeout=settings.request_timeout
                )
            else:
                resp = session.get(BASE, params=params, headers=HEADERS)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 429):
                wait = settings.request_delay * (3 ** (attempt + 1))
                log.warning("olx returned %s, backing off %.0fs", resp.status_code, wait)
                import time

                time.sleep(wait)
                continue
            log.warning("olx returned %s for %s", resp.status_code, params.get("query"))
            return None
        except Exception as exc:  # network hiccup
            log.warning("olx request failed (%s), retry %d", exc, attempt + 1)
            polite_sleep()
    return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _param_value(offer: dict, key: str) -> Any:
    for p in offer.get("params") or []:
        if p.get("key") == key:
            return p.get("value")
    return None


def _price_cents(offer: dict) -> tuple[int | None, str]:
    val = _param_value(offer, "price")
    if not isinstance(val, dict):
        return None, "EUR"
    raw = val.get("value")
    currency = val.get("currency") or "EUR"
    if raw is None:
        return None, currency
    try:
        cents = int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return None, currency
    # OLX uses 0 / 1 for "free" and "swap only" ads. Neither is a market price.
    if cents <= 100:
        return None, currency
    return cents, currency


def _posted_at(offer: dict) -> datetime | None:
    for key in ("created_time", "last_refresh_time", "valid_to_time"):
        val = offer.get(key)
        if val:
            try:
                return dateparser.parse(val)
            except (ValueError, TypeError):
                continue
    return None


def _location(offer: dict) -> str | None:
    loc = offer.get("location") or {}
    city = (loc.get("city") or {}).get("name")
    region = (loc.get("region") or {}).get("name")
    return ", ".join(x for x in (city, region) if x) or None


def parse_offer(offer: dict, category: str) -> ScrapedListing | None:
    oid = offer.get("id")
    if oid is None:
        return None
    price_cents, currency = _price_cents(offer)
    user = offer.get("user") or {}
    return ScrapedListing(
        site=SITE,
        site_listing_id=str(oid),
        category=category,
        url=offer.get("url") or f"https://www.olx.pt/d/anuncio/{oid}",
        title=(offer.get("title") or "").strip(),
        description=(offer.get("description") or "").strip()[:8000],
        price_cents=price_cents,
        currency=currency,
        location=_location(offer),
        seller_id=str(user.get("id")) if user.get("id") is not None else None,
        seller_is_pro=bool(user.get("is_business") or offer.get("business")),
        photo_count=len(offer.get("photos") or []),
        posted_at=_posted_at(offer),
        raw=offer,
    )


# --------------------------------------------------------------------------
# Collector
# --------------------------------------------------------------------------

class OlxCollector:
    site = SITE

    def __init__(self) -> None:
        self._session, self._is_curl = _session()

    def search(self, term: str, category: str,
               max_pages: int | None = None) -> Iterator[ScrapedListing]:
        offset = 0
        for page in range(max_pages or settings.max_pages):
            params = {
                "offset": offset,
                "limit": settings.page_size,
                "query": term,
                "sort_by": "created_at:desc",
                "filter_refiners": "spell_checker",
            }
            data = _get(self._session, self._is_curl, params)
            polite_sleep()
            if not data:
                return
            offers = data.get("data") or []
            if not offers:
                return
            for offer in offers:
                parsed = parse_offer(offer, category)
                if parsed and parsed.title:
                    yield parsed
            # Stop early when the API says there is no next page.
            if not ((data.get("links") or {}).get("next") or {}).get("href"):
                return
            offset += settings.page_size
