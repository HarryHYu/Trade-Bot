"""Claude client: rotates across Console orgs by month-to-date spend, logs every call's cost."""
from __future__ import annotations

import datetime as dt
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel
from sqlalchemy import func

from .config import get_settings
from .db import ApiUsage, select, session

T = TypeVar("T", bound=BaseModel)

# USD per million tokens: (input, output). Cache reads ~10% of input, writes 125%.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-fable-5-1": (10.0, 50.0),
}
WEB_SEARCH_USD = 0.01
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"}


class BudgetExceeded(RuntimeError):
    pass


def _month_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spend_by_key() -> dict[int, float]:
    with session() as s:
        rows = s.execute(
            select(ApiUsage.key_index, func.sum(ApiUsage.cost_usd))
            .where(ApiUsage.ts >= _month_start()).group_by(ApiUsage.key_index)
        ).all()
    return {k: float(v or 0) for k, v in rows}


def _cost(model: str, usage: Any, searches: int) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (usage.input_tokens * pin + cr * pin * 0.1 + cw * pin * 1.25
            + usage.output_tokens * pout) / 1e6 + searches * WEB_SEARCH_USD


class Claude:
    """Picks the key with most remaining free credit; once all credit is used, spends paid up to the cap."""

    def __init__(self) -> None:
        self.s = get_settings()
        keys = self.s.api_keys
        if not keys:
            raise RuntimeError("ANTHROPIC_API_KEYS is empty")
        self.clients = [anthropic.Anthropic(api_key=k, max_retries=3, timeout=900) for k in keys]
        self._skip: set[int] = set()

    def _pick(self) -> int:
        spend = spend_by_key()
        if sum(spend.values()) >= self.s.monthly_spend_cap_usd:
            raise BudgetExceeded(f"monthly cap ${self.s.monthly_spend_cap_usd} reached")
        live = [i for i in range(len(self.clients)) if i not in self._skip] or list(range(len(self.clients)))
        return min(live, key=lambda i: spend.get(i, 0.0))

    def _record(self, idx: int, model: str, purpose: str, resp: Any) -> None:
        searches = 0
        stu = getattr(resp.usage, "server_tool_use", None)
        if stu is not None:
            searches = getattr(stu, "web_search_requests", 0) or 0
        with session() as s:
            s.add(ApiUsage(
                key_index=idx, model=model, purpose=purpose,
                input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                web_searches=searches, cost_usd=_cost(model, resp.usage, searches),
            ))

    def _call(self, purpose: str, model: str, fn: Any) -> Any:
        for _ in range(len(self.clients) + 1):
            idx = self._pick()
            try:
                resp = fn(self.clients[idx])
            except (anthropic.RateLimitError, anthropic.PermissionDeniedError) as e:
                self._skip.add(idx)
                last = e
                continue
            except anthropic.BadRequestError as e:
                if "credit" in str(e).lower() and len(self._skip) < len(self.clients):
                    self._skip.add(idx)
                    last = e
                    continue
                raise
            self._record(idx, model, purpose, resp)
            if resp.stop_reason == "refusal":
                raise RuntimeError(f"{purpose}: model refused")
            return resp
        raise last  # noqa: F821

    def parse(self, purpose: str, model: str, system: str, user: str, schema: type[T],
              effort: str | None = None, max_tokens: int = 16000) -> T:
        """Single structured call. System prompt is cached so repeated triage is cheap."""
        kw: dict[str, Any] = dict(
            model=model, max_tokens=max_tokens, output_format=schema,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        if effort:
            kw["output_config"] = {"effort": effort}
        resp = self._call(purpose, model, lambda c: c.messages.parse(**kw))
        if resp.parsed_output is None:
            raise RuntimeError(f"{purpose}: no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def research(self, purpose: str, model: str, system: str, user: str,
                 effort: str = "high", max_searches: int = 15, max_continuations: int = 5) -> str:
        """Agentic research turn with server-side web search and fetch. Returns the final text."""
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches},
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        extra: dict[str, Any] = {}
        if model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

        def run(c: anthropic.Anthropic) -> Any:
            with c.beta.messages.stream(
                model=model, max_tokens=64000, tools=tools, messages=messages,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": effort}, **extra,
            ) as st:
                return st.get_final_message()

        for _ in range(max_continuations):
            resp = self._call(purpose, model, run)
            if resp.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
        return "\n".join(b.text for b in resp.content if b.type == "text").strip()


    def agent(self, purpose: str, model: str, system: str, user: str, tools: list[dict[str, Any]],
              run_tool: Any, effort: str = "high", max_turns: int = 30, max_searches: int = 12) -> str:
        """Agent loop: server web tools plus our own data tools. Returns the final text.
        run_tool(name, input) -> str executes client-side tools; errors go back as is_error results."""
        all_tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches},
            *tools,
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        extra: dict[str, Any] = {}
        if model in FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        resp = None
        for _ in range(max_turns):
            def call(c: anthropic.Anthropic) -> Any:
                with c.beta.messages.stream(model=model, max_tokens=32000, system=sys_blocks, tools=all_tools,
                                            messages=messages, output_config={"effort": effort}, **extra) as st:
                    return st.get_final_message()
            resp = self._call(purpose, model, call)
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":
                continue
            if resp.stop_reason != "tool_use":
                break
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    try:
                        out, err = run_tool(b.name, b.input), False
                    except Exception as e:  # tool failures go back to the model, never crash the run
                        out, err = f"Error: {e}", True
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        return "\n".join(b.text for b in (resp.content if resp else []) if b.type == "text").strip()


_claude: Claude | None = None


def claude() -> Claude:
    global _claude
    if _claude is None:
        _claude = Claude()
    return _claude
