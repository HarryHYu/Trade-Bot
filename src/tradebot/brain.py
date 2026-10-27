"""Triage (Haiku, cheap, every item) -> Lead -> Research (Opus, web tools) -> Thesis.
A thesis is one driver plus the interrogation: every check gets a verdict, so post-mortems can say
exactly which check failed."""
from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from .config import get_settings
from .db import Item, Lead, Thesis, log_decision, select, session, utcnow
from .knowledge import profile_context, append_lesson
from .llm import claude

log = logging.getLogger(__name__)

TRIAGE_SYSTEM = """You triage raw items for a research-driven trading desk. The edge is noticing real-world
drivers before they are priced in, especially from niche communities and first-party data.

For each item decide:
- kind: witness (first-hand observation, original media, concrete specifics), analysis (interpretation of
  public info), recap_or_promo (rehash, course-selling, engagement bait), rumour (second-hand, no specifics),
  official (company/government/regulator), data (a metric reading).
- tickers: listed securities plausibly affected at first or second order (US tickers or commodity ETFs).
- driver: one sentence naming the mechanism, if any. Empty if there is none.
- novelty 0-1: how unlikely it is that the market already knows. Mainstream wire coverage = low.
- materiality 0-1: size of the effect relative to the affected company's business.
- urgency: now (breaking, confirmed, tradable today), soon, slow.
Be harsh. Most items are noise; noise gets materiality near 0. Never invent tickers."""


class TriageOut(BaseModel):
    kind: Literal["witness", "analysis", "recap_or_promo", "rumour", "official", "data"]
    tickers: list[str]
    driver: str
    novelty: float = Field(ge=0, le=1)
    materiality: float = Field(ge=0, le=1)
    urgency: Literal["now", "soon", "slow"]
    reason: str


TIER_WEIGHT = {1: 1.0, 2: 0.8, 3: 0.7, 4: 0.45}
KIND_WEIGHT = {"official": 1.0, "data": 1.0, "witness": 0.9, "analysis": 0.6, "rumour": 0.35, "recap_or_promo": 0.0}


def triage_pending(limit: int = 200) -> int:
    s = get_settings()
    with session() as db:
        items = db.scalars(select(Item).where(Item.triaged.is_(False)).order_by(Item.observed_at).limit(limit)).all()
    made = 0
    for it in items:
        body = f"SOURCE: {it.source} (tier {it.source_tier})\nTITLE: {it.title}\nMETA: {json.dumps(it.meta)[:500]}\n\n{it.text[:6000]}"
        try:
            t = claude().parse("triage", s.triage_model, TRIAGE_SYSTEM, body, TriageOut, max_tokens=2000)
        except Exception as e:
            log.warning("triage failed on item %s: %s", it.id, e)
            continue
        priority = t.novelty * t.materiality * TIER_WEIGHT.get(it.source_tier, 0.4) * KIND_WEIGHT[t.kind]
        with session() as db:
            row = db.get(Item, it.id)
            row.triaged, row.triage = True, t.model_dump()
            if t.driver and t.tickers and priority >= 0.12:
                db.add(Lead(item_id=it.id, tickers=t.tickers, theme=t.driver, signal_kind=t.kind,
                            priority=priority, urgency=t.urgency, summary=t.reason))
                made += 1
    return made


RESEARCH_SYSTEM = """You are the research lead of a small, disciplined trading desk. You are given a lead: a possible
real-world driver for a security. Your job is to find out whether ONE clear driver exists and whether it will
go through to the price, and to say so honestly. Most leads die here; that is correct.

Method:
1. State the driver as a mechanism: what is happening, the channel into the company's reported numbers or
   valuation, rough size relative to the business, and timeline. If you cannot, there is no thesis.
2. Corroborate. Search primary sources first (company, filings, government, raw data), then established
   reporting, then independent first-hand witnesses. Reposts of one original are one source.
3. Interrogate the driver against the company's current situation. Answer every check with a verdict:
   - real_and_size: is it real, and is it big enough to matter for this company?
   - noticed: has the market already got it? (coverage, price move since it began, options, analyst notes)
   - transmission: does it actually reach the reported numbers/price, and when?
   - reversal: will it fade or reverse before the catalyst?
   - override: is a bigger force acting on this company that swamps it?
   - reaction: when the market does see it, how will it interpret it?
4. Name the catalyst that forces repricing, and the kill criteria that would prove you wrong.

MANDATORY DILIGENCE before any tier above watch. Do these from primary sources, not headlines, and
fill every diligence field. If you cannot verify one, say "unverified" and the thesis caps at small.
- filings: every filing since the last results (8-K, 10-Q, 13D/13G, Form 4, proxy). Who is buying, who is
  agitating, what changed.
- earnings quality: was the last profit or beat real, or from one-offs (accounting releases, gains)?
- product scope: exactly what the product is cleared/approved/licensed for, and where it can be sold
  (label limits, which regulators, which markets).
- buyer channel: who actually pays, and are they allowed to buy it (procurement lists, tenders,
  contracts databases). A real driver with no buyer is not revenue.
- materiality: the driver's plausible revenue against the company's revenue and market value, in numbers.
- competition: who else can serve the same demand right now.
- consensus and guidance: what is already expected, and whether guidance was set before the driver.

Use the security profile and situation provided; they hold what the desk has learned. Write a concise
research note, then end with the verdict fields requested."""


class Check(BaseModel):
    name: Literal["real_and_size", "noticed", "transmission", "reversal", "override", "reaction"]
    verdict: Literal["pass", "concern", "fail"]
    reasoning: str


class Diligence(BaseModel):
    filings_reviewed: str = Field(description="filings since last results and what they show")
    earnings_quality: str
    product_scope: str = Field(description="exact label/clearance/licence limits and markets")
    buyer_channel: str = Field(description="who pays and whether they can buy it; evidence")
    materiality: str = Field(description="driver revenue vs company revenue and market cap, in numbers")
    competition: str
    consensus_and_guidance: str
    unverified: list[str] = Field(description="diligence items that could not be verified")


class ThesisOut(BaseModel):
    has_thesis: bool
    ticker: str
    security_type: str = Field(description="e.g. consumer-gaming, energy-commodity, biotech-binary")
    driver: str
    mechanism: str
    direction: Literal["long", "short"]
    probability: float = Field(ge=0, le=1, description="probability the driver goes through to price")
    expected_move_pct: float = Field(description="expected move if it goes through, positive number")
    horizon_days: int
    checks: list[Check]
    catalysts: list[str]
    kill_criteria: list[str]
    hedge_idea: str
    diligence: Diligence
    situation_updates: list[str] = Field(description="new forces acting on the company to add to its situation doc")
    summary: str


def tier_for(t: ThesisOut) -> str:
    """Conviction tier. Big requires every check to pass: independent confirmation, not crowded."""
    if not t.has_thesis:
        return "none"
    verdicts = {c.name: c.verdict for c in t.checks}
    if "fail" in verdicts.values():
        return "watch"
    capped = bool(t.diligence.unverified)  # any unverified diligence item caps conviction at small
    ev = t.probability * t.expected_move_pct - (1 - t.probability) * t.expected_move_pct * 0.5
    concerns = sum(v == "concern" for v in verdicts.values())
    if not capped and ev >= 12 and concerns == 0 and len(verdicts) == 6:
        return "big"
    if not capped and ev >= 6 and concerns <= 1:
        return "standard"
    if ev >= 3:
        return "small"
    return "watch"


def research_lead(lead_id: int) -> int | None:
    s = get_settings()
    with session() as db:
        lead = db.get(Lead, lead_id)
        item = db.get(Item, lead.item_id)
        lead.status = "researching"
    ticker = lead.tickers[0]
    from . import market
    px = []
    for tk in lead.tickers[:4]:
        try:
            snap = market.snapshot(tk)
            if item.published_at:
                snap["move_since_item_pct"] = market.move_since(tk, item.published_at)
            px.append(snap)
        except Exception:
            px.append({"symbol": tk, "error": "no price data"})
    user = (f"LEAD (priority {lead.priority:.2f}, {lead.signal_kind}, urgency {lead.urgency}):\n{lead.theme}\n\n"
            f"ORIGINAL ITEM [{item.source}, tier {item.source_tier}] {item.title}\n{item.url}\n{item.text[:8000]}\n\n"
            f"CANDIDATE TICKERS: {', '.join(lead.tickers)}\n"
            f"PRICE ACTION (use for the 'noticed' check; a big move since the item means the market may have it):\n"
            f"{json.dumps(px, default=str)}\n\n{profile_context(ticker)}")
    note = claude().research("research", s.research_model, RESEARCH_SYSTEM, user, effort=s.research_effort)
    out = claude().parse(
        "thesis_extract", s.triage_model,
        "Extract the research note's conclusion into the schema exactly as the note states it. Do not add judgement.",
        note, ThesisOut, max_tokens=6000)
    tier = tier_for(out)
    with session() as db:
        lead = db.get(Lead, lead_id)
        lead.resolved_at = utcnow()
        lead.notes = note
        if tier == "none":
            lead.status = "dismissed"
            log_decision(db, "lead_dismissed", lead_id, reason=out.summary)
            return None
        th = Thesis(lead_id=lead_id, ticker=out.ticker.upper(), security_type=out.security_type,
                    driver=out.driver, mechanism=out.mechanism, direction=out.direction,
                    probability=out.probability, expected_move_pct=out.expected_move_pct,
                    horizon_days=out.horizon_days, tier=tier, checks=[c.model_dump() for c in out.checks],
                    catalysts=out.catalysts, kill_criteria=out.kill_criteria, summary=out.summary,
                    situation_snapshot=profile_context(out.ticker), evidence=[{"item": item.id, "note": note[:20000]}],
                    meta={"hedge_idea": out.hedge_idea, "diligence": out.diligence.model_dump()})
        db.add(th)
        lead.status = "thesis" if tier != "watch" else "watch"
        db.flush()
        log_decision(db, "thesis_opened", th.id, tier=tier, ticker=th.ticker, summary=out.summary)
        tid = th.id
    for u in out.situation_updates:
        append_lesson(out.ticker, out.security_type, "situation", u)
    return tid


def research_top(n: int | None = None) -> list[int]:
    s = get_settings()
    n = n or s.max_research_per_day
    with session() as db:
        ids = db.scalars(select(Lead.id).where(Lead.status == "new").order_by(Lead.priority.desc()).limit(n)).all()
    done = []
    for lid in ids:
        try:
            tid = research_lead(lid)
            if tid:
                done.append(tid)
        except Exception as e:
            log.exception("research failed on lead %s: %s", lid, e)
    return done
