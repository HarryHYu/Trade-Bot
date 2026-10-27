"""Multi-agent diligence. A thesis that clears the first pass is handed to eight specialists in parallel,
each with one narrow brief and a fixed set of questions. Every factual claim they make must carry a number
and a source. A verifier then re-opens the source for every claim. A judge combines the reports, and hard
rules in code cap the tier, whatever the judge says.

Rules (code-enforced in apply_caps):
- A specialist 'fail' on product_scope, buyer_channel or materiality  -> watch (no trade).
- Any claim the verifier marks 'wrong' that the thesis depends on       -> watch.
- More than 20% of claims unverifiable, or red team 'fail'              -> capped at small.
- Any specialist 'concern'                                              -> capped at standard.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import datatools
from .config import get_settings
from .llm import claude

log = logging.getLogger(__name__)

COMMON = """You are one specialist on a trading desk's diligence team. You check ONE dimension of a thesis,
from primary sources. Rules:
- Every factual statement you rely on must have: the number or fact, the exact source URL, and the source
  type (sec_filing, regulator, government_data, company_release, exchange_data, reputable_press, other).
- Prefer primary sources: SEC filings via the sec_* tools, regulators' own pages, government databases,
  exchange data. Press is acceptable only when no primary source exists, and you must say so.
- If you cannot verify something, say "unverified" explicitly. Never fill gaps with assumptions.
- Be specific: dates, amounts, units, periods. "Strong demand" is not a finding; "$143,100 order on 2026-08-17" is.
- End with a verdict on your dimension: pass (supports the thesis), concern (weakens it), fail (breaks it)."""

SPECIALISTS: dict[str, str] = {
    "filings": """DIMENSION: filings. List every SEC filing since the last periodic report (sec_filings), then read
the material ones (8-K items, 10-Q, 13D/13G and amendments, Form 4, proxy). Answer:
1. What material events were disclosed, with dates? 2. Who holds >5% and did any activist file a 13D, with
what stated purpose and cost basis? 3. Insider buying or selling, net, with amounts? 4. Any dilution (S-3,
ATM, S-8, converts)? 5. Anything in the risk factors or legal proceedings that changed since the prior filing?""",
    "financial_quality": """DIMENSION: financial quality. Use sec_financials and the latest 10-Q/press release. Answer:
1. Revenue for the last 5 quarters, with YoY and QoQ growth. 2. Was the latest profit/beat driven by one-offs
(contingent consideration, gains, tax, reversals)? Quantify. 3. Underlying operating cash flow and burn per
quarter. 4. Cash, investments, debt, and net cash per share using the latest share count. 5. Enterprise value
at the current price (use price_action for the price).""",
    "product_scope": """DIMENSION: product scope. For the product the thesis depends on, find the regulator's own
documents (FDA EUA letter/fact sheet/label, WHO listing, licence, approval). Answer:
1. Exactly what is it authorised/approved/licensed for, by whom, and since when? 2. Restrictions: settings
(lab only? point of care? CLIA level?), populations, sample types, markets. 3. Which other regulators matter for
the actual buyers, and is it listed/approved there? 4. Any performance data (sensitivity/specificity, field
studies) and their source.""",
    "buyer_channel": """DIMENSION: buyer channel. The thesis assumes someone will pay. Identify who. Answer:
1. Who are the actual buyers for this demand (governments, UN agencies, hospitals, consumers, distributors)?
2. Are they allowed to buy this product (procurement rules, required listings, tenders)? 3. Evidence of actual
orders: federal_awards for US government, tender databases, company statements, with amounts and dates.
4. If there is no evidence of orders, say so plainly.""",
    "materiality": """DIMENSION: materiality. Put numbers on it. Answer:
1. Plausible revenue from the driver in the next 1-2 quarters: low/base/high, with the arithmetic and inputs
(units, price, share). 2. That as a % of quarterly revenue and of market cap/EV. 3. Whether guidance and
consensus were set before or after the driver appeared (dates). 4. Conclusion: is this big enough to move the
stock, in numbers.""",
    "competition": """DIMENSION: competition. Answer:
1. Who else can serve the same demand right now (products, authorisations, capacity)? 2. Which of them the
key buyers already use or have listed. 3. Price or performance advantages either way, with sources.
4. Any new entrant that threatens the company's core revenue line (not just the driver).""",
    "priced_in": """DIMENSION: what is priced in. Use price_action, options_implied_move where available, and analyst
coverage. Answer:
1. Price path around each key date of the driver (did it react, how much, on what volume)? 2. Current
consensus for the next report and how it was set. 3. Options-implied move, if any. 4. How crowded is the
idea (coverage, social chatter, recent volume spikes)? 5. What would have to happen for upside not yet in the price.""",
    "red_team": """DIMENSION: red team. Your job is to kill the thesis. Find the strongest evidence it is wrong:
facts that contradict it, base rates for similar setups, reasons the driver will not reach revenue or price,
and what a short seller would say. Rank the top 5 risks with evidence and probability. Verdict 'fail' if any
risk alone is likely to break the thesis.""",
}


class Claim(BaseModel):
    statement: str
    value: str = Field(description="the number or fact, with units and date")
    source_url: str
    source_type: Literal["sec_filing", "regulator", "government_data", "company_release", "exchange_data",
                         "reputable_press", "other", "none"]
    material: bool = Field(description="true if the thesis depends on this claim")


class SpecialistReport(BaseModel):
    dimension: str
    verdict: Literal["pass", "concern", "fail"]
    summary: str
    findings: list[str]
    claims: list[Claim]
    red_flags: list[str]
    unverified: list[str]


class ClaimCheck(BaseModel):
    index: int
    status: Literal["confirmed", "wrong", "unverifiable"]
    correct_value: str = Field(description="what the source actually says, if different")
    note: str


class Verification(BaseModel):
    checks: list[ClaimCheck]


class Verdict(BaseModel):
    tier: Literal["watch", "small", "standard", "big"]
    probability: float = Field(ge=0, le=1)
    expected_move_pct: float
    horizon_days: int
    direction: Literal["long", "short"]
    reasoning: str
    what_would_upgrade: list[str]
    what_would_kill: list[str]


def _extract(text: str, schema: type[BaseModel], hint: str) -> Any:
    s = get_settings()
    return claude().parse("diligence_extract", s.triage_model,
                          f"Extract the report into the schema exactly as written. {hint} Do not add facts.",
                          text, schema, max_tokens=12000)


def run_specialist(name: str, thesis_brief: str) -> SpecialistReport:
    s = get_settings()
    note = claude().agent(f"diligence_{name}", s.diligence_model, COMMON + "\n\n" + SPECIALISTS[name],
                          thesis_brief, datatools.TOOL_DEFS, datatools.run, effort=s.diligence_effort)
    rep = _extract(note, SpecialistReport, f"dimension='{name}'.")
    rep.dimension = name
    return rep


VERIFIER = """You are the desk's fact checker. You receive numbered claims, each with a value and a source URL.
For EVERY claim, open the source (sec_document for sec.gov, web_fetch otherwise) and check the value exactly:
the number, the units, the date and the period. Mark confirmed only if the source states it. Mark wrong if the
source states something different, and give the correct value. Mark unverifiable if the source cannot be opened
or does not contain it. Be strict: a rounded or re-dated number that changes meaning is wrong."""


def verify(claims: list[Claim]) -> Verification:
    s = get_settings()
    if not claims:
        return Verification(checks=[])
    listing = "\n".join(f"[{i}] {c.statement} | value: {c.value} | source: {c.source_url}" for i, c in enumerate(claims))
    note = claude().agent("diligence_verify", s.verifier_model, VERIFIER, listing, datatools.TOOL_DEFS,
                          datatools.run, effort="medium", max_turns=40, max_searches=len(claims) + 5)
    return _extract(note, Verification, "One check per claim index.")


JUDGE = """You are the desk's head of research. You receive a thesis, eight specialist diligence reports, and a
fact-check of every claim. Decide the conviction tier and the numbers. Weight primary-source evidence over press.
A thesis whose driver has no buyer, no authorisation for the use case, or no material revenue does not trade,
however good the story. Be specific in reasoning and cite the reports."""


def judge(thesis_brief: str, reports: list[SpecialistReport], ver: Verification, claims: list[Claim]) -> Verdict:
    s = get_settings()
    packed = {"reports": [r.model_dump() for r in reports],
              "claims": [c.model_dump() | {"check": next((x.model_dump() for x in ver.checks if x.index == i), None)}
                         for i, c in enumerate(claims)]}
    return claude().parse("diligence_judge", s.judge_model, JUDGE,
                          f"THESIS:\n{thesis_brief}\n\nDILIGENCE:\n{json.dumps(packed)[:180000]}", Verdict,
                          max_tokens=16000)


ORDER = ["watch", "small", "standard", "big"]


def apply_caps(v: Verdict, reports: list[SpecialistReport], ver: Verification, claims: list[Claim]) -> tuple[str, list[str]]:
    """Hard rules. The judge can never exceed these."""
    reasons: list[str] = []
    cap = "big"
    by = {r.dimension: r for r in reports}
    for dim in ("product_scope", "buyer_channel", "materiality"):
        if dim in by and by[dim].verdict == "fail":
            cap = "watch"
            reasons.append(f"{dim} failed")
    wrong_material = [c for c in ver.checks if c.status == "wrong" and c.index < len(claims) and claims[c.index].material]
    if wrong_material:
        cap = "watch"
        reasons.append(f"{len(wrong_material)} material claim(s) wrong")
    unver = sum(c.status == "unverifiable" for c in ver.checks)
    if claims and unver / len(claims) > 0.2:
        cap = min(cap, "small", key=ORDER.index)
        reasons.append(f"{unver}/{len(claims)} claims unverifiable")
    if "red_team" in by and by["red_team"].verdict == "fail":
        cap = min(cap, "small", key=ORDER.index)
        reasons.append("red team found a likely thesis-breaker")
    if any(r.verdict == "concern" for r in reports):
        cap = min(cap, "standard", key=ORDER.index)
        reasons.append("at least one specialist concern")
    return min(v.tier, cap, key=ORDER.index), reasons


def run(thesis_brief: str) -> dict[str, Any]:
    """Full pipeline. Returns everything, so the journal keeps the whole audit trail."""
    with ThreadPoolExecutor(max_workers=len(SPECIALISTS)) as pool:
        futs = {n: pool.submit(run_specialist, n, thesis_brief) for n in SPECIALISTS}
        reports = []
        for n, f in futs.items():
            try:
                reports.append(f.result())
            except Exception as e:
                log.exception("specialist %s failed", n)
                reports.append(SpecialistReport(dimension=n, verdict="concern", summary=f"specialist failed: {e}",
                                                findings=[], claims=[], red_flags=[], unverified=[n]))
    claims = [c for r in reports for c in r.claims]
    ver = verify(claims)
    v = judge(thesis_brief, reports, ver, claims)
    tier, reasons = apply_caps(v, reports, ver, claims)
    return {"verdict": v.model_dump(), "final_tier": tier, "cap_reasons": reasons,
            "reports": [r.model_dump() for r in reports], "claims": [c.model_dump() for c in claims],
            "verification": ver.model_dump()}
