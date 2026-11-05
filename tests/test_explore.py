import datetime as dt

from tradebot import db, explore
from tradebot.config import Settings


class Fake:
    def __init__(self, parsed=None):
        self.parsed, self.calls = parsed, []

    def parse(self, purpose, model, system, user, schema, **kw):
        self.calls.append(purpose)
        return self.parsed[purpose] if isinstance(self.parsed, dict) else self.parsed

    def agent(self, purpose, model, system, user, tools, run_tool, **kw):
        self.calls.append(purpose)
        assert {t["name"] for t in tools} >= {"news_search", "fetch_url", "gdelt_search"}
        return "notes: found X at https://src"


def _setup(tmp_path, monkeypatch, **kw):
    db.init_db(f"sqlite:///{tmp_path}/e.db")
    s = Settings(**kw)
    monkeypatch.setattr(explore, "get_settings", lambda: s)
    return s


def test_allowance_spreads_monthly_budget(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pipeline_budget_usd_month=300)
    full = explore.allowance_left()
    assert 9 < full <= 300   # a day's share, not the whole month
    with db.session() as s:
        s.add(db.ApiUsage(key_index=0, model="m", purpose="explore", cost_usd=full + 1))
    assert explore.allowance_left() < 0


def test_auto_budget_uses_all_key_credit_minus_reserve():
    s = Settings(anthropic_api_keys="a,b", anthropic_key_credits_usd="350,200", monthly_spend_cap_usd=600)
    assert s.pipeline_budget == 535
    assert Settings(anthropic_api_keys="a,b", monthly_spend_cap_usd=600).pipeline_budget == 385   # normal month: 200+200
    assert Settings(anthropic_api_keys="a,b", anthropic_key_credits_usd="350,200",
                    monthly_spend_cap_usd=300).pipeline_budget == 285   # never above the hard cap


def test_director_queues_missions_and_dispatch_runs_them(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, missions_per_direction=2, explorer_concurrency=1, knowledge_dir=str(tmp_path))
    direction = explore.Direction(missions=[explore.MissionSpec(angle=f"a{i}", kind="theme", brief="b", seeds=["q"])
                                            for i in range(3)])
    dossier = explore.Dossier(summary="s", findings=[explore.Finding(fact="f", source="https://src")], affected=["X: y"],
                              surprises=[], unverified=[], follow_up_angles=["next"], interest=4)
    fake = Fake({"direct": direction, "dossier": dossier})
    monkeypatch.setattr(explore, "claude", lambda: fake)
    assert explore.direct() == 3
    with db.session() as s:
        assert len(s.scalars(db.select(db.Mission)).all()) == 2   # capped at missions_per_direction
    ids = [1]
    explore.run_mission(ids[0])
    with db.session() as s:
        m = s.get(db.Mission, 1)
    assert m.status == "done" and m.dossier["interest"] == 4
    assert "a0" in explore.recent_dossiers(dt.datetime(2000, 1, 1))


def test_no_spend_when_allowance_used(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, monthly_spend_cap_usd=0)
    monkeypatch.setattr(explore, "claude", lambda: (_ for _ in ()).throw(AssertionError("should not call")))
    assert explore.direct() == 0 and explore.dispatch() == 0


def test_tool_output_is_capped(monkeypatch):
    monkeypatch.setitem(explore._FUNCS, "news_search", lambda q: "x" * 50000)
    assert len(explore.run_tool("news_search", {"q": "a"})) == 12000
