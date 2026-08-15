from __future__ import annotations

import csv
import io
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import jobs, pipeline
from ..alerts import notify
from ..config import settings
from ..db import query
from ..normalize import catalog as catalog_mod
from ..normalize.run import PROMPT_TEMPLATE, normalize_category
from ..pricing import retail
from ..pricing.compare import compare
from ..pricing.deals import active_listings, find_deals
from ..pricing.stats import all_distributions, distribution
from . import charts, uistate

app = FastAPI(title="Scout", docs_url="/api")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Chart helpers are called straight from the templates so a page can drop a
# chart in without the route having to know about it.
templates.env.globals.update(
    meter=charts.meter,
    sparkline=charts.sparkline,
    settings=settings,
    # Lets the nav badge render on every page without each route plumbing it
    # through. Cached, so it costs a dict lookup rather than a rescore.
    new_deal_count=uistate.new_deal_count,
)


def _categories() -> list[str]:
    return list(catalog_mod.load_all()) or ["gpu"]


def _default_category() -> str:
    return _categories()[0]


def _counts(category: str) -> dict:
    row = query(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE disappeared_at IS NULL) AS active
        FROM listing WHERE category = %s
        """,
        (category,),
    )[0]
    return {"total": row["total"], "active": row["active"]}


# --------------------------------------------------------------------------
# Deals
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def deals_page(
    request: Request,
    category: str | None = None,
    product: str = "",
    max_price: float | None = None,
    pct: float = 0.20,
    min_score: float | None = None,
    confidence: str = "low",
    location: str = "",
):
    category = category or _default_category()
    cat = catalog_mod.get(category)
    counts = _counts(category)

    seen = uistate.seen_at(category) if counts["total"] else None
    deals = find_deals(
        category,
        limit=80,
        min_score=min_score or 0.0,
        max_percentile=pct,
        product_ids=[product] if product else None,
        max_price=max_price,
        location=location or None,
        min_confidence=confidence,
        seen_at=seen,
    ) if counts["total"] else []

    return templates.TemplateResponse(
        request, "deals.html",
        {
            "page": "deals", "category": category, "catalog": cat,
            "products": sorted(cat.products, key=lambda p: p.label),
            "deals": deals,
            "total_listings": counts["total"],
            "active_listings": counts["active"],
            "new_count": sum(1 for d in deals if d.is_new),
            "seen_at": seen,
            "alerts_on": notify.alerts_configured(),
            "f": {"product": product, "max_price": max_price, "pct": pct,
                  "min_score": min_score, "confidence": confidence,
                  "location": location},
        },
    )


@app.post("/deals/seen")
def deals_mark_seen(category: str = Form("")):
    uistate.mark_seen(category or _default_category())
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------
# Deal feed — the zero-configuration alternative to push notifications
# --------------------------------------------------------------------------

@app.get("/feed.xml")
def deal_feed(request: Request, category: str | None = None,
              min_score: float = 45.0, limit: int = 40):
    """RSS 2.0 of current deals.

    Point any feed reader at this and you get deal notifications without
    configuring a webhook, without Scout needing outbound network access, and
    without an account anywhere. Same score threshold the push channels use.
    """
    category = category or _default_category()
    base = str(request.base_url).rstrip("/")
    deals = find_deals(category, limit=limit, min_score=min_score)

    def esc(text) -> str:
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def cdata(html_fragment: str) -> str:
        """Wrap an HTML fragment for <description>.

        The alternative is escaping the whole fragment a second time on top of
        the per-value escaping, which works but produces `&amp;lt;b&amp;gt;`
        in the source and is miserable to debug. CDATA keeps the payload
        readable; the only thing that can break it is a literal `]]>`, which
        gets split across two sections.
        """
        return "<![CDATA[" + html_fragment.replace("]]>", "]]]]><![CDATA[>") + "]]>"

    items = []
    for d in deals:
        body = [
            # The seller's own wording, which is often the deciding detail
            # ("c/ garantia", "para peças") and is not in the item title.
            f"<p>{esc(d.title)}</p>",
            f"<p><strong>€{d.price_eur:,.0f}</strong> — {esc(d.product_label)} "
            f"({d.score:.0f}/100)</p>",
            f"<p>{d.below_median_pct:.0f}% below the €{d.median_eur:,.0f} median, "
            f"saving about €{d.saving_eur:,.0f}. "
            f"n={d.sample_size} comparable listings ({d.confidence} confidence).</p>",
        ]
        if d.retail_ratio:
            body.append(f"<p>{d.retail_ratio * 100:.0f}% of the "
                        f"€{d.retail_eur:,.0f} retail price.</p>")
        if d.location:
            body.append(f"<p>{esc(d.location)} · listed {d.days_listed:.0f} days ago</p>")
        if d.reasons:
            body.append("<ul>" + "".join(f"<li>{esc(r)}</li>" for r in d.reasons) + "</ul>")
        if d.cautions:
            body.append("<p><strong>Caution:</strong> "
                        + esc("; ".join(d.cautions)) + "</p>")

        items.append(
            "  <item>\n"
            f"    <title>€{d.price_eur:,.0f} · {esc(d.product_label)} · "
            f"{d.score:.0f}/100</title>\n"
            f"    <link>{esc(d.url)}</link>\n"
            f"    <guid isPermaLink=\"false\">scout-{d.listing_id}-"
            f"{int(d.price_eur * 100)}</guid>\n"
            f"    <description>{cdata(''.join(body))}</description>\n"
            f"    <category>{esc(d.product_label)}</category>\n"
            "  </item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n<channel>\n'
        f"  <title>Scout — {esc(category)} deals</title>\n"
        f"  <link>{esc(base)}/</link>\n"
        "  <description>Second-hand listings priced below their own "
        "market.</description>\n"
        "  <ttl>30</ttl>\n"
        + "\n".join(items) +
        "\n</channel>\n</rss>\n"
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------

@app.get("/products", response_class=HTMLResponse)
def products_page(request: Request, category: str | None = None):
    category = category or _default_category()
    cat = catalog_mod.get(category)
    dists = all_distributions(category)
    retail_prices = retail.current_retail(category)

    rows = []
    for product in cat.products:
        dist = dists[product.id]
        retail_eur = retail_prices.get(product.id)
        rows.append({
            "product": product, "dist": dist, "retail": retail_eur,
            "ratio": (dist.p50 / retail_eur) if (retail_eur and dist.p50) else None,
        })
    rows.sort(key=lambda r: (r["dist"].n == 0, -r["dist"].n))

    return templates.TemplateResponse(
        request, "products.html",
        {"page": "products", "category": category, "rows": rows,
         "window_days": settings.stats_window_days},
    )


@app.get("/product/{product_id}", response_class=HTMLResponse)
def product_page(request: Request, product_id: str, category: str | None = None):
    category = category or _default_category()
    cat = catalog_mod.get(category)
    product = cat.product(product_id)
    if product is None:
        return RedirectResponse("/products", status_code=303)

    dist = distribution(category, product_id)
    listings = active_listings(category, product_id)
    retail_eur = retail.current_retail(category).get(product_id)

    return templates.TemplateResponse(
        request, "product.html",
        {
            "page": "products", "category": category, "product": product,
            "dist": dist, "listings": listings, "retail": retail_eur,
            "ratio": (dist.p50 / retail_eur) if (retail_eur and dist.p50) else None,
            "attributes": cat.attributes,
            "chart": charts.histogram(
                dist.values, p25=dist.p25, p50=dist.p50,
                highlight=listings[0]["adjusted_eur"] if listings else None,
                highlight_label="cheapest live",
            ),
            "chart_table": charts.histogram_table(dist.values),
        },
    )


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------

@app.get("/compare", response_class=HTMLResponse)
def compare_page(
    request: Request,
    category: str | None = None,
    basis: str = Query("p25", pattern="^(p25|median|best|retail)$"),
    min_vram: float | None = None,
    max_tdp: float | None = None,
    only: str = "",
):
    category = category or _default_category()
    cat = catalog_mod.get(category)
    rows = compare(
        category,
        product_ids=[x.strip() for x in only.split(",") if x.strip()] or None,
        basis=basis,
        min_attributes={"vram_gb": min_vram} if min_vram else None,
        max_attributes={"tdp_w": max_tdp} if max_tdp else None,
    )
    return templates.TemplateResponse(
        request, "compare.html",
        {
            "page": "compare", "category": category, "rows": rows,
            "rank_by": cat.rank_by,
            "rank_by_label": cat.attributes.get(cat.rank_by, {}).get("label", cat.rank_by),
            "f": {"basis": basis, "min_vram": min_vram, "max_tdp": max_tdp, "only": only},
        },
    )


# --------------------------------------------------------------------------
# Live search
# --------------------------------------------------------------------------

@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", pages: int = 2,
                category: str | None = None):
    category = category or _default_category()
    results = pipeline.live_search(q, category, pages=max(1, min(pages, 5))) if q else []
    return templates.TemplateResponse(
        request, "search.html",
        {"page": "search", "category": category, "q": q, "pages": pages,
         "results": results,
         "dists": all_distributions(category) if q else {}},
    )


# --------------------------------------------------------------------------
# Catalog review
# --------------------------------------------------------------------------

@app.get("/catalog", response_class=HTMLResponse)
def catalog_page(request: Request, category: str | None = None):
    category = category or _default_category()
    cat = catalog_mod.get(category)
    rows = query(
        """
        SELECT title_hash, sample_title, occurrences, first_seen, last_seen
        FROM unmatched_title WHERE category = %s AND NOT resolved
        ORDER BY occurrences DESC, last_seen DESC LIMIT 300
        """,
        (category,),
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "occurrences"])
    for r in rows:
        writer.writerow([r["sample_title"], r["occurrences"]])

    return templates.TemplateResponse(
        request, "catalog.html",
        {"page": "catalog", "category": category, "catalog": cat, "rows": rows,
         "prompt": PROMPT_TEMPLATE.format(category=category),
         "csv": buf.getvalue(),
         "deep_terms": len(pipeline.deep_terms(category))},
    )


@app.post("/catalog/dismiss")
def catalog_dismiss(title_hash: str = Form(...), category: str = Form("")):
    category = category or _default_category()
    query(
        "UPDATE unmatched_title SET resolved = TRUE "
        "WHERE category = %s AND title_hash = %s RETURNING 1",
        (category, title_hash),
    )
    return RedirectResponse("/catalog", status_code=303)


# --------------------------------------------------------------------------
# Status & jobs
# --------------------------------------------------------------------------

@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request, category: str | None = None, tested: int = 0):
    category = category or _default_category()
    test_result = _LAST_TEST if tested else None

    row = query(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE disappeared_at IS NULL) AS active,
               COUNT(*) FILTER (WHERE disappeared_at IS NOT NULL) AS disappeared,
               COUNT(*) FILTER (
                   WHERE disappeared_at IS NOT NULL AND seen_count > 1
                     AND disappeared_at - first_seen < make_interval(hours => %s)
               ) AS sold_proxy,
               to_char(MAX(last_seen), 'DD Mon HH24:MI') AS last_seen,
               to_char(MIN(first_seen), 'DD Mon YYYY') AS oldest
        FROM listing WHERE category = %s
        """,
        (settings.sold_proxy_hours, category),
    )[0]
    stats = dict(row)
    stats["alerted"] = query("SELECT COUNT(*) AS n FROM alerted")[0]["n"]

    coverage = query(
        "SELECT match_kind, COUNT(*) AS n FROM normalized WHERE category = %s "
        "GROUP BY match_kind",
        (category,),
    )
    totals = {r["match_kind"]: r["n"] for r in coverage}
    matched = totals.get("rule", 0) + totals.get("fuzzy", 0)
    classifiable = matched + totals.get("none", 0)

    retail_rows = query(
        """
        SELECT source, COUNT(DISTINCT product_id) AS n,
               to_char(MAX(observed_on), 'DD Mon') AS last
        FROM retail_price WHERE category = %s GROUP BY source ORDER BY source
        """,
        (category,),
    )

    return templates.TemplateResponse(
        request, "status.html",
        {
            "page": "status", "category": category, "stats": stats,
            "totals": totals,
            "coverage_pct": (matched / classifiable * 100) if classifiable else 0.0,
            "unmatched_count": query(
                "SELECT COUNT(*) AS n FROM unmatched_title "
                "WHERE category = %s AND NOT resolved", (category,))[0]["n"],
            "retail_rows": retail_rows,
            "cycle_minutes": settings.cycle_minutes,
            "test_result": test_result,
            "test_sent": bool(test_result and any(
                v is not None for v in test_result.values())),
        },
    )


# Result of the most recent "send a test notification" click, so the redirect
# back to /status can show what happened.
_LAST_TEST: dict[str, bool | None] = {}


@app.post("/alerts/test")
def alerts_test():
    global _LAST_TEST
    _LAST_TEST = notify.send_test()
    return RedirectResponse("/status?tested=1", status_code=303)


def _launch(name: str, fn) -> RedirectResponse:
    jobs.start(name, fn)
    return RedirectResponse("/status", status_code=303)


@app.post("/jobs/bootstrap")
def job_bootstrap(category: str = Form("")):
    cat = category or _default_category()
    return _launch("full market sweep",
                   lambda progress: pipeline.bootstrap(cat, progress=progress))


@app.post("/jobs/cycle")
def job_cycle(category: str = Form("")):
    cat = category or _default_category()
    return _launch("quick cycle", lambda progress: pipeline.run_cycle(cat))


@app.post("/jobs/retail")
def job_retail(category: str = Form("")):
    cat = category or _default_category()
    return _launch("retail refresh",
                   lambda progress: {"prices": retail.collect_retail(cat)})


@app.post("/jobs/normalize")
def job_normalize(category: str = Form("")):
    cat = category or _default_category()
    return _launch("reclassify",
                   lambda progress: normalize_category(cat, force=True))


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------

@app.get("/api/job")
def api_job():
    return jobs.current().as_dict()


@app.get("/api/deals")
def api_deals(category: str = "gpu", min_score: float = 0.0, limit: int = 50):
    return [asdict(d) for d in find_deals(category, limit=limit, min_score=min_score)]


@app.get("/api/compare")
def api_compare(
    category: str = "gpu",
    basis: str = Query("p25", pattern="^(p25|median|best|retail)$"),
    min_vram_gb: float | None = None,
    max_tdp_w: float | None = None,
):
    mins = {"vram_gb": min_vram_gb} if min_vram_gb else None
    maxs = {"tdp_w": max_tdp_w} if max_tdp_w else None
    return [asdict(r) for r in compare(category, basis=basis,
                                       min_attributes=mins, max_attributes=maxs)]


@app.get("/api/health")
def health():
    row = query("SELECT COUNT(*) AS n, MAX(last_seen) AS last FROM listing")[0]
    return {"listings": row["n"], "last_seen": row["last"], "ok": True}


def serve() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
