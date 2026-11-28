"""Daily export for the review shift. The server makes the calls; evaluation, reflection and tuning happen in Claude Code
sessions on the Ultra plan, which read this bundle from the repo's `desk-feed` branch, then commit lessons and config
changes to main (the server pulls main every 10 minutes). One commit per export via the Git Data API.
Needs GITHUB_FEED_TOKEN (fine-grained, this repo only, Contents read/write)."""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func

from . import ledger
from .config import get_settings
from .db import ApiUsage, Decision, Lead, Mission, Proposal, Thesis, select, session, utcnow

log = logging.getLogger(__name__)
BRANCH = "desk-feed"


def _j(rows: Any) -> str:
    return json.dumps(rows, indent=1, default=str) + "\n"


def bundle(days: int = 30) -> dict[str, str | bytes]:
    """Everything a reviewer needs, as files: ledger, theses with their reasoning, trades, the pipeline's choices, spend."""
    since = utcnow() - dt.timedelta(days=days)
    files: dict[str, str | bytes] = {}
    ledger.merge_judgements()
    ledger.auto_resolve()
    files["REPORT.md"] = ledger.report()
    for name in ("trades", "predictions", "leads"):
        files[f"ledger/{name}.json"] = _j(ledger.load(name))
    png = Path(ledger._dir()) / "equity.png"
    if png.exists():
        files["equity.png"] = png.read_bytes()
    with session() as db:
        theses = db.scalars(select(Thesis).where(Thesis.opened_at >= since).order_by(Thesis.id)).all()
        files["theses.json"] = _j([dict(id=t.id, opened=t.opened_at, ticker=t.ticker, type=t.security_type, tier=t.tier,
                                        direction=t.direction, p=t.probability, move=t.expected_move_pct, horizon=t.horizon_days,
                                        status=t.status, driver=t.driver, mechanism=t.mechanism, checks=t.checks,
                                        catalysts=t.catalysts, kill=t.kill_criteria, summary=t.summary, outcome=t.outcome,
                                        failed_check=t.failed_check,
                                        diligence=(t.meta or {}).get("diligence"),
                                        cap_reasons=((t.meta or {}).get("diligence_run") or {}).get("cap_reasons"),
                                        note=((t.evidence or [{}])[0].get("note") or "")[:6000]) for t in theses])
        props = db.scalars(select(Proposal).where(Proposal.created_at >= since).order_by(Proposal.id)).all()
        files["trades_bot.json"] = _j([dict(id=p.id, thesis=p.thesis_id, role=p.role, symbol=p.symbol, side=p.side, qty=p.qty,
                                            state=p.state, ref=p.entry_ref_price, fill=p.fill_price, stop=p.stop_price,
                                            target=p.take_profit, exit=p.exit_price, exit_reason=p.exit_reason, pnl=p.pnl_usd,
                                            notional=p.notional, created=p.created_at, filled=p.filled_at, closed=p.closed_at,
                                            note=p.note) for p in props])
        evals = db.scalars(select(Decision).where(Decision.kind.in_(["evaluated", "lead_dismissed", "proposal_blocked",
                                                                      "thesis_opened", "earnings_predicted", "earnings_scored"]),
                                                  Decision.ts >= since).order_by(Decision.id)).all()
        files["decisions.json"] = _j([dict(ts=d.ts, kind=d.kind, ref=d.ref, payload=d.payload) for d in evals])
        missions = db.scalars(select(Mission).where(Mission.finished_at >= utcnow() - dt.timedelta(days=3))
                              .order_by(Mission.id)).all()
        files["missions_3d.json"] = _j([dict(id=m.id, kind=m.kind, angle=m.angle, status=m.status,
                                             interest=(m.dossier or {}).get("interest"), summary=(m.dossier or {}).get("summary"),
                                             affected=(m.dossier or {}).get("affected"), surprises=(m.dossier or {}).get("surprises"))
                                        for m in missions])
        funnel = {k: db.scalar(select(func.count()).select_from(Lead).where(Lead.created_at >= since, *w)) or 0
                  for k, w in (("haiku_flags", [Lead.signal_kind != "evaluated"]),
                               ("sonnet_shortlist", [Lead.signal_kind == "evaluated"]))}
        funnel["opus_theses"] = len(theses)
        funnel["trades"] = len([p for p in props if p.role == "main" and p.fill_price])
        spend = db.execute(select(func.date(ApiUsage.ts), ApiUsage.purpose, func.sum(ApiUsage.cost_usd), func.count())
                           .where(ApiUsage.ts >= since).group_by(func.date(ApiUsage.ts), ApiUsage.purpose)).all()
        files["pipeline.json"] = _j({"funnel_30d": funnel,
                                     "spend": [dict(day=d, purpose=p, usd=round(c or 0, 4), calls=n) for d, p, c, n in spend]})
    # lessons the server wrote into the knowledge base since the last export (situation updates from research)
    kdir = Path(get_settings().knowledge_dir)
    cutoff = (utcnow() - dt.timedelta(days=2)).timestamp()
    changed = {str(f.relative_to(kdir)): f.read_text()[-4000:] for f in kdir.rglob("*.md") if f.stat().st_mtime > cutoff}
    files["knowledge_changes.json"] = _j(changed)
    files["README.md"] = (f"# Desk feed (exported {utcnow():%Y-%m-%d %H:%M} UTC)\n\nRead REPORT.md first, then theses.json, "
                          "trades_bot.json, decisions.json, pipeline.json. Ledger raw files in ledger/. "
                          "Reviewer instructions: REVIEW.md on main.\n")
    return files


def push(files: dict[str, str | bytes], message: str) -> str:
    s = get_settings()
    if not s.github_feed_token:
        raise RuntimeError("GITHUB_FEED_TOKEN is not set: /setkey GITHUB_FEED_TOKEN <token>")
    api = f"https://api.github.com/repos/{s.github_feed_repo}"
    token = s.github_feed_token.strip().split()[0]
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(headers=h, timeout=60) as c:
        ref = c.get(f"{api}/git/ref/heads/{BRANCH}")
        parent = ref.json()["object"]["sha"] if ref.status_code == 200 else None
        base_tree = c.get(f"{api}/git/commits/{parent}").json()["tree"]["sha"] if parent else None
        tree = []
        for path, data in files.items():
            if isinstance(data, bytes):
                blob = c.post(f"{api}/git/blobs", json={"content": base64.b64encode(data).decode(), "encoding": "base64"})
                blob.raise_for_status()
                tree.append({"path": f"feed/{path}", "mode": "100644", "type": "blob", "sha": blob.json()["sha"]})
            else:
                tree.append({"path": f"feed/{path}", "mode": "100644", "type": "blob", "content": data})
        t = c.post(f"{api}/git/trees", json={"tree": tree} | ({"base_tree": base_tree} if base_tree else {}))
        t.raise_for_status()
        commit = c.post(f"{api}/git/commits", json={"message": message, "tree": t.json()["sha"],
                                                    "parents": [parent] if parent else []})
        commit.raise_for_status()
        sha = commit.json()["sha"]
        if parent:
            c.patch(f"{api}/git/refs/heads/{BRANCH}", json={"sha": sha}).raise_for_status()
        else:
            c.post(f"{api}/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": sha}).raise_for_status()
    return sha


def export() -> str:
    try:
        sha = push(bundle(), f"Desk export {utcnow():%Y-%m-%d %H:%M} UTC")
    except Exception as e:   # errors can carry request headers: never let the token reach a log or a chat
        from .telegram import redact
        raise RuntimeError(redact(f"{type(e).__name__}: {e}")) from None
    log.info("desk feed exported %s", sha)
    return sha
