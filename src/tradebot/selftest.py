"""One tiny call down every model and tool path the desk uses, so a broken key, model name or tool version shows up on
Telegram at startup instead of silently failing overnight. Costs a few cents. Also on demand: /selftest."""
from __future__ import annotations

import logging

from pydantic import BaseModel

from .config import get_settings
from .llm import claude

log = logging.getLogger(__name__)


class Ping(BaseModel):
    ok: bool
    word: str


def run() -> str:
    s = get_settings()
    try:
        c = claude()
    except Exception as e:
        return f"SELF-TEST\nFAIL no usable Anthropic key: {e}"
    checks = []

    def attempt(label: str, fn) -> None:
        try:
            fn()
            checks.append(f"OK   {label}")
        except Exception as e:  # report every failure, keep going
            log.exception("selftest %s", label)
            checks.append(f"FAIL {label}: {str(e)[:200]}")

    for label, model in (("triage (structured)", s.triage_model), ("explorer (structured)", s.explorer_model),
                         ("evaluator", s.evaluator_model), ("research model", s.research_model), ("judge", s.judge_model)):
        attempt(f"{label} {model}", lambda m=model: c.parse("selftest", m, "Reply in the schema.", "Say ok=true, word=ping.",
                                                             Ping, effort="low", max_tokens=1000))
    attempt(f"explorer tools {s.explorer_model}",
            lambda: c.agent("selftest", s.explorer_model, "Use news_search once, then answer in one line.",
                            "What is one business headline today?",
                            [{"name": "news_search", "description": "News search.",
                              "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}],
                            lambda name, inp: "- Example headline | https://example.com", effort="low", max_turns=3,
                            max_searches=1, max_fetches=0))
    attempt(f"web research {s.research_model}",
            lambda: c.research("selftest", s.research_model, "Answer in one line.",
                               "Search the web once: what is today's date in Sydney?", effort="low", max_searches=1,
                               max_continuations=2))
    return "SELF-TEST\n" + "\n".join(checks)
