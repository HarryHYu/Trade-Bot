import pytest

from tradebot import db, simbroker


@pytest.fixture
def sim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    db.init_db(f"sqlite:///{tmp_path}/sim.db")
    prices = {"XYZ": 100.0, "SPY": 500.0}
    monkeypatch.setattr(simbroker, "quote", lambda s: prices[s])
    monkeypatch.setattr(simbroker, "us_market_open", lambda now=None: True)
    b = simbroker.SimBroker()
    b.reset(10000)
    return b, prices


def test_buy_sell_cash_commission_and_pnl(sim):
    b, prices = sim
    o = b.submit_bracket("XYZ", "buy", 10, None, None, "c1")
    assert o["status"] == "filled" and o["filled_avg_price"] == pytest.approx(100.05)
    assert float(b.account()["cash"]) == pytest.approx(10000 - 1000.5 - 1.0)
    prices["XYZ"] = 110.0
    pos = b.positions()[0]
    assert float(pos["qty"]) == 10 and float(pos["unrealized_pl"]) == pytest.approx(99.5)
    b.close_position("XYZ")
    assert b.positions() == []
    assert float(b.account()["equity"]) == pytest.approx(10000 - 1000.5 - 1 + 1099.45 - 1)


def test_short_and_idempotent_orders(sim):
    b, prices = sim
    b.submit_bracket("XYZ", "sell", 5, None, None, "s1")
    b.submit_bracket("XYZ", "sell", 5, None, None, "s1")   # same client id: no double fill
    assert float(b.positions()[0]["qty"]) == -5
    prices["XYZ"] = 90.0
    assert float(b.positions()[0]["unrealized_pl"]) > 0


def test_closed_market_queues_then_fills(sim, monkeypatch):
    b, _ = sim
    monkeypatch.setattr(simbroker, "us_market_open", lambda now=None: False)
    assert b.submit_bracket("XYZ", "buy", 1, None, None, "q1")["status"] == "new"
    monkeypatch.setattr(simbroker, "us_market_open", lambda now=None: True)
    assert b.order_by_client_id("q1")["status"] == "filled"


def test_full_flow_propose_approve_fill_exit(sim, monkeypatch):
    b, prices = sim
    from tradebot import execution, risk
    monkeypatch.setattr("tradebot.telegram.send", lambda *a, **k: None)
    monkeypatch.setattr(b, "daily_bars", lambda s, limit=120: [dict(h=101, l=99, c=100) for _ in range(30)])
    monkeypatch.setattr(execution, "get_broker", lambda: b)
    import yaml, pathlib
    pathlib.Path("config/risk.yaml").write_text(yaml.safe_dump({
        "max_risk_per_trade_pct": 0.5, "tier_risk_multiplier": {"small": 0.5, "standard": 1.0, "big": 1.5},
        "max_position_pct": 8, "max_gross_exposure_pct": 120, "max_open_positions": 20, "daily_loss_halt_pct": 3,
        "dry_powder_pct": 15, "hedge_on_tiers": [], "allowed_symbols_regex": "^[A-Z]{1,5}$"}))
    pathlib.Path("config/strategy.yaml").write_text(yaml.safe_dump({
        "stop_atr_mult": 1.5, "breakeven_at_r": 0.75, "trail_activate_r": 1.0, "trail_atr_mult": 1.0,
        "target_r_min": 3.0, "default_time_stop_days": 10}))
    with db.session() as s:
        th = db.Thesis(ticker="XYZ", driver="d", mechanism="m", direction="long", probability=0.6,
                       expected_move_pct=10, horizon_days=14, tier="standard", checks=[], summary="s")
        s.add(th)
        s.flush()
        tid = th.id
    ids = execution.propose_for_thesis(tid)
    assert len(ids) == 1
    assert "approved" in execution.decide(ids[0], True, by="test")
    assert execution.execute_approved() == 1
    execution.sync_fills()
    with db.session() as s:
        p = s.get(db.Proposal, ids[0])
        assert p.state == "filled" and p.hold_until is not None
        stop = p.stop_price
    prices["XYZ"] = stop - 0.5          # price falls through the stop
    assert execution.manage_exits() == 1
    with db.session() as s:
        p = s.get(db.Proposal, ids[0])
        assert p.state == "closed" and p.exit_reason == "stop" and p.pnl_usd < 0
    assert b.positions() == []
