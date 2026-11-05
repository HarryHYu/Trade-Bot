from tradebot import brain, db, digest, main
from tradebot.config import Settings


class FakeClaude:
    def __init__(self, out):
        self.out, self.calls = out, []

    def parse(self, purpose, model, system, user, schema, **kw):
        self.calls.append((purpose, model, user))
        return self.out


def test_job_switch_only_runs_enabled_jobs(monkeypatch, tmp_path):
    db.init_db(f"sqlite:///{tmp_path}/j.db")
    monkeypatch.setattr(main, "get_settings", lambda: Settings(enabled_jobs="triage,digest"))
    ran = []
    main._job("triage", lambda: ran.append("triage"))()
    main._job("research", lambda: ran.append("research"))()
    with db.session() as s:
        db.kv_set(s, "research_paused", "1")
    main._job("triage", lambda: ran.append("paused"))()
    assert ran == ["triage"]


def test_triage_keeps_tickerless_drivers(monkeypatch, tmp_path):
    db.init_db(f"sqlite:///{tmp_path}/t.db")
    with db.session() as s:
        s.add(db.Item(source="news", source_tier=2, external_id="1", title="Shortage", text="x"))
    out = brain.TriageOut(kind="official", tickers=[], driver="Egg shortage spreads", novelty=0.8,
                          materiality=0.6, urgency="now", reason="r")
    monkeypatch.setattr(brain, "claude", lambda: FakeClaude(out))
    assert brain.triage_pending() == 1
    with db.session() as s:
        lead = s.scalars(db.select(db.Lead)).one()
    assert lead.tickers == [] and lead.theme == "Egg shortage spreads"


def test_digest_collates_new_leads_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    db.init_db(f"sqlite:///{tmp_path}/d.db")
    with db.session() as s:
        it = db.Item(source="news", source_tier=2, external_id="1", title="Game hits 170k players", url="https://u")
        s.add(it)
        s.flush()
        s.add(db.Lead(item_id=it.id, tickers=["TBLD.L"], theme="Indie hit", priority=0.5, urgency="now"))
    d = digest.Digest(headline="h", items=[digest.TodoItem(
        driver="Indie game record", why_now="w", chain=["publisher earns"], candidates=["TBLD.L"],
        first_checks=["Steam players"], measurable=True, urgency="now", priority=4, sources=["https://u"])])
    fake = FakeClaude(d)
    monkeypatch.setattr(digest, "claude", lambda: fake)
    sent = []
    monkeypatch.setattr("tradebot.telegram.send", lambda text, **k: sent.append(text))
    text = digest.run()
    assert "Indie game record" in text and "TBLD.L" in fake.calls[0][2]
    assert sent and list((tmp_path / "data" / "digests").iterdir())
    assert digest.run() is None   # nothing new since the last run
