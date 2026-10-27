# Trade-Bot

Research-driven trading bot. It reads niche sources constantly, finds one clear real-world driver
before it is priced in, interrogates it, sizes and hedges it, asks your phone for approval, trades,
and learns from every outcome.

```
collectors (every 10 min) -> Haiku triage (every 5 min) -> leads
leads -> Opus research with web search (every 30 min) -> thesis with 6 checks -> conviction tier
thesis -> risk engine sizing + hard limits + SPY hedge -> proposal -> Telegram Approve/Reject
approved -> Alpaca bracket order (stop + target) -> fills tracked -> post-mortem -> lessons in knowledge/
```

| Piece | File |
|---|---|
| Sources (edit freely) | `config/sources.yaml` |
| Risk limits (code-enforced) | `config/risk.yaml` |
| Collectors | `src/tradebot/collectors.py` |
| Triage, research, thesis, tiers | `src/tradebot/brain.py` |
| Knowledge base (profiles, situations, lessons, type briefings) | `knowledge/`, `src/tradebot/knowledge.py` |
| Claude client, two-key rotation, spend cap | `src/tradebot/llm.py` |
| Broker, sizing, approval gate | `broker.py`, `risk.py`, `execution.py` |
| Phone | `telegram.py` |
| Learning loop | `reflection.py` |

Safety: paper by default. Live needs `ALPACA_PAPER=false` **and** `TRADING_LIVE_CONFIRM=yes`.
Approval mode starts `manual`. `/halt` and `/flatten` work from Telegram at any time.

## Setup

1. Get keys: two Anthropic Console keys, Alpaca paper keys, a Telegram bot token from BotFather,
   a Reddit "script" app, a YouTube Data API key, a free healthchecks.io URL.
2. Telegram chat ID: message your bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
3. Cloud server: Ubuntu 24.04, 2 vCPU / 4 GB (Hetzner, DigitalOcean or Vultr).
4. On the server:
   ```bash
   git clone https://github.com/HarryHYu/Trade-Bot.git && cd Trade-Bot
   bash deploy/install.sh        # creates .env, edit it, then run again
   docker compose -f deploy/docker-compose.yml exec bot tradebot check
   ```

## Telegram

One bot, one private group. Any normal message is a chat with Claude (Opus 5.5), which can read the
database and knowledge base, check the account, search the web, make charts and send files. Send it
photos, screenshots, PDFs and text files. Rule changes come back as a button to confirm.

1. BotFather: `/newbot`, keep the token. Then `/setprivacy` -> Disable, so it can read group messages.
2. Make a Telegram group, turn on **Topics** in group settings, add the bot and make it admin.
3. Send a message in the group, open `https://api.telegram.org/bot<TOKEN>/getUpdates`, and copy the
   group's `chat.id` (starts with -100) into `TELEGRAM_CHAT_ID`. Put your own `from.id` in
   `TELEGRAM_ALLOWED_USER_IDS`.
4. On first start the bot creates topics: Proposals, Alerts, Brief, Research, Chat.
   Mute the ones you don't need pinging. A DM chat ID also works; everything then lands in the DM.

## Local commands
```bash
uv sync
uv run tradebot check      # verify credentials
uv run tradebot collect    # one collection pass
uv run tradebot triage     # triage pending items
uv run tradebot research   # research the top lead
uv run tradebot brief      # print the daily brief
uv run pytest
```

## Not built yet
Earnings protocol timeline (T-21 to T+1), commodities fast-lane playbooks with armed orders,
options instruments, watch-mode active re-search, browser tool for Claude, collector auto-writing,
shadow-mode parameter testing, web portal, voice notes in chat. The schema already has `Prediction` for earnings.
