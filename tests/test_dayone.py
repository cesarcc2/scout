"""Guards on the 'useful from the first sweep' behaviour."""

from __future__ import annotations

import pytest

from scout.pipeline import deep_terms
from scout.pricing.stats import Distribution
from scout.web import charts


def dist(n: int) -> Distribution:
    return Distribution(product_id="x", n=n, n_sold_proxy=0, p10=1, p25=2,
                        p50=3, p75=4, p90=5, mean=3)


@pytest.mark.parametrize("n,expected", [
    (0, "none"), (3, "none"), (4, "low"), (7, "low"),
    (8, "medium"), (14, "medium"), (15, "high"), (200, "high"),
])
def test_confidence_tiers(n, expected):
    assert dist(n).confidence == expected


def test_deep_terms_cover_every_product():
    terms = deep_terms("gpu")
    # Vendor words are noise in a search box, so product labels get them
    # stripped. (Hand-written broad terms in the catalog are left alone —
    # the author chose those deliberately.)
    assert "geforce rtx 5070 ti" not in terms
    assert "radeon rx 9070 xt" not in terms
    for expected in ("rtx 5070 ti", "rtx 4080 super", "rx 9070 xt", "rtx 4070"):
        assert expected in terms, f"{expected} missing from {terms}"
    # Capacity suffixes narrow a search too much to be useful.
    assert not any(t.endswith("16gb") for t in terms)
    assert len(terms) == len(set(terms)), "duplicate search terms waste requests"


# --------------------------------------------------------------------------
# Charts render without a browser, so they are cheap to assert on.
# --------------------------------------------------------------------------

def test_buckets_cover_all_values():
    values = [100.0, 150.0, 200.0, 250.0, 300.0, 900.0]
    bins = charts.buckets(values)
    assert sum(b.count for b in bins) == len(values)
    assert bins[0].lo == min(values)
    assert bins[-1].hi == pytest.approx(max(values))


def test_histogram_is_empty_safe():
    assert "Not enough listings" in charts.histogram([])


def test_histogram_renders_marks_and_labels():
    svg = charts.histogram([100.0] * 5 + [200.0] * 9 + [300.0] * 3,
                           p25=140, p50=205, highlight=95)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<title>" in svg, "bars need hover tooltips"
    assert "median" in svg and "p25" in svg
    assert "var(--series-1)" in svg and "var(--status-critical)" in svg


def test_meter_keeps_extreme_markers_inside_the_frame():
    """A listing at or below the floor must still draw a full dot."""
    svg = charts.meter(500, 550, 650, 700, value=100)
    cx = float(svg.split('<circle cx="')[1].split('"')[0])
    assert cx >= 4.5, "marker would be clipped at the left edge"
    svg_hi = charts.meter(500, 550, 650, 700, value=9999)
    cx_hi = float(svg_hi.split('<circle cx="')[1].split('"')[0])
    assert cx_hi <= 132 - 4.5, "marker would be clipped at the right edge"


def test_meter_degenerate_distribution():
    assert charts.meter(0, 0, 0, 0, value=10) == ""


def test_reference_labels_do_not_overprint():
    """p25 and the median often sit a few euros apart — the labels must move
    to a second row rather than print on top of each other."""
    svg = charts.histogram([100.0, 101.0, 102.0, 103.0, 104.0], p25=101.5, p50=102.0)
    ys = {seg.split('"')[0] for seg in svg.split('<text x=')[1:]
          for seg in [seg.split('y="')[1]]}
    assert len(ys) > 1, "labels all landed on the same baseline"
