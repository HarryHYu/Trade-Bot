"""End to end with fake models and prices: Haiku flag -> Sonnet shortlist -> Opus thesis -> paper trade -> exit -> ledger."""
import shutil
from pathlib import Path

import pytest

from tradebot import brain, db, evaluate, execution, ledger, review, simbroker
from tradebot.config import Settings

REPO = Path(__file__).resolve().parents[1]
CHECKS = ["real_and_size", "noticed", "transmission", "reversal", "override", "reaction"]


class FakeClaude:
    def __init__(self):
        self.calls = []

    def parse(self, purpose, model, system, user, schema, **kw):
        self.calls.append((purpose, model))
        if purpose == "triage":
            return brain.TriageOut(kind="data", tickers=["XYZ"], driver="Record players for XYZ's new game",
                                   novelty=0.9, materiality=0.8, urgency="soon", reason="first-hand counts")
        if purpose == "evaluate":
            return evaluate.Shortlist(candidates=[evaluate.Candidate(
                tickers=["XYZ"], direction_lean="long", driver="Record players for XYZ's new game", why_now="week 2 still rising",
                angle="check revenue share vs size", measurable_by="Steam player counts", sources=["https://example.com/a"],
                urgency="soon", priority=4)], passed_on=[])
        if purpose == "thesis_extract":
            return brain.ThesisOut(
                has_thesis=True, ticker="XYZ", security_type="consumer-gaming", driver="Record players", mechanism="sales",
                direction="long", probability=0.6, expected_move_pct=20, horizon_days=10,
                checks=[brain.Check(name=n, verdict="pass", reasoning="") for n in CHECKS], catalysts=["update"],
                kill_criteria=["players fade"], hedge_idea="", monitor=[], situation_updates=[], summary="buy XYZ",
                diligence=brain.Diligence(filings_reviewed="x", earnings_quality="x", product_scope="x", buyer_channel="x",
                                          materiality="x", competition="x", consensus_and_guidance="x", unverified=[]))
        raise AssertionError(purpose)

    def research(self, purpose, model, system, user, **kw):
        self.calls.append((purpose, model))
        assert "EVALUATOR SHORTLIST" in user   # Opus sees Sonnet's angle
        return "note: thesis holds"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO / "config", tmp_path / "config")
    (tmp_path / "knowledge").mkdir()
    s = Settings(pipeline_budget_usd_month=300, knowledge_dir=tmp_path / "knowledge", ledger_dir=tmp_path / "ledger")
    for mod in ("tradebot.config", "tradebot.brain", "tradebot.evaluate", "tradebot.execution", "tradebot.budget",
                "tradebot.broker", "tradebot.simbroker", "tradebot.ledger", "tradebot.review"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: s, raising=False)
    db.init_db(f"sqlite:///{tmp_path}/p.db")
    fake = FakeClaude()
    monkeypatch.setattr(brain, "claude", lambda: fake)
    monkeypatch.setattr(evaluate, "claude", lambda: fake)
    monkeypatch.setattr(brain, "diligence_budget_left", lambda: False)
    monkeypatch.setattr(brain, "untradable", lambda t: None)
    prices = {"XYZ": 100.0, "SPY": 500.0}
    monkeypatch.setattr(simbroker, "quote", lambda sym: prices[sym])
    monkeypatch.setattr(simbroker, "fx_to_usd", lambda sym: 1.0)
    monkeypatch.setattr(simbroker, "market_open", lambda sym=None, now=None: True)
    monkeypatch.setattr(simbroker.SimBroker, "daily_bars", lambda self, sym, limit=120: [dict(h=101, l=99, c=100)] * 30)
    monkeypatch.setattr("tradebot.market.snapshot", lambda t: {"symbol": t, "last": prices[t]})
    monkeypatch.setattr("tradebot.market.move_since", lambda t, since: 0.0)
    bars = tuple((f"2026-11-{d:02d}", 100, 101, 99, 100) for d in range(1, 28))
    monkeypatch.setattr(ledger, "history", lambda sym: bars)
    monkeypatch.setattr("tradebot.telegram.send", lambda *a, **k: None)
    simbroker.SimBroker().reset(10000)
    return fake, prices


def test_full_autonomous_chain(world):
    fake, prices = world
    with db.session() as s:
        s.add(db.Item(source="steam", source_tier=1, external_id="1", title="XYZ game record", text="players 170k"))
    assert brain.triage_pending() == 1
    assert evaluate.run() == 1
    with db.session() as s:
        raw = s.scalars(db.select(db.Lead).where(db.Lead.signal_kind != "evaluated")).one()
        assert raw.status == "reviewed"          # Haiku's flag was looked at by Sonnet
    tids = brain.research_top()
    assert len(tids) == 1
    models = {p: m for p, m in fake.calls}
    assert models["triage"].startswith("claude-haiku") and models["evaluate"].startswith("claude-sonnet")
    assert models["research"].startswith("claude-opus")
    # the thesis is on the record before any trade
    leads = {l["source"]: l for l in ledger.load("leads")}
    assert leads["sonnet"]["stance"] == "watch_long"          # Sonnet's pick is scored on its own
    assert leads["opus"]["thesis_id"] == tids[0]
    assert ledger.load("predictions")[0]["auto"]["op"] == "move_pct_ge"
    ids = execution.propose_for_thesis(tids[0])
    assert execution.execute_approved() == 1      # paper autotrade: no human needed
    execution.sync_fills()
    trade = ledger.load("trades")[0]
    opus = next(l for l in ledger.load("leads") if l["source"] == "opus")
    assert trade["status"] == "open" and trade["managed_by"] == "bot" and opus["acted"] is True
    with db.session() as s:
        stop = s.get(db.Proposal, ids[0]).stop_price
    prices["XYZ"] = stop - 1
    assert execution.manage_exits() == 1
    trade = ledger.load("trades")[0]
    assert trade["status"] == "closed" and trade["exit_reason"] == "stop"
    text = review.build()
    assert "sonnet" in ledger.source_summary() and "opus" in ledger.source_summary()
    assert "DAILY REVIEW" in text and "Opus theses" in text and "Scorecard: 1 closed" in text


def test_watch_theses_go_back_to_opus(tmp_path):
    import datetime as dt
    db.init_db(f"sqlite:///{tmp_path}/w.db")
    with db.session() as s:
        s.add(db.Thesis(ticker="BRBR", driver="whey costs easing", mechanism="m", direction="long", probability=0.5,
                        expected_move_pct=20, horizon_days=20, tier="watch", checks=[], summary="s",
                        catalysts=["Nov 17 report"], opened_at=db.utcnow() - dt.timedelta(days=6)))
    assert brain.recheck_watch() == 1
    assert brain.recheck_watch() == 0     # only once
    with db.session() as s:
        lead = s.scalars(db.select(db.Lead)).one()
        assert lead.signal_kind == "evaluated" and lead.tickers == ["BRBR"]


def test_rejections_are_paper_tracked_at_every_stage(world, monkeypatch):
    fake, prices = world

    def parse(purpose, model, system, user, schema, **kw):
        if purpose == "triage":
            return brain.TriageOut(kind="data", tickers=["XYZ"], driver="weak hint", novelty=0.5, materiality=0.4,
                                   urgency="slow", reason="minor", direction="up")
        if purpose == "evaluate":
            return evaluate.Shortlist(candidates=[], passed_on=[evaluate.Passed(ticker="ABC", lean="short",
                                                                                reason="already priced", idea="ABC loses share")])
        return FakeClaude.parse(fake, purpose, model, system, user, schema, **kw)
    monkeypatch.setattr(fake, "parse", parse)
    with db.session() as s:
        s.add(db.Item(source="news", source_tier=4, external_id="2", title="t", text="x"))
    brain.triage_pending()                  # priority below the bar -> Haiku discard, tracked
    prices["ABC"] = 50.0
    with db.session() as s:
        s.add(db.Lead(item_id=1, tickers=["XYZ"], theme="t", signal_kind="data", priority=0.3, urgency="soon"))
    evaluate.run()                          # Sonnet passes on ABC and quietly drops XYZ
    stages = sorted((l["stage"], l["ticker"], l.get("implied")) for l in ledger.load("leads") if l["stance"] == "rejected")
    assert ("haiku", "XYZ", "long") in stages and ("sonnet", "ABC", "short") in stages and ("sonnet", "XYZ", "long") in stages
    assert "rejections right" in ledger.rejection_summary() or "without a direction" in ledger.rejection_summary()


def test_untradable_filters_illiquid_and_repeats(world, monkeypatch):
    import datetime as dt
    monkeypatch.undo()   # use the real filter, with fake bars
    from tradebot import market, simbroker
    monkeypatch.setattr(simbroker, "fx_to_usd", lambda s: 1.0)
    monkeypatch.setattr(market, "bars", lambda s, r="3mo": [dict(c=0.5 if s == "PENNY" else 50, v=10_000 if s == "THIN" else 1_000_000)] * 30)
    assert "below floor" in brain.untradable("PENNY")
    assert "illiquid" in brain.untradable("THIN")
    assert brain.untradable("BIG") is None
    with db.session() as s:
        s.add(db.Thesis(ticker="BIG", driver="d", mechanism="m", direction="long", probability=0.5, expected_move_pct=10,
                        horizon_days=10, tier="watch", checks=[], summary="s"))
    assert "already researched" in brain.untradable("BIG")
