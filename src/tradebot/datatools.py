"""Structured data tools the research agents call. Primary sources only, returned as compact JSON text."""
from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from typing import Any

import httpx

from .config import get_settings
from . import market

UA_BROWSER = {"User-Agent": "Mozilla/5.0"}


def _sec_headers() -> dict[str, str]:
    return {"User-Agent": get_settings().sec_user_agent}


@lru_cache(maxsize=1)
def _ticker_map() -> dict[str, int]:
    r = httpx.get("https://www.sec.gov/files/company_tickers.json", headers=_sec_headers(), timeout=30)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in r.json().values()}


def cik_for(ticker: str) -> int:
    cik = _ticker_map().get(ticker.upper())
    if not cik:
        raise ValueError(f"No SEC CIK for {ticker} (not an SEC filer?)")
    return cik


def sec_filings(ticker: str, since: str | None = None, forms: list[str] | None = None) -> str:
    """Every filing since a date (default: 150 days), with links."""
    cik = cik_for(ticker)
    r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=_sec_headers(), timeout=30).json()
    rec = r["filings"]["recent"]
    since = since or (dt.date.today() - dt.timedelta(days=150)).isoformat()
    out = []
    for i, form in enumerate(rec["form"]):
        if rec["filingDate"][i] < since or (forms and form not in forms):
            continue
        acc = rec["accessionNumber"][i].replace("-", "")
        out.append({"date": rec["filingDate"][i], "form": form, "items": rec.get("items", [""] * len(rec["form"]))[i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{rec['primaryDocument'][i]}"})
    return json.dumps({"company": r.get("name"), "cik": cik, "filings": out})


def sec_document(url: str, start: int = 0, length: int = 15000) -> str:
    """Plain text of an SEC document (8-K, 10-Q, 13D...). Page through with start."""
    if not url.startswith("https://www.sec.gov/"):
        return "Only sec.gov URLs are allowed here; use web_fetch for others."
    t = httpx.get(url, headers=_sec_headers(), timeout=60).text
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&[a-z#0-9]+;", " ", t)
    t = re.sub(r"\s+", " ", t)
    return json.dumps({"total_chars": len(t), "start": start, "text": t[start:start + length]})


def sec_financials(ticker: str, concepts: list[str] | None = None, periods: int = 6) -> str:
    """Reported XBRL values (10-Q/10-K) for key concepts, most recent periods."""
    cik = cik_for(ticker)
    facts = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                      headers=_sec_headers(), timeout=60).json()["facts"]
    g = facts.get("us-gaap", {})
    concepts = concepts or [
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "GrossProfit", "OperatingIncomeLoss",
        "NetIncomeLoss", "CashAndCashEquivalentsAtCarryingValue", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "MarketableSecuritiesCurrent", "LongTermDebt", "Liabilities", "NetCashProvidedByUsedInOperatingActivities",
        "ShareBasedCompensation"]
    out: dict[str, Any] = {}
    for c in concepts:
        units = g.get(c, {}).get("units", {})
        vals = units.get("USD") or next(iter(units.values()), [])
        vals = [v for v in vals if v.get("form") in ("10-Q", "10-K")]
        seen, rows = set(), []
        for v in reversed(vals):
            key = (v.get("start"), v["end"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({"start": v.get("start"), "end": v["end"], "val": v["val"], "form": v["form"], "filed": v["filed"]})
            if len(rows) >= periods:
                break
        if rows:
            out[c] = rows
    so = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    if so:
        out["SharesOutstanding"] = so[-1]
    return json.dumps(out)


def federal_awards(recipient: str, keyword: str | None = None, since: str = "2022-01-01") -> str:
    """US federal contract awards and their recent transactions (USAspending.gov)."""
    f: dict[str, Any] = {"recipient_search_text": [recipient], "award_type_codes": ["A", "B", "C", "D"],
                         "time_period": [{"start_date": since, "end_date": dt.date.today().isoformat()}]}
    if keyword:
        f["keywords"] = [keyword]
    r = httpx.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", timeout=60, json={
        "filters": f, "limit": 15, "sort": "Last Modified Date", "order": "desc",
        "fields": ["Award ID", "Award Amount", "Description", "Start Date", "End Date", "Awarding Sub Agency",
                   "Last Modified Date", "generated_internal_id"]}).json()
    awards = []
    for a in r.get("results", [])[:8]:
        tx = httpx.post("https://api.usaspending.gov/api/v2/transactions/", timeout=60, json={
            "award_id": a["generated_internal_id"], "limit": 8, "sort": "action_date", "order": "desc"}).json()
        a["recent_transactions"] = [{"date": t["action_date"], "obligation": t["federal_action_obligation"]}
                                    for t in tx.get("results", [])]
        awards.append(a)
    return json.dumps(awards)


def price_action(symbol: str) -> str:
    snap = market.snapshot(symbol)
    b = market.bars(symbol, "6mo")
    monthly = [{"date": x["t"].date().isoformat(), "close": round(x["c"], 4), "volume": x["v"]} for x in b[::10]]
    return json.dumps({"snapshot": snap, "every_10_sessions": monthly}, default=str)


def options_implied_move(symbol: str, event_date: str) -> str:
    from .earnings import implied_move
    return json.dumps(implied_move(symbol, dt.date.fromisoformat(event_date)), default=str)


TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "sec_filings", "description": "List every SEC filing for a US ticker since a date (YYYY-MM-DD), optionally filtered by form types, with document URLs.",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "since": {"type": "string"},
                                                       "forms": {"type": "array", "items": {"type": "string"}}}, "required": ["ticker"]}},
    {"name": "sec_document", "description": "Read the plain text of an SEC document by URL. Use start to page.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "start": {"type": "integer"}}, "required": ["url"]}},
    {"name": "sec_financials", "description": "Reported XBRL financials (revenue, profit, cash, debt, cash flow, shares) by period.",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"},
                                                       "concepts": {"type": "array", "items": {"type": "string"}}}, "required": ["ticker"]}},
    {"name": "federal_awards", "description": "US federal contracts to a company (USAspending.gov), with recent obligation transactions.",
     "input_schema": {"type": "object", "properties": {"recipient": {"type": "string"}, "keyword": {"type": "string"},
                                                       "since": {"type": "string"}}, "required": ["recipient"]}},
    {"name": "price_action", "description": "Price and volume history and returns for any Yahoo symbol.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "options_implied_move", "description": "ATM straddle implied move (%) through a date, US optionable tickers.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "event_date": {"type": "string"}},
                      "required": ["symbol", "event_date"]}},
]
FUNCS = {"sec_filings": sec_filings, "sec_document": sec_document, "sec_financials": sec_financials,
         "federal_awards": federal_awards, "price_action": price_action, "options_implied_move": options_implied_move}


def run(name: str, inp: dict[str, Any]) -> str:
    out = FUNCS[name](**inp)
    return out[:40000]
