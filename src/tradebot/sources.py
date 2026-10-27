"""Source registry. The eyes can watch anything: the bot adds sources as research finds them, the owner adds them
from Telegram, and config/sources.yaml seeds the first set. Sources that need an account are requested from the
owner via Telegram instead of being skipped."""
from __future__ import annotations

import hashlib
import logging
import urllib.parse
from typing import Any

import httpx

from .config import load_yaml
from .db import Source, log_decision, select, session

log = logging.getLogger(__name__)
KINDS = ("rss", "news_query", "subreddit", "youtube_channel", "web_page", "sec_ticker", "steam_app", "roblox_game")
NEEDS_KEY = {"subreddit": "reddit_client_id", "youtube_channel": "youtube_api_key"}


def add(kind: str, key: str, name: str = "", tier: int = 3, ticker: str | None = None, reason: str = "",
        added_by: str = "bot") -> str:
    if kind not in KINDS:
        return f"Unknown kind {kind}. Use one of: {', '.join(KINDS)}"
    key = key.strip()
    with session() as db:
        row = db.scalars(select(Source).where(Source.kind == kind, Source.key == key)).first()
        if row:
            row.active = True
            return f"Already watching {kind}: {key} (re-activated)"
        db.add(Source(kind=kind, key=key, name=name or key[:120], tier=tier, ticker=(ticker or None),
                      reason=reason, added_by=added_by))
        log_decision(db, "source_added", kind, key=key, by=added_by, reason=reason)
    need = NEEDS_KEY.get(kind)
    from .config import get_settings
    if need and not getattr(get_settings(), need):
        request_signup(kind, f"I added a {kind} source ({key}) but need an API key ({need.upper()}) to read it.")
    return f"Now watching {kind}: {key}"


def remove(source_id: int) -> str:
    with session() as db:
        row = db.get(Source, source_id)
        if not row:
            return "No such source"
        row.active = False
        log_decision(db, "source_removed", row.kind, key=row.key)
        return f"Stopped watching {row.kind}: {row.key}"


def list_active(kind: str | None = None) -> list[Source]:
    with session() as db:
        q = select(Source).where(Source.active.is_(True))
        if kind:
            q = q.where(Source.kind == kind)
        return list(db.scalars(q).all())


def request_signup(what: str, why: str) -> None:
    """Anything needing a human (signup, login, payment, API key) goes to the owner's phone."""
    try:
        from .telegram import send
        send(f"ACTION NEEDED: {what}\n{why}\nSign up, then send me the key in Chat (I will tell you where it goes), "
             f"or put it in .env on the server.", topic="Alerts")
    except Exception:
        log.exception("could not send signup request")


def seed_from_config() -> int:
    """Load config/sources.yaml into the registry once. Safe to call on every start."""
    cfg = load_yaml("sources.yaml")
    n = 0
    for f in cfg.get("rss", []):
        n += add("rss", f["url"], f.get("name", ""), f.get("tier", 2), added_by="config").startswith("Now")
    for q in cfg.get("news_queries", []):
        n += add("news_query", q["q"], q.get("name", ""), q.get("tier", 2), q.get("ticker"), added_by="config").startswith("Now")
    for s in cfg.get("reddit", []):
        n += add("subreddit", s["name"], s["name"], s.get("tier", 4), added_by="config").startswith("Now")
    for c in cfg.get("youtube", []):
        n += add("youtube_channel", c["id"], c.get("name", ""), c.get("tier", 3), added_by="config").startswith("Now")
    for a in cfg.get("steam_apps", []):
        n += add("steam_app", str(a["appid"]), a.get("name", ""), 1, a.get("ticker"), added_by="config").startswith("Now")
    for u in cfg.get("roblox_universes", []):
        n += add("roblox_game", str(u), "", 1, "RBLX", added_by="config").startswith("Now")
    for t in cfg.get("sec_tickers", []):
        n += add("sec_ticker", t, t, 1, t, added_by="config").startswith("Now")
    for w in cfg.get("web_pages", []):
        n += add("web_page", w["url"], w.get("name", ""), w.get("tier", 2), w.get("ticker"), added_by="config").startswith("Now")
    return n


def news_query_url(q: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def fetch_web_page(src: Source) -> dict[str, Any] | None:
    """Return an item only when the page's main text changed since the last check."""
    import trafilatura
    r = httpx.get(src.key, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True)
    text = trafilatura.extract(r.text) or ""
    if not text:
        return None
    h = hashlib.sha256(text.encode()).hexdigest()
    if (src.state or {}).get("hash") == h:
        return None
    prev = (src.state or {}).get("text", "")
    with session() as db:
        row = db.get(Source, src.id)
        row.state = {"hash": h, "text": text[:20000]}
    if not prev:
        return None  # first snapshot is the baseline, not news
    import difflib
    added = [l[2:] for l in difflib.ndiff(prev.splitlines(), text.splitlines()) if l.startswith("+ ")]
    return dict(source=f"web/{src.name[:50]}", source_tier=src.tier, external_id=f"{src.id}:{h[:16]}", url=src.key,
                title=f"Page changed: {src.name}", text="\n".join(added)[:8000],
                meta={"source_id": src.id, "ticker": src.ticker, "kind": "page_change"})
