"""Paper ledger: every trade, prediction and lead we make, recorded in the repo and scored against real prices.
No API keys, no database: JSON files under ledger/ (git keeps the history) and free Yahoo prices.

- trades: positions with entry, stop, target and time stop. `check` closes them by the rules using daily bars.
- predictions: numeric ranges, directions and probabilities with a resolve date; scored when resolved (hit rate, Brier).
- leads: every idea we looked at, with our stance (long, short, avoid, watch...), scored by what the price did since,
  so ideas we skipped count too.
`report` writes ledger/REPORT.md and ledger/equity.png."""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
from functools import lru_cache
from pathlib import Path
from typing import Any

import shutil
import threading

import httpx

START_EQUITY = 10_000.0
UA = {"User-Agent": "Mozilla/5.0"}
LOCK = threading.RLock()   # scheduler jobs run in threads; every read-modify-write holds this


def _dir() -> Path:
    try:
        from .config import get_settings
        return Path(get_settings().ledger_dir)
    except Exception:
        return Path("ledger")


# ---------- storage ----------

def _path(name: str) -> Path:
    return _dir() / f"{name}.json"


def load(name: str) -> list[dict[str, Any]]:
    p = _path(name)
    return json.loads(p.read_text()) if p.exists() else []


def save(name: str, rows: list[dict[str, Any]]) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    tmp = _path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2, default=str) + "\n")
    tmp.replace(_path(name))


def seed(from_dir: Path = Path("ledger")) -> None:
    """First start on the server: copy the repo's ledger history into the live ledger directory."""
    d = _dir()
    if d.resolve() == from_dir.resolve() or (d / "trades.json").exists() or not from_dir.exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    for f in from_dir.glob("*.json"):
        shutil.copy(f, d / f.name)


def _locked(fn):
    def wrapper(*a, **k):
        with LOCK:
            return fn(*a, **k)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


def _next_id(rows: list[dict[str, Any]], prefix: str) -> str:
    return f"{prefix}{max([int(r['id'][1:]) for r in rows] + [0]) + 1}"


def _today() -> str:
    return dt.date.today().isoformat()


# ---------- prices ----------

@lru_cache(maxsize=256)
def history(symbol: str) -> tuple[tuple[str, float, float, float, float], ...]:
    """Daily (date, open, high, low, close) for the last two years."""
    j = httpx.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d",
                  headers=UA, timeout=30).json()["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    out = []
    for t, o, h, lo, c in zip(j["timestamp"], q["open"], q["high"], q["low"], q["close"]):
        if c is not None:
            out.append((dt.datetime.fromtimestamp(t, dt.UTC).date().isoformat(), o or c, h or c, lo or c, c))
    return tuple(out)


def close_on(symbol: str, day: str) -> float:
    """Close on the given day, or the last close before it."""
    rows = [r for r in history(symbol) if r[0] <= day]
    return rows[-1][4]


def last_close(symbol: str) -> tuple[str, float]:
    r = history(symbol)[-1]
    return r[0], r[4]


def benchmark_for(symbol: str) -> str:
    if symbol.endswith(".AX"):
        return "^AXJO"
    if symbol.endswith(".L"):
        return "^FTSE"
    if symbol.endswith(".KS"):
        return "^KS11"
    if symbol.endswith(".TW"):
        return "^TWII"
    return "SPY"


# ---------- trades ----------

@_locked
def open_trade(ticker: str, side: str, entry: float | None = None, stop: float | None = None, target: float | None = None,
               thesis: str = "", conviction: float = 3, notional: float = 1000, time_stop_days: int = 10,
               tags: list[str] | None = None, opened: str | None = None, exit_by: str | None = None,
               **extra: Any) -> dict[str, Any]:
    """extra: e.g. managed_by='bot' (the bot runs its own exits; check() leaves it alone) and proposal_id."""
    rows = load("trades")
    opened = opened or _today()
    entry = entry if entry is not None else close_on(ticker, opened)
    t = dict(id=_next_id(rows, "T"), ticker=ticker, side=side, opened=opened, entry=entry, stop=stop, target=target,
             time_stop_days=time_stop_days, exit_by=exit_by, conviction=conviction, notional=notional, thesis=thesis,
             tags=tags or [], benchmark=benchmark_for(ticker), status="open", closed=None, exit=None, exit_reason=None,
             **extra)
    rows.append(t)
    save("trades", rows)
    return t


@_locked
def close_trade(tid: str, price: float | None = None, reason: str = "manual", when: str | None = None) -> dict[str, Any]:
    rows = load("trades")
    t = next(r for r in rows if r["id"] == tid)
    when = when or _today()
    t.update(status="closed", closed=when, exit=price if price is not None else close_on(t["ticker"], when), exit_reason=reason)
    save("trades", rows)
    return t


def _ret(t: dict[str, Any], price: float) -> float:
    r = price / t["entry"] - 1
    return r if t["side"] == "long" else -r


def _r_multiple(t: dict[str, Any], price: float) -> float | None:
    if not t.get("stop"):
        return None
    risk = abs(t["entry"] - t["stop"])
    move = (price - t["entry"]) if t["side"] == "long" else (t["entry"] - price)
    return move / risk if risk else None


def _dead_money(t: dict[str, Any], price: float) -> bool:
    """Not working after the time stop: below +1R, or below +5% when there is no stop."""
    rm = _r_multiple(t, price)
    return rm < 1 if rm is not None else _ret(t, price) < 0.05


@_locked
def check(today: str | None = None) -> list[str]:
    """Apply stops, targets, exit-by dates and time stops to open trades, using daily bars after entry.
    Stop is assumed hit before target on a day that touches both (conservative); a gap through the stop fills at the open."""
    rows, events = load("trades"), []
    today = today or _today()
    for t in rows:
        if t["status"] != "open" or t.get("managed_by") == "bot":
            continue
        bars = [b for b in history(t["ticker"]) if t["opened"] < b[0] <= today]
        long = t["side"] == "long"
        for n, (day, o, h, lo, c) in enumerate(bars, 1):
            stop, tgt = t.get("stop"), t.get("target")
            if stop and ((long and lo <= stop) or (not long and h >= stop)):
                fill = min(o, stop) if long else max(o, stop)
                t.update(status="closed", closed=day, exit=fill, exit_reason="stop")
            elif tgt and ((long and h >= tgt) or (not long and lo <= tgt)):
                fill = max(o, tgt) if long else min(o, tgt)
                t.update(status="closed", closed=day, exit=fill, exit_reason="target")
            elif t.get("exit_by") and day >= t["exit_by"]:
                t.update(status="closed", closed=day, exit=c, exit_reason="exit_by")
            elif t.get("time_stop_days") and n >= t["time_stop_days"] and _dead_money(t, c):
                t.update(status="closed", closed=day, exit=c, exit_reason="time_stop")
            if t["status"] == "closed":
                events.append(f"{t['id']} {t['ticker']} closed {day} at {t['exit']:.4g} ({t['exit_reason']}), "
                              f"{_ret(t, t['exit']) * 100:+.1f}%")
                break
    save("trades", rows)
    return events


# ---------- predictions ----------

@_locked
def predict(subject: str, question: str, kind: str, resolve_by: str, made: str | None = None, low: float | None = None,
            high: float | None = None, point: float | None = None, consensus: float | None = None,
            direction: str | None = None, probability: float | None = None, notes: str = "",
            auto: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    """kind: 'range' (low/high/point, optional consensus), 'direction' (up/down/yes/no) or 'probability' (0-1 that it happens).
    auto: price condition that resolves a probability prediction by itself, e.g.
    {"symbol": "^GSPC", "op": "close_below", "level": 7037, "mode": "any"} (any day before resolve_by) or mode "on"
    (on the resolve date). ops: close_below, close_above, move_pct_ge / move_pct_le (vs "ref", level in %)."""
    rows = load("predictions")
    p = dict(id=_next_id(rows, "P"), subject=subject, question=question, kind=kind, made=made or _today(),
             resolve_by=resolve_by, low=low, high=high, point=point, consensus=consensus, direction=direction,
             probability=probability, notes=notes, status="open", actual=None, outcome=None, score=None,
             auto=auto, **extra)
    rows.append(p)
    save("predictions", rows)
    return p


@_locked
def resolve(pid: str, actual: float | None = None, outcome: str | bool | None = None, when: str | None = None) -> dict[str, Any]:
    rows = load("predictions")
    p = next(r for r in rows if r["id"] == pid)
    score: dict[str, Any] = {}
    if p["kind"] == "range":
        score["hit"] = p["low"] <= actual <= p["high"]
        if p.get("point"):
            score["error_pct"] = round((actual / p["point"] - 1) * 100, 2)
        if p.get("consensus") is not None and p.get("point") is not None:
            # did we lean the right way versus consensus?
            score["beat_consensus_call"] = (p["point"] - p["consensus"]) * (actual - p["consensus"]) > 0
    elif p["kind"] == "direction":
        score["hit"] = str(outcome).lower() == str(p["direction"]).lower()
    elif p["kind"] == "probability":
        happened = outcome in (True, "yes", "true", "1", 1)
        score["happened"] = happened
        score["brier"] = round((p["probability"] - (1.0 if happened else 0.0)) ** 2, 4)
    p.update(status="resolved", actual=actual, outcome=outcome, score=score, resolved=when or _today())
    save("predictions", rows)
    return p


def _met(spec: dict[str, Any], close: float) -> bool:
    op, level = spec["op"], float(spec["level"])
    if op == "close_below":
        return close < level
    if op == "close_above":
        return close > level
    move = (close / float(spec["ref"]) - 1) * 100
    return move >= level if op == "move_pct_ge" else move <= -abs(level)


def auto_resolve(today: str | None = None) -> list[str]:
    """Resolve probability predictions that carry a price condition."""
    today = today or _today()
    done = []
    for p in [p for p in load("predictions") if p["status"] == "open" and p.get("auto")]:
        spec = p["auto"]
        try:
            end = min(today, p["resolve_by"])
            bars = [b for b in history(spec["symbol"]) if p["made"] < b[0] <= end]
        except Exception:
            continue
        hit = next((b for b in bars if _met(spec, b[4])), None) if spec.get("mode", "any") == "any" else None
        if hit:
            resolve(p["id"], actual=hit[4], outcome="yes", when=hit[0])
        elif today >= p["resolve_by"] and bars:
            last = bars[-1][4]
            happened = _met(spec, last) if spec.get("mode") == "on" else False
            resolve(p["id"], actual=last, outcome="yes" if happened else "no", when=p["resolve_by"])
        else:
            continue
        done.append(p["id"])
    return done


# ---------- leads ----------

STANCE_SIGN = {"long": 1, "watch_long": 1, "short": -1, "watch_short": -1, "avoid": -1, "pass": 0, "watch": 0}


@_locked
def add_lead(ticker: str, stance: str, flagged: str, idea: str, acted: bool = False, ref: float | None = None,
             **extra: Any) -> dict[str, Any]:
    """stance: long / short / watch_long / watch_short (direction we leaned, maybe not traded) / avoid (said do not buy now)
    / pass or watch (no directional view; recorded but not scored)."""
    rows = load("leads")
    lead = dict(id=_next_id(rows, "L"), ticker=ticker, stance=stance, flagged=flagged, idea=idea, acted=acted,
                ref=ref if ref is not None else close_on(ticker, flagged), benchmark=benchmark_for(ticker), **extra)
    rows.append(lead)
    save("leads", rows)
    return lead


@_locked
def update(name: str, match: dict[str, Any], **fields: Any) -> int:
    """Set fields on every row whose keys equal `match` (e.g. mark a lead acted)."""
    rows = load(name)
    n = 0
    for r in rows:
        if all(r.get(k) == v for k, v in match.items()):
            r.update(fields)
            n += 1
    if n:
        save(name, rows)
    return n


def _lead_score(lead: dict[str, Any], today: str) -> dict[str, Any]:
    now = close_on(lead["ticker"], today)
    move = now / lead["ref"] - 1
    bmove = close_on(lead["benchmark"], today) / close_on(lead["benchmark"], lead["flagged"]) - 1
    sign = STANCE_SIGN.get(lead["stance"], 0)
    return dict(now=now, move=move, bench=bmove, edge=(move - bmove) * sign if sign else None, raw=move * sign if sign else None)


def source_summary(today: str | None = None) -> str:
    """Directional hit rate and average edge of ideas, split by who produced them (sonnet shortlist, opus thesis, manual)."""
    today = today or _today()
    groups: dict[str, list[float]] = {}
    for lead in load("leads"):
        if not STANCE_SIGN.get(lead["stance"], 0):
            continue
        try:
            e = _lead_score(lead, today)["edge"]
        except Exception:
            continue
        groups.setdefault(lead.get("source", "manual"), []).append(e)
    return "\n".join(f"- {src}: {sum(x > 0 for x in v)}/{len(v)} right vs benchmark, average edge {st.mean(v) * 100:+.1f}%"
                     for src, v in sorted(groups.items())) or "no scored ideas yet"


# ---------- report ----------

def _fmt(x: float | None, pct: bool = True) -> str:
    if x is None:
        return "-"
    return f"{x * 100:+.1f}%" if pct else f"{x:.2f}"


def _equity_curve(trades: list[dict[str, Any]], today: str) -> list[tuple[str, float]]:
    if not trades:
        return []
    start = min(t["opened"] for t in trades)
    days = [r[0] for r in history("SPY") if start <= r[0] <= today]
    curve = []
    for d in days:
        eq = START_EQUITY
        for t in trades:
            if t["opened"] > d:
                continue
            closed = t["status"] == "closed" and t["closed"] <= d
            px = t["exit"] if closed else close_on(t["ticker"], d)
            eq += t["notional"] * _ret(t, px)
        curve.append((d, eq))
    return curve


@_locked
def report(today: str | None = None, chart: bool = True) -> str:
    today = today or _today()
    trades, preds, leads = load("trades"), load("predictions"), load("leads")
    out = [f"# Desk scorecard ({today})", "",
           "Paper ledger: fixed notional per trade, prices from Yahoo closes. Regenerate with `tradebot ledger report`.", ""]

    # trades
    rows, closed_stats = [], []
    for t in trades:
        px = t["exit"] if t["status"] == "closed" else last_close(t["ticker"])[1]
        end = t["closed"] if t["status"] == "closed" else today
        r = _ret(t, px)
        b = close_on(t["benchmark"], end) / close_on(t["benchmark"], t["opened"]) - 1
        rm = _r_multiple(t, px)
        rows.append(f"| {t['id']} | {t['ticker']} | {t['side']} | {t['opened']} | {t['entry']:.4g} | "
                    f"{t['status']}{' ' + t['exit_reason'] if t['exit_reason'] else ''} | {px:.4g} | {_fmt(r)} | "
                    f"{_fmt(rm, False)} | {_fmt(r - (b if t['side'] == 'long' else -b))} | {t['conviction']} |")
        if t["status"] == "closed":
            closed_stats.append(dict(r=r, rm=rm, alpha=r - (b if t["side"] == "long" else -b), conv=t["conviction"],
                                     days=(dt.date.fromisoformat(t["closed"]) - dt.date.fromisoformat(t["opened"])).days))
    out += ["## Trades", "", "| id | ticker | side | opened | entry | status | last/exit | return | R | vs benchmark | conv |",
            "|---|---|---|---|---|---|---|---|---|---|---|", *rows, ""]
    if closed_stats:
        wins = [s for s in closed_stats if s["r"] > 0]
        losses = [s for s in closed_stats if s["r"] <= 0]
        gross_win, gross_loss = sum(s["r"] for s in wins), -sum(s["r"] for s in losses)
        rms = [s["rm"] for s in closed_stats if s["rm"] is not None]
        pf = f"{gross_win / gross_loss:.2f}" if gross_loss else "n/a"
        avg_r = f"{st.mean(rms):.2f}" if rms else "-"
        out += [f"**Closed trades:** {len(closed_stats)} | win rate {len(wins) / len(closed_stats):.0%} | "
                f"average return {_fmt(st.mean(s['r'] for s in closed_stats))} | average R {avg_r} | profit factor {pf} | "
                f"average vs benchmark {_fmt(st.mean(s['alpha'] for s in closed_stats))} | "
                f"average hold {st.mean(s['days'] for s in closed_stats):.0f} days", ""]
    curve = _equity_curve(trades, today)
    if curve:
        out += [f"**Paper equity:** ${curve[-1][1]:,.0f} from ${START_EQUITY:,.0f} "
                f"({_fmt(curve[-1][1] / START_EQUITY - 1)})", ""]

    # predictions
    res = [p for p in preds if p["status"] == "resolved"]
    out += ["## Predictions", ""]
    if res:
        hits = [p["score"]["hit"] for p in res if "hit" in p["score"]]
        briers = [p["score"]["brier"] for p in res if "brier" in p["score"]]
        lean = [p["score"]["beat_consensus_call"] for p in res if "beat_consensus_call" in p["score"]]
        out.append(f"Resolved {len(res)}: hit rate {sum(hits)}/{len(hits)}"
                   + (f" | leaned the right way vs consensus {sum(lean)}/{len(lean)}" if lean else "")
                   + (f" | mean Brier {st.mean(briers):.3f} (0 perfect, 0.25 = coin flip)" if briers else ""))
        out.append("")
    out += ["| id | made | subject | question | call | resolve by | status | actual | score |", "|---|---|---|---|---|---|---|---|---|"]
    for p in preds:
        call = (f"{p['low']}-{p['high']} (pt {p['point']}, cons {p['consensus']})" if p["kind"] == "range"
                else p["direction"] if p["kind"] == "direction" else f"p={p['probability']}")
        flag = "OVERDUE" if p["status"] == "open" and p["resolve_by"] < today else p["status"]
        sc = ", ".join(f"{k}={v}" for k, v in (p["score"] or {}).items())
        out.append(f"| {p['id']} | {p['made']} | {p['subject']} | {p['question']} | {call} | {p['resolve_by']} | {flag} | "
                   f"{p['actual'] if p['actual'] is not None else (p['outcome'] or '-')} | {sc or '-'} |")
    out.append("")

    # leads
    out += ["## Leads (every idea, traded or not, scored by what the price did since)", "",
            "Edge = move relative to the benchmark in the direction of our stance (avoid counts as a bet against).", "",
            "| id | flagged | ticker | stance | acted | ref | now | move | benchmark | edge | idea |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    scored = []
    for lead in leads:
        try:
            s = _lead_score(lead, today)
        except Exception:
            out.append(f"| {lead['id']} | {lead['flagged']} | {lead['ticker']} | {lead['stance']} | - | - | - | price error | | | {lead['idea']} |")
            continue
        if s["edge"] is not None:
            scored.append((lead, s))
        out.append(f"| {lead['id']} | {lead['flagged']} | {lead['ticker']} | {lead['stance']} | {'yes' if lead['acted'] else 'no'} | "
                   f"{lead['ref']:.4g} | {s['now']:.4g} | {_fmt(s['move'])} | {_fmt(s['bench'])} | {_fmt(s['edge'])} | {lead['idea']} |")
    if scored:
        right = sum(1 for _, s in scored if s["edge"] > 0)
        out += ["", f"**Directional leads:** {right}/{len(scored)} right vs benchmark | average edge "
                f"{_fmt(st.mean(s['edge'] for _, s in scored))}"]
        out.append("By source:\n" + source_summary(today))
        for name, keep in (("acted on", True), ("not acted on", False)):
            grp = [s for lead, s in scored if lead["acted"] == keep]
            if grp:
                out.append(f"- {name}: {sum(1 for s in grp if s['edge'] > 0)}/{len(grp)} right, average edge {_fmt(st.mean(s['edge'] for s in grp))}")
    text = "\n".join(out) + "\n"
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / "REPORT.md").write_text(text)
    if chart and curve:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        spy0 = close_on("SPY", curve[0][0])
        fig, ax = plt.subplots(figsize=(8, 3.5))
        ax.plot([dt.date.fromisoformat(d) for d, _ in curve], [e for _, e in curve], label="desk (paper)")
        ax.plot([dt.date.fromisoformat(d) for d, _ in curve],
                [START_EQUITY * close_on("SPY", d) / spy0 for d, _ in curve], label="SPY", alpha=0.7)
        ax.set_title("Paper equity vs SPY")
        ax.legend()
        fig.tight_layout()
        fig.savefig(_dir() / "equity.png", dpi=120)
        plt.close(fig)
    return text
