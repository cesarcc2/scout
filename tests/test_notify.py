"""Discord payload construction.

Worth testing carefully: a malformed embed is rejected by Discord with a 400
and the alert is simply lost, which on a deal-finder is the worst possible
failure mode — you never learn about the thing you were watching for.
"""

from __future__ import annotations

import json

import pytest

from scout.alerts import notify
from scout.pricing.deals import Deal

# Discord's documented hard limits.
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELDS = 25
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_EMBEDS = 10
MAX_TOTAL = 6000


def make_deal(**over) -> Deal:
    base = dict(
        listing_id=1, site="olx.pt", url="https://www.olx.pt/d/anuncio/1",
        title="RTX 5070 Ti Gigabyte Gaming OC com garantia e fatura",
        location="Porto, Porto", product_id="rtx_5070_ti",
        product_label="GeForce RTX 5070 Ti", price_eur=574.0, adjusted_eur=511.0,
        modifiers=["warranty", "invoice"], median_eur=665.0, p25_eur=641.0,
        p10_eur=602.0, p75_eur=763.0, p90_eur=803.0, pct_rank=0.02,
        retail_eur=879.0, retail_ratio=0.58, price_drop_pct=0.0,
        days_listed=1.0, photo_count=5, sample_size=31, confidence="high",
        score=81.4, reasons=["cheapest 2% of what's listed", "58% of retail"],
        cautions=[],
    )
    base.update(over)
    return Deal(**base)


def embed_size(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for f in embed.get("fields", []):
        total += len(f["name"]) + len(f["value"])
    return total


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_embed_is_valid_and_serialisable():
    embed = notify.build_embed(make_deal())
    json.dumps(embed)  # must not raise
    assert embed["url"].startswith("https://")
    assert len(embed["title"]) <= MAX_TITLE
    assert len(embed["description"]) <= MAX_DESCRIPTION
    assert len(embed["fields"]) <= MAX_FIELDS
    assert embed_size(embed) <= MAX_TOTAL
    for f in embed["fields"]:
        assert 0 < len(f["name"]) <= MAX_FIELD_NAME
        assert 0 < len(f["value"]) <= MAX_FIELD_VALUE


def test_headline_carries_price_product_and_score():
    """Colour must never be the only signal — the title has to say it."""
    embed = notify.build_embed(make_deal())
    assert "€574" in embed["title"]
    assert "RTX 5070 Ti" in embed["title"]
    assert "81/100" in embed["title"]


def test_reasons_and_cautions_reach_the_reader():
    embed = notify.build_embed(make_deal(
        cautions=["listed 61 days and still unsold", "one photo or fewer"]))
    assert "cheapest 2%" in embed["description"]
    assert "Caution" in embed["description"]
    assert "61 days" in embed["description"]


def test_pathological_title_is_truncated_not_rejected():
    embed = notify.build_embed(make_deal(
        title="X" * 5000,
        reasons=["y" * 500] * 20,
        cautions=["z" * 500] * 20,
        product_label="L" * 400,
    ))
    json.dumps(embed)
    assert len(embed["title"]) <= MAX_TITLE
    assert len(embed["description"]) <= MAX_DESCRIPTION
    assert embed_size(embed) <= MAX_TOTAL


def test_missing_retail_is_handled():
    embed = notify.build_embed(make_deal(retail_eur=None, retail_ratio=None))
    assert not any(f["name"] == "vs retail" for f in embed["fields"])


def test_score_colour_tiers():
    assert notify.build_embed(make_deal(score=80))["color"] == notify._COLOR_HOT
    assert notify.build_embed(make_deal(score=50))["color"] == notify._COLOR_WARM
    assert notify.build_embed(make_deal(score=20))["color"] == notify._COLOR_PLAIN


# --------------------------------------------------------------------------
# Batching
# --------------------------------------------------------------------------

def test_batches_respect_the_ten_embed_limit(monkeypatch):
    posted: list[dict] = []
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "https://x.invalid/hook")
    monkeypatch.setattr(notify, "_post_discord", lambda p: posted.append(p) or True)
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)

    deals = [make_deal(listing_id=i) for i in range(23)]
    sent = notify.send_discord(deals)

    assert sent == 23
    assert len(posted) == 3
    assert [len(p["embeds"]) for p in posted] == [10, 10, 3]
    # Only the first message carries the summary line.
    assert "23 new deals" in posted[0]["content"]
    assert "content" not in posted[1]
    # Never let a listing title turn into an @everyone.
    assert all(p["allowed_mentions"] == {"parse": []} for p in posted)


def test_nothing_sent_when_unconfigured(monkeypatch):
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "")
    assert notify.send_discord([make_deal()]) == 0


def test_singular_wording_for_one_deal(monkeypatch):
    posted: list[dict] = []
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "https://x.invalid/hook")
    monkeypatch.setattr(notify, "_post_discord", lambda p: posted.append(p) or True)
    notify.send_discord([make_deal()])
    assert "1 new deal**" in posted[0]["content"]


def test_send_test_reports_unconfigured_as_none(monkeypatch):
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "")
    monkeypatch.setattr(notify.settings, "ntfy_topic", "")
    assert notify.send_test() == {"discord": None, "ntfy": None}


# --------------------------------------------------------------------------
# Delivery failures must not silently swallow future alerts
# --------------------------------------------------------------------------

def test_failed_webhook_does_not_raise(monkeypatch):
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "https://x.invalid/hook")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)

    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.send_discord([make_deal()]) == 0
    assert notify.send_discord_test() is False


# --------------------------------------------------------------------------
# Running with no channels configured at all
# --------------------------------------------------------------------------

def test_unconfigured_alerts_touch_nothing(monkeypatch):
    """The regression that matters most.

    An earlier version recorded every deal in `alerted` even when no channel
    existed to deliver it. Running alert-free for a week therefore marked the
    whole market as already-notified, and the day you finally added a webhook
    you would hear nothing at all. Nothing may be recorded, and the database
    must not even be consulted.
    """
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "")
    monkeypatch.setattr(notify.settings, "ntfy_topic", "")

    touched: list = []
    monkeypatch.setattr(notify, "already_alerted",
                        lambda *a: touched.append("read") or False)
    monkeypatch.setattr(notify, "record_alerts", lambda d: touched.append("write"))

    assert notify.push_new_deals([make_deal() for _ in range(5)]) == 0
    assert touched == []


def test_alerts_configured_detects_either_channel(monkeypatch):
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "")
    monkeypatch.setattr(notify.settings, "ntfy_topic", "")
    assert notify.alerts_configured() is False

    monkeypatch.setattr(notify.settings, "ntfy_topic", "scout-deals")
    assert notify.alerts_configured() is True

    monkeypatch.setattr(notify.settings, "ntfy_topic", "")
    monkeypatch.setattr(notify.settings, "discord_webhook_url", "https://x.invalid/h")
    assert notify.alerts_configured() is True
