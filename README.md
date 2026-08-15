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
                    Discord / ntfy push
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
file you edit twice a year — currently Tom's Hardware's 1440p-ultra hierarchy,
rescaled so the RTX 5070 Ti = 100, with the source and fetch date recorded in
the file. Never ask a model to recall benchmark numbers: it will produce
plausible ones, and plausible-but-wrong is the worst possible input to a
ranking. Cards absent from the source table (the GTX/RTX 20 generation) carry
no `perf_index` at all rather than an invented one — they are still tracked and
alerted on, they just sit out the value ranking.

---

## Quick start

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and SCOUT_DISCORD_WEBHOOK_URL
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
| **Catalog** | Edit the YAML files in the browser — products, rules, backups, unmatched titles |
| **Status** | Coverage, collection stats, and buttons to run any job with live progress |
| **`/feed.xml`** | RSS of current deals — notifications with zero configuration |

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

## Notifications are optional

Scout is usable with nothing configured at all. Two things make the dashboard
self-sufficient:

- **The Deals page tracks what's new.** Anything discovered since you last hit
  *Mark all as seen* is flagged `NEW`, and the count rides in the nav on every
  page. That's the dashboard doing a notification's job.
- **`/feed.xml` is a plain RSS feed** of current deals (score ≥ 45 by default;
  `?min_score=` and `?limit=` override it). Point any reader at it and you get
  told about deals without Scout needing outbound network access, an account
  anywhere, or a single line of configuration.

Note that "new to you" is tracked separately from a listing's age. An ad posted
three months ago that Scout only found on today's sweep is *new to you* and
gets flagged, while still correctly reporting itself as 90 days old.

If you want push as well:

### Discord

Discord is the default, via a plain webhook — no bot to register, no token to
refresh, nothing to stay logged in to. In Discord: **Server Settings →
Integrations → Webhooks → New Webhook**, pick a channel, copy the URL into
`SCOUT_DISCORD_WEBHOOK_URL`. Then hit **Send a test notification** on the Status
page (or `python -m scout.cli test-alert`) to confirm it lands.

Deals arrive as embeds — price, gap to median, market position, gap to retail,
location, listing age — with the reasons behind the score and any cautions
against it, so you can judge one from the notification without opening the site.
They're **batched up to 10 per message**, which is both Discord's own limit and
the difference between one useful ping and twelve annoying ones after a sweep.

ntfy is available alongside or instead (`SCOUT_NTFY_TOPIC`). It sends one
notification per deal with a tap-through action, which is the better shape on a
phone lock screen. Enable either, both, or neither.

Each (listing, price) alerts once. If a seller drops the price you get a second
ping — usually the one worth acting on. With **no** channel configured Scout
skips the alerting step entirely rather than marking deals as notified, so
adding a webhook months later still tells you about everything currently live.

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

## Editing the catalog

The Catalog page edits `catalogs/*.yaml` in place. Tabs: **Products** (a table
of every rule with its live listing count, plus add/edit/delete), **Modifiers**,
**Search terms**, **Raw YAML**, **Unmatched**, **Backups**, **Files**.

Four things make this safe enough to use on the file that can silently ruin
every price distribution you have:

- **Nothing invalid is ever written.** A save is parsed, structurally checked,
  and then loaded through the real production loader. If any of that fails the
  file on disk is untouched and the editor tells you why, with a line number.
- **Every save is backed up** to `catalogs/.backups/`, and restoring is itself
  backed up. Twenty versions are kept.
- **Comments survive.** Structured edits splice the YAML text rather than
  re-serialising it — a PyYAML round-trip would strip every comment in the file,
  and the comments are where the reasoning lives.
- **The swallowing lint.** If one product requires `["5080"]` and another
  requires `["5080", "super"]`, the first will eat the second's listings unless
  it excludes `super`. That corrupts two price histories and raises no error
  anywhere, so the editor refuses to save it and tells you exactly which
  `none_of` token is missing.

**Test this rule** dry-runs a rule against listings you have already collected
before you save it: how many it would match, and — the important part — which
listings it would take from which other product.

Changing a catalog only affects future classification until you press
**Reclassify all listings now**, which re-runs the matcher over your entire
history.

### Adding a category

Nothing in `src/` knows what a GPU is. **Files → New category** writes a
commented starter file; add `bikes.yaml` and the whole pipeline works on bikes.
See `catalogs/README.md` for the format.

A file that fails to parse costs you that one category, not the app — the
dashboard keeps working and shows the error on the Catalog page.

**Note:** `catalogs/` must be mounted read-write for any of this. The shipped
`docker-compose.yml` does that; if you mount it `:ro`, saving is greyed out and
the page says so.

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

## Where the catalog numbers come from

`catalogs/gpu.yaml` is generated by `build_gpu_catalog.py`, which keeps the
sources next to the data:

| Field | Source |
|---|---|
| `perf_index` | Tom's Hardware GPU hierarchy, 1440p ultra rasterisation, fetched 2026-08-15, rescaled to RTX 5070 Ti = 100 |
| `vram_gb`, `tdp_w` | Manufacturer specifications, cross-checked against Wikipedia |
| `retail_fallback_eur` | Estimate: USD MSRP × 1.22 (Portuguese VAT + margin, calibrated on one observed PT price). Overridden by the retail scraper as soon as it has real data. Zero for anything no longer sold new. |

The cross-check earned its keep: the hierarchy table listed the RTX 5090 at
24GB and the RTX 5060 at 12GB, both wrong.

Covers 66 products across RTX 50/40/30/20, GTX 16/10, RX 9000/7000/6000 and
Intel Arc — 48 with a sourced `perf_index`. To change anything, edit the
generator and re-run it, or edit the YAML directly in the Catalog page; the
editor validates and lints either way.

The retail shop URLs in `src/scout/pricing/retail.py` still need a once-over —
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

## Dependencies

`requirements.txt` is a **full lock** — every transitive dependency pinned, not
just the direct ones. That is not fussiness. An earlier version pinned
`typer==0.12.5` and left `click` free; a rebuild months later installed click
8.4, which that Typer cannot drive, and the container died on startup with
`TypeError: Secondary flag is not valid for non-boolean flag` before serving a
single request. Every test still passed, because they ran against a different
set of versions than the image built.

`requirements.in` holds the loose ranges and is the source of truth for what
Scout actually needs. To change a dependency:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.in
.venv/bin/pip freeze > requirements.txt
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests -q        # in the venv, before shipping
```

The rule that matters: **run the suite inside a venv built from the lock**, not
against whatever your machine happens to have. `tests/test_startup.py` exists to
catch this class of failure — it asserts the Typer command group and the FastAPI
routes can actually be constructed under the installed versions, which is the
thing that broke.

## Tests

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m pytest tests -q      # 107 tests, no DB needed

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
  normalize/editor.py        catalog validation, linting, backups, safe writes
  pricing/stats.py           weighted percentiles, sold-proxy weighting
  pricing/retail.py          JSON-LD retail scraper, PT/ES shops
  pricing/deals.py           deal scoring with human-readable reasons
  pricing/compare.py         cost per unit of performance
  alerts/notify.py           Discord webhook + ntfy
  web/app.py                 routes + RSS feed
  web/uistate.py             "new since you last looked" tracking
  web/charts.py              server-rendered SVG histogram, meter, sparkline
  web/templates/             the UI
  jobs.py                    one-slot background job runner
  scheduler.py               APScheduler loop

requirements.in              loose ranges — what Scout depends on
requirements.txt             the lock — what the image installs
requirements-dev.txt         the lock plus pytest
```
