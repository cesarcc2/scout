from __future__ import annotations

import pytest

from scout.pricing.stats import percentile_rank, weighted_percentile


def uniform(values):
    return [(v, 1.0) for v in values]


def test_median_of_odd_set():
    assert weighted_percentile(uniform([100, 200, 300]), 0.5) == pytest.approx(200, abs=50)


def test_percentiles_are_ordered():
    data = uniform(list(range(100, 1100, 10)))
    p10 = weighted_percentile(data, 0.10)
    p50 = weighted_percentile(data, 0.50)
    p90 = weighted_percentile(data, 0.90)
    assert p10 < p50 < p90


def test_empty_and_single():
    assert weighted_percentile([], 0.5) == 0.0
    assert weighted_percentile([(742.0, 1.0)], 0.25) == 742.0


def test_weighting_pulls_toward_sold_prices():
    """Sold-proxy listings should drag the median toward prices the market
    actually accepted, not toward optimistic asking prices."""
    asking = [(900.0, 1.0)] * 10
    sold = [(700.0, 1.0)] * 5
    unweighted = weighted_percentile(asking + sold, 0.5)

    sold_weighted = [(700.0, 3.0)] * 5
    weighted = weighted_percentile(asking + sold_weighted, 0.5)

    assert weighted < unweighted


def test_percentile_rank_bounds():
    data = uniform([100, 200, 300, 400, 500])
    assert percentile_rank(data, 50) == 0.0
    assert percentile_rank(data, 600) == 1.0
    assert 0.4 < percentile_rank(data, 300) < 0.6


def test_percentile_rank_empty_is_neutral():
    assert percentile_rank([], 500) == 0.5


def test_a_cheap_listing_ranks_low():
    market = uniform([820, 850, 860, 875, 880, 900, 910, 930, 950, 1000])
    assert percentile_rank(market, 780) < 0.05
    assert percentile_rank(market, 1100) > 0.95
