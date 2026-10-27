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
    qty: int
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
    a = atr_pct(bars)
    stop_dist = max(2.5 * a, 0.03) * price
    risk_usd = equity * r["max_risk_per_trade_pct"] / 100 * r["tier_risk_multiplier"].get(tier, 0)
    if risk_usd <= 0:
        return None
    qty = math.floor(risk_usd / stop_dist)
    reasons = []
    cap = equity * r["max_position_pct"] / 100
    if qty * price > cap:
        qty = math.floor(cap / price)
        reasons.append("capped by max_position_pct")
    if qty < 1:
        return None
    sign = 1 if direction == "long" else -1
    stop = price - sign * stop_dist
    tp = price + sign * price * max(expected_move_pct / 100, 2 * stop_dist / price)
    return Sizing(qty, stop, tp, qty * stop_dist, qty * price, reasons)


def check_portfolio(symbol: str, notional: float, equity: float, positions: list[dict[str, Any]],
                    day_pnl_pct: float, tier: str) -> list[str]:
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
    cash_after = equity - gross - notional
    if tier != "big" and cash_after / equity * 100 < r["dry_powder_pct"]:
        v.append("would dip into dry powder reserve (big tier only)")
    return v
