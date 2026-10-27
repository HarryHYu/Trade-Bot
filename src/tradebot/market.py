"""Free market data from Yahoo's chart endpoint: US/ASX stocks, ETFs, futures (CL=F), forex (AUDUSD=X).
Used for the 'already noticed?' check and the quant timing layer. Not used for order fills."""
from __future__ import annotations

import datetime as dt
import statistics
from functools import lru_cache
from typing import Any

import httpx

UA = {"User-Agent": "Mozilla/5.0"}


def bars(symbol: str, range_: str = "6mo", interval: str = "1d") -> list[dict[str, Any]]:
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                  params={"range": range_, "interval": interval}, headers=UA, timeout=20)
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res.get("timestamp") or []):
        if q["close"][i] is None:
            continue
        out.append(dict(t=dt.datetime.fromtimestamp(ts, dt.UTC).replace(tzinfo=None),
                        o=q["open"][i], h=q["high"][i], l=q["low"][i], c=q["close"][i], v=q["volume"][i] or 0))
    return out


def snapshot(symbol: str) -> dict[str, Any]:
    """Price action summary for the research prompt: returns, volatility, volume anomaly, trend."""
    b = bars(symbol, "1y")
    if len(b) < 30:
        return {"symbol": symbol, "error": "not enough history"}
    c = [x["c"] for x in b]
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    vol20 = statistics.pstdev(rets[-20:]) * (252 ** 0.5)
    v = [x["v"] for x in b]
    vavg = statistics.mean(v[-60:-5]) or 1

    def ret(n: int) -> float | None:
        return round((c[-1] / c[-1 - n] - 1) * 100, 1) if len(c) > n else None

    return {"symbol": symbol, "last": round(c[-1], 4), "ret_5d_pct": ret(5), "ret_20d_pct": ret(20),
            "ret_60d_pct": ret(60), "ret_250d_pct": ret(min(250, len(c) - 1)),
            "ann_vol_20d_pct": round(vol20 * 100, 1), "volume_5d_vs_60d": round(statistics.mean(v[-5:]) / vavg, 2),
            "above_50dma": c[-1] > statistics.mean(c[-50:]), "pct_from_52w_high": round((c[-1] / max(c) - 1) * 100, 1)}


def move_since(symbol: str, since: dt.datetime) -> float | None:
    """% move from the first close on/after `since` to now. Answers 'has the price already reacted?'"""
    b = [x for x in bars(symbol, "1y") if x["t"] >= since - dt.timedelta(days=1)]
    if len(b) < 2:
        return None
    return round((b[-1]["c"] / b[0]["c"] - 1) * 100, 1)


@lru_cache(maxsize=512)
def exists(symbol: str) -> bool:
    try:
        return bool(bars(symbol, "5d"))
    except Exception:
        return False
