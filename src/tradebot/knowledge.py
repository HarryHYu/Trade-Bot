"""The knowledge base: plain markdown in knowledge/, versioned by git. Types hold the frameworks and
lessons that generalise; securities hold their own profile, situation and lessons. Nothing is overwritten:
entries are appended with a date and a reason."""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from .config import get_settings

SECTIONS = {"situation": "situation.md", "lesson": "lessons.md", "profile": "profile.md"}


def _root() -> Path:
    return get_settings().knowledge_dir


def ensure_security(ticker: str, security_type: str) -> Path:
    """New security: copy its type's profile template into its own folder."""
    root = _root()
    sec = root / "securities" / ticker.upper()
    if not sec.exists():
        sec.mkdir(parents=True)
        tmpl = root / "types" / security_type / "profile-template.md"
        if not tmpl.exists():
            tmpl = root / "types" / "_default" / "profile-template.md"
        body = tmpl.read_text() if tmpl.exists() else "# Profile\n"
        (sec / "profile.md").write_text(body.replace("{{TICKER}}", ticker.upper()).replace("{{TYPE}}", security_type))
        (sec / "situation.md").write_text(f"# {ticker.upper()} situation\nForces currently acting on the company.\n\n")
        (sec / "lessons.md").write_text(f"# {ticker.upper()} lessons\n\n")
        (sec / "type").write_text(security_type)
    return sec


def append_lesson(ticker: str, security_type: str, section: str, text: str, why: str = "") -> None:
    sec = ensure_security(ticker, security_type)
    stamp = dt.date.today().isoformat()
    with (sec / SECTIONS[section]).open("a") as f:
        f.write(f"- [{stamp}] {text}" + (f"  \n  why: {why}" if why else "") + "\n")


def append_type_briefing(security_type: str, finding: str, why: str, evidence: str) -> None:
    d = _root() / "types" / security_type
    d.mkdir(parents=True, exist_ok=True)
    with (d / "briefing.md").open("a") as f:
        f.write(f"\n## [{dt.date.today().isoformat()}] {finding}\nwhy: {why}\nevidence: {evidence}\n")


def profile_context(ticker: str) -> str:
    sec = _root() / "securities" / ticker.upper()
    if not sec.exists():
        return f"(No profile yet for {ticker.upper()}.)"
    t = (sec / "type").read_text().strip() if (sec / "type").exists() else "_default"
    parts = []
    for name in ("profile.md", "situation.md", "lessons.md"):
        p = sec / name
        if p.exists():
            parts.append(p.read_text()[-6000:])
    brief = _root() / "types" / t / "briefing.md"
    if brief.exists():
        parts.append(f"TYPE BRIEFING ({t}):\n" + brief.read_text()[-6000:])
    return "\n\n".join(parts)


def commit(message: str) -> None:
    """Every knowledge change is a commit with its reason."""
    root = _root()
    try:
        subprocess.run(["git", "add", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message, "--", str(root)], capture_output=True)
    except Exception:
        pass
