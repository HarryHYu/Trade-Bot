"""Earnings protocol. Every core watchlist name gets a prediction every quarter, traded or not:
T-21 open file (consensus) -> T-7 predict (our own bottom-up call vs consensus and the options-implied
move) -> T-1 freeze -> T+1..5 score against the actual print -> lesson. The prediction is mandatory;
the trade is optional and only when our edge beats what options already price."""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .brain import Check
from .config import get_settings, load_yaml
from .db import KV, Prediction, Thesis, kv_get, kv_set, log_decision, select, session, utcnow
from .knowledge import append_lesson, commit, ensure_security, profile_context
from .llm import claude
from . import market

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def calendar(days: int = 30) -> dict[str, dict[str, Any]]:
    """Next report date per core ticker, from Nasdaq's free calendar. Cached once a day."""
    core = load_yaml("watchlist.yaml").get("core", {})
    today = dt.date.today()
    with session() as db:
        cached = kv_get(db, "earnings_cal")
    if cached:
        c = json.loads(cached)
        if c.get("asof") == today.isoformat():
            return c["rows"]
    rows: dict[str, dict[str, Any]] = {}
    for i in range(days):
        d = today + dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            r = httpx.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()},
                          headers=UA, timeout=20).json()
        except Exception:
            continue
        for row in (r.get("data") or {}).get("rows") or []:
            if row["symbol"] in core and row["symbol"] not in rows:
                rows[row["symbol"]] = {"date": d.isoformat(), "time": row.get("time"),
                                       "eps_forecast": row.get("epsForecast"), "n_estimates": row.get("noOfEsts"),
                                       "quarter": row.get("fiscalQuarterEnding"), "last_year_eps": row.get("lastYearEPS")}
    with session() as db:
        kv_set(db, "earnings_cal", json.dumps({"asof": today.isoformat(), "rows": rows}))
    return rows


def implied_move(symbol: str, event: dt.date) -> dict[str, Any] | None:
    """ATM straddle on the first expiry after the report, as % of spot. The market's own prediction."""
    try:
        d = httpx.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json",
                      headers=UA, timeout=20, follow_redirects=True).json()["data"]
    except Exception:
        return None
    spot = d["current_price"]
    by_exp: dict[str, list[dict[str, Any]]] = {}
    for o in d["options"]:
        exp = dt.datetime.strptime(o["option"][len(symbol):len(symbol) + 6], "%y%m%d").date()
        if exp > event:
            by_exp.setdefault(exp.isoformat(), []).append(o)
    if not by_exp:
        return None
    exp = min(by_exp)
    opts = by_exp[exp]
    strike = min({int(o["option"][-8:]) / 1000 for o in opts}, key=lambda k: abs(k - spot))
    legs = [o for o in opts if int(o["option"][-8:]) / 1000 == strike]
    mid = sum((o["bid"] + o["ask"]) / 2 for o in legs)
    return {"spot": spot, "expiry": exp, "strike": strike, "straddle": round(mid, 2),
            "implied_move_pct": round(mid / spot * 100, 1)}


PREDICT_SYSTEM = """You are the desk's earnings analyst. Build OUR OWN estimate for the upcoming report,
bottom-up from evidence, not by restating analysts. Use the KPI proxies, the security profile, read-through
from peers, suppliers and customers that already reported, management commentary, and any alt-data you
can find (app rankings, player counts, web traffic, pricing checks, channel checks, job posts).

Then compare: our numbers vs consensus, and our expected stock reaction vs the options-implied move.
A trade only has edge when we expect a move larger than options already price, in a direction we are
confident about. Say 'no_view' when the evidence does not support a call; that is a valid and common answer.
Interrogate the call with the checks (real_and_size, noticed, transmission, reversal, override, reaction).
Write a concise note, then the structured fields."""


class KPI(BaseModel):
    name: str
    ours_low: float
    ours_mid: float
    ours_high: float
    consensus: float | None
    evidence: str


class EarningsCall(BaseModel):
    stance: Literal["beat", "miss", "inline", "no_view"]
    guidance: Literal["raise", "maintain", "cut", "unknown"]
    kpis: list[KPI]
    eps_mid: float | None
    eps_consensus: float | None
    expected_reaction_pct: float = Field(description="signed % stock move we expect the day after")
    confidence: float = Field(ge=0, le=1)
    checks: list[Check]
    trade_idea: str = Field(description="instrument/structure, e.g. long calls, put spread, stock, or pass")
    summary: str


class Actual(BaseModel):
    reported: bool
    eps: float | None
    kpis: dict[str, float]
    guidance: Literal["raise", "maintain", "cut", "unknown"]
    reaction_pct: float | None = Field(description="stock % move the session after the report")
    notes: str


def _predict(sym: str, cfg: dict[str, Any], cal: dict[str, Any], event: dt.date) -> tuple[EarningsCall, str, Any]:
    s = get_settings()
    im = implied_move(sym, event)
    ensure_security(sym, cfg.get("type", "_default"))
    user = (f"COMPANY {sym}, reports {event} ({cal.get('time')}), quarter {cal.get('quarter')}.\n"
            f"Consensus EPS {cal.get('eps_forecast')} from {cal.get('n_estimates')} estimates; last year {cal.get('last_year_eps')}.\n"
            f"KPIs that move the stock: {', '.join(cfg.get('kpis', []))}\n"
            f"Options-implied move: {json.dumps(im, default=str)}\nPrice action: {json.dumps(market.snapshot(sym), default=str)}\n\n"
            f"{profile_context(sym)}")
    note = claude().research("earnings_predict", s.prediction_model, PREDICT_SYSTEM, user, effort="high", max_searches=25)
    call = claude().parse("earnings_extract", s.triage_model,
                          "Extract the note's conclusion into the schema exactly as stated.", note, EarningsCall, max_tokens=8000)
    return call, note, im


def _tier(call: EarningsCall, implied_pct: float | None) -> str:
    if call.stance == "no_view" or any(c.verdict == "fail" for c in call.checks):
        return "watch"
    edge = abs(call.expected_reaction_pct) / max(implied_pct or 8.0, 1.0)
    if call.confidence >= 0.75 and edge >= 1.5:
        return "big"
    if call.confidence >= 0.65 and edge >= 1.1:
        return "standard"
    if call.confidence >= 0.6 and edge >= 0.9:
        return "small"
    return "watch"


def _score(sym: str, p: Prediction) -> None:
    s = get_settings()
    note = claude().research("earnings_score", s.triage_model,
                             "Find the actual reported results and the next-session stock reaction. Facts only.",
                             f"{sym} reported around {p.event_date}. Find EPS, these KPIs: "
                             f"{[k['name'] for k in p.payload.get('kpis', [])]}, guidance, and the stock's % move.",
                             effort="low", max_searches=8)
    act = claude().parse("earnings_actual", s.triage_model, "Extract into the schema.", note, Actual)
    if not act.reported:
        return
    pay = p.payload
    kpi_err = {}
    for k in pay.get("kpis", []):
        if k["name"] in act.kpis and k["ours_mid"]:
            a = act.kpis[k["name"]]
            kpi_err[k["name"]] = {"ours_err_pct": round((k["ours_mid"] / a - 1) * 100, 2) if a else None,
                                  "cons_err_pct": round((k["consensus"] / a - 1) * 100, 2) if a and k.get("consensus") else None,
                                  "in_range": k["ours_low"] <= a <= k["ours_high"]}
    exp, real = pay.get("expected_reaction_pct"), act.reaction_pct
    score = {"stance": pay.get("stance"), "direction_right": (exp or 0) * (real or 0) > 0 if real else None,
             "expected_reaction": exp, "actual_reaction": real, "implied_move": (pay.get("implied") or {}).get("implied_move_pct"),
             "guidance_right": pay.get("guidance") == act.guidance, "kpis": kpi_err}
    with session() as db:
        row = db.get(Prediction, p.id)
        row.actual, row.score, row.stage, row.scored_at = act.model_dump(), score, "scored", utcnow()
        log_decision(db, "earnings_scored", p.id, ticker=sym, **score)
    cfg = load_yaml("watchlist.yaml")["core"].get(sym, {})
    append_lesson(sym, cfg.get("type", "_default"), "lesson",
                  f"Earnings {p.event_date}: called {pay.get('stance')} / {exp}% vs actual {real}%. KPI errors {json.dumps(kpi_err)}",
                  why=act.notes[:500])
    commit(f"Score earnings prediction {sym} {p.event_date}")
    from .telegram import send
    send(f"EARNINGS SCORED {sym}: we said {pay.get('stance')} {exp}%, actual {real}% "
         f"({'right' if score['direction_right'] else 'wrong'} direction)", topic="Research")


def run_protocol() -> None:
    from .telegram import send
    core = load_yaml("watchlist.yaml").get("core", {})
    cal = calendar()
    today = dt.date.today()
    for sym, c in cal.items():
        event = dt.date.fromisoformat(c["date"])
        days = (event - today).days
        with session() as db:
            p = db.scalars(select(Prediction).where(Prediction.ticker == sym, Prediction.event == "earnings",
                                                    Prediction.event_date == event)).first()
            if p is None and days <= 21:
                db.add(Prediction(ticker=sym, event="earnings", event_date=event, consensus=c))
                log_decision(db, "earnings_opened", sym, event=c["date"])
        if p is not None and p.stage == "opened" and days <= 7:
            try:
                call, note, im = _predict(sym, core.get(sym, {}), c, event)
            except Exception:
                log.exception("earnings predict failed %s", sym)
                continue
            tier = _tier(call, (im or {}).get("implied_move_pct"))
            with session() as db:
                row = db.get(Prediction, p.id)
                row.payload = call.model_dump() | {"implied": im, "tier": tier, "note": note[:20000]}
                row.stage = "predicted"
                if tier != "watch":
                    th = Thesis(ticker=sym, security_type=core.get(sym, {}).get("type", "_default"),
                                driver=f"Earnings {event}: we expect {call.stance}, {call.expected_reaction_pct:+.0f}% vs implied "
                                       f"{(im or {}).get('implied_move_pct')}%", mechanism=call.summary,
                                direction="long" if call.expected_reaction_pct > 0 else "short",
                                probability=call.confidence, expected_move_pct=abs(call.expected_reaction_pct),
                                horizon_days=max(days + 2, 3), tier=tier, checks=[x.model_dump() for x in call.checks],
                                catalysts=[f"earnings {event} {c.get('time')}"], kill_criteria=["report passes"],
                                summary=call.summary, situation_snapshot=profile_context(sym),
                                meta={"hedge_idea": call.trade_idea, "earnings_prediction": p.id})
                    db.add(th)
                log_decision(db, "earnings_predicted", sym, tier=tier, stance=call.stance)
            kp = "; ".join(f"{k.name} {k.ours_mid:g} vs {k.consensus}" for k in call.kpis)
            send(f"EARNINGS CALL {sym} ({event}, {days}d): {call.stance.upper()} guidance {call.guidance}\n"
                 f"Expect {call.expected_reaction_pct:+.1f}% vs implied {(im or {}).get('implied_move_pct')}%  "
                 f"conf {call.confidence:.0%}  tier {tier}\nKPIs: {kp}\nTrade: {call.trade_idea}\n\n{call.summary}",
                 topic="Research")
        elif p is not None and p.stage == "predicted" and days <= 1:
            with session() as db:
                row = db.get(Prediction, p.id)
                row.stage, row.frozen_at = "frozen", utcnow()
    # score anything past its date
    with session() as db:
        due = db.scalars(select(Prediction).where(Prediction.stage.in_(["predicted", "frozen"]),
                                                  Prediction.event_date < today)).all()
    for p in due:
        if (today - p.event_date).days <= 10:
            try:
                _score(p.ticker, p)
            except Exception:
                log.exception("earnings score failed %s", p.ticker)
