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
    ("Suporte anti sag para placa gráfica RTX 4090", "accessory_only"),
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
    """A real card that is not in the catalog must come back unmatched rather
    than fuzzy-matching onto the nearest thing that looks similar."""
    for title in ("Radeon VII 16GB HBM2", "Quadro P2000 5GB", "GTX 980 Ti 6GB"):
        res = match(cat, title)
        assert res.product_id is None, f"{title} wrongly matched {res.product_id}"
        assert res.kind == "none"


def test_unrelated_listing_unmatched(cat):
    res = match(cat, "Cadeira gaming preta como nova")
    assert res.kind == "none"


# --------------------------------------------------------------------------
# Regressions found while sourcing real data for the catalog
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Monitor gaming 1080p 144Hz",
    "Setup para jogar em 1080p",
    "Procuro monitor 1080p",
])
def test_screen_resolutions_are_not_model_numbers(cat, title):
    """`1080p` survives the letter/digit split as the tokens {1080, p}, and
    1080 is a GPU model number — so a monitor advertised at 1080p was being
    classified as a GTX 1080 and joining that card's price distribution."""
    assert match(cat, title).product_id is None


def test_a_real_card_still_matches_alongside_a_resolution(cat):
    """Stripping resolutions must not cost us the genuine listing."""
    assert match(cat, "GTX 1080 8GB ideal para 1080p").product_id == "gtx_1080"
    assert match(cat, "RTX 3060 12GB para 1080p gaming").product_id == "rtx_3060_12"
    assert match(cat, "RX 7800 XT excelente a 1440p").product_id == "rx_7800_xt"


@pytest.mark.parametrize("title", [
    "RTX 4070 laptop 8GB",
    "Placa gráfica RTX 3070 de portátil",
    "RTX 4060 notebook",
    "rtx4070 portatil",
])
def test_fuzzy_matching_cannot_bypass_none_of(cat, title):
    """The hole that made `none_of` advisory rather than binding.

    A laptop card is rejected by the rule, falls through to the fuzzy alias
    pass, and `token_set_ratio` scores it 100 against the desktop alias because
    that scorer ignores extra tokens. A part worth a third of the price then
    joined the desktop distribution with no error anywhere.
    """
    assert match(cat, title).product_id is None


def test_fuzzy_matching_still_works_for_real_shorthand(cat):
    for title, expected in [("rtx4070 gaming oc", "rtx_4070"),
                            ("9070xt sapphire nitro", "rx_9070_xt"),
                            ("5070ti gigabyte", "rtx_5070_ti")]:
        assert match(cat, title).product_id == expected


@pytest.mark.parametrize("title,present,absent", [
    ("RTX 4070 com garantia até 2027",       {"warranty"},            set()),
    ("RTX 4070 ainda em garantia",           {"warranty"},            set()),
    ("RTX 4070 sem garantia",                {"no_returns"},          {"warranty"}),
    ("RTX 4070 s/ garantia",                 set(),                   {"warranty"}),
    ("RTX 4070 sem fatura",                  set(),                   {"invoice"}),
    ("RTX 4070 sem caixa",                   set(),                   {"boxed"}),
    ("RTX 4070 nunca minou",                 set(),                   {"mined_on"}),
    ("RTX 4070 sem mineração",               set(),                   {"mined_on"}),
    ("RTX 4070 usada para mineração",        {"mined_on"},            set()),
    ("RTX 4070 c/ garantia e fatura",        {"warranty", "invoice"}, set()),
])
def test_portuguese_negation_does_not_invert_a_modifier(cat, title, present, absent):
    """"sem garantia" means NO warranty. Unguarded, it matched the warranty
    pattern and handed the listing an 8% discount for a feature it lacks —
    and "em garantia" matched inside "s|em garantia" for good measure."""
    mods = set(match(cat, title).modifiers)
    assert present <= mods, f"{title}: expected {present}, got {sorted(mods)}"
    assert not (absent & mods), f"{title}: should not carry {absent & mods}"


def test_sem_artefactos_advertises_a_healthy_card(cat):
    """It reads like the faulty pattern but means the opposite."""
    healthy = match(cat, "RTX 3080 sem artefactos, a funcionar bem")
    assert healthy.product_id == "rtx_3080_10"
    broken = match(cat, "RTX 3080 com artefactos na imagem")
    assert broken.kind == "excluded" and broken.excluded_by == "faulty"


def test_backplate_as_a_feature_is_not_an_accessory_listing(cat):
    assert match(cat, "Backplate para RTX 3080").kind == "excluded"
    assert match(cat, "RTX 3080 com backplate branca").product_id == "rtx_3080_10"


@pytest.mark.parametrize("title,expected", [
    ("RTX 5060 Ti 16GB Gigabyte", "rtx_5060_ti_16"),
    ("RTX 5060 Ti Asus", "rtx_5060_ti_8"),
    ("Placa gráfica RTX 5060 MSI", "rtx_5060"),
    ("RTX 3090 Ti Founders", "rtx_3090_ti"),
    ("RTX 3090 24GB", "rtx_3090"),
    ("RTX 3060 Ti LHR", "rtx_3060_ti"),
    ("RTX 3060 12GB", "rtx_3060_12"),
    ("RX 9070 GRE 12GB", "rx_9070_gre"),
    ("RX 9070 XT Sapphire", "rx_9070_xt"),
    ("Radeon RX 9070 16GB", "rx_9070"),
    ("RX 9060 XT 16GB", "rx_9060_xt_16"),
    ("RX 9060 XT", "rx_9060_xt_8"),
    ("RX 7900 GRE", "rx_7900_gre"),
    ("RX 7900 XT 20GB", "rx_7900_xt"),
    ("RX 7600 XT 16GB", "rx_7600_xt"),
    ("RX 7600 8GB", "rx_7600"),
    ("RX 6800 XT Red Devil", "rx_6800_xt"),
    ("RX 6800 16GB", "rx_6800"),
    ("RX 6600 XT", "rx_6600_xt"),
    ("RX 6600 8GB", "rx_6600"),
    ("GTX 1660 Super 6GB", "gtx_1660_super"),
    ("GTX 1660 Ti", "gtx_1660_ti"),
    ("GTX 1080 Ti 11GB", "gtx_1080_ti"),
    ("RTX 2060 Super", "rtx_2060_super"),
    ("Intel Arc B580 12GB", "arc_b580"),
])
def test_every_family_disambiguates(cat, title, expected):
    assert match(cat, title).product_id == expected


def test_catalog_has_no_swallowing_rules():
    """Run the editor's lint over the shipped catalog itself."""
    import yaml

    from scout.normalize import editor

    data = yaml.safe_load(open("catalogs/gpu.yaml", encoding="utf-8"))
    assert editor.lint_variant_swallowing(data["products"]) == []


def test_perf_index_ordering_is_sane():
    """A cheap sanity check on the sourced benchmark numbers: within a family,
    a Ti must not rank below its non-Ti sibling."""
    import yaml

    data = yaml.safe_load(open("catalogs/gpu.yaml", encoding="utf-8"))
    perf = {p["id"]: (p.get("attributes") or {}).get("perf_index")
            for p in data["products"]}
    for faster, slower in [
        ("rtx_5090", "rtx_5080"), ("rtx_5080", "rtx_5070_ti"),
        ("rtx_5070_ti", "rtx_5070"), ("rtx_5070", "rtx_5060_ti_16"),
        ("rtx_4090", "rtx_4080_super"), ("rtx_4080_super", "rtx_4080"),
        ("rtx_4070_ti_super", "rtx_4070_ti"), ("rtx_4070_ti", "rtx_4070_super"),
        ("rx_9070_xt", "rx_9070"), ("rx_9070", "rx_9070_gre"),
        ("rx_7900_xtx", "rx_7900_xt"), ("rx_7900_xt", "rx_7800_xt"),
        ("rtx_3090", "rtx_3080_10"), ("rtx_3080_10", "rtx_3070"),
    ]:
        assert perf[faster] and perf[slower]
        assert perf[faster] > perf[slower], f"{faster} should beat {slower}"
