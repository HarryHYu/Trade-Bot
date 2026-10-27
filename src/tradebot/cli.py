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
    if s.alpaca_key_id:
        from .broker import Alpaca
        print("alpaca equity:", Alpaca().account()["equity"])
    if s.telegram_bot_token:
        from .telegram import send
        send("tradebot check: Telegram works")
        print("telegram: sent test message")


def main():
    app()
