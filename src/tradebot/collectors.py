"""The eyes. Each collector pulls one free source and stores new Items with an observed_at stamp.
Sources and their tiers come from config/sources.yaml. 1=primary, 2=established reporting,
3=expert communities, 4=general social."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Iterable

import feedparser
import httpx
from sqlalchemy.exc import IntegrityError

from .config import get_settings, load_yaml
from .db import Item, session

log = logging.getLogger(__name__)
UA = {"User-Agent": "tradebot/0.1"}


def _ts(struct: Any) -> dt.datetime | None:
    try:
        return dt.datetime(*struct[:6]) if struct else None
    except Exception:
        return None


def store(items: Iterable[dict[str, Any]]) -> int:
    """Insert items, skipping ones already seen (unique on source+external_id)."""
    n = 0
    for it in items:
        try:
            with session() as s:
                s.add(Item(**it))
            n += 1
        except IntegrityError:
            pass
    return n


def rss(feeds: list[dict[str, Any]]) -> int:
    out = []
    for f in feeds:
        parsed = feedparser.parse(f["url"], agent=UA["User-Agent"])
        for e in parsed.entries[:50]:
            out.append(dict(
                source=f.get("name", "rss"), source_tier=f.get("tier", 2),
                external_id=e.get("id") or e.get("link", ""), url=e.get("link"),
                title=e.get("title"), text=(e.get("summary") or "")[:4000],
                published_at=_ts(e.get("published_parsed")), meta={"feed": f["url"]},
            ))
    return store(out)


def reddit(subs: list[dict[str, Any]]) -> int:
    s = get_settings()
    if not s.reddit_client_id:
        return 0
    tok = httpx.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(s.reddit_client_id, s.reddit_client_secret),
        data={"grant_type": "client_credentials"}, headers={"User-Agent": s.reddit_user_agent}, timeout=30,
    ).json()["access_token"]
    h = {"Authorization": f"bearer {tok}", "User-Agent": s.reddit_user_agent}
    out = []
    with httpx.Client(headers=h, timeout=30) as c:
        for sub in subs:
            r = c.get(f"https://oauth.reddit.com/r/{sub['name']}/new", params={"limit": 50})
            for ch in r.json().get("data", {}).get("children", []):
                d = ch["data"]
                out.append(dict(
                    source=f"reddit/{sub['name']}", source_tier=sub.get("tier", 4),
                    external_id=d["id"], url="https://reddit.com" + d["permalink"],
                    title=d.get("title"), text=(d.get("selftext") or "")[:6000], author=d.get("author"),
                    published_at=dt.datetime.fromtimestamp(d["created_utc"], dt.UTC).replace(tzinfo=None),
                    meta={"score": d.get("score"), "comments": d.get("num_comments"),
                          "link": d.get("url"), "upvote_ratio": d.get("upvote_ratio")},
                ))
    return store(out)


def sec_filings(forms: list[str]) -> int:
    """Latest filings feed from EDGAR. Form 4 = insider trades, 8-K = material events."""
    s = get_settings()
    out = []
    for form in forms:
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&count=100&output=atom"
        r = httpx.get(url, headers={"User-Agent": s.sec_user_agent}, timeout=30)
        for e in feedparser.parse(r.text).entries:
            out.append(dict(
                source=f"sec/{form}", source_tier=1, external_id=e.get("id", e.get("link")),
                url=e.get("link"), title=e.get("title"), text=(e.get("summary") or "")[:3000],
                published_at=_ts(e.get("updated_parsed")), meta={"form": form},
            ))
    return store(out)


def youtube(channels: list[dict[str, Any]]) -> int:
    """New uploads per channel with transcript. Velocity is tracked via meta.views on re-poll."""
    s = get_settings()
    if not s.youtube_api_key:
        return 0
    from youtube_transcript_api import YouTubeTranscriptApi
    out = []
    with httpx.Client(timeout=30) as c:
        for ch in channels:
            r = c.get("https://www.googleapis.com/youtube/v3/search", params=dict(
                key=s.youtube_api_key, channelId=ch["id"], part="snippet", order="date",
                maxResults=5, type="video"))
            for v in r.json().get("items", []):
                vid = v["id"]["videoId"]
                try:
                    tr = YouTubeTranscriptApi().fetch(vid)
                    text = " ".join(x.text for x in tr)[:40000]
                except Exception:
                    text = v["snippet"].get("description", "")
                out.append(dict(
                    source=f"youtube/{ch.get('name', ch['id'])}", source_tier=ch.get("tier", 3),
                    external_id=vid, url=f"https://youtu.be/{vid}", title=v["snippet"]["title"],
                    text=text, author=v["snippet"]["channelTitle"],
                    published_at=dt.datetime.fromisoformat(v["snippet"]["publishedAt"].rstrip("Z")),
                ))
    return store(out)


def alpaca_news() -> int:
    s = get_settings()
    if not s.alpaca_key_id:
        return 0
    r = httpx.get("https://data.alpaca.markets/v1beta1/news", params={"limit": 50},
                  headers={"APCA-API-KEY-ID": s.alpaca_key_id, "APCA-API-SECRET-KEY": s.alpaca_secret_key},
                  timeout=30)
    out = [dict(
        source="alpaca/benzinga", source_tier=2, external_id=str(n["id"]), url=n.get("url"),
        title=n.get("headline"), text=(n.get("summary") or "")[:4000], author=n.get("author"),
        published_at=dt.datetime.fromisoformat(n["created_at"].rstrip("Z")),
        meta={"symbols": n.get("symbols", [])},
    ) for n in r.json().get("news", [])]
    return store(out)


def roblox_games(universe_ids: list[int]) -> int:
    """Concurrent players per game. One item per poll, so velocity is a time series in the DB."""
    if not universe_ids:
        return 0
    r = httpx.get("https://games.roblox.com/v1/games",
                  params={"universeIds": ",".join(map(str, universe_ids))}, timeout=30)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    out = [dict(
        source="roblox/games", source_tier=1,
        external_id=f"{g['id']}:{now:%Y%m%d%H%M}", title=g["name"],
        text=f"{g['name']}: {g.get('playing', 0)} playing, {g.get('visits', 0)} visits",
        published_at=now, meta={"universe_id": g["id"], "playing": g.get("playing"),
                                "visits": g.get("visits"), "kind": "metric"},
    ) for g in r.json().get("data", [])]
    return store(out)


def roblox_charts(pages: int = 20) -> int:
    """Roblox explore charts: 'top-earning' (ranked by Robux spent in the last 7 days) and 'top-playing-now'.
    One snapshot item per chart per hour, so spend concentration and new-game share become time series."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    out = []
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=30) as c:
        for sort_id in ("top-earning", "top-playing-now"):
            games, tok = [], ""
            for _ in range(pages):
                params = {"sessionId": "tradebot", "sortId": sort_id, "device": "all", "country": "all"}
                if tok:
                    params["pageToken"] = tok
                r = c.get("https://apis.roblox.com/explore-api/v1/get-sort-content", params=params).json()
                games += [{"id": g["universeId"], "name": g["name"], "ccu": g.get("playerCount")} for g in r.get("games", [])]
                tok = r.get("nextPageToken") or ""
                if not tok:
                    break
            top = ", ".join(f"{i + 1}. {g['name']} ({g['ccu']})" for i, g in enumerate(games[:10]))
            out.append(dict(source=f"roblox/{sort_id}", source_tier=1, external_id=f"{sort_id}:{now:%Y%m%d%H}",
                            title=f"Roblox {sort_id} snapshot", text=top, published_at=now, triaged=True,
                            meta={"kind": "metric", "games": games[:500]}))
    return store(out)


def steam_players(apps: list[dict[str, Any]]) -> int:
    """Live concurrent players per Steam game, one item per poll, so retention is a time series.
    Free, no key. Pair each game with the listed company that earns from it."""
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    out = []
    with httpx.Client(timeout=20) as c:
        for a in apps:
            r = c.get("https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                      params={"appid": a["appid"]})
            n = r.json().get("response", {}).get("player_count")
            if n is None:
                continue
            out.append(dict(
                source="steam/players", source_tier=1, external_id=f"{a['appid']}:{now:%Y%m%d%H}",
                title=a["name"], text=f"{a['name']} ({a.get('ticker', '')}): {n} playing on Steam",
                published_at=now, triaged=True,  # metrics feed the trackers, not triage
                meta={"appid": a["appid"], "players": n, "ticker": a.get("ticker"), "kind": "metric"},
            ))
    return store(out)


def run_all() -> dict[str, int]:
    """Read every active source in the registry. One broken source never stops the rest."""
    from . import sources as reg
    reg.seed_from_config()
    by: dict[str, list] = {}
    for s in reg.list_active():
        by.setdefault(s.kind, []).append(s)
    jobs = {
        "rss": lambda: rss([{"name": s.name, "url": s.key, "tier": s.tier} for s in by.get("rss", [])]),
        "news_query": lambda: rss([{"name": f"news/{s.name[:40]}", "url": reg.news_query_url(s.key), "tier": s.tier}
                                   for s in by.get("news_query", [])]),
        "reddit": lambda: reddit([{"name": s.key, "tier": s.tier} for s in by.get("subreddit", [])]),
        "youtube": lambda: youtube([{"id": s.key, "name": s.name, "tier": s.tier} for s in by.get("youtube_channel", [])]),
        "sec": lambda: sec_filings(load_yaml("sources.yaml").get("sec_forms", ["8-K", "4"])),
        "alpaca_news": alpaca_news,
        "roblox": lambda: roblox_games([int(s.key) for s in by.get("roblox_game", [])]),
        "roblox_charts": roblox_charts,
        "steam": lambda: steam_players([{"appid": int(s.key), "name": s.name, "ticker": s.ticker} for s in by.get("steam_app", [])]),
        "web_pages": lambda: store([x for s in by.get("web_page", []) if (x := _safe_page(s))]),
    }
    counts = {}
    for name, fn in jobs.items():
        try:
            counts[name] = fn()
        except Exception as e:
            log.warning("collector %s failed: %s", name, e)
            counts[name] = -1
    return counts


def _safe_page(s):
    from .sources import fetch_web_page
    try:
        return fetch_web_page(s)
    except Exception as e:
        log.warning("web page %s failed: %s", s.key, e)
        return None
