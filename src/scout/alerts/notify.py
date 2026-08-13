from __future__ import annotations

import logging

import httpx

from ..config import settings
from ..db import connect, query
from ..pricing.deals import Deal

log = logging.getLogger(__name__)


def format_deal(deal: Deal) -> tuple[str, str]:
    title = f"€{deal.price_eur:,.0f} · {deal.product_label} ({deal.score:.0f}/100)"
    lines = [
        deal.title[:110],
        "",
        f"€{deal.price_eur:,.0f}"
        + (f" (≈€{deal.adjusted_eur:,.0f} adjusted)" if abs(deal.adjusted_eur - deal.price_eur) > 5 else ""),
        f"Median €{deal.median_eur:,.0f} · p25 €{deal.p25_eur:,.0f} · n={deal.sample_size}",
        f"{deal.below_median_pct:.0f}% below median",
    ]
    if deal.retail_ratio:
        lines.append(f"Retail €{deal.retail_eur:,.0f} → {deal.retail_ratio * 100:.0f}% of new")
    if deal.modifiers:
        lines.append("Flags: " + ", ".join(deal.modifiers))
    if deal.location:
        lines.append(f"📍 {deal.location}")
    if deal.reasons:
        lines.append("")
        lines.append("· " + "\n· ".join(deal.reasons))
    return title, "\n".join(lines)


def send_ntfy(deal: Deal) -> bool:
    if not settings.ntfy_topic:
        return False
    title, body = format_deal(deal)
    try:
        httpx.post(
            f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic}",
            content=body.encode("utf-8"),
            headers={
                "Title": title.encode("utf-8"),
                "Priority": "4" if deal.score >= 70 else "3",
                "Tags": "moneybag",
                "Click": deal.url,
                "Actions": f"view, Open listing, {deal.url}",
            },
            timeout=10,
        ).raise_for_status()
        return True
    except Exception as exc:
        log.warning("ntfy push failed: %s", exc)
        return False


def send_telegram(deal: Deal) -> bool:
    if not (settings.telegram_token and settings.telegram_chat_id):
        return False
    title, body = format_deal(deal)
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
            json={
                "chat_id": settings.telegram_chat_id,
                "text": f"*{title}*\n\n{body}\n\n{deal.url}",
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        ).raise_for_status()
        return True
    except Exception as exc:
        log.warning("telegram push failed: %s", exc)
        return False


def already_alerted(listing_id: int, price_cents: int) -> bool:
    rows = query(
        "SELECT 1 FROM alerted WHERE listing_id = %s AND price_cents = %s",
        (listing_id, price_cents),
    )
    return bool(rows)


def record_alert(deal: Deal) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO alerted (listing_id, price_cents, score)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (deal.listing_id, int(round(deal.price_eur * 100)), deal.score),
        )


def push_new_deals(deals: list[Deal], min_score: float = 45.0) -> int:
    """Alert once per (listing, price). A seller who drops the price gets a
    second ping, which is usually the one worth acting on."""
    sent = 0
    for deal in deals:
        if deal.score < min_score:
            continue
        cents = int(round(deal.price_eur * 100))
        if already_alerted(deal.listing_id, cents):
            continue
        delivered = send_ntfy(deal) or send_telegram(deal)
        record_alert(deal)
        if delivered:
            sent += 1
            log.info("alerted: %s €%.0f (%.0f)", deal.product_label, deal.price_eur, deal.score)
    return sent
