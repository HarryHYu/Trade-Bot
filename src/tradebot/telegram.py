"""Telegram: proposals arrive with Approve/Reject buttons; commands for status, halt, resume, mode.
Only messages from TELEGRAM_CHAT_ID are obeyed."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import get_settings
from .db import KV, Proposal, Thesis, kv_get, kv_set, log_decision, select, session

log = logging.getLogger(__name__)


def _api(method: str, **body: Any) -> Any:
    s = get_settings()
    if not s.telegram_bot_token:
        return None
    r = httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/{method}", json=body, timeout=40)
    return r.json()


def send(text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
    s = get_settings()
    body: dict[str, Any] = {"chat_id": s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": buttons}
    _api("sendMessage", **body)


def announce_new_proposals() -> None:
    with session() as db:
        rows = db.scalars(select(Proposal).where(Proposal.state == "proposed", Proposal.role == "main",
                                                 Proposal.note == "")).all()
        for p in rows:
            th = db.get(Thesis, p.thesis_id)
            hedges = db.scalars(select(Proposal).where(Proposal.parent_id == p.id)).all()
            hedge_txt = "".join(f"\nHedge: {h.side} {h.qty:g} {h.symbol}" for h in hedges)
            checks = " ".join(f"{c['name']}:{c['verdict']}" for c in th.checks)
            send(f"PROPOSAL #{p.id} [{th.tier.upper()}]\n{p.side.upper()} {p.qty:g} {p.symbol} @ ~{p.entry_ref_price:.2f}"
                 f"\nStop {p.stop_price:.2f}  Target {p.take_profit:.2f}  Risk ${p.risk_usd:.0f}{hedge_txt}"
                 f"\n\nDriver: {th.driver}\nP={th.probability:.0%} move={th.expected_move_pct:.0f}% {th.horizon_days}d"
                 f"\nChecks: {checks}\n\n{th.summary[:1200]}",
                 [[{"text": "Approve", "callback_data": f"a:{p.id}"}, {"text": "Reject", "callback_data": f"r:{p.id}"}]])
            p.note = "announced"


HELP = "/status  /positions  /pnl  /halt  /flatten  /resume  /mode manual|threshold|auto"


def _handle_command(text: str) -> str:
    from .broker import Alpaca
    from .execution import halt
    cmd, *args = text.strip().split()
    if cmd == "/halt":
        halt(False)
        return "Halted. No new orders."
    if cmd == "/flatten":
        halt(True)
        return "Halted and flattening all positions."
    if cmd == "/resume":
        with session() as db:
            kv_set(db, "halted", "0")
            log_decision(db, "resume", "")
        return "Resumed."
    if cmd == "/mode" and args and args[0] in ("manual", "threshold", "auto"):
        with session() as db:
            kv_set(db, "approval_mode", args[0])
        return f"Approval mode set to {args[0]} (applies from next proposal)."
    if cmd in ("/status", "/pnl"):
        a = Alpaca().account()
        with session() as db:
            halted = kv_get(db, "halted") == "1"
            open_th = len(db.scalars(select(Thesis.id).where(Thesis.status == "open")).all())
        day = float(a["equity"]) - float(a["last_equity"])
        return (f"Equity ${float(a['equity']):,.0f}  day {day:+,.0f}\nOpen theses {open_th}  "
                f"{'HALTED' if halted else 'running'}  paper={not get_settings().is_live}")
    if cmd == "/positions":
        ps = Alpaca().positions()
        return "\n".join(f"{p['symbol']} {p['qty']} pnl {float(p['unrealized_pl']):+,.0f}" for p in ps) or "Flat."
    return HELP


def poll_once() -> None:
    from .execution import decide
    s = get_settings()
    with session() as db:
        offset = int(kv_get(db, "tg_offset", "0"))
    r = _api("getUpdates", offset=offset, timeout=25)
    if not r or not r.get("ok"):
        return
    for u in r["result"]:
        offset = u["update_id"] + 1
        try:
            if "callback_query" in u:
                cq = u["callback_query"]
                if str(cq["message"]["chat"]["id"]) != s.telegram_chat_id:
                    continue
                act, pid = cq["data"].split(":")
                msg = decide(int(pid), act == "a", by="telegram")
                _api("answerCallbackQuery", callback_query_id=cq["id"], text=msg)
                send(msg)
            elif "message" in u and str(u["message"]["chat"]["id"]) == s.telegram_chat_id:
                send(_handle_command(u["message"].get("text", "")))
        except Exception as e:
            log.exception("telegram update failed")
            send(f"Error: {e}")
    with session() as db:
        kv_set(db, "tg_offset", str(offset))
