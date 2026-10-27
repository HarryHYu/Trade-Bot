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

Full walkthrough: [docs/SERVER_SETUP.md](docs/SERVER_SETUP.md)


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

## Trading style: act now

Lots of trades on drivers that are visibly happening now. Buy as the move starts, ride it while the driver and the
trend hold, cut dead money fast. No bets that need an event months away (`config/strategy.yaml`).

- Fast lane: breaking, high-priority leads get a 3-agent check (news verification, priced-in, red team) within
  minutes and enter at small size. The full 8-specialist review runs afterwards and can kill the trade.
- Exits are bot-managed every minute: initial stop at 1.5 ATR, stop to breakeven at +0.75R, trailing stop from +1R,
  hard target at 3R or the expected move, a dead-money time stop after 10 trading days for trades not yet up 1R, and
  an immediate exit if the thesis is killed. Winners are never time-stopped.
- Risk: 0.5% of equity per trade, up to 20 positions, 8% max per name, unhedged by default.

## Research quality: multi-agent diligence

Any thesis that clears the first pass goes to eight specialists in parallel, each with one narrow brief
and primary-source data tools (SEC filings and XBRL financials, USAspending federal awards, prices,
options) plus web search/fetch:

| Specialist | Checks |
|---|---|
| filings | every filing since last results: 8-K, 10-Q, 13D/13G, Form 4, dilution |
| financial_quality | one-offs behind profits, cash burn, net cash per share, EV |
| product_scope | exact regulator authorisation and its limits |
| buyer_channel | who pays, whether they are allowed to buy, evidence of orders |
| materiality | driver revenue vs company revenue and market cap, with arithmetic |
| competition | who else serves the demand, and threats to the core business |
| priced_in | price reaction at each key date, consensus, implied move, crowding |
| red_team | the strongest case that the thesis is wrong |

Every claim must carry a value and a source URL. A verifier re-opens every source and marks each claim
confirmed, wrong or unverifiable. A judge (Claude Fable 5.1) sets the tier, and hard rules in code cap it:
a failed product_scope, buyer_channel or materiality, or any wrong material claim, means no trade; over 20%
unverifiable claims or a red-team fail caps at small; any concern caps at standard.
Run it by hand: `tradebot diligence OSUR "only FDA-EUA Bundibugyo rapid test, activist sale pressure"`.
Budget: `MAX_DILIGENCE_PER_DAY` (default 2). A full run costs roughly $10, so 2 a day is about $600 a month; raise it when the hit rate justifies it.

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
