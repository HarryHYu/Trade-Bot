"""Alpaca REST. Paper unless both ALPACA_PAPER=false and TRADING_LIVE_CONFIRM=yes."""
from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


class Alpaca:
    def __init__(self) -> None:
        s = get_settings()
        base = "https://api.alpaca.markets" if s.is_live else "https://paper-api.alpaca.markets"
        h = {"APCA-API-KEY-ID": s.alpaca_key_id, "APCA-API-SECRET-KEY": s.alpaca_secret_key}
        self.t = httpx.Client(base_url=base, headers=h, timeout=30)
        self.d = httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=30)

    def _j(self, r: httpx.Response) -> Any:
        r.raise_for_status()
        return r.json() if r.content else None

    def account(self) -> dict[str, Any]:
        return self._j(self.t.get("/v2/account"))

    def positions(self) -> list[dict[str, Any]]:
        return self._j(self.t.get("/v2/positions"))

    def clock(self) -> dict[str, Any]:
        return self._j(self.t.get("/v2/clock"))

    def last_price(self, symbol: str) -> float:
        q = self._j(self.d.get(f"/v2/stocks/{symbol}/trades/latest"))
        return float(q["trade"]["p"])

    def daily_bars(self, symbol: str, limit: int = 120) -> list[dict[str, Any]]:
        r = self._j(self.d.get(f"/v2/stocks/{symbol}/bars", params={"timeframe": "1Day", "limit": limit, "adjustment": "all"}))
        return r.get("bars") or []

    def order_by_client_id(self, cid: str) -> dict[str, Any] | None:
        r = self.t.get("/v2/orders:by_client_order_id", params={"client_order_id": cid})
        return None if r.status_code == 404 else self._j(r)

    def submit_bracket(self, symbol: str, side: str, qty: float, stop: float | None, tp: float | None,
                       client_order_id: str) -> dict[str, Any]:
        """Idempotent: if this client_order_id already exists, return it instead of double-submitting."""
        existing = self.order_by_client_id(client_order_id)
        if existing:
            return existing
        body: dict[str, Any] = dict(symbol=symbol, side=side, qty=str(int(qty)), type="market",
                                    time_in_force="day", client_order_id=client_order_id)
        if stop and tp:
            body |= dict(order_class="bracket", take_profit={"limit_price": round(tp, 2)},
                         stop_loss={"stop_price": round(stop, 2)})
        elif stop:
            body |= dict(order_class="oto", stop_loss={"stop_price": round(stop, 2)})
        return self._j(self.t.post("/v2/orders", json=body))

    def close_position(self, symbol: str) -> Any:
        return self._j(self.t.delete(f"/v2/positions/{symbol}"))

    def close_all(self) -> Any:
        return self._j(self.t.delete("/v2/positions", params={"cancel_orders": "true"}))
