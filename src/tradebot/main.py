"""Scheduler: runs every loop on its interval. One process, restarts itself via Docker."""
from __future__ import annotations

import datetime as dt
import logging

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler

from . import brain, collectors, earnings, execution, inbox, reflection, telegram
from .config import get_settings
from .db import init_db, kv_get, session

log = logging.getLogger("tradebot")


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
    for tid in brain.research_top(5):
        if s.alpaca_key_id:  # no broker connected yet: theses still reach the phone
            execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def fast_cycle() -> None:
    s = get_settings()
    for tid in brain.fast_lane():
        if s.alpaca_key_id:
            execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def inbox_cycle() -> None:
    s = get_settings()
    for tid in inbox.import_inbox():
        if s.alpaca_key_id:
            execution.propose_for_thesis(tid)
    telegram.announce_new_theses()
    telegram.announce_new_proposals()


def trade_cycle() -> None:
    if not get_settings().alpaca_key_id:
        return
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
    sch.add_job(_safe(brain.triage_pending), "interval", minutes=5, max_instances=1,
                next_run_time=now + dt.timedelta(minutes=2))
    sch.add_job(_safe(fast_cycle), "interval", minutes=2, max_instances=1)
    sch.add_job(_safe(inbox_cycle), "interval", minutes=5, max_instances=1, next_run_time=now + dt.timedelta(minutes=1))
    sch.add_job(_safe(research_cycle), "interval", minutes=30, max_instances=1)
    sch.add_job(_safe(trade_cycle), "interval", minutes=1, max_instances=1)
    sch.add_job(_safe(heartbeat), "interval", minutes=5)
    sch.add_job(_safe(reflection.post_mortems), "cron", hour=7, minute=0)
    sch.add_job(_safe(earnings.run_protocol), "cron", hour=6, minute=0)
    sch.add_job(_safe(earnings.run_protocol), "date", run_date=now + dt.timedelta(minutes=5))
    def daily_brief() -> None:
        telegram.send(reflection.daily_brief(), topic="Brief")
    sch.add_job(_safe(daily_brief), "cron", hour=7, minute=30)
    sch.add_job(_safe(reflection.weekly_review), "cron", day_of_week="sat", hour=9)
    telegram.start_listener()
    telegram.send(f"Tradebot started. paper={not s.is_live} mode={s.approval_mode}")
    sch.start()
