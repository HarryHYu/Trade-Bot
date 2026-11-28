"""Sonnet evaluator: the step between the cheap swarm and Opus. Reads what Haiku triage flagged and what the explorers
dug up, and shortlists the few candidates worth an Opus deep dive. It does not make trade calls; it decides where the
expensive attention goes, with an angle for each. Shortlisted candidates become Leads (signal_kind 'evaluated') that
research_top hands to Opus."""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import budget
from .config import get_settings
from .db import Item, Lead, Mission, Thesis, kv_get, kv_set, log_decision, select, session, utcnow
from .llm import claude

log = logging.getLogger(__name__)

EVALUATE_SYSTEM = """You are the desk's evaluator. Below are raw flags from a cheap scanning model and dossiers from
research explorers. Pick the few candidates that deserve a senior analyst's deep dive today, and say how to attack each.

What earns a slot (the desk's rules, learned from losses):
- An "act now" driver: visible today, the repricing underway or likely within about six weeks. Nothing that needs an
  event months away.
- Measurable in first-hand data (player counts, prices, orders, filings, contract awards, official statistics), not a
  "might". A story without data is a watch, not a candidate.
- A clear buyer/transmission: who pays whom, and is it material relative to the company's size?
- Not already priced: check the price action and coverage notes you are given; a stock that already ran on the news
  needs new information to qualify.
- Non-obvious links (suppliers, competitors, substitutes, landlords, lenders) beat the headline name.
- Shorts are allowed and often better in a falling market.
Skip anything the desk already holds or researched in the last week (listed below) unless something material changed.
Be selective: zero candidates is fine. Never invent tickers, numbers or URLs."""


class Candidate(BaseModel):
    tickers: list[str] = Field(description="listed securities, best first, Yahoo symbols (e.g. MYR.AX, TBLD.L)")
    direction_lean: Literal["long", "short", "unclear"]
    driver: str
    why_now: str
    angle: str = Field(description="how the analyst should attack it: what to verify first, what would kill it")
    measurable_by: str = Field(description="the first-hand data series or document that tracks the driver")
    sources: list[str]
    urgency: Literal["now", "soon", "slow"]
    priority: int = Field(ge=1, le=5)
    from_leads: list[int] = Field(default_factory=list)
    from_missions: list[int] = Field(default_factory=list)


class Passed(BaseModel):
    ticker: str = Field(description="Yahoo symbol, or empty if there is no listed security")
    lean: Literal["long", "short", "unclear"] = Field(description="the direction the idea implied")
    reason: str
    idea: str


class Shortlist(BaseModel):
    candidates: list[Candidate]
    passed_on: list[Passed] = Field(description="the notable ideas you rejected, each with the direction it implied and why")


def _context(since: dt.datetime) -> tuple[str, list[int]]:
    with session() as db:
        leads = db.execute(select(Lead, Item).join(Item, Item.id == Lead.item_id)
                           .where(Lead.created_at >= since, Lead.status == "new", Lead.signal_kind != "evaluated")
                           .order_by(Lead.priority.desc()).limit(80)).all()
        missions = db.scalars(select(Mission).where(Mission.status == "done", Mission.finished_at >= since)
                              .order_by(Mission.id.desc()).limit(40)).all()
        recent = db.scalars(select(Thesis).where(Thesis.opened_at >= utcnow() - dt.timedelta(days=7))).all()
        dismissed = db.scalars(select(Lead).where(Lead.status.in_(["dismissed", "filtered"]),
                                                  Lead.resolved_at >= utcnow() - dt.timedelta(days=7))).all()
    parts = ["RAW FLAGS (Haiku triage):"]
    parts += [f"- lead#{l.id} [{l.urgency} p={l.priority:.2f} {l.signal_kind}] {l.theme} | tickers {','.join(l.tickers) or '-'} | "
              f"{(i.title or '')[:160]} | {i.url or ''}" for l, i in leads]
    parts.append("\nEXPLORER DOSSIERS:")
    for m in missions:
        d = m.dossier or {}
        parts.append(f"- mission#{m.id} [{m.kind}, interest {d.get('interest', '?')}/5] {m.angle}\n  {d.get('summary', '')[:700]}\n"
                     f"  affected: {'; '.join(d.get('affected', [])[:8])}\n  surprises: {'; '.join(d.get('surprises', [])[:5])}\n"
                     f"  sources: {' '.join(f.get('source', '') for f in d.get('findings', [])[:6])}")
    parts.append("\nALREADY RESEARCHED OR HELD (last 7 days): " + (", ".join(f"{t.ticker} ({t.tier}, {t.status})" for t in recent) or "none"))
    parts.append("REJECTED BY OPUS OR THE TRADABILITY FILTER (last 7 days, do not resubmit without new information): "
                 + (", ".join(sorted({tk for l in dismissed for tk in (l.tickers or [])[:1]})) or "none")
                 + ". Only shortlist securities that are tradable: share price above $1 and roughly $2M+ traded a day.")
    watch = Path(get_settings().knowledge_dir) / "watch.md"
    if watch.exists():
        parts.append("\nDESK WATCH LIST (latest):\n" + watch.read_text()[-4000:])
    parts.append("\nWHAT HAS AND HASN'T WORKED (scorecard by source, recent lessons):\n" + _track_record())
    return "\n".join(parts), [l.id for l, _ in leads]


def _track_record() -> str:
    from . import ledger
    out = []
    try:
        out.append(ledger.source_summary())
    except Exception:
        pass
    with session() as db:
        done = db.scalars(select(Thesis).where(Thesis.outcome.is_not(None)).order_by(Thesis.closed_at.desc()).limit(8)).all()
        out += [f"- {t.ticker}: {(t.outcome or {}).get('lesson_for_security', '')[:200]}" for t in done]
    from .ledger import movers_digest
    md = movers_digest()
    if md:
        out.append("REAL CAUSES OF PAST BIG MOVES (movers bank: early signs to look for):\n" + md)
    refl = Path(get_settings().knowledge_dir) / "reflections"
    if refl.exists():
        for f in sorted(refl.glob("*.md"))[-2:]:
            text = f.read_text()
            out.append(text[text.find("## Rules"):][:1500] if "## Rules" in text else "")
    return "\n".join(x for x in out if x) or "no record yet"


def run(max_candidates: int = 4) -> int:
    s = get_settings()
    if budget.left("evaluate") <= 0:
        return 0
    with session() as db:
        last = kv_get(db, "evaluate_last")
    since = dt.datetime.fromisoformat(last) if last else utcnow() - dt.timedelta(hours=12)
    ctx, lead_ids = _context(since)
    if not lead_ids and "mission#" not in ctx:
        return 0
    out = claude().parse("evaluate", s.evaluator_model, EVALUATE_SYSTEM,
                         ctx + f"\n\nShortlist at most {max_candidates} candidates.", Shortlist,
                         effort="medium", max_tokens=12000)
    now = utcnow()
    made = 0
    with session() as db:
        for c in out.candidates[:max_candidates]:
            if not c.tickers:
                continue
            text = (f"EVALUATOR SHORTLIST (Sonnet). Direction lean: {c.direction_lean}\nDRIVER: {c.driver}\nWHY NOW: {c.why_now}\n"
                    f"ANGLE: {c.angle}\nMEASURABLE BY: {c.measurable_by}\nSOURCES: {' '.join(c.sources)}\n")
            for mid in c.from_missions[:3]:
                m = db.get(Mission, mid)
                if m and m.dossier:
                    text += f"\nDOSSIER mission#{mid} {m.angle}:\n{json.dumps(m.dossier)[:5000]}\n"
            item = Item(source="evaluator", source_tier=2, external_id=f"eval-{now.isoformat()}-{made}",
                        url=(c.sources or [None])[0], title=c.driver[:300], text=text, published_at=now,
                        triaged=True, meta={"candidate": c.model_dump()})
            db.add(item)
            db.flush()
            db.add(Lead(item_id=item.id, tickers=[t.upper() for t in c.tickers], theme=c.driver, signal_kind="evaluated",
                        priority=0.5 + 0.1 * c.priority, urgency=c.urgency, summary=c.angle))
            made += 1
        picked = {tk.upper() for c in out.candidates for tk in c.tickers} | {x.ticker.upper() for x in out.passed_on if x.ticker}
        quiet_rejects = []
        for lid in lead_ids:
            row = db.get(Lead, lid)
            if row and row.status == "new":
                row.status = "reviewed"
                item = db.get(Item, row.item_id)
                if row.tickers and row.tickers[0].upper() not in picked and len(quiet_rejects) < 20:
                    d = ((item.triage or {}) if item else {}).get("direction")
                    quiet_rejects.append((row.tickers[0].upper(), {"up": "long", "down": "short"}.get(d), row.theme, row.id))
        kv_set(db, "evaluate_last", now.isoformat())
        log_decision(db, "evaluated", "", candidates=[c.model_dump() for c in out.candidates],
                     passed_on=[x.model_dump() for x in out.passed_on])
    # Score Sonnet's picks on their own, whatever Opus later decides, so the review can judge each stage; and paper-track
    # what Sonnet threw away, with why.
    from . import ledger
    for x in out.passed_on:
        if x.ticker:
            ledger.reject(x.ticker.upper(), x.lean if x.lean != "unclear" else None, "sonnet", x.reason, x.idea)
    for tk, implied, theme, lid in quiet_rejects:
        ledger.reject(tk, implied, "sonnet", "not shortlisted (no reason given)", theme, lead_id=lid)
    for c in out.candidates[:max_candidates]:
        if not c.tickers:
            continue
        stance = {"long": "watch_long", "short": "watch_short"}.get(c.direction_lean, "watch")
        try:
            ledger.add_lead(c.tickers[0].upper(), stance, ledger._today(), c.driver[:240], source="sonnet",
                            ref=ledger.last_close(c.tickers[0].upper())[1], confidence=c.priority / 5)
        except Exception:
            log.exception("could not record shortlist pick %s", c.tickers)
    return made
