"""Telegram: one bot. In a group with Topics enabled it creates Proposals, Alerts, Brief, Research and
Chat topics and posts each kind of message in its own thread. In a DM everything lands in the DM.
Any non-command message is a conversation with Claude, including photos and files."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .db import Proposal, Thesis, kv_get, kv_set, log_decision, select, session

log = logging.getLogger(__name__)
TOPICS = ["Proposals", "Alerts", "Brief", "Research", "Chat"]
_pool = ThreadPoolExecutor(max_workers=3)


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{get_settings().telegram_bot_token}/{method}"


def _api(method: str, files: dict[str, Any] | None = None, **body: Any) -> Any:
    if not get_settings().telegram_bot_token:
        return None
    if files:
        r = httpx.post(_url(method), data={k: str(v) for k, v in body.items()}, files=files, timeout=120)
    else:
        r = httpx.post(_url(method), json=body, timeout=60)
    j = r.json()
    if not j.get("ok"):
        log.warning("telegram %s failed: %s", method, j)
    return j


def topic_id(name: str) -> int | None:
    with session() as db:
        v = kv_get(db, f"tg_topic:{name}")
    return int(v) if v else None


def setup_topics() -> None:
    """Create the topics once if the chat is a forum group. Safe to call every start."""
    s = get_settings()
    info = _api("getChat", chat_id=s.telegram_chat_id)
    if not info or not info.get("result", {}).get("is_forum"):
        return
    for name in TOPICS:
        if topic_id(name) is None:
            r = _api("createForumTopic", chat_id=s.telegram_chat_id, name=name)
            if r and r.get("ok"):
                with session() as db:
                    kv_set(db, f"tg_topic:{name}", str(r["result"]["message_thread_id"]))


def _target(topic: str | None, thread_id: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {"chat_id": get_settings().telegram_chat_id}
    tid = thread_id if thread_id is not None else (topic_id(topic) if topic else None)
    if tid:
        body["message_thread_id"] = tid
    return body


def send(text: str, buttons: list[list[dict[str, str]]] | None = None, topic: str | None = "Alerts",
         thread_id: int | None = None) -> None:
    for i in range(0, max(len(text), 1), 4000):
        body = _target(topic, thread_id) | {"text": text[i:i + 4000], "disable_web_page_preview": True}
        if buttons and i + 4000 >= len(text):
            body["reply_markup"] = {"inline_keyboard": buttons}
        _api("sendMessage", **body)


def send_file(path: Path, caption: str = "", topic: str | None = "Chat", thread_id: int | None = None) -> None:
    is_img = path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    method, field = ("sendPhoto", "photo") if is_img else ("sendDocument", "document")
    with path.open("rb") as f:
        _api(method, files={field: (path.name, f)}, caption=caption[:1000], **_target(topic, thread_id))


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
                 f"\nChecks: {checks}\n\n{th.summary[:1200]}\n\nAsk about it in Chat.",
                 [[{"text": "Approve", "callback_data": f"a:{p.id}"}, {"text": "Reject", "callback_data": f"r:{p.id}"}]],
                 topic="Proposals")
            p.note = "announced"


HELP = ("Just talk to me, or send photos and files.\n"
        "/status  /positions  /halt  /flatten  /resume  /mode manual|threshold|auto  /new (fresh chat)")


def _command(text: str, thread: str) -> str:
    from .broker import Alpaca
    from .execution import halt
    cmd, *args = text.strip().split()
    cmd = cmd.split("@")[0]
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
            log_decision(db, "mode", args[0])
        return f"Approval mode is now {args[0]}."
    if cmd == "/new":
        from .db import ChatTurn
        with session() as db:
            db.add(ChatTurn(thread=thread, role="assistant", text="--- new conversation ---"))
            kv_set(db, f"chat_reset:{thread}", str(int(time.time())))
        return "Fresh start."
    if cmd == "/status":
        a = Alpaca().account()
        with session() as db:
            halted = kv_get(db, "halted") == "1"
            mode = kv_get(db, "approval_mode", get_settings().approval_mode)
            open_th = len(db.scalars(select(Thesis.id).where(Thesis.status == "open")).all())
        day = float(a["equity"]) - float(a["last_equity"])
        return (f"Equity ${float(a['equity']):,.0f}  day {day:+,.0f}\nOpen theses {open_th}  mode {mode}  "
                f"{'HALTED' if halted else 'running'}  paper={not get_settings().is_live}")
    if cmd == "/positions":
        ps = Alpaca().positions()
        return "\n".join(f"{p['symbol']} {p['qty']} pnl {float(p['unrealized_pl']):+,.0f}" for p in ps) or "Flat."
    return HELP


def _download(file_id: str) -> tuple[bytes, str]:
    r = _api("getFile", file_id=file_id)
    path = r["result"]["file_path"]
    data = httpx.get(f"https://api.telegram.org/file/bot{get_settings().telegram_bot_token}/{path}", timeout=120).content
    return data, path.rsplit("/", 1)[-1]


def _chat(msg: dict[str, Any]) -> None:
    from .chat import media_block, reply
    tid = msg.get("message_thread_id")
    thread = str(tid or "dm")
    try:
        _api("sendChatAction", chat_id=msg["chat"]["id"], action="typing", **({"message_thread_id": tid} if tid else {}))
        media = []
        if msg.get("photo"):
            data, name = _download(msg["photo"][-1]["file_id"])
            media.append(media_block(data, name, "image/jpeg"))
        if msg.get("document"):
            d = msg["document"]
            if d.get("file_size", 0) > 20_000_000:
                send("That file is over Telegram's 20MB bot limit.", topic=None, thread_id=tid)
                return
            data, name = _download(d["file_id"])
            blk = media_block(data, d.get("file_name", name), d.get("mime_type"))
            if blk is None:
                send(f"I can't read {d.get('file_name')} (binary format). Send PDF, image or text.", topic=None, thread_id=tid)
                return
            media.append(blk)
        text, ctx = reply(thread, msg.get("text") or msg.get("caption") or "", [m for m in media if m])
        send(text, buttons=ctx.buttons or None, topic=None, thread_id=tid)
        for path, cap in ctx.files:
            send_file(path, cap, topic=None, thread_id=tid)
    except Exception as e:
        log.exception("chat failed")
        send(f"Chat error: {e}", topic=None, thread_id=tid)


def _allowed(user_id: int) -> bool:
    ids = [x.strip() for x in get_settings().telegram_allowed_user_ids.split(",") if x.strip()]
    return not ids or str(user_id) in ids


def handle(u: dict[str, Any]) -> None:
    from .chat import apply_change
    from .execution import decide
    s = get_settings()
    if "callback_query" in u:
        cq = u["callback_query"]
        if str(cq["message"]["chat"]["id"]) != s.telegram_chat_id or not _allowed(cq["from"]["id"]):
            return
        act, ref = cq["data"].split(":", 1)
        if act in ("a", "r"):
            out = decide(int(ref), act == "a", by=f"telegram:{cq['from']['id']}")
        elif act == "c":
            out = apply_change(ref, by=f"telegram:{cq['from']['id']}")
        else:
            with session() as db:
                kv_set(db, f"cfg:{ref}", "")
            out = "Cancelled."
        _api("answerCallbackQuery", callback_query_id=cq["id"], text=out[:190])
        send(out, topic=None, thread_id=cq["message"].get("message_thread_id"))
        return
    msg = u.get("message")
    if not msg or str(msg["chat"]["id"]) != s.telegram_chat_id or not _allowed(msg.get("from", {}).get("id", 0)):
        return
    text = msg.get("text") or ""
    tid = msg.get("message_thread_id")
    if text.startswith("/"):
        send(_command(text, str(tid or "dm")), topic=None, thread_id=tid)
    else:
        _pool.submit(_chat, msg)  # chat runs in the background so buttons stay instant


def listen_forever() -> None:
    """Long-polls Telegram continuously in its own thread."""
    if not get_settings().telegram_bot_token:
        return
    try:
        setup_topics()
    except Exception:
        log.exception("topic setup failed")
    with session() as db:
        offset = int(kv_get(db, "tg_offset", "0"))
    while True:
        try:
            r = httpx.post(_url("getUpdates"), json={"offset": offset, "timeout": 50}, timeout=65).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                with session() as db:
                    kv_set(db, "tg_offset", str(offset))
                try:
                    handle(u)
                except Exception:
                    log.exception("update failed")
        except Exception:
            log.exception("telegram poll failed")
            time.sleep(5)


def start_listener() -> threading.Thread:
    t = threading.Thread(target=listen_forever, name="telegram", daemon=True)
    t.start()
    return t
