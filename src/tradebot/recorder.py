"""Writes what the bot does into the paper ledger, so every idea, call and trade is scored later.
- a new thesis -> a lead (with our stance) and a probability prediction that resolves itself from prices
- a fill -> an open ledger trade (the bot manages its exits)
- an exit -> the ledger trade is closed
Prices go into the ledger in the security's own currency, the same as its Yahoo history. Nothing here may break trading:
every hook swallows and logs its own errors."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from . import ledger

log = logging.getLogger(__name__)
CONVICTION = {"watch": 1, "small": 2, "standard": 3, "big": 4}


def _fx(symbol: str) -> float:
    try:
        from .config import get_settings
        if get_settings().broker == "sim":
            from .simbroker import fx_to_usd
            return fx_to_usd(symbol)
    except Exception:
        log.exception("fx lookup failed for %s", symbol)
    return 1.0


def _safe(fn):
    def run(*a: Any, **k: Any) -> Any:
        try:
            return fn(*a, **k)
        except Exception:
            log.exception("ledger hook %s failed", fn.__name__)
            return None
    return run


@_safe
def on_thesis(thesis_id: int) -> None:
    from .db import Thesis, session
    with session() as db:
        th = db.get(Thesis, thesis_id)
        t = dict(ticker=th.ticker, tier=th.tier, direction=th.direction, driver=th.driver, p=th.probability,
                 move=th.expected_move_pct, horizon=th.horizon_days)
    today = ledger._today()
    ref = ledger.last_close(t["ticker"])[1]
    long = t["direction"] == "long"
    stance = ("long" if long else "short") if t["tier"] in ("small", "standard", "big") else ("watch_long" if long else "watch_short")
    ledger.add_lead(t["ticker"], stance, today, t["driver"][:240], acted=False, ref=ref, thesis_id=thesis_id, tier=t["tier"])
    if t["p"] and t["move"]:
        days = max(int(round((t["horizon"] or 10) * 7 / 5)), 1)
        ledger.predict(t["ticker"], f"{'Rises' if long else 'Falls'} {abs(t['move']):.0f}% within {t['horizon']} trading days "
                       f"(thesis #{thesis_id}, {t['tier']})", "probability",
                       (dt.date.today() + dt.timedelta(days=days)).isoformat(), probability=round(t["p"], 3),
                       auto={"symbol": t["ticker"], "op": "move_pct_ge" if long else "move_pct_le",
                             "level": abs(t["move"]), "ref": ref, "mode": "any"},
                       thesis_id=thesis_id)


@_safe
def on_fill(proposal_id: int) -> None:
    from .db import Proposal, Thesis, session
    with session() as db:
        p = db.get(Proposal, proposal_id)
        if p.role != "main" or not p.fill_price:
            return
        th = db.get(Thesis, p.thesis_id) if p.thesis_id else None
        row = dict(symbol=p.symbol, side="long" if p.side == "buy" else "short", fill=p.fill_price, stop=p.stop_price,
                   tp=p.take_profit, notional=p.notional or 0, rationale=p.rationale or "", tier=th.tier if th else "small",
                   thesis_id=p.thesis_id)
    k = _fx(row["symbol"])
    ledger.open_trade(row["symbol"], row["side"], entry=row["fill"] / k, stop=(row["stop"] / k) if row["stop"] else None,
                      target=(row["tp"] / k) if row["tp"] else None, thesis=row["rationale"][:300],
                      conviction=CONVICTION.get(row["tier"], 2), notional=round(row["notional"], 2), time_stop_days=0,
                      tags=[row["tier"], "bot"], managed_by="bot", proposal_id=proposal_id, thesis_id=row["thesis_id"])
    if row["thesis_id"]:
        ledger.update("leads", {"thesis_id": row["thesis_id"]}, acted=True)


@_safe
def on_close(proposal_id: int) -> None:
    from .db import Proposal, session
    with session() as db:
        p = db.get(Proposal, proposal_id)
        exit_px, reason, symbol = p.exit_price, p.exit_reason or "closed", p.symbol
    match = [t for t in ledger.load("trades") if t.get("proposal_id") == proposal_id and t["status"] == "open"]
    for t in match:
        price = (exit_px / _fx(symbol)) if exit_px else None
        ledger.close_trade(t["id"], price, reason)
