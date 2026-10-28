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
    anthropic_workspace_id: str = ""   # one workspace id for all keys (organisation-scoped keys need one)
    anthropic_workspace_ids: str = ""  # or one per key, comma-separated, same order as ANTHROPIC_API_KEYS
    monthly_credit_usd_per_key: float = 200.0
    # Hard ceiling on total API spend per calendar month, credits plus paid. Raise it when you want.
    monthly_spend_cap_usd: float = 600.0
    triage_model: str = "claude-haiku-5-5"
    research_model: str = "claude-opus-5-5"
    prediction_model: str = "claude-fable-5-1"
    research_effort: str = "high"
    max_research_per_day: int = 40
    # Multi-agent diligence on any thesis that clears the first pass
    diligence_model: str = "claude-opus-5-5"
    verifier_model: str = "claude-opus-5-5"
    judge_model: str = "claude-fable-5-1"
    diligence_effort: str = "high"
    max_diligence_per_day: int = 2  # ~$10 per full run (8 agents + verifier + judge); 2/day is ~$600/month

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

    def workspace_for(self, index: int) -> str:
        ids = [w.strip() for w in self.anthropic_workspace_ids.split(",") if w.strip()]
        return ids[index] if index < len(ids) else self.anthropic_workspace_id

    @property
    def is_live(self) -> bool:
        return (not self.alpaca_paper) and self.trading_live_confirm.lower() == "yes"


# Settings the owner can change from Telegram. Secrets are set with /setkey (message deleted, never sent to Claude).
SECRET_SETTINGS = {"anthropic_api_keys", "alpaca_key_id", "alpaca_secret_key", "reddit_client_id",
                   "reddit_client_secret", "youtube_api_key", "finnhub_api_key", "heartbeat_url"}
EDITABLE_SETTINGS = {"monthly_spend_cap_usd", "monthly_credit_usd_per_key", "max_diligence_per_day",
                     "max_research_per_day", "approval_mode", "auto_approve_max_usd", "proposal_ttl_minutes",
                     "research_effort", "diligence_effort", "chat_effort", "triage_model", "research_model",
                     "prediction_model", "diligence_model", "verifier_model", "judge_model", "chat_model",
                     "sec_user_agent", "reddit_user_agent", "anthropic_workspace_id",
                     "anthropic_workspace_ids"}
# Never changeable from chat: telegram_* (would cut the connection), alpaca_paper/trading_live_confirm (live trading).
OVERRIDES_FILE = Path("data/settings_overrides.json")


def _overrides() -> dict[str, Any]:
    import json
    try:
        return json.loads(OVERRIDES_FILE.read_text())
    except Exception:
        return {}


@lru_cache
def get_settings() -> Settings:
    """.env values, with anything the owner changed from Telegram layered on top."""
    return Settings(**_overrides())


def set_override(name: str, value: Any) -> Any:
    """Validate and persist one setting, then reload everything that depends on it. Returns the parsed value."""
    import json
    import os
    name = name.lower()
    if name not in SECRET_SETTINGS | EDITABLE_SETTINGS:
        raise ValueError(f"{name} cannot be changed from Telegram")
    data = _overrides()
    data[name] = value
    parsed = getattr(Settings(**data), name)  # raises if the value is invalid
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(json.dumps(data, indent=1))
    os.chmod(OVERRIDES_FILE, 0o600)
    get_settings.cache_clear()
    try:
        from . import llm
        llm._claude = None  # rebuild API clients with new keys or models
    except Exception:
        pass
    return parsed


def load_yaml(name: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    path = s.config_dir / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}
