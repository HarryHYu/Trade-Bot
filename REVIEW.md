# Review shift: evaluate, reflect, tune (Claude Code on the Ultra plan)

The server makes every trading decision on the API (Haiku scans, Sonnet shortlists, Opus calls, the simulator trades) and
records everything. **Evaluation, reflection and tuning happen here**, in Claude Code, from the daily export. Nothing the
server does is "judged" until a review shift looks at it.

## 1. Get the data
```
git fetch origin desk-feed main && git checkout main && git pull
git show origin/desk-feed:feed/README.md          # export time
git show origin/desk-feed:feed/REPORT.md          # scorecard: trades, predictions, ideas, rejected ideas
```
Then read as needed from `origin/desk-feed:feed/`: `theses.json` (Opus calls with their reasoning and diligence),
`trades_bot.json` (fills, exits, reasons), `decisions.json` (Sonnet shortlists and pass-reasons, Opus dismissals, risk blocks,
earnings calls), `pipeline.json` (funnel and spend by stage), `missions_3d.json` (explorer dossiers),
`knowledge_changes.json` (situation notes the server wrote), raw ledger in `ledger/`. Prices: Yahoo chart API (curl works).
Also read `knowledge/tuning_log.md` (what was changed before and why) so changes are judged, not repeated.

## 2. Evaluate (be quantitative, then explain the mechanism)
- **Trades:** win rate, average R, vs benchmark; for each closed trade: right thesis/bad timing, wrong thesis, sizing, luck.
- **Every stage separately** (REPORT "By source" and "Rejected ideas"): Haiku discards, Sonnet shortlist vs Sonnet passes,
  Opus theses vs Opus dismissals, risk blocks. Which stage adds value, which throws away winners? Read the rejection reasons
  of the biggest misses.
- **Calibration:** resolved probability predictions (Brier, hit rate by probability bucket). Are Opus's probabilities and
  expected moves too high or too low? Earnings KPI ranges: hit rate and lean vs consensus.
- **Patterns:** cluster errors by security type, tier, direction, horizon, driver kind, market regime. Fewer than ~20 cases
  is a hypothesis, not a finding; say which.
- **Cost:** spend per stage vs what each stage produced.

## 3. Act on it (commit to main; the server pulls within 10 minutes)
Allowed changes, smallest effective change first, one idea per change:
- `knowledge/`: lessons per security (`securities/<T>/lessons.md`), type briefings (`types/<type>/`), `watch.md`, a dated
  note in `knowledge/reviews/YYYY-MM-DD.md`. The server feeds these into Opus's research context.
- `config/strategy.yaml`, `config/risk.yaml` (stops, targets, time stops, tier risk, regime filter), `config/sources.yaml`
  (add or drop sources and news searches).
- Prompts in `src/tradebot/brain.py` (TRIAGE_SYSTEM, RESEARCH_SYSTEM), `evaluate.py` (EVALUATE_SYSTEM), `explore.py`
  (DIRECTOR_SYSTEM, EXPLORER_SYSTEM), `diligence.py`; tier rules in `brain.tier_for`.
- Budget split in `src/tradebot/budget.py` GROUPS if a stage is starved or wasteful.

Rules: run `uv run pytest -q` before committing any code change; never raise risk limits above risk.yaml's current caps by more
than 25% in one review; never switch to live trading; never touch secrets; record every change in
`knowledge/tuning_log.md` (date, change, evidence, expected effect, how to judge it next time).

## 4. Report
Post nothing to Telegram from here. End with a short summary in the session: what was evaluated, what changed, what to
watch. A deeper weekly review on Saturdays: re-judge the last weeks' changes in `tuning_log.md` and revert what didn't help.

## Scheduled review shift (prompt to paste into a Claude Code scheduled task, daily ~08:40 Sydney)
> You are the daily review shift for an automated paper-trading desk. Attach repo HarryHYu/Trade-Bot with push access and work
> on main. Read REVIEW.md and follow it exactly; the data is on the desk-feed branch (feed/). If there is no export from the
> last 2 days, stop and report that. Evaluate every stage quantitatively, act with the smallest effective changes (tests before
> code commits, log every change in knowledge/tuning_log.md, write knowledge/reviews/YYYY-MM-DD.md; on Saturdays re-judge and
> revert earlier changes), commit, rebase on origin/main and push. Finish with a short summary.
