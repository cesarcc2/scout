"""The matcher is the load-bearing component: a wrong match silently poisons a
price distribution, which is worse than no match at all. These cases are real
phrasings from OLX.pt / Wallapop-style listings.
"""

from __future__ import annotations

import pytest

from scout.normalize import catalog as catalog_mod
from scout.normalize.matcher import adjusted_cents, match
from scout.normalize.text import normalize


@pytest.fixture(scope="module")
def cat():
    return catalog_mod.get("gpu")


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("RTX 5070Ti", "rtx 5070 ti"),
    ("rtx5070ti", "rtx 5070 ti"),
    ("Placa Gráfica RTX 4070 SUPER 12GB", "placa grafica rtx 4070 super 12gb"),
    ("RX 9070XT 16 GB", "rx 9070 xt 16gb"),
    ("GTX 1080 Ti - 11GO", "gtx 1080 ti 11gb"),
    ("Radeon RX7900XTX!!!", "radeon rx 7900 xtx"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# --------------------------------------------------------------------------
# Variant disambiguation — the thing that actually goes wrong in practice
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("RTX 5070 Ti Gigabyte Gaming OC", "rtx_5070_ti"),
    ("Placa gráfica RTX 5070ti nova", "rtx_5070_ti"),
    ("RTX 5070 ASUS Prime 12GB", "rtx_5070"),
    ("Nvidia RTX 4080 SUPER 16GB MSI", "rtx_4080_super"),
    ("RTX 4080 Gainward Phantom", "rtx_4080"),
    ("RTX 4070 Ti SUPER 16GB Ventus", "rtx_4070_ti_super"),
    ("rtx 4070 ti 12gb", "rtx_4070_ti"),
    ("RTX 4070 Super Founders", "rtx_4070_super"),
    ("Placa Gráfica RTX 4070 12GB", "rtx_4070"),
    ("RX 9070 XT Sapphire Pulse 16GB", "rx_9070_xt"),
    ("Radeon RX 9070 16GB", "rx_9070"),
    ("RX 7900 XTX Nitro+ 24GB", "rx_7900_xtx"),
    ("Radeon RX 7900 XT 20GB", "rx_7900_xt"),
    ("RX 7900 GRE 16GB", "rx_7900_gre"),
    ("RTX 3060 12GB usada", "rtx_3060_12"),
    ("RTX 5060 Ti 16GB", "rtx_5060_ti_16"),
    ("RTX 5060 Ti 8GB", "rtx_5060_ti_8"),
])
def test_rule_match(cat, title, expected):
    res = match(cat, title)
    assert res.kind == "rule", f"{title!r} -> {res.kind}"
    assert res.product_id == expected, f"{title!r} -> {res.product_id}"


def test_base_model_never_steals_ti(cat):
    """The single most important invariant in the catalog."""
    assert match(cat, "RTX 5070 Ti").product_id == "rtx_5070_ti"
    assert match(cat, "RTX 5070").product_id == "rtx_5070"
    assert match(cat, "RTX 4080 Super").product_id == "rtx_4080_super"
    assert match(cat, "RTX 4080").product_id == "rtx_4080"
    assert match(cat, "RX 9070 XT").product_id == "rx_9070_xt"
    assert match(cat, "RX 9070").product_id == "rx_9070"


# --------------------------------------------------------------------------
# Exclusions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,reason", [
    ("Procuro RTX 4070 Super", "wanted_ad"),
    ("COMPRO placa gráfica RTX 3080", "wanted_ad"),
    ("RTX 3080 para peças, não liga", "faulty"),
    ("RTX 3070 avariada com artefactos", "faulty"),
    ("PC Gaming completo i7 + RTX 3070", "whole_system"),
    ("Rig de mineração com 6x RTX 3060", "mining_rig"),
    ("Suporte anti sag para placa gráfica RTX 4090", "water_block_only"),
])
def test_exclusions(cat, title, reason):
    res = match(cat, title)
    assert res.kind == "excluded", f"{title!r} was not excluded"
    assert res.excluded_by == reason
    assert not res.usable


def test_excluded_listings_carry_no_modifiers(cat):
    """Excluded rows must never contribute an adjusted price."""
    res = match(cat, "Procuro RTX 5070 Ti com garantia e fatura")
    assert res.modifiers == []
    assert res.adjust_pct == 0


# --------------------------------------------------------------------------
# Price-normalizing modifiers
# --------------------------------------------------------------------------

def test_modifiers_detected(cat):
    res = match(
        cat,
        "RTX 5070 Ti selada, nunca usada",
        "Ainda com garantia até 2027, com fatura e caixa original.",
    )
    assert res.product_id == "rtx_5070_ti"
    assert set(res.modifiers) >= {"warranty", "invoice", "sealed_new", "boxed"}
    assert res.adjust_pct < 0


def test_adjusted_price_discounts_a_better_listing(cat):
    """A sealed, warrantied card is worth more, so its price is shaved down
    before being compared against bare used units."""
    res = match(cat, "RTX 5070 Ti selada com garantia e fatura")
    assert adjusted_cents(80000, res.adjust_pct) < 80000


def test_mining_wording_penalises(cat):
    res = match(cat, "RTX 3080 usada para mineração, undervolt 24/7")
    assert "mined_on" in res.modifiers
    assert res.adjust_pct > 0
    assert adjusted_cents(30000, res.adjust_pct) > 30000


def test_adjustment_is_clamped(cat):
    assert adjusted_cents(100000, -400) == 65000
    assert adjusted_cents(100000, 400) == 135000
    assert adjusted_cents(None, -10) is None


# --------------------------------------------------------------------------
# Unknown products
# --------------------------------------------------------------------------

def test_unknown_product_is_not_forced_into_a_match(cat):
    """GTX 1080 Ti is deliberately not in the catalog. It must come back
    unmatched rather than fuzzy-matching onto something wrong."""
    res = match(cat, "GTX 1080 Ti 11GB EVGA")
    assert res.product_id is None
    assert res.kind == "none"


def test_unrelated_listing_unmatched(cat):
    res = match(cat, "Cadeira gaming preta como nova")
    assert res.kind == "none"
