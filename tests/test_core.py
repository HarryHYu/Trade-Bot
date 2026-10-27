from tradebot import brain, risk

ALL = ["real_and_size", "noticed", "transmission", "reversal", "override", "reaction"]


def _t(verdicts, p=0.7, move=25):
    return brain.ThesisOut(has_thesis=True, ticker="X", security_type="t", driver="d", mechanism="m",
                           direction="long", probability=p, expected_move_pct=move, horizon_days=30,
                           checks=[brain.Check(name=n, verdict=v, reasoning="") for n, v in zip(ALL, verdicts)],
                           catalysts=[], kill_criteria=[], hedge_idea="", situation_updates=[], summary="")


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
