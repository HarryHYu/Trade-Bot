"""Imports research written by scheduled Claude Code sessions (on the Max plan) into the desk.
Heavy thinking happens there; here we only validate, apply hard caps, and hand the thesis to the normal flow."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from .brain import ThesisOut, tier_for
from .db import Decision, Thesis, log_decision, select, session
from .knowledge import append_lesson, profile_context

log = logging.getLogger(__name__)
INBOX = Path("inbox")
ORDER = ["watch", "small", "standard", "big"]


def _imported() -> set[str]:
    with session() as db:
        return {d.ref for d in db.scalars(select(Decision).where(Decision.kind == "inbox_imported"))}


def import_inbox() -> list[int]:
    if not INBOX.exists():
        return []
    done = _imported()
    made = []
    for f in sorted(INBOX.glob("*.json")):
        if f.name in done:
            continue
        try:
            raw = json.loads(f.read_text())
            out = ThesisOut(has_thesis=True, ticker=raw["ticker"], security_type=raw.get("security_type", "_default"),
                            driver=raw["driver"], mechanism=raw["mechanism"], direction=raw["direction"],
                            probability=raw["probability"], expected_move_pct=raw["expected_move_pct"],
                            horizon_days=raw["horizon_days"], checks=raw.get("checks", []),
                            catalysts=raw.get("catalysts", []), kill_criteria=raw.get("kill_criteria", []),
                            hedge_idea=raw.get("hedge_idea", ""), monitor=raw.get("monitor", []),
                            diligence=raw["diligence"], situation_updates=raw.get("situation_updates", []),
                            summary=raw.get("summary", ""))
        except (KeyError, ValueError, ValidationError) as e:
            log.warning("inbox file %s invalid: %s", f.name, e)
            with session() as db:
                log_decision(db, "inbox_imported", f.name, error=str(e)[:500])
            from .telegram import send
            send(f"Inbox file {f.name} was invalid and skipped: {str(e)[:300]}", topic="Alerts")
            continue
        # Hard caps still apply: the session's own tier can only be lowered, never raised.
        tier = min(raw.get("tier", "small"), tier_for(out), key=ORDER.index) if tier_for(out) != "none" else "watch"
        with session() as db:
            th = Thesis(ticker=out.ticker.upper(), security_type=out.security_type, driver=out.driver,
                        mechanism=out.mechanism, direction=out.direction, probability=out.probability,
                        expected_move_pct=out.expected_move_pct, horizon_days=out.horizon_days, tier=tier,
                        checks=[c.model_dump() for c in out.checks], catalysts=out.catalysts,
                        kill_criteria=out.kill_criteria, summary=out.summary,
                        situation_snapshot=profile_context(out.ticker),
                        evidence=[{"inbox_file": f.name, "sources": raw.get("sources", [])}],
                        meta={"origin": "claude_code_session", "diligence": out.diligence.model_dump(),
                              "full_diligence_done": True, "fast": False})
            db.add(th)
            db.flush()
            log_decision(db, "inbox_imported", f.name, thesis=th.id, tier=tier)
            log_decision(db, "thesis_opened", th.id, tier=tier, ticker=th.ticker, summary=out.summary, origin="inbox")
            tid = th.id
        from . import sources as reg
        for m in out.monitor[:8]:
            reg.add("web_page" if m.startswith("http") else "news_query", m, ticker=out.ticker,
                    reason=f"inbox thesis {tid}", added_by="research_session")
        for u in out.situation_updates:
            append_lesson(out.ticker, out.security_type, "situation", u)
        made.append(tid)
    return made
