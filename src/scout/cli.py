from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table

from . import pipeline
from .config import settings
from .db import init_db
from .normalize import catalog as catalog_mod
from .normalize.run import export_unmatched, normalize_category
from .pricing.compare import compare as compare_products
from .pricing.deals import find_deals
from .pricing.retail import collect_retail
from .pricing.stats import all_distributions

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)

app = typer.Typer(add_completion=False, help="Second-hand price scout")
console = Console()


@app.command("init")
def cmd_init():
    """Create the database schema."""
    init_db()
    console.print("[green]schema ready[/]")


@app.command("collect")
def cmd_collect(category: str = "gpu",
                deep: bool = typer.Option(False, help="query every catalog product by name")):
    """Fetch listings from every configured site."""
    console.print(pipeline.collect(category, deep=deep))


@app.command("bootstrap")
def cmd_bootstrap(category: str = "gpu"):
    """First run: sweep the whole active market and price it immediately.

    Takes 10-20 minutes and leaves you with a real distribution per product,
    built from every listing currently live. No waiting for history.
    """
    with console.status("sweeping the market...") as status:
        def progress(step, total, term):
            status.update(f"[{step}/{total}] {term}")
        console.print(pipeline.bootstrap(category, progress=progress))


@app.command("search")
def cmd_search(query_text: str, category: str = "gpu", pages: int = 2):
    """Search the site right now and price what comes back."""
    rows = pipeline.live_search(query_text, category, pages=pages)
    table = Table(box=None, header_style="dim")
    for col in ("price", "identified as", "vs median", "n", "title"):
        table.add_column(col, justify="right" if col != "title" and col != "identified as" else "left")
    for r in rows:
        table.add_row(
            f"EUR {r['price_eur']:,.0f}",
            r["product_label"] or (f"[dim]filtered: {r['excluded_by']}[/]" if r["excluded_by"] else "[dim]unknown[/]"),
            f"{-r['vs_median']:+.0f}%" if r["vs_median"] is not None else "-",
            str(r["sample_size"]),
            r["title"][:56],
        )
    console.print(table)


@app.command("normalize")
def cmd_normalize(category: str = "gpu",
                  force: bool = typer.Option(False, help="reclassify everything")):
    """Re-derive structured data. Run with --force after editing a catalog."""
    console.print(normalize_category(category, force=force))


@app.command("retail")
def cmd_retail(category: str = "gpu"):
    """Refresh retail baselines. Once a day is plenty."""
    console.print(f"wrote {collect_retail(category)} retail prices")


@app.command("deals")
def cmd_deals(category: str = "gpu", limit: int = 20, min_score: float = 0.0):
    """Show current deals."""
    deals = find_deals(category, limit=limit, min_score=min_score)
    if not deals:
        console.print("[yellow]no deals — needs more history[/]")
        raise typer.Exit()
    table = Table(box=None, header_style="dim")
    for col in ("score", "product", "price", "adj", "median", "vs med", "retail", "where"):
        table.add_column(col, justify="right" if col not in ("product", "where") else "left")
    for d in deals:
        table.add_row(
            f"{d.score:.0f}", d.product_label, f"€{d.price_eur:,.0f}",
            f"€{d.adjusted_eur:,.0f}", f"€{d.median_eur:,.0f}",
            f"-{d.below_median_pct:.0f}%",
            f"{d.retail_ratio * 100:.0f}%" if d.retail_ratio else "—",
            (d.location or "")[:22],
        )
    console.print(table)
    for d in deals[:5]:
        console.print(f"[dim]{d.score:>3.0f}[/] {d.url}")


@app.command("compare")
def cmd_compare(
    category: str = "gpu",
    basis: str = typer.Option("p25", help="p25 | median | best | retail"),
    min_vram: float = typer.Option(0, help="filter: minimum VRAM in GB"),
    max_tdp: float = typer.Option(0, help="filter: maximum board power in W"),
    only: str = typer.Option("", help="comma-separated product ids"),
):
    """Rank products by cost per unit of performance."""
    rows = compare_products(
        category,
        product_ids=[x.strip() for x in only.split(",") if x.strip()] or None,
        basis=basis,
        min_attributes={"vram_gb": min_vram} if min_vram else None,
        max_attributes={"tdp_w": max_tdp} if max_tdp else None,
    )
    cat = catalog_mod.get(category)
    table = Table(box=None, header_style="dim")
    for col in ("product", cat.rank_by, "p25", "median", "retail", "€/pt", "value", "n"):
        table.add_column(col, justify="left" if col == "product" else "right")
    for r in rows:
        table.add_row(
            r.label,
            f"{r.attributes.get(cat.rank_by, 0):.0f}",
            f"€{r.used_p25:,.0f}" if r.used_p25 else "—",
            f"€{r.used_median:,.0f}" if r.used_median else "—",
            f"€{r.retail_eur:,.0f}" if r.retail_eur else "—",
            f"€{r.cost_per_point:.2f}" if r.cost_per_point else "—",
            f"{r.value_index:.0f}" if r.value_index else "—",
            str(r.sample_size),
        )
    console.print(table)


@app.command("stats")
def cmd_stats(category: str = "gpu"):
    """Show the price distribution behind every product."""
    table = Table(box=None, header_style="dim")
    for col in ("product", "n", "sold", "p10", "p25", "median", "p75", "p90"):
        table.add_column(col, justify="left" if col == "product" else "right")
    cat = catalog_mod.get(category)
    for pid, d in all_distributions(category).items():
        if d.n == 0:
            continue
        p = cat.product(pid)
        table.add_row(
            p.label if p else pid, str(d.n), str(d.n_sold_proxy),
            f"€{d.p10:,.0f}", f"€{d.p25:,.0f}", f"€{d.p50:,.0f}",
            f"€{d.p75:,.0f}", f"€{d.p90:,.0f}",
        )
    console.print(table)


@app.command("export-unmatched")
def cmd_export(category: str = "gpu", limit: int = 200):
    """Dump titles the matcher missed, plus the prompt to fix them."""
    path = export_unmatched(category, limit)
    console.print(f"[green]{path}[/]")
    console.print(f"[dim]prompt: {path.parent / (path.stem + '_prompt.md')}[/]")


@app.command("test-alert")
def cmd_test_alert():
    """Fire a test notification at every configured channel."""
    from .alerts.notify import send_test

    results = send_test()
    if not any(v is not None for v in results.values()):
        console.print("[yellow]no channels configured — set SCOUT_DISCORD_WEBHOOK_URL[/]")
        raise typer.Exit(1)
    for channel, ok in results.items():
        if ok is None:
            console.print(f"[dim]{channel}: not configured[/]")
        else:
            console.print(f"[{'green' if ok else 'red'}]{channel}: "
                          f"{'delivered' if ok else 'failed - check the logs'}[/]")


@app.command("cycle")
def cmd_cycle(category: str = "gpu",
              alert: bool = typer.Option(True, help="push notifications")):
    """One full pass: collect → normalize → score → alert."""
    console.print(pipeline.run_cycle(category, alert=alert))


@app.command("serve")
def cmd_serve(scheduler: bool = typer.Option(True, help="run the background loop too")):
    """Start the dashboard (and, by default, the scheduler)."""
    init_db()
    if scheduler:
        from .scheduler import start_scheduler

        start_scheduler()
    from .web.app import serve

    console.print(f"[green]dashboard on http://{settings.host}:{settings.port}[/]")
    serve()


if __name__ == "__main__":
    app()
