import json
from types import SimpleNamespace as NS

import pytest

from tradebot import chat, db, telegram


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk.yaml").write_text("max_risk_per_trade_pct: 1.0\nmax_open_positions: 12\n")
    (tmp_path / "knowledge" / "types").mkdir(parents=True)
    (tmp_path / "knowledge" / "types" / "b.md").write_text("hello")
    db.init_db(f"sqlite:///{tmp_path}/t.db")
    from tradebot.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "telegram_chat_id", "42")
    monkeypatch.setattr(s, "telegram_bot_token", "x")
    monkeypatch.setattr(s, "config_dir", tmp_path / "config")
    monkeypatch.setattr(s, "knowledge_dir", tmp_path / "knowledge")


def test_sql_is_read_only():
    ctx = chat.Ctx()
    assert "Only a single SELECT" in chat.run_tool("sql_query", {"sql": "DELETE FROM items"}, ctx)
    assert json.loads(chat.run_tool("sql_query", {"sql": "select count(*) n from items"}, ctx)) == [{"n": 0}]


def test_knowledge_paths_are_sandboxed():
    ctx = chat.Ctx()
    assert chat.run_tool("read_knowledge", {"path": "types/b.md"}, ctx) == "hello"
    with pytest.raises(ValueError):
        chat.run_tool("read_knowledge", {"path": "../config/risk.yaml"}, ctx)


def test_rule_change_needs_confirmation(tmp_path):
    ctx = chat.Ctx()
    chat.run_tool("propose_change", {"target": "risk", "key": "max_risk_per_trade_pct", "value": 0.5, "reason": "test"}, ctx)
    assert "1.0" in (tmp_path / "config" / "risk.yaml").read_text()
    cid = ctx.buttons[0][0]["callback_data"].split(":")[1]
    assert "0.5" in chat.apply_change(cid, by="t")
    assert "0.5" in (tmp_path / "config" / "risk.yaml").read_text()
    assert "expired" in chat.apply_change(cid, by="t")


def test_message_round_trip_with_fake_claude(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "_api", lambda m, files=None, **b: sent.append((m, b)) or {"ok": True, "result": {}})

    class FakeClaude:
        calls = 0

        def _call(self, purpose, model, fn):
            FakeClaude.calls += 1
            if FakeClaude.calls == 1:
                return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="list_knowledge", input={})])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="You have one file.")])

    monkeypatch.setattr(chat, "claude", lambda: FakeClaude())
    telegram._chat({"chat": {"id": 42}, "text": "what do you know?", "from": {"id": 1}})
    texts = [b.get("text") for m, b in sent if m == "sendMessage"]
    assert "You have one file." in texts
    assert chat._history("dm")[-1]["content"] == "You have one file."


def test_strangers_are_ignored(monkeypatch):
    called = []
    monkeypatch.setattr(telegram._pool, "submit", lambda *a: called.append(a))
    telegram.handle({"update_id": 1, "message": {"chat": {"id": 999}, "text": "hi", "from": {"id": 5}}})
    assert not called


def test_settings_from_telegram(tmp_path, monkeypatch):
    from tradebot import config
    monkeypatch.setattr(config, "OVERRIDES_FILE", tmp_path / "ov.json")
    config.get_settings.cache_clear()
    sent, deleted = [], []
    monkeypatch.setattr(telegram, "_api", lambda m, files=None, **b: (deleted.append(b) if m == "deleteMessage" else sent.append(b)) or {"ok": True})
    monkeypatch.setattr(config.Settings, "model_config", config.Settings.model_config | {"env_file": None})
    s = config.get_settings()
    monkeypatch.setattr(s, "telegram_chat_id", "42", raising=False)
    # /set changes a normal setting
    assert "MAX_DILIGENCE_PER_DAY = 1" in telegram._command("/set MAX_DILIGENCE_PER_DAY 1", "dm")
    assert config.get_settings().max_diligence_per_day == 1
    # invalid values are rejected
    assert "Could not set" in telegram._command("/set MAX_DILIGENCE_PER_DAY lots", "dm")
    # telegram and live-trading settings are not changeable
    assert "Usage" in telegram._command("/set TELEGRAM_BOT_TOKEN x", "dm")
    # /setkey stores a secret, deletes the message, and never echoes the value
    out = telegram._setkey("/setkey ANTHROPIC_API_KEYS sk-ant-secret1,sk-ant-secret2")
    assert "secret1" not in out and config.get_settings().api_keys == ["sk-ant-secret1", "sk-ant-secret2"]
    config.get_settings.cache_clear()
