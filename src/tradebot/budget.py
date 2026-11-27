"""Daily API allowance, split between the pipeline's stages so no stage can starve the others.
The month's budget (all key credit minus a reserve, or PIPELINE_BUDGET_USD_MONTH) is spread evenly over the days left;
each group gets a share of today's allowance. Triage, chat and alerts are never blocked here (the hard monthly cap
in llm.py still applies)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func

from .config import get_settings
from .db import ApiUsage, select, session, utcnow

# Each group's share of the daily allowance, and the ApiUsage.purpose prefixes that count against it.
GROUPS: dict[str, tuple[float, tuple[str, ...]]] = {
    "explore": (0.30, ("explore", "dossier", "direct")),
    "evaluate": (0.07, ("evaluate",)),
    "research": (0.48, ("research", "thesis_extract", "diligence", "earnings")),
    "review": (0.08, ("post_mortem", "weekly_review", "collate")),
}


def _spent(since: dt.datetime, prefixes: tuple[str, ...] | None = None) -> float:
    from sqlalchemy import or_
    q = select(func.sum(ApiUsage.cost_usd)).where(ApiUsage.ts >= since)
    if prefixes is not None:
        q = q.where(or_(*[ApiUsage.purpose.like(f"{p}%") for p in prefixes]))
    with session() as db:
        return float(db.scalar(q) or 0)


def daily_allowance() -> float:
    s = get_settings()
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start + dt.timedelta(days=32)).replace(day=1)
    days_left = max((next_month - day_start).days, 1)
    spent_before_today = _spent(month_start) - _spent(day_start)
    return max(s.pipeline_budget - spent_before_today, 0.0) / days_left


def left(group: str | None = None) -> float:
    """Dollars left today for a group (or overall when group is None)."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = daily_allowance()
    if group is None:
        return total - _spent(day_start)
    share, purposes = GROUPS[group]
    # Unused share rolls over to other groups late in the day: never block a group while the day as a whole has room
    # and its own share is not exhausted.
    return min(total * share - _spent(day_start, purposes), total - _spent(day_start))


def status() -> str:
    lines = [f"Today's allowance ${daily_allowance():.2f}, left ${left():.2f}"]
    for g in GROUPS:
        lines.append(f"  {g}: ${left(g):.2f} left")
    return "\n".join(lines)
