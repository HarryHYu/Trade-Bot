"""Learning loop. Post-mortem per closed trade (which check failed and why), daily brief, and a weekly
pattern review that only proposes changes; adopting them is a separate, logged decision."""
from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from pydantic import BaseModel

from .config import get_settings
from .db import Decision, Lead, Proposal, Thesis, log_decision, select, session, utcnow
from .knowledge import append_lesson, append_type_briefing, commit
from .llm import claude, spend_by_key


class PostMortem(BaseModel):
    outcome: Literal["win", "loss", "flat"]
    failed_check: Literal["none", "real_and_size", "noticed", "transmission", "reversal", "override",
                          "reaction", "timing", "sizing", "hedge", "luck"]
    what_happened: str
    why: str
    lesson_for_security: str
    generalises_to_type: bool


def post_mortems() -> int:
    s = get_settings()
    n = 0
    with session() as db:
        done = {d.ref for d in db.scalars(select(Decision).where(Decision.kind == "post_mortem"))}
        closed = [p for p in db.scalars(select(Proposal).where(Proposal.state == "closed", Proposal.role == "main"))
                  if str(p.id) not in done]
    for p in closed:
        with session() as db:
            th = db.get(Thesis, p.thesis_id)
        prompt = (f"Closed trade: {p.side} {p.qty:g} {p.symbol} filled {p.fill_price} exit {p.exit_price} pnl {p.pnl_usd}.\n"
                  f"Thesis: {th.driver}\nMechanism: {th.mechanism}\nChecks at entry: {json.dumps(th.checks)}\n"
                  f"Situation at entry:\n{th.situation_snapshot[-4000:]}\n\nResearch note:\n{(th.evidence or [{}])[0].get('note', '')[:8000]}\n\n"
                  "Find out what actually happened since entry (search), then say which check failed, if any, and why. "
                  "Distinguish a wrong thesis from a right thesis with bad timing, sizing or luck.")
        note = claude().research("post_mortem", s.research_model, "You are the desk's honest post-mortem analyst.", prompt, effort="medium")
        pm = claude().parse("post_mortem_extract", s.triage_model, "Extract the post-mortem into the schema.", note, PostMortem)
        append_lesson(th.ticker, th.security_type, "lesson", pm.lesson_for_security, why=pm.why)
        with session() as db:
            t = db.get(Thesis, th.id)
            t.failed_check, t.status, t.closed_at = pm.failed_check, "closed", utcnow()
            t.outcome = pm.model_dump() | {"pnl": p.pnl_usd}
            log_decision(db, "post_mortem", p.id, **pm.model_dump())
        n += 1
    if n:
        commit(f"Post-mortems for {n} closed trades")
    return n


class Finding(BaseModel):
    pattern: str
    bucket: str
    sample_size: int
    status: Literal["hypothesis", "confirmed"]
    mechanism_why: str
    proposed_change: str
    security_type: str


class WeeklyReview(BaseModel):
    findings: list[Finding]
    looked_and_found_nothing: list[str]


def weekly_review() -> WeeklyReview | None:
    s = get_settings()
    with session() as db:
        rows = db.scalars(select(Thesis).where(Thesis.outcome.is_not(None))).all()
        prior = [d.payload for d in db.scalars(select(Decision).where(Decision.kind == "weekly_finding"))]
    if len(rows) < 10:
        return None
    data = [dict(id=t.id, ticker=t.ticker, type=t.security_type, tier=t.tier, direction=t.direction,
                 p=t.probability, move=t.expected_move_pct, horizon=t.horizon_days, checks=t.checks,
                 failed=t.failed_check, outcome=t.outcome, opened=str(t.opened_at)) for t in rows]
    sys = ("You review a trading desk's scored outcomes for systematic error. Split errors by every attribute "
           "(type, tier, signal kind, failed check, horizon, direction, situation). A pattern only counts if errors "
           "cluster in a bucket with at least 20 outcomes; smaller is a hypothesis. A hypothesis from earlier weeks "
           "becomes confirmed only if NEW outcomes since then agree. Explain the mechanism (why), never just the number. "
           "Record what you checked and found nothing in. Propose changes; never adopt them.")
    out = claude().parse("weekly_review", s.research_model, sys,
                         f"PRIOR FINDINGS:\n{json.dumps(prior)[-20000:]}\n\nOUTCOMES:\n{json.dumps(data)[-150000:]}",
                         WeeklyReview, effort="high")
    with session() as db:
        for f in out.findings:
            log_decision(db, "weekly_finding", f.security_type, **f.model_dump())
    for f in out.findings:
        if f.status == "confirmed":
            append_type_briefing(f.security_type, f.pattern, f.mechanism_why, f"n={f.sample_size}; bucket {f.bucket}")
    commit("Weekly review findings")
    return out


def daily_brief() -> str:
    since = utcnow() - dt.timedelta(days=1)
    with session() as db:
        theses = db.scalars(select(Thesis).where(Thesis.opened_at >= since)).all()
        leads = db.scalars(select(Lead).where(Lead.created_at >= since)).all()
    spend = spend_by_key()
    lines = [f"DAILY BRIEF  leads {len(leads)}  theses {len(theses)}  API month ${sum(spend.values()):.2f}"]
    for t in sorted(theses, key=lambda t: t.tier != "big"):
        lines.append(f"\n[{t.tier.upper()}] {t.ticker} {t.direction}  P={t.probability:.0%} move {t.expected_move_pct:.0f}%\n{t.driver}")
    return "\n".join(lines)
