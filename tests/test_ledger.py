from tradebot import ledger

BARS = (("2026-11-02", 10, 10.5, 9.8, 10.2), ("2026-11-03", 10.2, 10.4, 8.9, 9.0), ("2026-11-04", 9.0, 9.5, 8.8, 9.2))


def _setup(tmp_path, monkeypatch, bars=BARS):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ledger, "history", lambda s: bars)


def test_stop_is_applied_from_daily_bars(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    t = ledger.open_trade("XYZ", "long", entry=10, stop=9.5, target=12, opened="2026-11-01")
    events = ledger.check(today="2026-11-04")
    rows = ledger.load("trades")
    assert rows[0]["exit_reason"] == "stop" and rows[0]["exit"] == 9.5 and rows[0]["closed"] == "2026-11-03"
    assert t["id"] in events[0]


def test_short_target_and_exit_by(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ledger.open_trade("XYZ", "short", entry=10.2, stop=11, target=9.0, opened="2026-11-01")
    ledger.open_trade("XYZ", "long", entry=10, opened="2026-11-01", exit_by="2026-11-02")
    ledger.check(today="2026-11-04")
    a, b = ledger.load("trades")
    assert a["exit_reason"] == "target" and a["exit"] == 9.0
    assert b["exit_reason"] == "exit_by" and b["exit"] == 10.2


def test_predictions_score_ranges_directions_and_probabilities(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = ledger.predict("X", "bookings", "range", "2026-11-01", low=1600, high=1680, point=1640, consensus=1630)
    d = ledger.predict("Oil", "falls", "direction", "2026-11-06", direction="down")
    p = ledger.predict("SPX", "10% drop", "probability", "2027-02-13", probability=0.3)
    assert ledger.resolve(r["id"], actual=1697)["score"] == {"hit": False, "error_pct": 3.48, "beat_consensus_call": True}
    assert ledger.resolve(d["id"], outcome="down")["score"]["hit"] is True
    assert ledger.resolve(p["id"], outcome="no")["score"]["brier"] == 0.09


def test_report_scores_leads_against_benchmark(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    ledger.add_lead("XYZ", "avoid", "2026-11-02", "lottery", ref=10.2)
    ledger.open_trade("XYZ", "long", entry=10, opened="2026-11-02")
    text = ledger.report(today="2026-11-04", chart=False)
    assert "Directional leads" in text and "| L1 |" in text and "Paper equity" in text


def test_earnings_calls_are_recorded_and_scored(tmp_path, monkeypatch):
    import datetime as dt
    from tradebot import db, recorder
    _setup(tmp_path, monkeypatch)
    db.init_db(f"sqlite:///{tmp_path}/e.db")
    with db.session() as s:
        p = db.Prediction(ticker="RBLX", event="earnings", event_date=dt.date(2026, 10, 29), stage="predicted",
                          payload={"kpis": [{"name": "bookings", "ours_low": 1600, "ours_mid": 1640, "ours_high": 1680,
                                             "consensus": 1630}], "expected_reaction_pct": -5})
        s.add(p)
        s.flush()
        pid = p.id
    recorder.on_earnings_call(pid)
    assert len(ledger.load("predictions")) == 2
    with db.session() as s:
        s.get(db.Prediction, pid).actual = {"kpis": {"bookings": 1697}, "reaction_pct": -17.0}
    recorder.on_earnings_scored(pid)
    rows = {r["kpi"]: r for r in ledger.load("predictions")}
    assert rows["bookings"]["score"]["hit"] is False and rows["_reaction"]["score"]["hit"] is True


def test_auto_resolve_price_conditions(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)   # closes 10.2, 9.0, 9.2 on Nov 2-4
    a = ledger.predict("XYZ", "drops below 9.5", "probability", "2026-12-31", made="2026-11-01", probability=0.3,
                       auto={"symbol": "XYZ", "op": "close_below", "level": 9.5, "mode": "any"})
    b = ledger.predict("XYZ", "above 11 on Nov 4", "probability", "2026-11-04", made="2026-11-01", probability=0.2,
                       auto={"symbol": "XYZ", "op": "close_above", "level": 11, "mode": "on"})
    c = ledger.predict("XYZ", "up 10% by Dec", "probability", "2026-12-31", made="2026-11-01", probability=0.5,
                       auto={"symbol": "XYZ", "op": "move_pct_ge", "level": 10, "ref": 10, "mode": "any"})
    assert sorted(ledger.auto_resolve(today="2026-11-05")) == sorted([a["id"], b["id"]])
    rows = {r["id"]: r for r in ledger.load("predictions")}
    assert rows[a["id"]]["outcome"] == "yes" and rows[a["id"]]["resolved"] == "2026-11-03"
    assert rows[b["id"]]["outcome"] == "no" and rows[c["id"]]["status"] == "open"
