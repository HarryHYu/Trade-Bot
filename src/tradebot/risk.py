"""Sizing and hard limits. Pure code: no model involved."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any

from .config import load_yaml


@dataclass
class Sizing:
    qty: float
    stop: float
    take_profit: float
    risk_usd: float
    notional: float
    reasons: list[str]


def atr_pct(bars: list[dict[str, Any]], n: int = 20) -> float:
    if len(bars) < n + 1:
        return 0.05
    trs = []
    for prev, b in zip(bars[-n - 1:-1], bars[-n:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev["c"]), abs(b["l"] - prev["c"])) / b["c"])
    return statistics.mean(trs)


def size(tier: str, direction: str, price: float, equity: float, bars: list[dict[str, Any]],
         expected_move_pct: float) -> Sizing | None:
    r = load_yaml("risk.yaml")
    st = load_yaml("strategy.yaml")
    a = atr_pct(bars)
    stop_dist = max(st.get("stop_atr_mult", 1.5) * a, 0.02) * price
    small = equity < r.get("small_account_below_usd", 2000)
    risk_pct = r.get("small_account_risk_per_trade_pct", 5.0) if small else r["max_risk_per_trade_pct"]
    risk_usd = equity * risk_pct / 100 * r["tier_risk_multiplier"].get(tier, 0)
    if risk_usd <= 0:
        return None
    rnd = (lambda q: math.floor(q * 1000) / 1000) if small else math.floor  # fractional shares when small
    qty = rnd(risk_usd / stop_dist)
    reasons = ["small-account mode: fractional, long only, no hedge"] if small else []
    cap = equity * r.get("small_account_max_position_pct" if small else "max_position_pct", r["max_position_pct"]) / 100
    if qty * price > cap:
        qty = rnd(cap / price)
        reasons.append("capped by max position size")
    if qty * price < (1 if small else price):
        return None
    sign = 1 if direction == "long" else -1
    stop = price - sign * stop_dist
    tp = price + sign * price * max(expected_move_pct / 100, st.get("target_r_min", 2.0) * stop_dist / price)
    return Sizing(qty, stop, tp, qty * stop_dist, qty * price, reasons)


def check_portfolio(symbol: str, notional: float, equity: float, positions: list[dict[str, Any]],
                    day_pnl_pct: float, tier: str, direction: str = "long") -> list[str]:
    """Returns violations. Empty list means allowed."""
    r = load_yaml("risk.yaml")
    v = []
    if not re.match(r["allowed_symbols_regex"], symbol):
        v.append(f"symbol {symbol} not allowed")
    if day_pnl_pct <= -r["daily_loss_halt_pct"]:
        v.append("daily loss halt active")
    if len(positions) >= r["max_open_positions"]:
        v.append("max open positions")
    gross = sum(abs(float(p["market_value"])) for p in positions)
    if (gross + notional) / equity * 100 > r["max_gross_exposure_pct"]:
        v.append("gross exposure cap")
    small = equity < r.get("small_account_below_usd", 2000)
    if small and direction == "short":
        v.append("small account: shorting not allowed")
    cash_after = equity - gross - notional
    powder = r.get("small_account_dry_powder_pct", 10) if small else r["dry_powder_pct"]
    if tier != "big" and cash_after / equity * 100 < powder:
        v.append("would dip into dry powder reserve (big tier only)")
    return v


def exit_decision(side: str, entry: float, stop: float, target: float | None, peak: float, px: float,
                  now, hold_until, thesis_alive: bool, st: dict, r: float | None = None) -> tuple[str | None, float, float]:
    """Pure exit rule. Returns (reason or None, updated stop, updated peak). Long and short handled symmetrically.
    r is the ORIGINAL risk per share (entry to initial stop); it never changes as the stop moves."""
    sign = 1 if side == "buy" else -1
    r = r or abs(entry - stop) or entry * 0.02
    atr = r / st.get("stop_atr_mult", 1.5)
    peak = max(peak, px) if sign > 0 else min(peak, px)
    gain_r = (px - entry) * sign / r
    best_r = (peak - entry) * sign / r
    new_stop = stop
    if best_r >= st.get("breakeven_at_r", 0.75):
        new_stop = max(new_stop, entry) if sign > 0 else min(new_stop, entry)
    if best_r >= st.get("trail_activate_r", 1.0):
        trail = peak - sign * st.get("trail_atr_mult", 1.0) * atr
        new_stop = max(new_stop, trail) if sign > 0 else min(new_stop, trail)
    if not thesis_alive:
        return "thesis_killed", new_stop, peak
    if (px - new_stop) * sign <= 0:
        locked = (new_stop - entry) * sign  # profit locked in by the stop
        reason = "trailing_stop" if locked > 1e-9 else ("breakeven_stop" if abs(locked) <= 1e-9 else "stop")
        return reason, new_stop, peak
    if target and (px - target) * sign >= 0:
        return "target", new_stop, peak
    if hold_until and now >= hold_until and best_r < st.get("trail_activate_r", 1.0):
        return "time_stop", new_stop, peak  # dead money only; winners ride the trailing stop
    return None, new_stop, peak
