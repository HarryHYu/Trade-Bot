from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/tradebot.db"
    timezone: str = "Australia/Sydney"

    # Claude: comma-separated keys, one per Console org. Rotated by month-to-date spend.
    anthropic_api_keys: str = ""
    monthly_credit_usd_per_key: float = 200.0
    # Hard ceiling on total API spend per calendar month, credits plus paid. Raise it when you want.
    monthly_spend_cap_usd: float = 600.0
    triage_model: str = "claude-haiku-5-5"
    research_model: str = "claude-opus-5-5"
    prediction_model: str = "claude-fable-5-1"
    research_effort: str = "high"
    max_research_per_day: int = 40

    # Broker (Alpaca). Paper by default; live requires ALPACA_PAPER=false AND TRADING_LIVE_CONFIRM=yes.
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    trading_live_confirm: str = ""

    # Approval: manual = every trade needs a tap; threshold = auto under auto_approve_max_usd; auto = no taps.
    approval_mode: str = "manual"
    auto_approve_max_usd: float = 0.0
    proposal_ttl_minutes: int = 240

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # your DM id, or a group id (topics are created automatically)
    telegram_allowed_user_ids: str = ""  # comma-separated; empty = anyone in the chat
    chat_model: str = "claude-opus-5-5"
    chat_effort: str = "medium"

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "tradebot/0.1 (research bot)"
    youtube_api_key: str = ""
    finnhub_api_key: str = ""
    sec_user_agent: str = "tradebot research admin@example.com"

    heartbeat_url: str = ""  # e.g. a healthchecks.io ping URL

    knowledge_dir: Path = Path("knowledge")
    config_dir: Path = Path("config")

    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.anthropic_api_keys.split(",") if k.strip()]

    @property
    def is_live(self) -> bool:
        return (not self.alpaca_paper) and self.trading_live_confirm.lower() == "yes"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml(name: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    path = s.config_dir / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}
