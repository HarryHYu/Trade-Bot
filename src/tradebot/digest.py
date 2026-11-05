"""Collator: Sonnet turns the day's Haiku leads into a ranked research to-do list.
No web tools, so a run costs cents. The heavy research happens in Claude Code sessions on the Max plan,
which read the list from Telegram, data/digests/ or the desk-feed branch on GitHub."""
from __future__ import annotations

import base64
import datetime as dt
import logging
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .config import get_settings
from .db import Item, Lead, kv_get, kv_set, select, session, utcnow
from .llm import claude

log = logging.getLogger(__name__)
DIGEST_DIR = Path("data/digests")

COLLATOR_SYSTEM = """You collate raw leads for a trading desk. A cheap model (Haiku) has scanned thousands of news items,
posts and data readings and flagged the ones below. Your job is NOT to research or decide trades: a stronger analyst
will do that later. Your job is to turn the noise into a short, ranked to-do list the analyst can work through fast.

Rules:
- Merge duplicates and items about the same underlying thing into one entry. Several independent sources on one
  thing raise its priority; one source repeated by many outlets does not.
- Think things-first: the entry is the real-world driver (an outbreak, a shortage, a viral product, a collapse, a
  ruling), then who it touches: suppliers, buyers, competitors, substitutes, landlords, lenders. Name listed
  companies only when the chain is plausible; never invent tickers.
- Prefer drivers that are measurable now (player counts, prices, filings, orders, official data) over stories.
- For each entry say exactly what the analyst should check first to kill or confirm it.
- Drop noise, promotions, recaps and anything already obviously priced (mainstream headline moves).
- Copy source URLs exactly from the input. Never invent numbers or URLs.
Explorer dossiers are deeper digs and usually carry the best material; use their surprises and affected companies.
Return at most 20 entries, best first."""


class TodoItem(BaseModel):
    driver: str = Field(description="the real-world thing, one sentence")
    why_now: str
    chain: list[str] = Field(description="who is touched and how: 'X supplies Y', 'Z competes'")
    candidates: list[str] = Field(description="listed tickers worth checking, may be empty")
    first_checks: list[str]
    measurable: bool = Field(description="is there first-hand data that tracks the driver")
    urgency: Literal["now", "days", "weeks"]
    priority: int = Field(ge=1, le=5)
    sources: list[str]


class Digest(BaseModel):
    headline: str = Field(description="one line: the most important thing in this batch")
    items: list[TodoItem]


def _batch(since: dt.datetime, limit: int = 80) -> str:
    with session() as db:
        rows = db.execute(select(Lead, Item).join(Item, Item.id == Lead.item_id)
                          .where(Lead.created_at >= since).order_by(Lead.priority.desc()).limit(limit)).all()
    out = []
    for lead, it in rows:
        out.append(f"- [{lead.urgency}] p={lead.priority:.2f} kind={lead.signal_kind} tickers={','.join(lead.tickers) or '-'}\n"
                   f"  driver: {lead.theme}\n  item: {(it.title or '')[:200]} | {it.source} | {it.url or ''}\n"
                   f"  note: {lead.summary[:300]}")
    return "\n".join(out)


def render(d: Digest, when: dt.datetime) -> str:
    lines = [f"# Desk to-do {when:%Y-%m-%d %H:%M} UTC", "", d.headline, ""]
    for i, t in enumerate(d.items, 1):
        lines += [f"## {i}. [P{t.priority} {t.urgency}{' measurable' if t.measurable else ''}] {t.driver}",
                  f"Why now: {t.why_now}",
                  "Chain: " + "; ".join(t.chain),
                  "Check: " + ", ".join(t.candidates) if t.candidates else "Check: (no ticker yet)",
                  "First checks: " + "; ".join(t.first_checks),
                  "Sources: " + " ".join(t.sources), ""]
    return "\n".join(lines)


def push_feed(path: str, text: str) -> None:
    """Optional: publish to the repo's desk-feed branch so research sessions can read it. Needs GITHUB_FEED_TOKEN."""
    s = get_settings()
    if not s.github_feed_token:
        return
    url = f"https://api.github.com/repos/{s.github_feed_repo}/contents/{path}"
    h = {"Authorization": f"Bearer {s.github_feed_token}", "Accept": "application/vnd.github+json"}
    sha = httpx.get(url, headers=h, params={"ref": "desk-feed"}, timeout=20).json().get("sha")
    body = {"message": f"feed: {path}", "branch": "desk-feed", "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    httpx.put(url, headers=h, json=body, timeout=20).raise_for_status()


def run() -> str | None:
    s = get_settings()
    now = utcnow()
    with session() as db:
        last = kv_get(db, "digest_last")
    since = dt.datetime.fromisoformat(last) if last else now - dt.timedelta(hours=12)
    from .explore import recent_dossiers
    dossiers = recent_dossiers(since)
    batch = _batch(since) + ("\n\nEXPLORER DOSSIERS (deeper digs on chosen angles):\n" + dossiers if dossiers else "")
    if not batch.strip():
        return None
    d = claude().parse("collate", s.collator_model, COLLATOR_SYSTEM, batch, Digest, max_tokens=8000)
    text = render(d, now)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    (DIGEST_DIR / f"{now:%Y%m%d-%H%M}.md").write_text(text)
    with session() as db:
        kv_set(db, "digest_last", now.isoformat())
    from . import telegram
    telegram.send(text, topic="Research")
    try:
        push_feed(f"feed/{now:%Y%m%d-%H%M}.md", text)
        push_feed("feed/latest.md", text)
    except Exception as e:
        log.warning("feed push failed: %s", e)
    return text
