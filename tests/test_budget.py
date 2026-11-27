from tradebot import budget, db
from tradebot.config import Settings


def test_groups_get_their_share_and_cannot_starve_others(tmp_path, monkeypatch):
    db.init_db(f"sqlite:///{tmp_path}/b.db")
    s = Settings(pipeline_budget_usd_month=300)
    monkeypatch.setattr(budget, "get_settings", lambda: s)
    day = budget.daily_allowance()
    assert 300 / 31 <= day <= 300
    assert abs(budget.left("research") - day * 0.48) < 1e-6
    with db.session() as x:
        x.add(db.ApiUsage(key_index=0, model="m", purpose="explore", cost_usd=day * 0.30))
    assert budget.left("explore") <= 1e-9          # explore used its share
    assert budget.left("research") > 0             # research untouched
    with db.session() as x:
        x.add(db.ApiUsage(key_index=0, model="m", purpose="diligence_red_team", cost_usd=day))
    assert budget.left("research") < 0 and budget.left() < 0
