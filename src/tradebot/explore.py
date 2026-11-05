"""Exploration swarm. Sonnet (the director) reads what Haiku triage flagged and picks angles worth digging into;
many Haiku explorers then dig: cover the main ground, follow links down rabbit holes, and file a dossier of facts
with sources. Nobody here decides trades: the dossiers feed the collator's to-do list for the Max-plan sessions.
Spend is paced by a daily allowance so the month's pipeline budget is used evenly, never blown in an hour."""
from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func

from . import datatools
from .config import get_settings
from .db import ApiUsage, Item, Lead, Mission, kv_get, kv_set, select, session, utcnow
from .llm import claude

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (tradebot research)"}


# ---------- budget pacing ----------

def _spent_since(t: dt.datetime) -> float:
    with session() as db:
        return float(db.scalar(select(func.sum(ApiUsage.cost_usd)).where(ApiUsage.ts >= t)) or 0)


def allowance_left() -> float:
    """Today's share of what is left of the monthly pipeline budget, minus what today already spent."""
    s = get_settings()
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    days_left = max((next_month - day_start).days, 1)
    spent_before_today = _spent_since(month_start) - _spent_since(day_start)
    today = (s.pipeline_budget_usd_month - spent_before_today) / days_left
    return today - _spent_since(day_start)


# ---------- free tools for explorers ----------

def news_search(q: str, n: int = 15) -> str:
    """Bing News (direct article URLs) plus Google News headlines."""
    import feedparser
    out = []
    try:
        r = httpx.get("https://www.bing.com/news/search", params={"q": q, "format": "rss"}, headers=UA, timeout=20)
        for e in feedparser.parse(r.text).entries[:n]:
            link = urllib.parse.parse_qs(urllib.parse.urlparse(e.get("link", "")).query).get("url", [e.get("link", "")])[0]
            out.append(f"- {e.get('published', '')[:16]} | {e.get('title', '')} | {link}")
    except Exception as ex:
        out.append(f"(bing failed: {ex})")
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    for e in feedparser.parse(httpx.get(url, headers=UA, timeout=20).text).entries[:n]:
        out.append(f"- {e.get('published', '')[:16]} | {e.get('title', '')} | (google redirect, search the title to open)")
    return "\n".join(out) or "no results"


def gdelt_search(q: str, timespan: str = "3d", n: int = 25) -> str:
    """GDELT: worldwide news in many languages, updated every 15 minutes."""
    r = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc",
                  params={"query": q, "mode": "artlist", "format": "json", "maxrecords": n, "timespan": timespan,
                          "sort": "datedesc"}, headers=UA, timeout=25)
    arts = (r.json() if r.text.strip().startswith("{") else {}).get("articles", [])
    return "\n".join(f"- {a.get('seendate', '')[:8]} | {a.get('domain', '')} | {a.get('title', '')} | {a.get('url', '')}"
                     for a in arts) or "no results"


def fetch_url(url: str, start: int = 0) -> str:
    """Readable text of a page plus its outbound links, so the explorer can follow them."""
    import trafilatura
    from lxml import html as lh
    r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
    text = trafilatura.extract(r.text) or r.text[:3000]
    links = []
    try:
        doc = lh.fromstring(r.text)
        doc.make_links_absolute(str(r.url))
        seen, host = set(), urllib.parse.urlparse(str(r.url)).netloc
        external, internal = [], []
        for a in doc.iter("a"):
            href, label = a.get("href", "").split("#")[0], " ".join(a.text_content().split())[:80]
            if href.startswith("http") and href not in seen and len(label) > 3 and href != str(r.url):
                seen.add(href)
                (internal if urllib.parse.urlparse(href).netloc == host else external).append(f"- {label} -> {href}")
        links = external[:30] + internal[-20:]   # off-site links first: sources, filings, other coverage
    except Exception:
        pass
    chunk = text[start:start + 8000]
    more = f"\n[more text: call again with start={start + 8000}]" if len(text) > start + 8000 else ""
    return f"URL: {r.url}\n\n{chunk}{more}\n\nLINKS:\n" + "\n".join(links)


def steam_players(appid: int) -> str:
    r = httpx.get("https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
                  params={"appid": appid}, timeout=15).json()
    return json.dumps(r.get("response", {}))


EXPLORER_TOOLS: list[dict[str, Any]] = [
    {"name": "news_search", "description": "Google News search. Fast, free: use it a lot, with many different phrasings.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "gdelt_search", "description": "GDELT worldwide news search (all languages, local outlets). timespan like 1d, 3d, 2w.",
     "input_schema": {"type": "object", "properties": {"q": {"type": "string"}, "timespan": {"type": "string"}}, "required": ["q"]}},
    {"name": "fetch_url", "description": "Read any web page: returns its text and its outbound links. Follow links that look like primary sources or new leads. Use start to page long pages.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "start": {"type": "integer"}}, "required": ["url"]}},
    {"name": "steam_players", "description": "Current concurrent players for a Steam app id.",
     "input_schema": {"type": "object", "properties": {"appid": {"type": "integer"}}, "required": ["appid"]}},
    *datatools.TOOL_DEFS,
]
_FUNCS = {"news_search": news_search, "gdelt_search": gdelt_search, "fetch_url": fetch_url, "steam_players": steam_players}


def run_tool(name: str, inp: dict[str, Any]) -> str:
    out = _FUNCS[name](**inp) if name in _FUNCS else datatools.run(name, inp)
    return out[:12000]   # keeps Haiku under its 100k-token price step


# ---------- director (Sonnet) ----------

DIRECTOR_SYSTEM = """You direct a swarm of cheap research explorers for a trading desk. You do not evaluate trades and you
do not look for the final answer yourself. Your job is to pick ANGLES: the most promising things to dig into right now,
and how to approach each one so the explorers come back with something a senior analyst can act on.

Think like a curious, well-read investigator: things first (an outbreak, a shortage, a viral product, a collapse, a
ruling, a data spike, a political shift), then the chains around them: suppliers, buyers, competitors, substitutes,
landlords, lenders, partners' partners, the market nobody is watching. Look for second- and third-order links, things
the mainstream has not connected, and anything measurable in first-hand data. Also keep covering the main ground: the
biggest stories of the day still deserve a pass for what everyone else is missing.

Mix mission kinds: 'event' (a happening), 'theme' (a trend or area), 'security' (one company deep-dive), 'chain'
(map who gains/loses around a driver), 'sweep' (broad scan of an area: e.g. small caps moving on no news, a country's
business press, a niche community). Do not repeat recent missions unless something changed; build on their
follow-up angles instead. Give each explorer a sharp brief and good seeds (search queries, URLs, tickers)."""


class MissionSpec(BaseModel):
    angle: str = Field(description="short title")
    kind: Literal["event", "theme", "security", "chain", "sweep"]
    brief: str = Field(description="what to find out, what perspective to take, what would be surprising")
    seeds: list[str] = Field(description="starting search queries, URLs or tickers")


class Direction(BaseModel):
    missions: list[MissionSpec]


def _context() -> str:
    since = utcnow() - dt.timedelta(hours=6)
    with session() as db:
        leads = db.execute(select(Lead, Item).join(Item, Item.id == Lead.item_id).where(Lead.created_at >= since)
                           .order_by(Lead.priority.desc()).limit(80)).all()
        recent = db.scalars(select(Mission).where(Mission.created_at >= utcnow() - dt.timedelta(days=2))
                            .order_by(Mission.id.desc()).limit(40)).all()
    parts = ["LEADS FLAGGED BY TRIAGE (last 6h, best first):"]
    parts += [f"- [{l.urgency} p={l.priority:.2f}] {l.theme} | tickers {','.join(l.tickers) or '-'} | {(i.title or '')[:150]} | {i.url or ''}"
              for l, i in leads]
    parts.append("\nRECENT MISSIONS (status, angle, and the follow-ups their explorers suggested):")
    for m in recent:
        follow = "; ".join((m.dossier or {}).get("follow_up_angles", [])[:4])
        parts.append(f"- [{m.status}] {m.angle}" + (f" -> follow-ups: {follow}" if follow else ""))
    watch = Path(get_settings().knowledge_dir) / "watch.md"
    if watch.exists():
        parts.append("\nDESK WATCH LIST (latest entries):\n" + watch.read_text()[-6000:])
    return "\n".join(parts)


def direct() -> int:
    s = get_settings()
    if allowance_left() <= 0:
        return 0
    with session() as db:
        queued = db.scalar(select(func.count()).select_from(Mission).where(Mission.status == "queued")) or 0
    if queued >= s.missions_per_direction * 2:
        return 0   # explorers are behind; don't pile up
    d = claude().parse("direct", s.director_model, DIRECTOR_SYSTEM,
                       _context() + f"\n\nIssue {s.missions_per_direction} missions now.", Direction,
                       effort="medium", max_tokens=12000)
    with session() as db:
        for m in d.missions[: s.missions_per_direction]:
            db.add(Mission(angle=m.angle, kind=m.kind, brief=m.brief, seeds=m.seeds))
    return len(d.missions)


# ---------- explorers (Haiku) ----------

EXPLORER_SYSTEM = """You are a research explorer for a trading desk. You get a mission (an angle and a brief) but you are
NOT limited to it: dig very deep. Cover the main ground first (the key facts, numbers, dates, who is involved), then
follow the rabbit holes: open the links in what you read, chase primary sources (company announcements, filings,
government and regulator pages, official data, trackers, local press, niche communities), and pull on anything
surprising even if it leads somewhere new. Search many different phrasings and languages; use fetch_url on the best
results and follow their links. Note every listed company that is touched, on any exchange, and how.

Rules: record facts with their source URL and date; mark anything second-hand or unverified; never invent numbers,
tickers or URLs. Do not decide trades. When you have covered the ground and followed the best threads, stop and write
your notes: what you found, the surprises, who is affected, what you could not verify, and angles worth a follow-up."""


class Finding(BaseModel):
    fact: str
    source: str
    date: str = ""
    verified: bool = True


class Dossier(BaseModel):
    summary: str
    findings: list[Finding]
    affected: list[str] = Field(description="listed companies touched and how, e.g. 'MYR.AX: competitor gains share'")
    surprises: list[str]
    unverified: list[str]
    follow_up_angles: list[str]
    interest: int = Field(ge=1, le=5, description="how likely this contains something the market has not priced")


def run_mission(mid: int) -> None:
    s = get_settings()
    with session() as db:
        m = db.get(Mission, mid)
        m.status, m.started_at = "running", utcnow()
        task = f"MISSION ({m.kind}): {m.angle}\n\nBRIEF: {m.brief}\n\nSEEDS: {json.dumps(m.seeds)}\n\nToday is {utcnow():%Y-%m-%d}."
    try:
        notes = claude().agent("explore", s.explorer_model, EXPLORER_SYSTEM, task, EXPLORER_TOOLS, run_tool,
                               effort="medium", max_turns=s.explorer_max_turns,
                               max_searches=s.explorer_web_searches, max_fetches=0)
        dossier = claude().parse("dossier", s.explorer_model,
                                 "Turn these research notes into a dossier. Keep every source URL exactly as written.",
                                 notes[:60000] or "no notes", Dossier, max_tokens=8000)
        with session() as db:
            m = db.get(Mission, mid)
            m.status, m.finished_at, m.dossier, m.notes = "done", utcnow(), dossier.model_dump(), notes[:20000]
    except Exception as e:
        log.exception("mission %s failed", mid)
        with session() as db:
            m = db.get(Mission, mid)
            m.status, m.finished_at, m.notes = "failed", utcnow(), str(e)[:2000]


_pool: ThreadPoolExecutor | None = None


def dispatch() -> int:
    """Start queued missions up to the concurrency limit while today's allowance lasts."""
    global _pool
    s = get_settings()
    if allowance_left() <= 0:
        return 0
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=max(s.explorer_concurrency, 1), thread_name_prefix="explorer")
    with session() as db:
        running = db.scalar(select(func.count()).select_from(Mission).where(Mission.status == "running")) or 0
        room = s.explorer_concurrency - running
        ids = db.scalars(select(Mission.id).where(Mission.status == "queued").order_by(Mission.id).limit(max(room, 0))).all()
        for i in ids:
            db.get(Mission, i).status = "running"
    for i in ids:
        _pool.submit(run_mission, i)
    return len(ids)


def recover() -> None:
    """Missions left 'running' by a restart go back in the queue."""
    with session() as db:
        for m in db.scalars(select(Mission).where(Mission.status == "running")).all():
            m.status = "queued"


def recent_dossiers(since: dt.datetime, limit: int = 40) -> str:
    with session() as db:
        ms = db.scalars(select(Mission).where(Mission.status == "done", Mission.finished_at >= since)
                        .order_by(Mission.id.desc()).limit(limit)).all()
    out = []
    for m in ms:
        d = m.dossier or {}
        out.append(f"- DOSSIER [{m.kind}, interest {d.get('interest', '?')}/5] {m.angle}\n  {d.get('summary', '')[:600]}\n"
                   f"  affected: {'; '.join(d.get('affected', [])[:8])}\n  surprises: {'; '.join(d.get('surprises', [])[:5])}\n"
                   f"  sources: {' '.join(f.get('source', '') for f in d.get('findings', [])[:6])}")
    return "\n".join(out)
