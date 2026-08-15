"""Push notifications.

Two channels, both webhook-only so there is no bot to register, no token to
refresh and nothing to keep logged in:

- **Discord** — a webhook URL you create in Channel Settings → Integrations.
  Deals arrive as embeds, batched up to 10 per message, which is both what
  Discord allows and what stops a good sweep from firing twelve separate pings.
- **ntfy** — one notification per deal with a tap-through action, which is the
  better shape on a phone lock screen.

Enable either, both, or neither.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..db import connect, query
from ..pricing.deals import Deal

log = logging.getLogger(__name__)

# Discord accepts at most 10 embeds in one message.
EMBEDS_PER_MESSAGE = 10

# Colours match the dashboard's status palette. The score is always in the
# embed title, so the colour is a redundant cue rather than the only one.
_COLOR_HOT = 0x0CA30C      # status good
_COLOR_WARM = 0xFAB219     # status warning
_COLOR_PLAIN = 0x2A78D6    # series blue


def _headline(deal: Deal) -> str:
    return f"€{deal.price_eur:,.0f} · {deal.product_label} · {deal.score:.0f}/100"


def format_deal(deal: Deal) -> tuple[str, str]:
    """Plain-text rendering, used by ntfy."""
    lines = [
        deal.title[:110],
        "",
        f"€{deal.price_eur:,.0f}"
        + (f" (≈€{deal.adjusted_eur:,.0f} like-for-like)"
           if abs(deal.adjusted_eur - deal.price_eur) > 5 else ""),
        f"{deal.below_median_pct:.0f}% below the €{deal.median_eur:,.0f} median"
        f" — saves about €{deal.saving_eur:,.0f}",
        f"n={deal.sample_size} comparable listings ({deal.confidence} confidence)",
    ]
    if deal.retail_ratio:
        lines.append(f"{deal.retail_ratio * 100:.0f}% of the €{deal.retail_eur:,.0f} retail price")
    if deal.modifiers:
        lines.append("Flags: " + ", ".join(m.replace("_", " ") for m in deal.modifiers))
    if deal.location:
        lines.append(f"{deal.location} · listed {deal.days_listed:.0f}d ago")
    if deal.reasons:
        lines += ["", "· " + "\n· ".join(deal.reasons)]
    if deal.cautions:
        lines += ["", "Caution: " + "; ".join(deal.cautions)]
    return _headline(deal), "\n".join(lines)


# --------------------------------------------------------------------------
# Discord
# --------------------------------------------------------------------------

def build_embed(deal: Deal) -> dict:
    fields = [
        {
            "name": "Price",
            "value": (f"**€{deal.price_eur:,.0f}**"
                      + (f"\n≈€{deal.adjusted_eur:,.0f} like-for-like"
                         if abs(deal.adjusted_eur - deal.price_eur) > 5 else "")),
            "inline": True,
        },
        {
            "name": "vs median",
            "value": (f"−{deal.below_median_pct:.0f}% "
                      f"(€{deal.median_eur:,.0f})\nsaves ≈€{deal.saving_eur:,.0f}"),
            "inline": True,
        },
        {
            "name": "Market",
            "value": f"n={deal.sample_size}\n{deal.confidence} confidence",
            "inline": True,
        },
    ]
    if deal.retail_ratio:
        fields.append({
            "name": "vs retail",
            "value": f"{deal.retail_ratio * 100:.0f}% of €{deal.retail_eur:,.0f}",
            "inline": True,
        })
    if deal.location:
        fields.append({"name": "Where", "value": deal.location, "inline": True})
    fields.append({
        "name": "Listed",
        "value": f"{deal.days_listed:.0f} days ago",
        "inline": True,
    })

    description = deal.title[:240]
    if deal.reasons:
        description += "\n\n" + " · ".join(deal.reasons)
    if deal.cautions:
        description += "\n\n⚠️ **Caution:** " + "; ".join(deal.cautions)
    if deal.modifiers:
        description += "\n`" + "` `".join(m.replace("_", " ") for m in deal.modifiers) + "`"

    color = (_COLOR_HOT if deal.score >= 65
             else _COLOR_WARM if deal.score >= 45 else _COLOR_PLAIN)

    return {
        "title": _headline(deal)[:256],
        "url": deal.url,
        "description": description[:4000],
        "color": color,
        "fields": fields[:25],
        "footer": {"text": f"{deal.site} · score {deal.score:.0f}/100"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _post_discord(payload: dict) -> bool:
    """POST with one retry on Discord's own rate-limit response."""
    url = settings.discord_webhook_url
    for attempt in range(2):
        try:
            resp = httpx.post(url, json=payload, timeout=15)
            if resp.status_code == 429:
                wait = float(resp.json().get("retry_after", 2))
                log.warning("discord rate-limited, waiting %.1fs", wait)
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("discord webhook failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(2)
    return False


def send_discord(deals: list[Deal]) -> int:
    """Batch deals into as few messages as Discord allows.

    One message with ten embeds is a far better notification than ten messages,
    especially after a full market sweep turns up a pile of candidates at once.
    """
    if not settings.discord_webhook_url or not deals:
        return 0

    sent = 0
    for i in range(0, len(deals), EMBEDS_PER_MESSAGE):
        chunk = deals[i:i + EMBEDS_PER_MESSAGE]
        payload = {
            "username": "Scout",
            "content": (f"**{len(deals)} new deal{'s' if len(deals) != 1 else ''}**"
                        if i == 0 else None),
            "embeds": [build_embed(d) for d in chunk],
            "allowed_mentions": {"parse": []},
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        if _post_discord(payload):
            sent += len(chunk)
        # Discord allows 5 requests per 2s per webhook; stay well inside it.
        if i + EMBEDS_PER_MESSAGE < len(deals):
            time.sleep(1.0)
    return sent


def send_discord_test() -> bool:
    """Used by the dashboard's 'Send a test notification' button."""
    if not settings.discord_webhook_url:
        return False
    return _post_discord({
        "username": "Scout",
        "content": "Scout is wired up correctly — deal alerts will arrive here.",
        "allowed_mentions": {"parse": []},
    })


# --------------------------------------------------------------------------
# ntfy
# --------------------------------------------------------------------------

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


def send_ntfy_test() -> bool:
    if not settings.ntfy_topic:
        return False
    try:
        httpx.post(
            f"{settings.ntfy_url.rstrip('/')}/{settings.ntfy_topic}",
            content=b"Scout is wired up correctly - deal alerts will arrive here.",
            headers={"Title": b"Scout test", "Tags": "white_check_mark"},
            timeout=10,
        ).raise_for_status()
        return True
    except Exception as exc:
        log.warning("ntfy test failed: %s", exc)
        return False


def send_test() -> dict[str, bool | None]:
    """None = channel not configured, True/False = delivered or not."""
    return {
        "discord": send_discord_test() if settings.discord_webhook_url else None,
        "ntfy": send_ntfy_test() if settings.ntfy_topic else None,
    }


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def already_alerted(listing_id: int, price_cents: int) -> bool:
    return bool(query(
        "SELECT 1 FROM alerted WHERE listing_id = %s AND price_cents = %s",
        (listing_id, price_cents),
    ))


def record_alerts(deals: list[Deal]) -> None:
    if not deals:
        return
    with connect() as conn:
        for deal in deals:
            conn.execute(
                "INSERT INTO alerted (listing_id, price_cents, score) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (deal.listing_id, int(round(deal.price_eur * 100)), deal.score),
            )


def alerts_configured() -> bool:
    return bool(settings.discord_webhook_url or settings.ntfy_topic)


def push_new_deals(deals: list[Deal], min_score: float = 45.0) -> int:
    """Alert once per (listing, price).

    A seller who drops the price gets a second ping, which is usually the one
    worth acting on.

    When a channel *is* configured but delivery fails, the deal is still
    recorded: a webhook that was down for an hour should not dump a backlog on
    you afterwards. When **no** channel is configured at all we return
    immediately without recording anything — otherwise running alert-free for a
    week would silently mark every deal as already-notified, and the day you
    finally set up Discord you would hear nothing. Notifications are optional;
    quietly poisoning them for later is not an acceptable price for that.
    """
    if not alerts_configured():
        return 0

    fresh = [
        d for d in deals
        if d.score >= min_score
        and not already_alerted(d.listing_id, int(round(d.price_eur * 100)))
    ]
    if not fresh:
        return 0

    delivered = send_discord(fresh)
    for deal in fresh:
        if send_ntfy(deal):
            delivered = max(delivered, 1)

    record_alerts(fresh)
    log.info("alerted on %d deals (top: %s €%.0f)",
             len(fresh), fresh[0].product_label, fresh[0].price_eur)
    return len(fresh)
