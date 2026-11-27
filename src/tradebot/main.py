"""Scheduler: runs every loop on its interval. One process, restarts itself via Docker."""
from __future__ import annotations

import datetime as dt
import logging

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler

from . import brain, collectors, digest, earnings, evaluate, execution, explore, inbox, ledger, reflection, review, telegram
from .config import get_settings
from .db import init_db, kv_get, session

log = logging.getLogger("tradebot")


def _paused() -> bool:
    with session() as db:
        return kv_get(db, "research_paused", "0") == "1"


def _job(name: str, fn):
    """Jobs that spend Claude tokens: run only if listed in ENABLED_JOBS and /pause is off.
    Chat, collectors and trading are not gated."""
    def run():
        if _paused() or not get_settings().job_on(name):
            return
        fn()
    run.__name__ = getattr(fn, "__name__", "job")
    return run


def _safe(fn):
    def run():
        try:
            fn()
        except Exception:
            log.exception("job %s failed", fn.__name__)
    run.__name__ = getattr(fn, "__name__", "job")
    run.__qualname__ = run.__name__
    return run


def research_cycle() -> None:
    s = get_settings()
    brain.followup_diligence()
    for tid in brain.research_top(2):
        execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def fast_cycle() -> None:
    s = get_settings()
    for tid in brain.fast_lane():
        execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def inbox_cycle() -> None:
    s = get_settings()
    for tid in inbox.import_inbox():
        execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def trade_cycle() -> None:
    execution.execute_approved()
    execution.sync_fills()
    execution.manage_exits()


def heartbeat() -> None:
    url = get_settings().heartbeat_url
    if url:
        httpx.get(url, timeout=10)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    init_db()
    sch = BlockingScheduler(timezone=s.timezone)
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo(s.timezone))
    sch.add_job(_safe(collectors.run_all), "interval", minutes=10, max_instances=1, next_run_time=now)
    sch.add_job(_safe(_job("triage", brain.triage_pending)), "interval", minutes=5, max_instances=1,
                next_run_time=now + dt.timedelta(minutes=2))
    sch.add_job(_safe(_job("fast", fast_cycle)), "interval", minutes=2, max_instances=1)
    sch.add_job(_safe(_job("inbox", inbox_cycle)), "interval", minutes=5, max_instances=1, next_run_time=now + dt.timedelta(minutes=1))
    sch.add_job(_safe(_job("research", research_cycle)), "interval", minutes=30, max_instances=1)
    sch.add_job(_safe(trade_cycle), "interval", minutes=1, max_instances=1)
    sch.add_job(_safe(heartbeat), "interval", minutes=5)
    sch.add_job(_safe(_job("reviews", reflection.post_mortems)), "cron", hour=7, minute=0)
    sch.add_job(_safe(_job("earnings", earnings.run_protocol)), "cron", hour=6, minute=0)
    sch.add_job(_safe(_job("earnings", earnings.run_protocol)), "date", run_date=now + dt.timedelta(minutes=5))
    # Collated to-do list for the Max-plan research sessions: before the evening scan, pre-open, mid-session, morning
    sch.add_job(_safe(_job("digest", digest.run)), "cron", hour="3,6,12,19,22", minute=30)
    # Exploration swarm: Sonnet picks angles hourly, Haiku explorers run continuously within the daily allowance
    explore.recover()
    sch.add_job(_safe(_job("direct", explore.direct)), "interval", minutes=60, max_instances=1,
                next_run_time=now + dt.timedelta(minutes=4))
    sch.add_job(_safe(_job("explore", explore.dispatch)), "interval", minutes=1, max_instances=1)
    # Sonnet shortlists for Opus every hour, after the explorers have had time to file
    sch.add_job(_safe(_job("evaluate", evaluate.run)), "interval", minutes=60, max_instances=1,
                next_run_time=now + dt.timedelta(minutes=25))
    # Daily performance review after the US close (08:20 Sydney covers both daylight-saving settings)
    ledger.seed()
    sch.add_job(_safe(review.send_daily), "cron", hour=8, minute=20)
    sch.add_job(_safe(ledger.auto_resolve), "cron", hour="*/6", minute=5)
    sch.add_job(_safe(_job("reviews", reflection.weekly_review)), "cron", day_of_week="sat", hour=9)
    telegram.start_listener()
    from .broker import broker_label
    telegram.send(f"Tradebot started. Broker: {broker_label()}. AI jobs: {s.enabled_jobs}.\n"
                  f"Models: triage {s.triage_model}, explore {s.explorer_model}, evaluate {s.evaluator_model}, "
                  f"research {s.research_model}, judge {s.judge_model}.\nPaper autotrade: {s.paper_autotrade and s.broker == 'sim'}")
    sch.start()
