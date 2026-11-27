# Desk scorecard (2026-11-27)

Paper ledger: fixed notional per trade, prices from Yahoo closes. Regenerate with `tradebot ledger report`.

## Trades

| id | ticker | side | opened | entry | status | last/exit | return | R | vs benchmark | conv |
|---|---|---|---|---|---|---|---|---|---|---|
| T1 | OSUR | long | 2026-10-27 | 4.17 | closed stop (3.85 close rule and 3.50 stop) | 3.46 | -17.0% | -1.06 | -14.7% | 3 |
| T2 | RIG | long | 2026-10-30 | 6.76 | closed exit_by | 6.23 | -7.8% | -0.75 | -7.4% | 2 |
| T3 | W | long | 2026-10-30 | 118.6 | closed stop (close below 112) / T-1 | 111.3 | -6.1% | - | -5.9% | 2.5 |
| T4 | FIGS | long | 2026-10-30 | 21.44 | closed exit_by | 21.94 | +2.3% | 0.26 | +0.9% | 2 |
| T5 | TBLD.L | long | 2026-10-30 | 12.25 | closed stop | 10.6 | -13.5% | -1.00 | -13.1% | 3 |

**Closed trades:** 5 | win rate 20% | average return -8.4% | average R -0.64 | profit factor 0.05 | average vs benchmark -8.1% | average hold 7 days

**Paper equity:** $9,579 from $10,000 (-4.2%)

## Predictions

Resolved 8: hit rate 5/8 | leaned the right way vs consensus 6/6

| id | made | subject | question | call | resolve by | status | actual | score |
|---|---|---|---|---|---|---|---|---|
| P1 | 2026-10-27 | RBLX | Q3 bookings $M | 1600-1680 (pt 1640, cons 1630) | 2026-10-29 | resolved | 1697 | hit=False, error_pct=3.48, beat_consensus_call=True |
| P2 | 2026-10-27 | RBLX | Q3 DAU M | 130-140 (pt 134, cons 130.65) | 2026-10-29 | resolved | 138 | hit=True, error_pct=2.99, beat_consensus_call=True |
| P3 | 2026-10-27 | RBLX | Q3 hours B | 32-35 (pt 33.5, cons 32.24) | 2026-10-29 | resolved | 34 | hit=True, error_pct=1.49, beat_consensus_call=True |
| P4 | 2026-10-27 | RBLX | Q4 bookings guide midpoint $M | 1900-2050 (pt 1975, cons 2000) | 2026-10-29 | resolved | 1945 | hit=True, error_pct=-1.52, beat_consensus_call=True |
| P5 | 2026-10-27 | RBLX | Q3 bookings per hour YoY | down | 2026-10-29 | resolved | up | hit=False |
| P6 | 2026-10-27 | OSUR | Q3 revenue $M (called a beat) | 31.0-31.8 (pt 31.4, cons 30.87) | 2026-11-09 | resolved | 30.9 | hit=False, error_pct=-1.59, beat_consensus_call=True |
| P7 | 2026-10-30 | OSUR | Q4 revenue guide midpoint $M | 30-33 (pt 31.5, cons 31.7) | 2026-11-09 | resolved | 31.0 | hit=True, error_pct=-1.59, beat_consensus_call=True |
| P8 | 2026-10-30 | Brent | Oil falls after the midterms (by Nov 6) | down | 2026-11-06 | resolved | down | hit=True |
| P9 | 2026-11-13 | S&P 500 | Closes >=10% below its 2026 closing high (7818.93) by 2027-02-13 | p=0.3 | 2027-02-13 | open | - | - |
| P10 | 2026-11-13 | S&P 500 | Closes >=20% below its 2026 closing high by 2027-11-13 | p=0.25 | 2027-11-13 | open | - | - |
| P11 | 2026-11-13 | S&P 500 | Closes >=30% below its 2026 closing high by 2027-11-13 | p=0.1 | 2027-11-13 | open | - | - |
| P12 | 2026-11-13 | SMH | Below its 2026-11-13 close (486.75) on 2027-05-13 (AI stocks lose or go nowhere) | p=0.55 | 2027-05-13 | open | - | - |
| P13 | 2026-11-13 | BTC | Weekly close below $58,500 before 2027-03-31 (capitulation leg) | p=0.4 | 2027-03-31 | open | - | - |
| P14 | 2026-11-13 | BTC | Above $82,000 on 2026-12-31 | p=0.2 | 2026-12-31 | open | - | - |

## Leads (every idea, traded or not, scored by what the price did since)

Edge = move relative to the benchmark in the direction of our stance (avoid counts as a bet against).

| id | flagged | ticker | stance | acted | ref | now | move | benchmark | edge | idea |
|---|---|---|---|---|---|---|---|---|---|---|
| L1 | 2026-10-27 | RBLX | pass | no | 54.47 | 55.63 | +2.1% | -6.9% | - | Q3 print: engagement beat noticed, no edge on the move |
| L2 | 2026-10-27 | CAPR | pass | no | 6.1 | 1.95 | -68.0% | -6.9% | - | FDA decision Nov 22 priced as a lottery, no probability edge |
| L3 | 2026-10-27 | RAPP | watch_long | no | 27.54 | 25.32 | -8.1% | -6.9% | -1.2% | Cluster insider buying after bipolar Ph2 dip; long-horizon only |
| L4 | 2026-10-27 | PSX | watch_short | no | 268.5 | 236.1 | -12.1% | -6.9% | +5.2% | Refiners price peak cracks as durable while diesel cracks fall |
| L5 | 2026-10-27 | VLO | watch_short | no | 387.3 | 323 | -16.6% | -6.9% | +9.7% | Refiners price peak cracks as durable while diesel cracks fall |
| L6 | 2026-10-27 | MPC | watch_short | no | 436 | 346.3 | -20.6% | -6.9% | +13.7% | Refiners price peak cracks as durable while diesel cracks fall |
| L7 | 2026-10-27 | DINO | watch_short | no | 114.2 | 85.76 | -24.9% | -6.9% | +18.0% | Refiners price peak cracks as durable while diesel cracks fall |
| L8 | 2026-10-27 | PBF | watch_short | no | 73.63 | 56.33 | -23.5% | -6.9% | +16.6% | Refiners price peak cracks as durable while diesel cracks fall |
| L9 | 2026-10-27 | CVI | watch_short | no | 57.43 | 30.43 | -47.0% | -6.9% | +40.1% | Refiners price peak cracks as durable while diesel cracks fall |
| L10 | 2026-10-27 | DK | watch_short | no | 70.3 | 52.87 | -24.8% | -6.9% | +17.9% | Refiners price peak cracks as durable while diesel cracks fall |
| L11 | 2026-10-27 | URA | watch_long | no | 35.86 | 27.71 | -22.7% | -6.9% | -15.8% | Uranium term price 19-yr high vs miners -42%; sulfur/acid chain not the trigger yet |
| L12 | 2026-10-27 | MOS | pass | no | 18.79 | 20.35 | +8.3% | -6.9% | - | Sulfur shock losers already priced |
| L13 | 2026-10-27 | 036570.KS | watch_long | no | 1.987e+05 | 1.89e+05 | -4.9% | -12.8% | +7.9% | AION 2 retention better than past NCSoft launches; market prices a fade |
| L14 | 2026-10-27 | ALI=F | watch | no | 3285 | 2839 | -13.6% | -6.9% | - | Aluminium falling despite Gulf supply risk: unexplained |
| L15 | 2026-10-27 | APT | watch_long | no | 5.44 | 5.78 | +6.3% | -6.9% | +13.1% | Ebola outside Africa would re-rate PPE micro-caps (armed playbook) |
| L16 | 2026-10-27 | LAKE | watch_long | no | 9.16 | 8.39 | -8.4% | -6.9% | -1.5% | Ebola PPE playbook |
| L17 | 2026-10-27 | OSUR | long | yes | 4.16 | 3.41 | -18.0% | -6.9% | -11.1% | Ebola rapid test + cash floor + activist |
| L18 | 2026-10-30 | TBLD.L | long | yes | 13 | 9.5 | -26.9% | -0.4% | -26.6% | Probably Stolen Steam record |
| L19 | 2026-10-29 | BRBR | watch_long | no | 9.06 | 15.66 | +72.8% | -6.8% | +79.7% | Whey protein costs easing from June record; needs FY27 cost commentary (Nov 17) |
| L20 | 2026-10-29 | SFD | pass | no | 18.55 | 19.03 | +2.6% | -6.8% | - | Lean hogs at 2020 lows, packer already priced |
| L21 | 2026-11-03 | BZ=F | watch_short | no | 103.6 | 91.98 | -11.2% | -5.0% | +6.2% | Oil falls after the midterms on peace/Hormuz headlines (planned Nov 4 entry) |
| L22 | 2026-10-29 | RIG | long | yes | 6.69 | 5.03 | -24.8% | -6.8% | -18.0% | Oil beta ride, exit by Nov 2 |
| L23 | 2026-10-29 | W | long | yes | 120.1 | 97.11 | -19.2% | -6.8% | -12.3% | Momentum ride, exit by Nov 3 |
| L24 | 2026-10-29 | FIGS | long | yes | 21.56 | 20.88 | -3.2% | -6.8% | +3.7% | Momentum ride, exit by Nov 4 |
| L25 | 2026-10-29 | EL | pass | no | 108.8 | 109.6 | +0.7% | -6.8% | - | Run-up into print, no edge either way |
| L26 | 2026-10-29 | CART | pass | no | 57.11 | 57.68 | +1.0% | -6.8% | - | DoorDash deal spread, not an earnings trade |
| L27 | 2026-10-29 | PLNT | avoid | no | 53.72 | 58.01 | +8.0% | -6.8% | -14.8% | Rebound fading into print |
| L28 | 2026-10-29 | MRNA | pass | no | 222.8 | 220.8 | -0.9% | -6.8% | - | Cancer vaccine win fully priced |
| L29 | 2026-10-29 | TTWO | avoid | no | 193.4 | 227.3 | +17.6% | -6.8% | -24.4% | Falling into GTA VI launch, no run-up to ride |
| L30 | 2026-10-29 | IREN | avoid | no | 26.25 | 12.85 | -51.0% | -6.8% | +44.2% | AI/miner rebound: wait for earnings/CPI/NVDA |
| L31 | 2026-10-29 | CIFR | avoid | no | 8.1 | 5.51 | -32.0% | -6.8% | +25.1% | AI/miner rebound: wait |
| L32 | 2026-10-29 | COHR | avoid | no | 237.2 | 196.2 | -17.3% | -6.8% | +10.5% | AI optics rebound: wait |
| L33 | 2026-10-29 | SITM | avoid | no | 488.1 | 386.1 | -20.9% | -6.8% | +14.0% | AI rebound: wait |
| L34 | 2026-11-05 | MYR.AX | avoid | no | 0.25 | 0.225 | -10.0% | -2.1% | +7.9% | David Jones collapse: don't chase Myer at 0.25, buy 0.21-0.22 or on closure list |
| L35 | 2026-11-05 | SCG.AX | watch_long | no | 3.61 | 3.59 | -0.6% | -2.1% | +1.5% | DJ closures hand Scentre back cheap space (needs closure list) |
| L36 | 2026-11-05 | ABY.AX | watch_long | no | 0.265 | 0.155 | -41.5% | -2.1% | -39.4% | Prestige beauty share from DJ; tiny cap (needs closure list/AGM) |
| L37 | 2026-11-12 | NVDA | pass | no | 166.2 | 149.3 | -10.2% | -5.3% | - | CEO resignation/Megaspeed: stay out until the Nov 17 print |
| L38 | 2026-11-12 | AMD | avoid | no | 585.3 | 406 | -30.6% | -5.3% | +25.3% | Rotation winner already priced after +21% week |
| L39 | 2026-11-12 | GDS | pass | no | 20.05 | 13.8 | -31.2% | -5.3% | - | Possible Megaspeed data-centre link (speculative) |
| L40 | 2026-11-12 | CRCL | watch_long | no | 63.82 | 48.27 | -24.4% | -5.3% | -19.0% | Stablecoin rule effective Jan 18 vs beaten-down issuer |
| L41 | 2026-11-13 | BTC-USD | pass | no | 6.825e+04 | 6.162e+04 | -9.7% | -5.0% | - | No position: long on weekly close > 75k, risk-off < 58.5k |

**Directional leads:** 19/30 right vs benchmark | average edge +5.9%
- acted on: 1/5 right, average edge -12.9%
- not acted on: 18/25 right, average edge +9.6%
