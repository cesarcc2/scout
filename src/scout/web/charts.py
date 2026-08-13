"""Server-rendered SVG charts.

No JavaScript charting library, no CDN, no build step — the homelab box may not
have internet access to a CDN and a dashboard that breaks offline is useless.
Inline SVG inherits the page's CSS custom properties, so light/dark theming and
the whole palette swap happen in one place with no chart code involved.

Follows the house data-viz rules: single sequential hue for a single series,
bars capped at 24px with a 4px rounded data-end and a 2px surface gap, hairline
recessive gridlines, text in ink tokens rather than the series color, native
per-mark hover tooltips, and a table view alongside every chart.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

BAR_MAX = 24.0
BAR_GAP = 2.0
RADIUS = 4.0


def _esc(value: str) -> str:
    return html.escape(str(value), quote=True)


def _money(value: float) -> str:
    return f"€{value:,.0f}"


def _bar_path(x: float, y: float, w: float, h: float, r: float = RADIUS) -> str:
    """Rounded at the data end, square at the baseline."""
    r = min(r, w / 2, h)
    if h <= 0:
        return ""
    return (
        f"M{x:.1f},{y + h:.1f} "
        f"V{y + r:.1f} "
        f"Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} "
        f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"V{y + h:.1f} Z"
    )


@dataclass(slots=True)
class Bucket:
    lo: float
    hi: float
    count: int


def buckets(values: list[float], target: int = 14) -> list[Bucket]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [Bucket(lo, lo, len(values))]
    n = max(5, min(target, max(5, len(values) // 3)))
    width = (hi - lo) / n
    out = [Bucket(lo + i * width, lo + (i + 1) * width, 0) for i in range(n)]
    for v in values:
        idx = min(n - 1, int((v - lo) / width))
        out[idx].count += 1
    return out


def histogram(
    values: list[float],
    p25: float = 0.0,
    p50: float = 0.0,
    highlight: float | None = None,
    highlight_label: str = "this listing",
    width: float = 720,
    height: float = 240,
) -> str:
    """Price distribution for one product. One series, so no legend — the
    heading says what is plotted."""
    bins = buckets(values)
    if not bins:
        return '<p class="muted">Not enough listings yet to draw a distribution.</p>'

    pad_l, pad_r, pad_t, pad_b = 44.0, 14.0, 44.0, 34.0
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    lo, hi = bins[0].lo, bins[-1].hi
    span = (hi - lo) or 1.0
    max_count = max(b.count for b in bins) or 1

    def x_of(price: float) -> float:
        return pad_l + (price - lo) / span * plot_w

    slot_w = plot_w / len(bins)
    bar_w = min(BAR_MAX, max(3.0, slot_w - BAR_GAP))

    parts: list[str] = [
        f'<svg class="chart" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'role="img" aria-label="Price distribution, {len(values)} listings" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]

    # Horizontal gridlines: hairline, solid, recessive. 0 and the max only.
    for frac in (0.0, 0.5, 1.0):
        y = pad_t + plot_h - frac * plot_h
        count = round(max_count * frac)
        parts.append(
            f'<line x1="{pad_l:.1f}" x2="{width - pad_r:.1f}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="var(--grid)" stroke-width="1" shape-rendering="crispEdges"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8:.1f}" y="{y + 4:.1f}" text-anchor="end" '
            f'class="tick">{count}</text>'
        )

    # Bars.
    for b in bins:
        h = b.count / max_count * plot_h
        if h <= 0:
            continue
        cx = x_of((b.lo + b.hi) / 2)
        x = cx - bar_w / 2
        y = pad_t + plot_h - h
        tip = f"{b.count} listing{'s' if b.count != 1 else ''} · {_money(b.lo)}–{_money(b.hi)}"
        parts.append(
            f'<path d="{_bar_path(x, y, bar_w, h)}" fill="var(--series-1)">'
            f"<title>{_esc(tip)}</title></path>"
        )

    # Reference rules, direct-labelled in ink tokens rather than the mark hue.
    # p25 and the median often sit within a few euros of each other, so labels
    # are staggered onto a second row when they would collide rather than being
    # allowed to overprint.
    refs = [(x_of(v), lbl, v) for v, lbl in ((p25, "p25"), (p50, "median"))
            if v and lo <= v <= hi]
    refs.sort()
    last_x, last_row = -1e9, 1
    for x, label, value in refs:
        text = f"{label} {_money(value)}"
        est_w = len(text) * 6.2
        row = 0 if (x - last_x) > est_w else (1 - last_row)
        last_x, last_row = x, row
        label_y = pad_t - 24 + row * 14
        parts.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{label_y + 4:.1f}" '
            f'y2="{pad_t + plot_h:.1f}" stroke="var(--baseline)" stroke-width="1"/>'
        )
        anchor = "start" if x < width - est_w - 20 else "end"
        dx = 5 if anchor == "start" else -5
        parts.append(
            f'<text x="{x + dx:.1f}" y="{label_y:.1f}" text-anchor="{anchor}" '
            f'class="tick">{_esc(text)}</text>'
        )

    # The listing being examined. Status colour always carries a text label —
    # red/green are not distinguishable for every reader, so hue never carries
    # meaning on its own here.
    if highlight is not None and lo - span <= highlight <= hi + span:
        x = max(pad_l, min(width - pad_r, x_of(highlight)))
        parts.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t:.1f}" '
            f'y2="{pad_t + plot_h + 6:.1f}" stroke="var(--status-critical)" '
            f'stroke-width="2"/>'
        )
        parts.append(
            f'<circle cx="{x:.1f}" cy="{pad_t + plot_h + 6:.1f}" r="4" '
            f'fill="var(--status-critical)" stroke="var(--surface-1)" '
            f'stroke-width="2"/>'
        )
        anchor = "start" if x < width - 140 else "end"
        parts.append(
            f'<text x="{x + (6 if anchor == "start" else -6):.1f}" '
            f'y="{pad_t + 12:.1f}" text-anchor="{anchor}" class="tick strong">'
            f"{_esc(highlight_label)} {_money(highlight)}</text>"
        )

    # Baseline + x ticks at clean positions.
    y0 = pad_t + plot_h
    parts.append(
        f'<line x1="{pad_l:.1f}" x2="{width - pad_r:.1f}" y1="{y0:.1f}" y2="{y0:.1f}" '
        f'stroke="var(--baseline)" stroke-width="1" shape-rendering="crispEdges"/>'
    )
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        price = lo + frac * span
        x = pad_l + frac * plot_w
        anchor = "start" if frac == 0 else ("end" if frac == 1 else "middle")
        parts.append(
            f'<text x="{x:.1f}" y="{y0 + 18:.1f}" text-anchor="{anchor}" '
            f'class="tick">{_money(price)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def histogram_table(values: list[float]) -> str:
    """The table view every chart is required to have."""
    bins = buckets(values)
    if not bins:
        return ""
    rows = "".join(
        f"<tr><td>{_money(b.lo)} – {_money(b.hi)}</td>"
        f'<td class="num">{b.count}</td></tr>'
        for b in bins
    )
    return (
        "<details class='tableview'><summary>Table view</summary>"
        "<table class='mini'><thead><tr><th>Price range</th>"
        "<th class='num'>Listings</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def meter(p10: float, p25: float, p75: float, p90: float, value: float,
          width: float = 132, height: float = 18) -> str:
    """Where one listing sits in its market, for a table cell.

    Track is a light step of the same ramp; the interquartile band is the mid
    step; the marker carries a surface ring so it stays legible on top of the
    band. Hover gives the numbers.
    """
    lo, hi = p10, p90
    if hi <= lo:
        return ""
    # Inset so a marker pinned at either extreme still draws its full dot and
    # ring instead of being sliced off by the viewBox.
    inset = 7.0
    span_w = width - inset * 2
    track_y, track_h = height / 2 - 3, 6.0

    def x_of(v: float) -> float:
        return inset + max(0.0, min(1.0, (v - lo) / (hi - lo))) * span_w

    x_lo, x_hi = x_of(p25), x_of(p75)
    x_val = x_of(value)
    tip = (f"{_money(value)} · p10 {_money(p10)} · p25 {_money(p25)} · "
           f"p75 {_money(p75)} · p90 {_money(p90)}")
    return (
        f'<svg class="meter" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" '
        f'aria-label="{_esc(tip)}"><title>{_esc(tip)}</title>'
        # Track: the same hue, washed back, so state reads across the whole bar.
        f'<rect x="{inset:.1f}" y="{track_y:.1f}" width="{span_w:.1f}" '
        f'height="{track_h:.0f}" rx="3" fill="var(--series-1)" opacity="0.18"/>'
        # Interquartile band: where most of the market actually sits.
        f'<rect x="{x_lo:.1f}" y="{track_y:.1f}" width="{max(2.0, x_hi - x_lo):.1f}" '
        f'height="{track_h:.0f}" rx="3" fill="var(--series-1)"/>'
        # This listing. Surface ring keeps it legible on top of the band.
        f'<circle cx="{x_val:.1f}" cy="{height / 2:.1f}" r="4.5" '
        f'fill="var(--status-critical)" stroke="var(--surface-1)" stroke-width="2"/>'
        f"</svg>"
    )


def sparkline(values: list[float], width: float = 120, height: float = 28) -> str:
    """Price trajectory of a single listing. 2px line, round caps, end dot."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = width / (len(values) - 1)
    pts = [
        (i * step, height - 4 - (v - lo) / span * (height - 8))
        for i, v in enumerate(values)
    ]
    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    ex, ey = pts[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" aria-hidden="true">'
        f'<path d="{d}" fill="none" stroke="var(--series-1)" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="var(--series-1)" '
        f'stroke="var(--surface-1)" stroke-width="2"/></svg>'
    )
