"""Telegram chat with Claude. It can read the bot's database and knowledge, check the account,
research, make charts and send files. Changes to rules come back as a proposal you confirm."""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text as sqltext

from .config import get_settings, load_yaml
from .db import ChatTurn, kv_set, log_decision, select, session
from .llm import claude

log = logging.getLogger(__name__)
OUT_DIR = Path("data/outbox")

SYSTEM = """You are the trading desk's partner, chatting with the owner on Telegram. Talk like a sharp,
direct colleague: short messages, plain text (Telegram does not render markdown tables), no fluff.

You run inside the desk's own system and have tools for it: the database (items, leads, theses,
proposals, predictions, decisions, api_usage), the knowledge base (security profiles, situations,
lessons, type briefings), the broker account, web search, charts and files. Use them instead of guessing,
and say when something is not in the data. The owner may send photos, screenshots, PDFs and files:
read them properly.

You can start or stop watching any source (add_source/list_sources/remove_source) when the owner asks, or when the
conversation shows something worth tracking. If a source needs an account or API key, tell the owner exactly where
to sign up and which .env variable the key goes in.

You never place trades or change rules directly. To change any setting, risk limit or strategy rule, call
propose_change; the owner confirms with a button. API keys are never handled in chat: tell the owner to send
/setkey NAME value (the bot deletes that message). Paper vs live is never changed from chat."""

TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 10},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
    {"name": "sql_query", "description": "Read-only SQL (SQLite) over the desk database. Tables: items, leads, theses, "
     "proposals, predictions, decisions, api_usage, chat_turns. Max 100 rows returned.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "list_knowledge", "description": "List files in the knowledge base.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_knowledge", "description": "Read a knowledge base file by its relative path.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "account", "description": "Broker account equity, day P&L and open positions.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "price_chart", "description": "Make a daily price chart PNG for a US ticker and send it to the owner.",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}, "days": {"type": "integer"}},
                      "required": ["symbol"]}},
    {"name": "send_file", "description": "Send a file to the owner: a knowledge path, or new content you write "
     "(e.g. a CSV or notes). Give either path, or filename plus content.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "filename": {"type": "string"},
                                                       "content": {"type": "string"}, "caption": {"type": "string"}}}},
    {"name": "add_source", "description": "Start watching something: kind is one of rss, news_query, subreddit, "
     "youtube_channel, web_page, sec_ticker, steam_app, roblox_game. key is the URL, query, subreddit name, channel id, "
     "ticker or app id.",
     "input_schema": {"type": "object", "properties": {"kind": {"type": "string"}, "key": {"type": "string"},
                                                       "name": {"type": "string"}, "ticker": {"type": "string"},
                                                       "reason": {"type": "string"}}, "required": ["kind", "key"]}},
    {"name": "list_sources", "description": "List everything currently being watched, with ids.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "remove_source", "description": "Stop watching a source by id.",
     "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "propose_change", "description": "Propose changing any bot setting. target is 'risk' (config/risk.yaml), "
     "'strategy' (config/strategy.yaml) or 'setting' (spend cap, diligence per day, models, efforts, approval mode, etc.; "
     "see show_settings). The owner confirms by button. API keys are never set here: tell the owner to use /setkey.",
     "input_schema": {"type": "object", "properties": {"target": {"type": "string", "enum": ["risk", "strategy", "setting"]},
                                                       "key": {"type": "string"}, "value": {}, "reason": {"type": "string"}},
                      "required": ["target", "key", "value", "reason"]}},
    {"name": "show_settings", "description": "Current settings, risk limits and strategy rules, and which API keys are set.",
     "input_schema": {"type": "object", "properties": {}}},
]


class Ctx:
    """Collects files and buttons the tools want sent alongside the reply."""
    def __init__(self) -> None:
        self.files: list[tuple[Path, str]] = []
        self.buttons: list[list[dict[str, str]]] = []


def _kroot() -> Path:
    return get_settings().knowledge_dir.resolve()


def _safe_path(rel: str) -> Path:
    p = (_kroot() / rel).resolve()
    if not p.is_relative_to(_kroot()):
        raise ValueError("path outside knowledge base")
    return p


def run_tool(name: str, inp: dict[str, Any], ctx: Ctx) -> str:
    if name == "sql_query":
        q = inp["sql"].strip().rstrip(";")
        if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
            return "Only a single SELECT is allowed."
        from .db import _engine, init_db
        if _engine is None:
            init_db()
        from . import db as dbmod
        with dbmod._engine.connect() as c:
            if c.dialect.name == "sqlite":
                c.execute(sqltext("PRAGMA query_only = ON"))
            rows = c.execute(sqltext(q)).mappings().fetchmany(100)
            if c.dialect.name == "sqlite":
                c.execute(sqltext("PRAGMA query_only = OFF"))
        return json.dumps([dict(r) for r in rows], default=str)[:30000]
    if name == "list_knowledge":
        return "\n".join(str(p.relative_to(_kroot())) for p in sorted(_kroot().rglob("*")) if p.is_file())[:20000]
    if name == "read_knowledge":
        return _safe_path(inp["path"]).read_text()[:40000]
    if name == "account":
        from .broker import Alpaca
        b = Alpaca()
        a = b.account()
        return json.dumps({"equity": a["equity"], "last_equity": a["last_equity"], "cash": a["cash"],
                           "positions": [{k: p[k] for k in ("symbol", "qty", "avg_entry_price", "market_value", "unrealized_pl")}
                                         for p in b.positions()]})
    if name == "price_chart":
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from .broker import Alpaca
        sym = inp["symbol"].upper()
        bars = Alpaca().daily_bars(sym, int(inp.get("days") or 120))
        if not bars:
            return f"No bars for {sym}."
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
        ax.plot([b["t"][:10] for b in bars], [b["c"] for b in bars], color="#2b6cb0")
        ax.set_title(f"{sym} daily close")
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(6))
        ax.grid(alpha=0.3)
        fig.tight_layout()
        path = OUT_DIR / f"{sym}-{uuid.uuid4().hex[:6]}.png"
        fig.savefig(path)
        plt.close(fig)
        ctx.files.append((path, f"{sym}, last {len(bars)} days"))
        return f"Chart made and will be sent. Last close {bars[-1]['c']}, first {bars[0]['c']}."
    if name == "send_file":
        if inp.get("path"):
            p = _safe_path(inp["path"])
        else:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            fname = re.sub(r"[^\w.\-]", "_", inp.get("filename") or "notes.txt")
            p = OUT_DIR / fname
            p.write_text(inp.get("content", ""))
        ctx.files.append((p, inp.get("caption", "")))
        return f"{p.name} will be sent."
    if name == "add_source":
        from . import sources as reg
        return reg.add(inp["kind"], inp["key"], inp.get("name", ""), ticker=inp.get("ticker"),
                       reason=inp.get("reason", ""), added_by="owner")
    if name == "list_sources":
        from . import sources as reg
        return "\n".join(f"#{s.id} {s.kind}: {s.name or s.key} ({s.added_by})" for s in reg.list_active())[:20000] or "None"
    if name == "remove_source":
        from . import sources as reg
        return reg.remove(int(inp["id"]))
    if name == "show_settings":
        from .config import EDITABLE_SETTINGS, SECRET_SETTINGS
        s = get_settings()
        return json.dumps({"settings": {n: getattr(s, n) for n in sorted(EDITABLE_SETTINGS)},
                           "keys_set": {n: bool(getattr(s, n)) for n in sorted(SECRET_SETTINGS)},
                           "risk": load_yaml("risk.yaml"), "strategy": load_yaml("strategy.yaml")}, default=str)
    if name == "propose_change":
        from .config import EDITABLE_SETTINGS
        target = inp.get("target", "risk")
        if target == "setting":
            if inp["key"].lower() not in EDITABLE_SETTINGS:
                return f"Not changeable here. Settings: {', '.join(sorted(EDITABLE_SETTINGS))}. Keys use /setkey."
            old = getattr(get_settings(), inp["key"].lower())
        else:
            cfg = load_yaml(f"{target}.yaml")
            if inp["key"] not in cfg:
                return f"Unknown key. Valid {target} keys: {', '.join(cfg)}"
            old = cfg[inp["key"]]
        cid = uuid.uuid4().hex[:8]
        with session() as db:
            kv_set(db, f"cfg:{cid}", json.dumps(inp | {"target": target}))
        ctx.buttons = [[{"text": f"Set {inp['key']} = {inp['value']}", "callback_data": f"c:{cid}"},
                        {"text": "Cancel", "callback_data": f"x:{cid}"}]]
        return f"Proposal shown to the owner: {target}.{inp['key']} {old} -> {inp['value']}."
    return f"Unknown tool {name}"


def apply_change(cid: str, by: str) -> str:
    import yaml
    from .db import kv_get
    from .knowledge import commit
    with session() as db:
        raw = kv_get(db, f"cfg:{cid}")
        if not raw:
            return "That change expired or was already handled."
        ch = json.loads(raw)
        kv_set(db, f"cfg:{cid}", "")
        target = ch.get("target", "risk")
        if target == "setting":
            from .config import set_override
            old = getattr(get_settings(), ch["key"].lower())
            try:
                new = set_override(ch["key"], ch["value"])
            except Exception as e:
                return f"Could not apply: {e}"
        else:
            path = get_settings().config_dir / f"{target}.yaml"
            data = yaml.safe_load(path.read_text())
            old, new = data.get(ch["key"]), ch["value"]
            data[ch["key"]] = new
            path.write_text(yaml.safe_dump(data, sort_keys=False))
        log_decision(db, "config_change", ch["key"], target=target, old=old, new=new, reason=ch["reason"], by=by)
    if target != "setting":
        commit(f"{target}.yaml: {ch['key']} {old} -> {new} ({ch['reason']})")
    return f"Done: {ch['key']} {old} -> {new}"


def media_block(data: bytes, filename: str, mime: str | None) -> dict[str, Any] | None:
    b64 = base64.standard_b64encode(data).decode()
    mime = mime or ""
    if mime.startswith("image/") or filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        mt = mime if mime.startswith("image/") else "image/jpeg"
        return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
    if mime == "application/pdf" or filename.lower().endswith(".pdf"):
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
    try:
        return {"type": "text", "text": f"FILE {filename}:\n{data.decode('utf-8')[:200000]}"}
    except UnicodeDecodeError:
        return None


def _history(thread: str, n: int = 30) -> list[dict[str, Any]]:
    with session() as db:
        rows = db.scalars(select(ChatTurn).where(ChatTurn.thread == thread)
                          .order_by(ChatTurn.id.desc()).limit(n)).all()[::-1]
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].text == "--- new conversation ---":
            rows = rows[i + 1:]
            break
    msgs: list[dict[str, Any]] = []
    for r in rows:  # merge consecutive same-role turns; history must start with user
        if msgs and msgs[-1]["role"] == r.role:
            msgs[-1]["content"] += "\n" + r.text
        elif msgs or r.role == "user":
            msgs.append({"role": r.role, "content": r.text})
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()
    # Cap history by size too (~40k chars, about 10k tokens): drop the oldest exchanges first.
    while sum(len(m["content"]) for m in msgs) > 40000 and len(msgs) > 2:
        msgs = msgs[2:]
    return msgs


def reply(thread: str, user_text: str, media: list[dict[str, Any]]) -> tuple[str, Ctx]:
    s = get_settings()
    ctx = Ctx()
    content: list[dict[str, Any]] = [*media, {"type": "text", "text": user_text or "(see attached)"}]
    messages = _history(thread) + [{"role": "user", "content": content}]
    note = user_text + (f" [sent {len(media)} attachment(s)]" if media else "")
    with session() as db:
        db.add(ChatTurn(thread=thread, role="user", text=note or "(attachment)"))
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    resp = None
    for _ in range(15):
        def call(c: Any) -> Any:
            return c.beta.messages.create(
                model=s.chat_model, max_tokens=16000, system=system, tools=TOOLS, messages=messages,
                output_config={"effort": s.chat_effort}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        resp = claude()._call("chat", s.chat_model, call)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                try:
                    out, err = run_tool(b.name, b.input, ctx), False
                except Exception as e:
                    out, err = f"Error: {e}", True
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
        messages.append({"role": "user", "content": results})
    text = "\n".join(b.text for b in (resp.content if resp else []) if b.type == "text").strip() or "(no reply)"
    with session() as db:
        db.add(ChatTurn(thread=thread, role="assistant", text=text))
    return text, ctx
