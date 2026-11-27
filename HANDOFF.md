# Handoff: start here (for any new Claude session or account)

Read this, then `README.md`, `knowledge/watch.md`, `knowledge/macro/`, `knowledge/reflections/` and `knowledge/paper_trades.md`.
Everything the desk knows lives in this repo; chat history does not carry between accounts.

## What this is
A research-driven paper-trading desk for the owner (Harry). Style: "act now": trade drivers visibly happening now, hold days to
weeks, cut dead money fast. Find real-world THINGS first (outbreaks, shortages, viral games, collapses, rulings, data spikes), then
link them to listed companies. Primary sources over press and broker claims. Paper trading only.

## How the work is split
- **Server (24/7, API credit):** a DigitalOcean droplet runs the bot in Docker (`deploy/`). Collectors (free), Haiku triage, an
  exploration swarm (Sonnet director picks angles hourly, Haiku explorers dig and file dossiers), and a Sonnet collator that posts a
  ranked to-do list to the Telegram "Research" topic 5x a day. Spend is paced to the monthly key credit (`/missions` shows it).
  Expensive API research jobs are OFF (`ENABLED_JOBS`). Telegram bot "Caplet" in the group "trading desk" is the control panel.
- **Claude Code on the Max plan (this kind of session):** the deep research, diligence and trade calls. Start from the latest to-do
  list (Telegram, or the `desk-feed` branch once `GITHUB_FEED_TOKEN` is set), the watch list and open positions.

## Rules learned the hard way (see knowledge/reflections)
1. The driver must be measurable in first-hand data now; "might get orders" stays on watch (OSUR loss).
2. Name the actual buyer and the approval/listing they buy under.
3. Earnings reactions follow the NEXT-quarter guide vs consensus; check the last 4-8 reactions; exit run-ups by T-1 unless there is a
   measured edge on the guide (RBLX).
4. Event trades: enter BEFORE the event (oil after the midterms was right but late).
5. Minimum conviction 3/5 to trade; no chasing headline pops within 3 sessions; ~10 trading-day time stop on unconfirmed theses.

## Open items (as of 2026-11-27)
- Confirm the server runs the swarm (`git pull && bash deploy/install.sh`, `/unpause`, credits and cap set) and check real cost per mission.
- Set `GITHUB_FEED_TOKEN` (repo-scoped, Contents read/write) so to-do lists land on the `desk-feed` branch for sessions to read.
- Merge branch `paper-simulator` (built-in paper broker) into main, then build the web dashboard (behind Tailscale Funnel + password)
  and Telegram charts, so every call is logged at real prices.
- Daily macro tripwires and the mega-cap correlation measure as bot checks (thresholds in knowledge/macro/2026-11-13-regime.md).
- Open paper position: tinyBuild (TBLD.L) long from 12.25p, driver = Steam players for Probably Stolen (app 4348910).
- Watching: oil after an Iran deal, Bitcoin levels ($75k weekly close long / $58.5k risk-off / capitulation signals), Nvidia after the
  CEO resignation and Megaspeed probe, David Jones closure list (Myer/Scentre/Adore Beauty), stablecoin rule effective Jan 18.

## Scheduled research shift (prompt to recreate as a Claude Code scheduled task on any account)
> You are a research shift for an automated trading desk. Attach repo HarryHYu/Trade-Bot (push access), work on main. Read HANDOFF.md,
> inbox/README.md, knowledge/watch.md, config/strategy.yaml, the latest to-do list (desk-feed branch if present). Start from things
> happening now, trace who is affected, check what is priced, do primary-source diligence, follow the rules above. Write theses as
> inbox/YYYYMMDD-HHMM-TICKER.json (format in inbox/README.md) and always a scan note inbox/YYYYMMDD-HHMM-scan.md. Edit only inbox/.
> Commit, rebase on origin/main, push. Zero ideas is a valid outcome.
Suggested times (Sydney): ~19:30 evening scan, ~23:00 pre-open, ~03:30 mid-session. No shift right after the US close.

## Secrets
Never in this repo or in chat. Keys are set from Telegram with `/setkey` (the message is deleted and never reaches Claude).
