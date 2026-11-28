# Tuning log
Every change made by a review shift: date | change | evidence | expected effect | how to judge it next time.

- 2026-11-28 | Regime filter: trades against SPY's 50-day trend get half size (config/risk.yaml regime) | Oct 27-Nov 27: all
  longs lost in a falling tape; short ideas were the best | fewer/lighter losing longs in downtrends | compare average R of
  against-trend vs with-trend trades after 20+ trades.
- 2026-11-28 | Watch theses re-checked by Opus after 5 days (brain.recheck_watch) | BRBR +73% sat on watch with a dated trigger |
  watch ideas whose trigger fired become trades | count re-checks that turned into trades and their outcome.
- 2026-11-28 | Rejected ideas paper-tracked at every stage (haiku, sonnet, opus, risk) | owner request; skipped ideas beat traded
  ones last month | know which stage throws away winners | REPORT "Rejected ideas" by stage after 2-4 weeks.
- 2026-11-28 | Opus research checks: 'noticed' compares the move since public data first showed and broker targets; 'reaction'
  treats binary events by their record and dated catalysts with strong leading data as setups | judgement pass: TBLD
  (sell-the-news), CAPR (skewed FDA), TTWO (dated launch) | fewer longs bought after the data is priced; directional stances
  on skewed binaries | re-judge similar cases in 4 weeks via 'Calls that teach the most'.
- 2026-11-28 | Movers bank seeded with 10 judged moves; digest fed to Sonnet, Opus and the explorer director | judgement pass |
  the swarm flags early signs (self-help guidance, controlled-company deals, DC/ERP cutovers, neocloud financing) | count
  dossiers/theses citing a movers-bank pattern and their outcomes.
- 2026-11-28 | Fast lane OFF; Opus daily cap counts only Sonnet-shortlisted research; tradability floor before Opus (price >= 1,
  ~$2M+/day traded, no repeat within 5 days; filtered ideas paper-tracked as stage "filter"); evaluator every 30 min and told
  about recent rejections | first export: 452 Haiku flags -> 66 Sonnet picks -> 12 Opus theses, ALL from the fast lane, all
  watch-tier untradable micro-caps (BZAI researched 6x, VIVK 4x); 0 full research runs, 0 trades; fast lane used up the cap |
  Opus researches Sonnet's picks; theses on tradable names; first paper trades | next export: count research vs research_fast
  calls, theses by tier, trades opened.
- 2026-11-28 | Emergency lane back ON, Sonnet-gated: urgent Haiku flags (last 2h, priority >= 0.25) -> tradability filter ->
  one batched Sonnet "is this a genuine emergency?" check every 3 min -> Opus fast research (max 4/day, small size);
  non-emergencies stay in the normal queue; emergency picks scored as source "sonnet_emergency" | owner request; the
  Haiku-only lane wasted all Opus research on micro-cap filings | fast reaction to real breaking news without junk |
  emergency runs per day, their tiers, and the outcome of emergency trades vs the normal queue.
