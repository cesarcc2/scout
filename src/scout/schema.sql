-- Raw, append-only-ish record of every listing we have ever seen.
-- We never delete: the disappearance of a row is itself the signal.
CREATE TABLE IF NOT EXISTS listing (
    id              BIGSERIAL PRIMARY KEY,
    site            TEXT        NOT NULL,
    site_listing_id TEXT        NOT NULL,
    category        TEXT        NOT NULL,

    url             TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    price_cents     BIGINT,
    currency        TEXT        NOT NULL DEFAULT 'EUR',
    location        TEXT,
    seller_id       TEXT,
    seller_is_pro   BOOLEAN     NOT NULL DEFAULT FALSE,
    photo_count     INT         NOT NULL DEFAULT 0,
    posted_at       TIMESTAMPTZ,

    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    disappeared_at  TIMESTAMPTZ,
    seen_count      INT         NOT NULL DEFAULT 1,

    raw             JSONB       NOT NULL DEFAULT '{}'::jsonb,

    UNIQUE (site, site_listing_id)
);

CREATE INDEX IF NOT EXISTS listing_category_seen  ON listing (category, last_seen DESC);
CREATE INDEX IF NOT EXISTS listing_active         ON listing (disappeared_at) WHERE disappeared_at IS NULL;

-- Every price we have ever observed for a listing. Sellers drop prices over
-- time and that trajectory is a strong "motivated seller" signal.
CREATE TABLE IF NOT EXISTS price_point (
    listing_id  BIGINT      NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_cents BIGINT      NOT NULL,
    PRIMARY KEY (listing_id, observed_at)
);

-- Output of the deterministic normalizer. One row per listing, rewritten
-- whenever the catalog changes (normalization is cheap and idempotent).
CREATE TABLE IF NOT EXISTS normalized (
    listing_id       BIGINT PRIMARY KEY REFERENCES listing(id) ON DELETE CASCADE,
    category         TEXT   NOT NULL,
    product_id       TEXT,
    match_kind       TEXT   NOT NULL,          -- rule | fuzzy | none | excluded
    match_score      REAL   NOT NULL DEFAULT 0,
    excluded_by      TEXT,
    modifiers        TEXT[] NOT NULL DEFAULT '{}',
    adjust_pct       REAL   NOT NULL DEFAULT 0,
    adjusted_cents   BIGINT,
    normalized_title TEXT   NOT NULL DEFAULT '',
    catalog_version  TEXT   NOT NULL DEFAULT '',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS normalized_product ON normalized (category, product_id);
CREATE INDEX IF NOT EXISTS normalized_kind    ON normalized (match_kind);

-- Retail baselines, one row per product per source per day.
CREATE TABLE IF NOT EXISTS retail_price (
    category    TEXT        NOT NULL,
    product_id  TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    observed_on DATE        NOT NULL,
    price_cents BIGINT      NOT NULL,
    url         TEXT,
    in_stock    BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (category, product_id, source, observed_on)
);

-- Deals we have already pushed, so the same ad never alerts twice at the
-- same price.
CREATE TABLE IF NOT EXISTS alerted (
    listing_id  BIGINT      NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
    price_cents BIGINT      NOT NULL,
    alerted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    score       REAL        NOT NULL DEFAULT 0,
    PRIMARY KEY (listing_id, price_cents)
);

-- Titles the deterministic matcher could not classify. This is the ONLY
-- place an LLM is useful, and it is a batch job you run when you feel like it.
CREATE TABLE IF NOT EXISTS unmatched_title (
    id           BIGSERIAL PRIMARY KEY,
    category     TEXT NOT NULL,
    title_hash   TEXT NOT NULL,
    sample_title TEXT NOT NULL,
    occurrences  INT  NOT NULL DEFAULT 1,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved     BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (category, title_hash)
);
