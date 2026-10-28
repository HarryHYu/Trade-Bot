import anthropic
import httpx

from tradebot import llm


class FakeResp:
    stop_reason = "end_turn"
    usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()


def _auth_error():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)


def test_console_user_key_retries_with_bearer(monkeypatch, tmp_path):
    from tradebot import db
    db.init_db(f"sqlite:///{tmp_path}/l.db")
    s = llm.get_settings()
    monkeypatch.setattr(s, "anthropic_api_keys", "sk-ant-usr-abc")
    c = llm.Claude()
    calls = []

    def fn(client):
        calls.append(client.auth_token)
        if client.auth_token is None:
            raise _auth_error()
        return FakeResp()

    assert c._call("t", "claude-haiku-5-5", fn) is not None
    assert calls == [None, "sk-ant-usr-abc"]  # first x-api-key only, then x-api-key + Bearer


def test_standard_key_401_is_not_retried_forever(monkeypatch, tmp_path):
    from tradebot import db
    db.init_db(f"sqlite:///{tmp_path}/l2.db")
    s = llm.get_settings()
    monkeypatch.setattr(s, "anthropic_api_keys", "sk-ant-api03-x")
    c = llm.Claude()
    try:
        c._call("t", "claude-haiku-5-5", lambda client: (_ for _ in ()).throw(_auth_error()))
        assert False
    except RuntimeError as e:
        assert "rejected" in str(e)
