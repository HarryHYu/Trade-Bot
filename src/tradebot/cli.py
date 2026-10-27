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
