"""Database models. Every observation carries when it was observed, so replays are point-in-time."""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import get_settings


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Item(Base):
    """One raw observation from any source."""
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_tier: Mapped[int] = mapped_column(Integer, default=4)  # 1 primary .. 4 general social
    external_id: Mapped[str] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(256))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    triaged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    triage: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    tickers: Mapped[list[Any]] = mapped_column(default=list)
    theme: Mapped[str] = mapped_column(Text, default="")
    signal_kind: Mapped[str] = mapped_column(String(32), default="")
    priority: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    urgency: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new|researching|thesis|dismissed|watch
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    notes: Mapped[str] = mapped_column(Text, default="")


class Thesis(Base):
    __tablename__ = "theses"
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id"))
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    security_type: Mapped[str] = mapped_column(String(64), default="general")
    driver: Mapped[str] = mapped_column(Text)
    mechanism: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(8))  # long|short
    probability: Mapped[float] = mapped_column(Float)
    expected_move_pct: Mapped[float] = mapped_column(Float)
    horizon_days: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(16))  # watch|small|standard|big
    checks: Mapped[list[Any]] = mapped_column(default=list)
    catalysts: Mapped[list[Any]] = mapped_column(default=list)
    kill_criteria: Mapped[list[Any]] = mapped_column(default=list)
    situation_snapshot: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|closed|killed|expired
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failed_check: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Proposal(Base):
    """A fully built order waiting on the approval gate. States:
    proposed -> approved | rejected | expired -> submitted -> filled | canceled -> closed"""
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int | None] = mapped_column(ForeignKey("theses.id"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"))  # hedge legs point at their main leg
    role: Mapped[str] = mapped_column(String(16), default="main")  # main|hedge
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))  # buy|sell
    qty: Mapped[float] = mapped_column(Float)
    entry_ref_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    risk_usd: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    fill_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    pnl_usd: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(32))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    filled_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    hold_until: Mapped[dt.datetime | None] = mapped_column(DateTime)  # time stop
    peak_price: Mapped[float | None] = mapped_column(Float)           # best price since entry
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("ticker", "event", "event_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    event: Mapped[str] = mapped_column(String(32))  # earnings|...
    event_date: Mapped[dt.date] = mapped_column()
    stage: Mapped[str] = mapped_column(String(16), default="opened")  # opened|predicted|frozen|scored
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    consensus: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    score: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    frozen_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    scored_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class Decision(Base):
    """Append-only log of every decision, with its inputs and reasoning."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    ref: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    key_index: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    web_searches: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class KV(Base):
    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Source(Base):
    """Anything the eyes watch. Added from config, by research, or by the owner via Telegram."""
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))   # rss|news_query|subreddit|youtube_channel|web_page|sec_ticker|steam_app|roblox_game
    key: Mapped[str] = mapped_column(String(512))   # url, query, subreddit, channel id, ticker, app id
    name: Mapped[str] = mapped_column(String(256), default="")
    tier: Mapped[int] = mapped_column(Integer, default=3)
    ticker: Mapped[str | None] = mapped_column(String(16))  # who earns from it, if any
    reason: Mapped[str] = mapped_column(Text, default="")
    added_by: Mapped[str] = mapped_column(String(32), default="config")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)  # e.g. last content hash for web pages
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ChatTurn(Base):
    """Telegram chat history (text only; media is described, not stored)."""
    __tablename__ = "chat_turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    thread: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)


_engine = None
_Session: sessionmaker[Session] | None = None


def init_db(url: str | None = None) -> None:
    global _engine, _Session
    url = url or get_settings().database_url
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, pool_pre_ping=True)
    from . import simbroker  # noqa: F401  registers the paper-broker tables
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(_engine, expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        init_db()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def log_decision(s: Session, kind: str, ref: str | int = "", **payload: Any) -> None:
    s.add(Decision(kind=kind, ref=str(ref), payload=payload))


def kv_get(s: Session, key: str, default: str = "") -> str:
    row = s.get(KV, key)
    return row.value if row else default


def kv_set(s: Session, key: str, value: str) -> None:
    row = s.get(KV, key)
    if row:
        row.value = value
    else:
        s.add(KV(key=key, value=value))


def is_halted(s: Session) -> bool:
    return kv_get(s, "halted") == "1"


__all__ = [n for n in dir() if not n.startswith("_")] + ["select"]
