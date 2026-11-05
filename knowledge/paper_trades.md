# Manual paper trades (until the bot's own paper broker is connected)
Every entry is scored after its catalyst. Format: date | ticker | side | entry | stop | targets | size rule | thesis | catalyst | status

- 2026-10-27 | OSUR | long | limit 4.10-4.20 (ref close 4.17; skip if opens > 4.40) | stop 3.50 | T1 5.00, T2 5.50-6.50 | small: risk ~1% of paper equity | Only FDA-EUA rapid Bundibugyo Ebola test, outbreak ~7x since Q3 guide, ~$2.33/sh cash, activist sale pressure | Q3 report 2026-11-09 | CLOSED 2026-11-02 at stop (~3.46, about -17%)
- 2026-10-27 update | OSUR | after deep dig: tier SMALL (was standard). Ebola EUA is lab-only and not WHO-listed, so Q3 Ebola revenue is capped. Keep size small, stop 3.50 unchanged, T1 4.60 (was 5.00). Upside now mainly from activist/strategic news. Prediction file: knowledge/securities/OSUR/prediction-2026Q3.md
- 2026-10-27 | RBLX | NO TRADE (watch) | ref close ~54.4 | Q3 report 2026-10-29 AMC | Call: engagement beat, bookings in line, monetization per user down; options imply ~14%; stock +30%/20d; no edge beyond implied move. Score the prediction file after the print. | status: PASS

## Scorecard 2026-11-06 (reconstructed from calls; entries at the next open after the call, exits per stated rule)
| Trade | Entry | Exit | Result | Note |
|---|---|---|---|---|
| OSUR long | 4.17 (Oct 27) | 3.46 (Nov 2, stop) | -17.0% | broken buyer channel; hypothetical driver |
| RIG ride | 6.76 (Oct 30 open) | 6.23 (Nov 2 close, T-1 rule) | -7.8% | conviction 2; oil beta |
| W ride | 118.59 (Oct 30 open) | 111.32 (Nov 3 close; stop <112 also hit) | -6.1% | conviction 2.5; missed +11% print pop by rule |
| FIGS ride | 21.44 (Oct 30 open) | 21.94 (Nov 4 close, T-1 rule) | +2.3% | rule then avoided -10% |
| TBLD.L long | 12.25p (Oct 30 open) | open, 13.0p | +6.1% | measured driver (Steam), still rising |
Total: 5 trades, 2 wins / 3 losses, average -4.5% per trade. Oct 30 batch only: average -1.4% vs SPY -3.5% over Oct 30 open -> Nov 5.
Not traded: RBLX pass (avoided -18% print, then it recovered to -6%); oil short planned for Nov 4 entry, but the whole -8.7% happened Nov 4.
Lessons: conviction-2 momentum rides in a risk-off tape lost money: minimum conviction 3 to trade. Measured-driver trade is the only clear winner.
Event-driven entries must be placed BEFORE the event (e.g. Nov 3 close for an election-driven oil move), not after.
