# Server setup (about 30 minutes)

The bot runs on one small cloud server, 24/7, in Docker. You only touch it to update.

## 0. Before you start
- A Mac with Terminal (built in).
- Cloud providers and Anthropic require account holders to be 18+, so the accounts go in a parent's name.
- No broker is needed yet: without broker keys the bot researches and sends theses to Telegram, no orders.

## 1. Make an SSH key on your Mac (2 min)
Open Terminal and run, pressing Enter at every prompt:
```bash
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```
Copy the whole line that starts with `ssh-ed25519`.

## 2. Create the server (5 min)
1. Sign up at https://www.hetzner.com/cloud and create a project.
2. Add Server:
   - Location: Ashburn (US East). Close to US exchanges and data sources.
   - Image: Ubuntu 24.04.
   - Type: Shared vCPU, 4 vCPU / 8 GB (about AUD 20-30/month). Resize later with one click.
   - SSH keys: paste the line from step 1.
   - Firewall: create one that allows only inbound TCP 22 (SSH). Nothing else needs to be open.
3. Create, then copy the server's IPv4 address.

## 3. Get the keys you need now (15 min)
Put them in your notes app for step 5. Never paste them into chats.
| Key | Where |
|---|---|
| Two Anthropic API keys | platform.claude.com, one in each Console org linked to a Max plan |
| Telegram bot token | Message @BotFather: `/newbot`, then `/setprivacy` -> your bot -> Disable |
| Telegram group ID and your user ID | Make a group, turn on Topics, add the bot as admin, send "hi", open `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy `chat.id` (starts -100) and `from.id` |
| SEC contact | Just your email, used in the SEC user agent |
| Optional: Reddit | reddit.com/prefs/apps, create a "script" app |
| Optional: YouTube | console.cloud.google.com, enable YouTube Data API v3, create an API key |
| Optional: heartbeat | healthchecks.io, create a check, copy its ping URL |

## 4. Connect and install (5 min)
```bash
ssh root@YOUR_SERVER_IP
apt-get update && apt-get install -y git unattended-upgrades
git clone https://github.com/HarryHYu/Trade-Bot.git && cd Trade-Bot
bash deploy/install.sh
```
The first run installs Docker, creates `.env`, and stops so you can fill it in.

## 5. Fill in .env
```bash
nano .env
```
Minimum to start:
```
ANTHROPIC_API_KEYS=sk-ant-key1,sk-ant-key2
MONTHLY_SPEND_CAP_USD=600
MAX_DILIGENCE_PER_DAY=2
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=-100...
TELEGRAM_ALLOWED_USER_IDS=your from.id
SEC_USER_AGENT=tradebot your-email@example.com
TIMEZONE=Australia/Sydney
```
Leave the ALPACA lines empty. Save with Ctrl+O, Enter, exit with Ctrl+X.

## 6. Start it
```bash
bash deploy/install.sh
docker compose -f deploy/docker-compose.yml exec bot tradebot check
```
`check` shows the number of Anthropic keys and sends a test message to Telegram. Within a minute the bot
creates the Proposals, Alerts, Brief, Research and Chat topics in your group and starts collecting.

## 7. Day to day
| Task | Command (from `~/Trade-Bot` on the server) |
|---|---|
| Live logs | `docker compose -f deploy/docker-compose.yml logs -f --tail 100` |
| Update to the latest code | `git pull && docker compose -f deploy/docker-compose.yml up -d --build` |
| API spend so far | `docker compose -f deploy/docker-compose.yml exec bot tradebot costs` |
| Diligence by hand | `docker compose -f deploy/docker-compose.yml exec bot tradebot diligence OSUR "thesis"` |
| Stop / start | `docker compose -f deploy/docker-compose.yml down` / `up -d` |
| Halt trading from your phone | `/halt` in Telegram |

Backups: the install script adds a nightly database backup (kept a week, by weekday) and a nightly commit
of the knowledge folder. The bot restarts itself after crashes and server reboots.

## Troubleshooting
- `check` says 0 keys: `.env` has a typo in ANTHROPIC_API_KEYS, or a space after a comma.
- No Telegram message: the bot is not admin in the group, privacy is not disabled, or the chat ID lacks the minus sign.
- Anything else: copy the last 50 log lines (not your keys) into a Claude session.
