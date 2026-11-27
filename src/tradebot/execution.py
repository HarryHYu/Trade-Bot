"""Thesis -> Proposal(s) -> approval gate -> broker. Every transition is logged.
proposed -> approved | rejected | expired -> submitted -> filled -> closed"""
from __future__ import annotations

import datetime as dt
import logging
import uuid

from .broker import Alpaca, get_broker
from .config import get_settings, load_yaml
from .db import Proposal, Thesis, is_halted, log_decision, select, session, utcnow
from .risk import check_portfolio, exit_decision, size

log = logging.getLogger(__name__)


def propose_for_thesis(thesis_id: int) -> list[int]:
    s = get_settings()
    br = get_broker()
    with session() as db:
        th = db.get(Thesis, thesis_id)
        if th.tier in ("watch", "none") or is_halted(db):
            return []
    acct = br.account()
    equity = float(acct["equity"])
    day_pnl_pct = (equity / float(acct["last_equity"]) - 1) * 100
    price = br.last_price(th.ticker)
    sz = size(th.tier, th.direction, price, equity, br.daily_bars(th.ticker), th.expected_move_pct)
    if not sz:
        return []
    violations = check_portfolio(th.ticker, sz.notional, equity, br.positions(), day_pnl_pct, th.tier, th.direction)
    with session() as db:
        if violations:
            log_decision(db, "proposal_blocked", thesis_id, violations=violations)
            return []
        expires = utcnow() + dt.timedelta(minutes=s.proposal_ttl_minutes)
        main = Proposal(thesis_id=thesis_id, symbol=th.ticker, side="buy" if th.direction == "long" else "sell",
                        qty=sz.qty, entry_ref_price=price, stop_price=sz.stop, take_profit=sz.take_profit,
                        notional=sz.notional, risk_usd=sz.risk_usd, rationale=th.summary,
                        client_order_id=f"tb-{uuid.uuid4().hex[:20]}", expires_at=expires)
        db.add(main)
        db.flush()
        ids = [main.id]
        r = load_yaml("risk.yaml")
        hedge_sym = r.get("hedge_beta_with")
        small = equity < r.get("small_account_below_usd", 2000)
        if not small and th.tier in r.get("hedge_on_tiers", []) and hedge_sym and hedge_sym != th.ticker:
            hp = br.last_price(hedge_sym)
            hq = int(sz.notional * r.get("hedge_ratio", 0.5) / hp)
            if hq >= 1:
                h = Proposal(thesis_id=thesis_id, parent_id=main.id, role="hedge", symbol=hedge_sym,
                             side="sell" if main.side == "buy" else "buy", qty=hq, entry_ref_price=hp,
                             notional=hq * hp, rationale=f"beta hedge for #{main.id}",
                             client_order_id=f"tb-{uuid.uuid4().hex[:20]}", expires_at=expires)
                db.add(h)
                db.flush()
                ids.append(h.id)
        log_decision(db, "proposed", thesis_id, proposals=ids, sizing=sz.__dict__)
    auto_approve(ids)
    return ids


def auto_approve(ids: list[int]) -> None:
    from .db import kv_get
    s = get_settings()
    with session() as db:
        mode = kv_get(db, "approval_mode", s.approval_mode)  # /mode from Telegram overrides .env
        if mode == "manual":
            return
        for pid in ids:
            p = db.get(Proposal, pid)
            if mode == "auto" or (mode == "threshold" and p.notional <= s.auto_approve_max_usd):
                p.state, p.decided_at, p.decided_by = "approved", utcnow(), "auto"


def decide(proposal_id: int, approve: bool, by: str) -> str:
    with session() as db:
        p = db.get(Proposal, proposal_id)
        if p is None or p.state != "proposed":
            return f"#{proposal_id} is not awaiting a decision"
        group = [p] + list(db.scalars(select(Proposal).where(Proposal.parent_id == p.id)))
        for g in group:
            g.state, g.decided_at, g.decided_by = ("approved" if approve else "rejected"), utcnow(), by
        log_decision(db, "approved" if approve else "rejected", proposal_id, by=by)
    return f"#{proposal_id} {'approved' if approve else 'rejected'}"


def execute_approved() -> int:
    br = get_broker()
    n = 0
    with session() as db:
        if is_halted(db):
            return 0
        for p in db.scalars(select(Proposal).where(Proposal.state == "proposed", Proposal.expires_at < utcnow())):
            p.state = "expired"
        approved = db.scalars(select(Proposal).where(Proposal.state == "approved")).all()
    if not approved or not br.clock()["is_open"]:
        return 0
    for p in approved:
        try:
            # Plain market entry. Stops, trailing, targets and time stops are managed by manage_exits.
            o = br.submit_bracket(p.symbol, p.side, p.qty, None, None, p.client_order_id)
            with session() as db:
                row = db.get(Proposal, p.id)
                row.state, row.broker_order_id = "submitted", o["id"]
                log_decision(db, "submitted", p.id, order=o["id"])
            n += 1
        except Exception as e:
            log.exception("submit failed for %s", p.id)
            with session() as db:
                db.get(Proposal, p.id).note = f"submit error: {e}"
    return n


def sync_fills() -> None:
    br = get_broker()
    held = {p["symbol"] for p in br.positions()}
    with session() as db:
        for p in db.scalars(select(Proposal).where(Proposal.state.in_(["submitted", "filled"]))):
            o = br.order_by_client_id(p.client_order_id)
            if not o:
                continue
            if p.state == "submitted" and o["status"] == "filled":
                p.state, p.fill_price = "filled", float(o["filled_avg_price"])
                p.filled_at, p.peak_price = utcnow(), p.fill_price
                days = load_yaml("strategy.yaml").get("default_time_stop_days", 5)
                p.hold_until = p.filled_at + dt.timedelta(days=round(days * 7 / 5))  # trading days -> calendar
            elif p.state == "submitted" and o["status"] in ("canceled", "expired", "rejected"):
                p.state = "canceled"
            elif p.state == "filled" and p.symbol not in held:
                p.state, p.closed_at = "closed", utcnow()
                legs = [l for l in (o.get("legs") or []) if l.get("status") == "filled"]
                if legs and p.fill_price:
                    p.exit_price = float(legs[0]["filled_avg_price"])
                    sign = 1 if p.side == "buy" else -1
                    p.pnl_usd = (p.exit_price - p.fill_price) * p.qty * sign
                log_decision(db, "closed", p.id, pnl=p.pnl_usd)


def halt(flatten: bool = False) -> None:
    from .db import kv_set
    with session() as db:
        kv_set(db, "halted", "1")
        log_decision(db, "halt", "", flatten=flatten)
    if flatten:
        get_broker().close_all()


def manage_exits() -> int:
    """Every minute while the market is open: update best price, move stops to breakeven and trail them,
    and exit on stop, trailing stop, target, time stop, or a killed thesis. Hedge legs close with their main leg."""
    br = get_broker()
    if not br.clock()["is_open"]:
        return 0
    st = load_yaml("strategy.yaml")
    held = {p["symbol"]: p for p in br.positions()}
    n = 0
    with session() as db:
        rows = db.scalars(select(Proposal).where(Proposal.state == "filled", Proposal.role == "main")).all()
    for p in rows:
        if p.symbol not in held or not p.fill_price:
            continue
        px = float(held[p.symbol]["current_price"])
        with session() as db:
            th = db.get(Thesis, p.thesis_id) if p.thesis_id else None
            alive = th is None or th.status == "open"
        reason, new_stop, peak = exit_decision(p.side, p.fill_price, p.stop_price or p.fill_price * 0.97,
                                               p.take_profit, p.peak_price or p.fill_price, px, utcnow(),
                                               p.hold_until, alive, st,
                                               r=(p.risk_usd / p.qty) if p.risk_usd and p.qty else None)
        with session() as db:
            row = db.get(Proposal, p.id)
            if new_stop != row.stop_price:
                log_decision(db, "stop_moved", p.id, old=row.stop_price, new=new_stop, price=px)
            row.stop_price, row.peak_price = new_stop, peak
        if not reason:
            continue
        br.close_position(p.symbol)
        with session() as db:
            row = db.get(Proposal, p.id)
            long = row.side == "buy"
            row.exit_price, row.exit_reason = px, reason
            row.pnl_usd = (px - row.fill_price) * row.qty * (1 if long else -1)
            row.state, row.closed_at = "closed", utcnow()
            log_decision(db, "closed", p.id, reason=reason, price=px, pnl=row.pnl_usd)
            for h in db.scalars(select(Proposal).where(Proposal.parent_id == p.id, Proposal.state == "filled")):
                try:
                    br.close_position(h.symbol)
                except Exception:
                    log.exception("hedge close failed %s", h.symbol)
                h.state, h.closed_at, h.exit_reason = "closed", utcnow(), f"main_{reason}"
        from .telegram import send
        send(f"EXIT {p.symbol} ({reason}) @ {px:.2f}  pnl ${(px - p.fill_price) * p.qty * (1 if p.side == 'buy' else -1):,.0f}",
             topic="Alerts")
        n += 1
    return n


manage_stops = manage_exits  # backwards-compatible name
