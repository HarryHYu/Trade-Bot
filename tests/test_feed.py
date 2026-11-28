import httpx
import respx

from tradebot import feed
from tradebot.config import Settings

API = "https://api.github.com/repos/HarryHYu/Trade-Bot"


@respx.mock
def test_push_makes_one_commit_on_desk_feed(monkeypatch):
    monkeypatch.setattr(feed, "get_settings", lambda: Settings(github_feed_token="t"))
    respx.get(f"{API}/git/ref/heads/desk-feed").mock(return_value=httpx.Response(200, json={"object": {"sha": "p1"}}))
    respx.get(f"{API}/git/commits/p1").mock(return_value=httpx.Response(200, json={"tree": {"sha": "t0"}}))
    blob = respx.post(f"{API}/git/blobs").mock(return_value=httpx.Response(201, json={"sha": "b1"}))
    tree = respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t1"}))
    commit = respx.post(f"{API}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c1"}))
    ref = respx.patch(f"{API}/git/refs/heads/desk-feed").mock(return_value=httpx.Response(200, json={}))
    assert feed.push({"REPORT.md": "hi", "equity.png": b"\x89PNG"}, "export") == "c1"
    body = tree.calls[0].request.content.decode()
    assert '"base_tree": "t0"' in body.replace('":"', '": "') and "feed/REPORT.md" in body
    assert blob.called and commit.called and ref.called


def test_secrets_are_never_echoed(monkeypatch):
    from tradebot import telegram
    monkeypatch.setattr(telegram, "get_settings", lambda: Settings(github_feed_token="github_pat_ABCDEFGH12345678"))
    msg = telegram.redact("Illegal header value b'Bearer github_pat_ABCDEFGH12345678\\n/export' and ghp_zzzzzzzz9")
    assert "ABCDEFGH" not in msg and "zzzz" not in msg and "***" in msg
