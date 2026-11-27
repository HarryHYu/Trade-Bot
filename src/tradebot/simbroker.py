"""Built-in paper broker. No account needed: fills at live market prices (Yahoo) with slippage and
IBKR-style commission, tracks cash and positions in the database, supports longs and shorts.
Same interface as broker.Alpaca, so the rest of the bot does not care which one is active."""
from __future__ import annotations

import datetime as dt
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import Float, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .db import Base, KV, kv_get, kv_set, select, session, utcnow

SLIPPAGE = 0.0005          # 5 bps against you on every fill
COMMISSION_PER_SHARE = 0.005
COMMISSION_MIN = 1.0
NY = ZoneInfo("America/New_York")


class SimOrder(Base):
    __tablename__ = "sim_orders"
    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="new")
    filled_avg_price: Mapped[float | None] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    filled_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class SimPosition(Base):
    __tablename__ = "sim_positions"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    qty: Mapped[float] = mapped_column(Float)        # negative = short
    avg_price: Mapped[float] = mapped_column(Float)


_px_cache: dict[str, tuple[float, float]] = {}


def quote(symbol: str) -> float:
    hit = _px_cache.get(symbol)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                  params={"range": "1d", "interval": "1m"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()
    res = r["chart"]["result"][0]
    closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c]
    px = float(closes[-1] if closes else res["meta"]["regularMarketPrice"])
    _px_cache[symbol] = (time.time(), px)
    return px


def us_market_open(now: dt.datetime | None = None) -> bool:
    n = (now or dt.datetime.now(dt.UTC)).astimezone(NY)
    return n.weekday() < 5 and dt.time(9, 30) <= n.time() < dt.time(16, 0)


class SimBroker:
    name = "sim"

    def _cash(self, db) -> float:
        v = kv_get(db, "sim_cash")
        if not v:
            v = str(get_settings().sim_starting_cash)
            kv_set(db, "sim_cash", v)
        return float(v)

    def _equity(self, db) -> float:
        pos = db.scalars(select(SimPosition)).all()
        return self._cash(db) + sum(p.qty * quote(p.symbol) for p in pos)

    def account(self) -> dict[str, Any]:
        with session() as db:
            eq = self._equity(db)
            today = dt.datetime.now(NY).date().isoformat()
            if kv_get(db, "sim_last_equity_date") != today:   # yesterday's close = first reading of a new day
                kv_set(db, "sim_last_equity", kv_get(db, "sim_equity_now", str(eq)))
                kv_set(db, "sim_last_equity_date", today)
            kv_set(db, "sim_equity_now", str(eq))
            return {"equity": str(eq), "last_equity": kv_get(db, "sim_last_equity", str(eq)),
                    "cash": str(self._cash(db)), "broker": "sim"}

    def positions(self) -> list[dict[str, Any]]:
        out = []
        with session() as db:
            for p in db.scalars(select(SimPosition)).all():
                px = quote(p.symbol)
                out.append({"symbol": p.symbol, "qty": str(p.qty), "avg_entry_price": str(p.avg_price),
                            "current_price": str(px), "market_value": str(p.qty * px),
                            "unrealized_pl": str((px - p.avg_price) * p.qty)})
        return out

    def clock(self) -> dict[str, Any]:
        return {"is_open": us_market_open()}

    def last_price(self, symbol: str) -> float:
        return quote(symbol)

    def daily_bars(self, symbol: str, limit: int = 120) -> list[dict[str, Any]]:
        from . import market
        return [{"t": b["t"].isoformat(), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
                for b in market.bars(symbol, "1y")][-limit:]

    def _fill(self, db, o: SimOrder) -> None:
        px = quote(o.symbol)
        px = px * (1 + SLIPPAGE) if o.side == "buy" else px * (1 - SLIPPAGE)
        fee = max(COMMISSION_MIN, COMMISSION_PER_SHARE * abs(o.qty))
        signed = o.qty if o.side == "buy" else -o.qty
        pos = db.get(SimPosition, o.symbol)
        if pos is None:
            db.add(SimPosition(symbol=o.symbol, qty=signed, avg_price=px))
        else:
            new_qty = pos.qty + signed
            if pos.qty == 0 or (pos.qty > 0) == (signed > 0):          # adding to the same side
                pos.avg_price = (pos.avg_price * abs(pos.qty) + px * abs(signed)) / abs(new_qty)
            elif (new_qty > 0) != (pos.qty > 0) and new_qty != 0:       # flipped through zero
                pos.avg_price = px
            pos.qty = new_qty
            if abs(pos.qty) < 1e-9:
                db.delete(pos)
        kv_set(db, "sim_cash", str(self._cash(db) - signed * px - fee))
        o.status, o.filled_avg_price, o.commission, o.filled_at = "filled", px, fee, utcnow()

    def order_by_client_id(self, cid: str) -> dict[str, Any] | None:
        with session() as db:
            o = db.get(SimOrder, cid)
            if o is None:
                return None
            if o.status == "new" and us_market_open():
                self._fill(db, o)
            return {"id": o.client_order_id, "status": o.status, "filled_avg_price": o.filled_avg_price,
                    "symbol": o.symbol, "legs": []}

    def submit_bracket(self, symbol: str, side: str, qty: float, stop: float | None, tp: float | None,
                       client_order_id: str) -> dict[str, Any]:
        """Market order. Stops and targets are managed by the bot (execution.manage_exits)."""
        with session() as db:
            o = db.get(SimOrder, client_order_id)
            if o is None:
                o = SimOrder(client_order_id=client_order_id, symbol=symbol.upper(), side=side, qty=float(qty))
                db.add(o)
                db.flush()
                if us_market_open():
                    self._fill(db, o)
            return {"id": o.client_order_id, "status": o.status, "filled_avg_price": o.filled_avg_price}

    def close_position(self, symbol: str) -> Any:
        with session() as db:
            pos = db.get(SimPosition, symbol)
            if pos is None:
                return None
            o = SimOrder(client_order_id=f"close-{symbol}-{time.time_ns()}", symbol=symbol,
                         side="sell" if pos.qty > 0 else "buy", qty=abs(pos.qty))
            db.add(o)
            db.flush()
            self._fill(db, o)
            return {"id": o.client_order_id, "status": o.status}

    def close_all(self) -> Any:
        with session() as db:
            syms = [p.symbol for p in db.scalars(select(SimPosition)).all()]
        return [self.close_position(s) for s in syms]

    def reset(self, cash: float | None = None) -> None:
        with session() as db:
            for p in db.scalars(select(SimPosition)).all():
                db.delete(p)
            kv_set(db, "sim_cash", str(cash if cash is not None else get_settings().sim_starting_cash))
            kv_set(db, "sim_last_equity_date", "")
