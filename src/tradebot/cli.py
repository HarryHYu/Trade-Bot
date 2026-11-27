import typer

app = typer.Typer(help="tradebot")


@app.command()
def run():
    """Run the full scheduler (what the server runs)."""
    from .main import run as r
    r()


@app.command()
def collect():
    from .collectors import run_all
    from .db import init_db
    init_db()
    print(run_all())


@app.command()
def triage(limit: int = 50):
    from .brain import triage_pending
    print(triage_pending(limit), "leads")


@app.command()
def research(n: int = 1):
    from .brain import research_top
    print(research_top(n))


@app.command()
def diligence(ticker: str, thesis: str):
    """Run the full 8-specialist diligence + fact check + judge on any idea, by hand."""
    import json as _j
    from .db import init_db
    from . import diligence as d
    init_db()
    res = d.run(f"TICKER {ticker.upper()}\nTHESIS: {thesis}")
    print(_j.dumps({k: res[k] for k in ("final_tier", "cap_reasons", "verdict")}, indent=1))
    for r in res["reports"]:
        print(f"\n[{r['dimension']}] {r['verdict']}: {r['summary']}")
        for f in r["findings"]:
            print("  -", f)


@app.command()
def costs(days: int = 30):
    """Measured API spend by purpose and model, from the call log."""
    import datetime as _dt
    from sqlalchemy import func
    from .db import ApiUsage, Decision, init_db, select, session
    init_db()
    since = _dt.datetime.now(_dt.UTC).replace(tzinfo=None) - _dt.timedelta(days=days)
    with session() as s:
        rows = s.execute(select(ApiUsage.purpose, ApiUsage.model, func.count(), func.sum(ApiUsage.input_tokens),
                                func.sum(ApiUsage.cache_read_tokens), func.sum(ApiUsage.output_tokens),
                                func.sum(ApiUsage.web_searches), func.sum(ApiUsage.cost_usd))
                         .where(ApiUsage.ts >= since).group_by(ApiUsage.purpose, ApiUsage.model)).all()
        runs = len(s.scalars(select(Decision.id).where(Decision.kind == "diligence_run", Decision.ts >= since)).all())
    total = dil = 0.0
    print(f"{'purpose':24} {'model':18} {'calls':>5} {'in':>9} {'cached':>9} {'out':>8} {'search':>6} {'usd':>8}")
    for p, m, n, i, c, o, w, usd in sorted(rows, key=lambda r: -(r[7] or 0)):
        total += usd or 0
        if p.startswith("diligence"):
            dil += usd or 0
        print(f"{p:24} {m:18} {n:>5} {i or 0:>9} {c or 0:>9} {o or 0:>8} {w or 0:>6} {usd or 0:>8.2f}")
    print(f"\nTotal ${total:.2f} over {days} days. Diligence ${dil:.2f} across {runs} runs"
          + (f" = ${dil / runs:.2f} per run." if runs else "."))


@app.command()
def brief():
    from .reflection import daily_brief
    print(daily_brief())


@app.command()
def check():
    """Verify each configured credential works."""
    from .config import get_settings
    s = get_settings()
    print("anthropic keys:", len(s.api_keys), " paper:", not s.is_live)
    from .broker import broker_label, get_broker
    print("broker:", broker_label(), " equity:", get_broker().account()["equity"])
    if s.telegram_bot_token:
        from .telegram import send
        send("tradebot check: Telegram works")
        print("telegram: sent test message")


ledger_app = typer.Typer(help="Paper ledger: trades, predictions and leads, scored against real prices (no API keys).")
app.add_typer(ledger_app, name="ledger")


@ledger_app.command("open")
def ledger_open(ticker: str, side: str = "long", entry: float = typer.Option(None), stop: float = typer.Option(None),
                target: float = typer.Option(None), thesis: str = "", conviction: float = 3, notional: float = 1000,
                time_stop_days: int = 10, exit_by: str = typer.Option(None), opened: str = typer.Option(None),
                tag: list[str] = typer.Option(None)):
    """Open a paper trade (entry defaults to the latest close)."""
    from . import ledger
    print(ledger.open_trade(ticker, side, entry, stop, target, thesis, conviction, notional, time_stop_days, tag, opened, exit_by))


@ledger_app.command("close")
def ledger_close(trade_id: str, price: float = typer.Option(None), reason: str = "manual", when: str = typer.Option(None)):
    from . import ledger
    print(ledger.close_trade(trade_id, price, reason, when))


@ledger_app.command("check")
def ledger_check():
    """Apply stops, targets, exit-by dates and time stops to open trades."""
    from . import ledger
    print("\n".join(ledger.check()) or "No exits.")


@ledger_app.command("predict")
def ledger_predict(subject: str, question: str, kind: str, resolve_by: str, low: float = typer.Option(None),
                   high: float = typer.Option(None), point: float = typer.Option(None), consensus: float = typer.Option(None),
                   direction: str = typer.Option(None), probability: float = typer.Option(None), notes: str = ""):
    """kind: range | direction | probability."""
    from . import ledger
    print(ledger.predict(subject, question, kind, resolve_by, None, low, high, point, consensus, direction, probability, notes))


@ledger_app.command("resolve")
def ledger_resolve(prediction_id: str, actual: float = typer.Option(None), outcome: str = typer.Option(None)):
    from . import ledger
    print(ledger.resolve(prediction_id, actual, outcome))


@ledger_app.command("lead")
def ledger_lead(ticker: str, stance: str, idea: str, flagged: str = typer.Option(None), acted: bool = False):
    """Record an idea we looked at. stance: long | short | watch_long | watch_short | avoid | watch."""
    from . import ledger
    print(ledger.add_lead(ticker, stance, flagged or ledger._today(), idea, acted))


@ledger_app.command("judge")
def ledger_judge(lead_id: str, verdict: str, signal: str, cause: str, notes: str,
                 confidence: float = typer.Option(None, help="how confident the call was, 0-1, if known")):
    """Judge an idea on its signal and reasoning. verdict: reasoning_error | signal_missed | right_reason | lucky | unlucky |
    unrelated. signal: played_out | failed | unclear. cause: driver | market | other_news | unknown."""
    from . import ledger
    print(ledger.judge(lead_id, verdict, signal, cause, notes, confidence))


@ledger_app.command("tojudge")
def ledger_tojudge(n: int = 15):
    """The biggest unjudged surprises, kept or rejected, for a review shift to judge."""
    from . import ledger
    print("\n".join(ledger.biggest_calls(n=n)) or "Nothing to judge.")


@ledger_app.command("report")
def ledger_report():
    """Run exits, then write ledger/REPORT.md and ledger/equity.png."""
    from . import ledger
    for e in ledger.check():
        print(e)
    print(ledger.report())


def main():
    app()
