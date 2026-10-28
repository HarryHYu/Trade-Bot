import json

from tradebot import db, inbox


def _good(**over):
    d = {"ticker": "osur", "security_type": "diagnostics", "direction": "long", "driver": "d", "mechanism": "m",
         "probability": 0.6, "expected_move_pct": 20, "horizon_days": 14, "tier": "standard",
         "checks": [{"name": n, "verdict": "pass", "reasoning": ""} for n in
                    ["real_and_size", "noticed", "transmission", "reversal", "override", "reaction"]],
         "monitor": [], "summary": "s",
         "diligence": {k: "x" for k in ["filings_reviewed", "earnings_quality", "product_scope", "buyer_channel",
                                        "materiality", "competition", "consensus_and_guidance"]} | {"unverified": []}}
    return d | over


def test_inbox_import_caps_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db.init_db(f"sqlite:///{tmp_path}/i.db")
    monkeypatch.setattr("tradebot.telegram.send", lambda *a, **k: None)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "a.json").write_text(json.dumps(_good()))
    (tmp_path / "inbox" / "b.json").write_text(json.dumps(_good(tier="big", diligence=_good()["diligence"] | {"unverified": ["buyer_channel"]})))
    (tmp_path / "inbox" / "bad.json").write_text("{not json")
    ids = inbox.import_inbox()
    assert len(ids) == 2
    with db.session() as s:
        tiers = sorted(s.get(db.Thesis, i).tier for i in ids)
    assert tiers == ["small", "standard"]   # session's 'big' was capped: unverified diligence
    assert inbox.import_inbox() == []        # never re-imported
