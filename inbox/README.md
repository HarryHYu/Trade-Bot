# Research inbox

Scheduled Claude Code research sessions (run on the Max plan, not API credits) write findings here.
The bot imports every new `*.json` file, opens a thesis, posts it to Telegram Research, adds the listed
monitoring sources, and runs it through normal risk rules. Imported files are recorded, never re-imported.

Sessions write ONLY in this folder. One file per opportunity, named `YYYYMMDD-HHMM-TICKER.json`.

```json
{
  "ticker": "OSUR",
  "security_type": "diagnostics",
  "direction": "long",
  "driver": "One sentence: what is happening now",
  "mechanism": "How it reaches the stock, with numbers",
  "probability": 0.55,
  "expected_move_pct": 20,
  "horizon_days": 14,
  "tier": "small",
  "checks": [{"name": "noticed", "verdict": "pass", "reasoning": "..."}],
  "catalysts": ["Q3 report 2026-11-04"],
  "kill_criteria": ["..."],
  "monitor": ["news search query", "https://status-page"],
  "diligence": {"filings_reviewed": "...", "earnings_quality": "...", "product_scope": "...",
                "buyer_channel": "...", "materiality": "...", "competition": "...",
                "consensus_and_guidance": "...", "unverified": []},
  "sources": ["https://..."],
  "summary": "Short note for the owner"
}
```
Tier is capped at small unless every diligence field is filled and `unverified` is empty.
