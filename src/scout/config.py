from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCOUT_", env_file=".env", extra="ignore")

    dsn: str = "postgresql://scout:scout@localhost:5432/scout"

    catalog_dir: Path = Path("catalogs")
    data_dir: Path = Path("data")

    # Collector politeness. These defaults are deliberately slow.
    request_delay: float = 4.0
    request_jitter: float = 1.5
    max_pages: int = 8
    page_size: int = 40
    user_agent_profile: str = "chrome124"
    request_timeout: float = 25.0
    max_retries: int = 3

    cycle_minutes: int = 45
    retail_hour: int = 7

    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Deal thresholds
    deal_percentile: float = 0.20
    min_samples: int = 8
    stats_window_days: int = 45
    # Listings that vanish faster than this are treated as "probably sold".
    sold_proxy_hours: int = 48
    # Sold-proxy listings count for more when building the distribution,
    # because they reflect prices the market actually accepted.
    sold_weight: float = 2.0

    host: str = "0.0.0.0"
    port: int = 8077


settings = Settings()
