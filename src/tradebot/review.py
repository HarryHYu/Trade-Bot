"""Daily performance review for Telegram: how the paper account did, what the desk did in the last 24 hours, and how
its calls are scoring. Built from the broker, the database and the ledger; no model calls."""
from __future__ import annotations

import datetime as dt
import statistics as st
from pathlib import Path

from sqlalchemy import func

from . import budget, ledger
from .broker import get_broker
from .db import Item, Lead, Mission, Proposal, Thesis, select, session, utcnow
from .llm import spend_by_key


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.1f}%"


def _spy_change(since: str) -> float | None:
    try:
        return ledger.last_close("SPY")[1] / ledger.close_on("SPY", since) - 1
    except Exception:
        return None


def build(hours: int = 24) -> str:
    since = utcnow() - dt.timedelta(hours=hours)
    today = ledger._today()
    lines = [f"DAILY REVIEW {today}"]

    # account
    try:
        br = get_broker()
        a = br.account()
        eq, last = float(a["equity"]), float(a["last_equity"])
        from .config import get_settings
        start = get_settings().sim_starting_cash
        lines.append(f"Paper account ${eq:,.0f}  day {_pct(eq / last - 1)}  since start {_pct(eq / start - 1)}"
                     f"  | SPY day {_pct(_spy_change((dt.date.today() - dt.timedelta(days=1)).isoformat()))}")
        pos = br.positions()
        if pos:
            lines.append("Open positions:")
            for p in pos:
                q, avg, cur = float(p["qty"]), float(p["avg_entry_price"]), float(p["current_price"])
                lines.append(f"  {p['symbol']} {'long' if q > 0 else 'short'} {abs(q):g} @ {avg:.2f} -> {cur:.2f} "
                             f"({_pct((cur / avg - 1) * (1 if q > 0 else -1))}, ${float(p['unrealized_pl']):+,.0f})")
        else:
            lines.append("No open positions.")
    except Exception as e:
        lines.append(f"(account unavailable: {e})")

    # last 24h activity
    with session() as db:
        items = db.scalar(select(func.count()).select_from(Item).where(Item.observed_at >= since)) or 0
        leads = db.scalar(select(func.count()).select_from(Lead).where(Lead.created_at >= since, Lead.signal_kind != "evaluated")) or 0
        shortlisted = db.scalar(select(func.count()).select_from(Lead).where(Lead.created_at >= since, Lead.signal_kind == "evaluated")) or 0
        missions = db.scalar(select(func.count()).select_from(Mission).where(Mission.finished_at >= since, Mission.status == "done")) or 0
        theses = db.scalars(select(Thesis).where(Thesis.opened_at >= since)).all()
        opened = db.scalars(select(Proposal).where(Proposal.filled_at >= since, Proposal.role == "main")).all()
        closed = db.scalars(select(Proposal).where(Proposal.closed_at >= since, Proposal.role == "main")).all()
        lessons = db.scalars(select(Thesis).where(Thesis.closed_at >= since, Thesis.outcome.is_not(None))).all()
        th_rows = [(t.ticker, t.tier, t.direction, t.probability, t.expected_move_pct, t.driver) for t in theses]
        op_rows = [(p.symbol, p.side, p.fill_price, p.notional) for p in opened]
        cl_rows = [(p.symbol, p.exit_reason, p.pnl_usd) for p in closed]
        ls_rows = [(t.ticker, (t.outcome or {}).get("lesson_for_security", "")) for t in lessons]
    lines.append(f"\nPipeline (24h): {items} items read, {leads} Haiku flags, {missions} explorer dossiers, "
                 f"{shortlisted} shortlisted by Sonnet, {len(th_rows)} Opus theses")
    for tk, tier, d, p, mv, drv in th_rows:
        lines.append(f"  [{tier}] {tk} {d} p={p:.0%} move {mv:.0f}%: {drv[:110]}")
    for sym, side, px, notional in op_rows:
        lines.append(f"  OPENED {sym} {'long' if side == 'buy' else 'short'} @ {px:.2f} (${notional or 0:,.0f})")
    for sym, reason, pnl in cl_rows:
        lines.append(f"  CLOSED {sym} ({reason}) pnl ${pnl or 0:+,.0f}")
    for tk, lesson in ls_rows:
        if lesson:
            lines.append(f"  LESSON {tk}: {lesson[:160]}")

    # scorecard
    trades = ledger.load("trades")
    closed_t = [t for t in trades if t["status"] == "closed" and t.get("exit")]
    if closed_t:
        rets = [ledger._ret(t, t["exit"]) for t in closed_t]
        lines.append(f"\nScorecard: {len(closed_t)} closed trades, win rate {sum(r > 0 for r in rets) / len(rets):.0%}, "
                     f"average {_pct(st.mean(rets))}, best {_pct(max(rets))}, worst {_pct(min(rets))}")
    preds = [p for p in ledger.load("predictions") if p["status"] == "resolved"]
    hits = [p["score"]["hit"] for p in preds if "hit" in (p["score"] or {})]
    briers = [p["score"]["brier"] for p in preds if "brier" in (p["score"] or {})]
    if preds:
        lines.append(f"Predictions: {len(preds)} resolved, hit rate {sum(hits)}/{len(hits)}"
                     + (f", mean Brier {st.mean(briers):.3f} (0.25 = coin flip)" if briers else ""))
    newly = [p for p in preds if p.get("resolved", "") >= (dt.date.today() - dt.timedelta(days=1)).isoformat()]
    for p in newly:
        lines.append(f"  resolved {p['id']} {p['subject']}: {p['question'][:80]} -> {p['outcome'] or p['actual']}")
    edges = []
    for lead in ledger.load("leads"):
        if ledger.STANCE_SIGN.get(lead["stance"], 0):
            try:
                edges.append(ledger._lead_score(lead, today)["edge"])
            except Exception:
                pass
    if edges:
        lines.append(f"Ideas: {sum(e > 0 for e in edges)}/{len(edges)} directional calls right vs benchmark, average edge {_pct(st.mean(edges))}")
        lines.append("By who picked them:\n" + ledger.source_summary(today))

    # spend
    lines.append(f"\nAPI: month ${sum(spend_by_key().values()):.2f}; {budget.status().splitlines()[0]}")
    return "\n".join(lines)


def send_daily() -> None:
    from . import telegram
    ledger.auto_resolve()
    ledger.check()
    ledger.report()
    telegram.send(build(), topic="Brief")
    png = Path(ledger._dir()) / "equity.png"
    if png.exists():
        telegram.send_file(png, caption="Paper equity vs SPY", topic="Brief")
