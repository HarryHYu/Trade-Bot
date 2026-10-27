from tradebot import db, sources


def test_registry_add_dedupe_remove(tmp_path, monkeypatch):
    db.init_db(f"sqlite:///{tmp_path}/s.db")
    monkeypatch.setattr(sources, "request_signup", lambda *a: None)
    assert sources.add("news_query", "helium shortage", ticker="APD").startswith("Now")
    assert "Already" in sources.add("news_query", "helium shortage")
    assert sources.add("telepathy", "x").startswith("Unknown")
    s = sources.list_active("news_query")[0]
    assert "Stopped" in sources.remove(s.id)
    assert not sources.list_active("news_query")


def test_news_query_url_is_google_news_rss():
    u = sources.news_query_url('"FDA approves" Ebola')
    assert u.startswith("https://news.google.com/rss/search?") and "FDA" in u
