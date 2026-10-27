from tradebot import brain, risk

ALL = ["real_and_size", "noticed", "transmission", "reversal", "override", "reaction"]


def _t(verdicts, p=0.7, move=25, unverified=()):
    return brain.ThesisOut(has_thesis=True, ticker="X", security_type="t", driver="d", mechanism="m",
                           direction="long", probability=p, expected_move_pct=move, horizon_days=30,
                           checks=[brain.Check(name=n, verdict=v, reasoning="") for n, v in zip(ALL, verdicts)],
                           catalysts=[], kill_criteria=[], hedge_idea="", situation_updates=[], summary="",
                           diligence=brain.Diligence(filings_reviewed="", earnings_quality="", product_scope="",
                                                     buyer_channel="", materiality="", competition="",
                                                     consensus_and_guidance="", unverified=list(unverified)))


def test_big_needs_every_check_to_pass():
    assert brain.tier_for(_t(["pass"] * 6)) == "big"
    assert brain.tier_for(_t(["pass"] * 5 + ["concern"])) != "big"


def test_any_failed_check_means_watch():
    assert brain.tier_for(_t(["pass"] * 5 + ["fail"])) == "watch"


def test_sizing_respects_risk_and_position_caps():
    bars = [dict(h=101, l=99, c=100) for _ in range(30)]
    sz = risk.size("standard", "long", 100, 100_000, bars, 15)
    assert sz.risk_usd <= 1000 + 1e-6
    assert sz.notional <= 15_000
    assert sz.stop < 100 < sz.take_profit


def test_small_account_uses_fractional_shares():
    bars = [dict(h=101, l=99, c=100) for _ in range(30)]
    sz = risk.size("standard", "long", 450, 300, bars, 15)
    assert sz is not None and sz.qty != int(sz.qty)
    assert sz.notional <= 300 * 0.30 + 1e-6


def test_small_account_blocks_shorts():
    v = risk.check_portfolio("RBLX", 50, 300, [], 0, "standard", "short")
    assert any("short" in x for x in v)


def test_earnings_tier_needs_edge_over_implied_move():
    from tradebot import earnings
    from tradebot.brain import Check
    checks = [Check(name=n, verdict="pass", reasoning="") for n in ALL]
    mk = lambda conf, move, stance="beat": earnings.EarningsCall(
        stance=stance, guidance="raise", kpis=[], eps_mid=None, eps_consensus=None,
        expected_reaction_pct=move, confidence=conf, checks=checks, trade_idea="", summary="")
    assert earnings._tier(mk(0.8, 25), 14) == "big"
    assert earnings._tier(mk(0.8, 10), 14) == "watch"      # expecting less than options already price
    assert earnings._tier(mk(0.9, 30, "no_view"), 14) == "watch"


def test_unverified_diligence_caps_at_small():
    assert brain.tier_for(_t(["pass"] * 6, unverified=["buyer_channel"])) == "small"


def test_act_now_desk_rejects_far_off_catalysts():
    t = _t(["pass"] * 6)
    t.horizon_days = 120   # needs an event months away
    assert brain.tier_for(t) == "watch"
    t.horizon_days = 30    # repricing within weeks is fine
    assert brain.tier_for(t) == "big"


def test_exit_rules_breakeven_trail_target_time():
    import datetime as dt
    st = {"stop_atr_mult": 1.5, "breakeven_at_r": 0.75, "trail_activate_r": 1.0, "trail_atr_mult": 1.0}
    now = dt.datetime(2026, 1, 10)
    # long from 100, stop 97 (R=3, ATR=2). Price 102.5 (+0.83R): stop moves to breakeven, no exit
    r, stop, peak = risk.exit_decision("buy", 100, 97, 110, 100, 102.5, now, None, True, st)
    assert r is None and stop == 100
    # runs to 106 (+2R): trail = 106 - 2 = 104
    r, stop, peak = risk.exit_decision("buy", 100, stop, 110, peak, 106, now, None, True, st, r=3)
    assert r is None and stop == 104 and peak == 106
    # pulls back to 103.9: trailing stop exit
    r, *_ = risk.exit_decision("buy", 100, stop, 110, peak, 103.9, now, None, True, st, r=3)
    assert r == "trailing_stop"
    # target and time stop
    assert risk.exit_decision("buy", 100, 97, 110, 100, 110.5, now, None, True, st)[0] == "target"
    assert risk.exit_decision("buy", 100, 97, 110, 100, 101, now, now, True, st)[0] == "time_stop"
    # a winner past 1R is not time-stopped
    assert risk.exit_decision("buy", 100, 97, 120, 104, 104, now, now, True, st)[0] is None
    # short side mirrors: short from 100, stop 103, price 96 (+1.33R) -> trail at 96+2 = 98
    r, stop, _ = risk.exit_decision("sell", 100, 103, 90, 100, 96, now, None, True, st)
    assert r is None and stop == 98
    # killed thesis exits immediately
    assert risk.exit_decision("buy", 100, 97, 110, 100, 101, now, None, False, st)[0] == "thesis_killed"
