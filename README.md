# Scout — second-hand price intelligence for a homelab

Tracks classified-ad listings, builds a price distribution per product from
**the market as it actually is**, and tells you when something is genuinely
cheap — both against the used market and against retail. Also ranks different
products against each other by cost per unit of performance, so "5070 Ti or
9070 XT or 4080 Super?" becomes a sorted table rather than an argument.

Comes with a web UI, and works from the first sweep — no warm-up period.

**AI calls in the steady-state pipeline: zero.**

That is the design, not a limitation. "Is €640 a good price for an RTX 5070 Ti
in Portugal?" is a statistics question, and the answer is sitting in the sixty
other 5070 Ti ads that are live on OLX this minute. A percentile over those
beats any model, because no model has that data. The one job a language model
is actually good at here — reading messy ad titles — is a batch job you run by
hand once a month.

---

## What it does

```
OLX.pt ──► collector ──► raw listings (kept forever, incl. disappearances)
                              │
                              ▼
                    deterministic normalizer          ← YAML catalog, no AI
                    (regex + fuzzy, ~99% coverage)
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
     price distribution   retail baseline   spec table
     (p10/p25/median,     (JSON-LD from     (perf index,
      sold-weighted)       PT/ES shops)      VRAM, TDP)
              └───────────────┼────────────────┘
                              ▼
                    deal score + value ranking
                              │
                      ntfy / Telegram push
                        + web dashboard
```

### The parts that make it work

**Posting dates, not discovery dates.** OLX tells you when each ad went up, so
listing age is real from the very first sweep. A cheap ad posted this morning is
the one worth driving for; a cheap ad still sitting there after two months is
cheap for a reason nobody wrote down, and gets flagged as such.

**Disappearance tracking.** Every listing that vanishes within 48 hours of being
posted is treated as *probably sold*. Those prices get double weight in the
distribution. This is the closest thing to real transaction data you can get
without an API, and it's free — you only have to not delete anything. This is
the one signal that genuinely needs time to accumulate; everything else works
from a single snapshot.

**Confidence instead of silence.** A product with six comparable listings gets
scored and labelled *low confidence*, not hidden. Six listings tell you
something real — just less than forty — and an empty dashboard tells you
nothing at all. The Deals page lets you raise the bar when you want to.

**Price normalization.** A sealed card with warranty and an invoice is worth
more than a bare one. Modifiers in the catalog shave that premium off before
comparison, so you're comparing like with like. Mining wording adds a penalty.
"Negociável" discounts the asking price, because it isn't the real one.

**Variant disambiguation.** The single most common way these systems produce
garbage is letting an "RTX 5070" rule swallow "RTX 5070 Ti" listings, which
drags one distribution up and the other down. Every catalog rule has a
`none_of` list, and the test suite asserts the invariant directly.

**Cross-product ranking is a lookup table.** `perf_index` comes from a YAML
file you edit twice a year. Never ask a model to recall benchmark numbers — it
will produce plausible ones, and plausible-but-wrong is the worst possible
input to a ranking.

---

## Quick start

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and SCOUT_NTFY_TOPIC
docker compose up -d
docker compose exec scout python -m scout.cli init
```

Open `http://<homelab>:8077` and press **Sweep the market now**.

That first sweep queries every product in the catalog by name, pages deep, and
takes 10–20 minutes at the polite request rate. When it finishes you have a
complete price distribution per product built from every listing currently live
on OLX — **there is no warm-up period.** Everything a listing needs to be scored
(where it sits in its market, how it compares to retail, how long it has been
sitting there) comes from that one snapshot, because OLX publishes each ad's
posting date and Scout uses it as the listing's real age.

What history adds later is the *sold* signal — which listings vanish quickly,
and therefore what the market actually pays rather than what it asks. That makes
the numbers better over the following weeks. It was never needed to get started.

After that the scheduler keeps it current: a quick cycle every 45 minutes
(jittered), retail refresh once a day.

### The web UI

| Page | What it's for |
|---|---|
| **Deals** | What's underpriced right now, filtered by product, budget, location, and how much data is behind the call |
| **Products** | Every catalog product with its live price distribution |
| **Product detail** | Price histogram, the p25/median markers, and every live listing ranked |
| **Compare** | Cost per point of performance, with min-VRAM and max-TDP filters |
| **Live search** | Type anything, fetch OLX right now, get it priced against the catalog immediately |
| **Catalog** | The unrecognised titles plus a copy-paste prompt — the monthly AI chore |
| **Status** | Coverage, collection stats, and buttons to run any job with live progress |

No CDN, no JavaScript framework, no build step: charts are server-rendered SVG,
so the dashboard works on a box with no internet access. Light and dark themes
are both hand-stepped rather than auto-inverted.

### CLI

Everything in the UI is also a command:

```bash
python -m scout.cli bootstrap                      # the full first sweep
python -m scout.cli search "rtx 5070 ti"           # fetch and price it now
python -m scout.cli stats                          # the distributions
python -m scout.cli deals --limit 20               # what's cheap right now
python -m scout.cli compare --min-vram 16 --max-tdp 320
python -m scout.cli compare --basis retail         # is used even worth it?
python -m scout.cli normalize --force              # after editing a catalog
python -m scout.cli export-unmatched               # the monthly AI chore
```

---

## The AI part (all of it)

Once a month:

```bash
python -m scout.cli export-unmatched --category gpu
```

This writes `data/unmatched_gpu.csv` — titles the matcher couldn't classify,
sorted by frequency — and `data/unmatched_gpu_prompt.md`, a ready-made prompt.
Paste both into a Claude chat, get YAML back, append it to `catalogs/gpu.yaml`,
run `normalize --force`, and your entire history is reclassified.

That's it. No API key, no subscription automation, no pipeline that breaks when
an auth token expires. Roughly one chat message a month, using the Teams seat
you already have, for the one task where a model genuinely beats regex.

If you later want the risk-analysis step automated — reading descriptions of
the top few candidates for scam signals — that's the natural place to spend a
few euros of API credit on Haiku. It is deliberately not wired in, because it
isn't needed for the system to be useful.

---

## Adding a category

Nothing in `src/` knows what a GPU is. Drop `catalogs/bikes.yaml` in, restart,
and the whole pipeline works on bikes. See `catalogs/README.md` for the format.

The only category-specific work is the product list and the `perf_index`
equivalent — whatever numeric attribute makes different products comparable.
For bikes that might be frame size; for phones, a benchmark score; for some
categories there isn't one, and you just leave `rank_by` empty and use the
per-product distributions.

---

## Adding Wallapop

Deliberately not included yet, because it is a different class of problem and
would have held up everything above.

OLX exposes a clean JSON API and sits behind ordinary Cloudflare — a
TLS-impersonating HTTP client at one request per few seconds is enough.
Wallapop's `api.wallapop.com/api/v3/search` requires a signed `X-Signature`
header plus DataDome cookies. Two viable routes:

1. Reverse the signature out of their JS bundle. Fast when it works, breaks
   whenever they rotate it.
2. Run a Playwright container with a persistent profile, let the real page make
   its own XHRs, and intercept the responses. Uglier, survives their changes.

Either way it slots in as `src/scout/collectors/wallapop.py` implementing the
same `Collector` protocol and yielding `ScrapedListing` — the normalizer,
pricing and alerting layers need no changes. Add it to `COLLECTORS` in
`pipeline.py`.

---

## Before you trust the compare view

The `perf_index` values in `catalogs/gpu.yaml` are **approximations I have not
verified**. Replace them with TechPowerUp relative-performance percentages or
your own 3DMark numbers before making a buying decision on them. Everything
else in the system is derived from data it collects itself; this one table is
the exception, and it's plain YAML precisely so it's a two-minute fix.

The retail shop URLs in `src/scout/pricing/retail.py` also need a once-over —
shops change search URL schemes, and a shop that stops returning JSON-LD gives
you zero rows (logged), not wrong prices. Catalog `retail_fallback_eur` values
cover the gap until real retail data arrives.

---

## Running it responsibly

- Default delay is 4s ± jitter between requests, single IP, ~8 pages per query
  term. Don't speed it up. Getting IP-banned costs days of data; being slow
  costs nothing, because second-hand listings don't move in seconds.
- Keep the data to yourself. Personal price tracking is one thing;
  redistributing scraped listings is where the terms-of-service grey area stops
  being grey.
- `mark_disappeared` is a heuristic — a listing can also vanish because the
  seller edited the title out of your search terms. It requires several missed
  sweeps before believing it, but treat the sold-proxy signal as directional,
  not exact.

---

## Tests

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest tests -q                 # 60 unit tests, no DB needed

# end-to-end against a real Postgres, with fabricated listings
SCOUT_DSN=postgresql://scout:scout@localhost:5432/scout \
  PYTHONPATH=src python tests/e2e_synthetic.py

# the day-one path: one sweep, zero history, must still produce deals
SCOUT_DSN=... PYTHONPATH=src python tests/e2e_synthetic.py --day-one
```

`--day-one` is the important one: it seeds a single sweep with no
disappearances, no price history and nothing seen twice, then asserts that deals
still come out. That's the regression guard on the "no waiting" promise.

The unit tests focus on the matcher, because that's where a silent bug does the
most damage: a wrong match corrupts a price distribution and every deal derived
from it, with no error anywhere.

## Layout

```
catalogs/gpu.yaml            products, match rules, modifiers  ← you edit this
src/scout/
  collectors/olx.py          OLX.pt JSON API adapter
  collectors/base.py         upsert, price history, disappearance tracking
  normalize/text.py          title normalization
  normalize/matcher.py       rule + fuzzy matching, price adjustment
  normalize/run.py           batch classify, unmatched export + prompt
  pricing/stats.py           weighted percentiles, sold-proxy weighting
  pricing/retail.py          JSON-LD retail scraper, PT/ES shops
  pricing/deals.py           deal scoring with human-readable reasons
  pricing/compare.py         cost per unit of performance
  alerts/notify.py           ntfy / Telegram
  web/app.py                 routes
  web/charts.py              server-rendered SVG histogram, meter, sparkline
  web/templates/             the UI
  jobs.py                    one-slot background job runner
  scheduler.py               APScheduler loop
```
