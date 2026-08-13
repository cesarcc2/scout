from __future__ import annotations

import logging
import random

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import pipeline
from .config import settings
from .normalize import catalog as catalog_mod
from .pricing.retail import collect_retail

log = logging.getLogger(__name__)


def _cycle_all() -> None:
    for category in catalog_mod.load_all():
        try:
            pipeline.run_cycle(category)
        except Exception:
            log.exception("cycle failed for %s", category)


def _retail_all() -> None:
    for category in catalog_mod.load_all():
        try:
            collect_retail(category)
        except Exception:
            log.exception("retail refresh failed for %s", category)


def start_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="Europe/Lisbon")
    # Jitter so the collector never hits the site on a perfectly regular clock.
    sched.add_job(
        _cycle_all,
        IntervalTrigger(minutes=settings.cycle_minutes,
                        jitter=int(settings.cycle_minutes * 12)),
        id="cycle", max_instances=1, coalesce=True,
    )
    sched.add_job(
        _retail_all,
        CronTrigger(hour=settings.retail_hour, minute=random.randint(0, 55)),
        id="retail", max_instances=1, coalesce=True,
    )
    sched.start()
    log.info("scheduler started: cycle every %dm, retail at %02d:00",
             settings.cycle_minutes, settings.retail_hour)
    return sched
