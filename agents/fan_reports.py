"""Gamified fan reporting — distributed crowd-sensing via attendees (lite).

What it does for hackathon demo:
  - A submission form (in-dashboard, simulating the would-be mobile app)
    lets fans flag CROWD_CONGESTION, SUSPICIOUS_ACTIVITY, LOST_ITEM,
    LOST_CHILD, MEDICAL, or INFRASTRUCTURE issues
  - Each report earns points weighted by category + verification status
  - High/critical reports get routed straight into the Commander
    incident pipeline, so attendees become a live sensor network
  - A leaderboard (badge tiers from Newcomer → Stadium Guardian) closes
    the engagement loop

Production version would back this with a real mobile app, server-side
verification (rate-limit + duplicate detection), and personalised
recommendations driven by user location and preferences.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORTS_FILE = DATA_DIR / "fan_reports.json"

# Points per category (verified reports get a 2x multiplier).
CATEGORY_POINTS = {
    "CROWD_CONGESTION": 10,
    "SUSPICIOUS_ACTIVITY": 25,
    "LOST_ITEM": 5,
    "LOST_CHILD": 30,
    "MEDICAL": 30,
    "INFRASTRUCTURE": 10,
    "OTHER": 5,
}

# Maps fan-report categories to the severity that the Commander uses.
CATEGORY_SEVERITY = {
    "CROWD_CONGESTION": "medium",
    "SUSPICIOUS_ACTIVITY": "high",
    "LOST_ITEM": "low",
    "LOST_CHILD": "critical",
    "MEDICAL": "critical",
    "INFRASTRUCTURE": "medium",
    "OTHER": "low",
}

# (min_points, badge_name)
_BADGE_TIERS = [
    (0, "Newcomer"),
    (25, "Spotter"),
    (75, "Sentinel"),
    (200, "Veteran"),
    (500, "Stadium Guardian"),
]


def _read_reports() -> list[dict]:
    if not REPORTS_FILE.exists():
        return []
    try:
        return json.loads(REPORTS_FILE.read_text())
    except Exception:
        return []


def _write_reports(reports: list[dict]) -> None:
    REPORTS_FILE.parent.mkdir(exist_ok=True)
    REPORTS_FILE.write_text(json.dumps(reports, indent=2, default=str))


def submit_report(
    reporter_id: str,
    category: str,
    zone: str,
    summary: str,
    verified: bool = False,
) -> dict:
    """Record a fan report. Routes high/critical ones to the Commander."""
    category = (category or "OTHER").upper()
    base = CATEGORY_POINTS.get(category, 5)
    points = base * (2 if verified else 1)
    record = {
        "report_id": f"FR-{int(time.time()*1000)}",
        "reporter_id": (reporter_id or "anon").strip()[:48],
        "category": category,
        "zone": (zone or "UNKNOWN").upper().strip()[:32],
        "summary": (summary or "").strip()[:280],
        "severity": CATEGORY_SEVERITY.get(category, "low"),
        "verified": bool(verified),
        "points_awarded": points,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    reports = _read_reports()
    reports.append(record)
    _write_reports(reports[-500:])  # keep store bounded

    if record["severity"] in ("high", "critical"):
        try:
            from agents import commander
            commander.log_incident({
                "type": record["category"],
                "severity": record["severity"],
                "zone": record["zone"],
                "summary": f"[fan-report] {record['summary']}",
                "source": "fan_report",
                "fan_report_id": record["report_id"],
            })
            record["routed_to_commander"] = True
        except Exception as e:
            record["routing_error"] = str(e)[:160]
    return record


def get_recent_reports(limit: int = 10) -> list[dict]:
    return list(reversed(_read_reports()))[:limit]


def get_leaderboard(top_n: int = 5) -> list[dict]:
    totals: dict[str, dict] = {}
    for r in _read_reports():
        rid = r.get("reporter_id", "anon")
        if rid not in totals:
            totals[rid] = {"reporter_id": rid, "points": 0, "reports": 0, "verified": 0}
        totals[rid]["points"] += r.get("points_awarded", 0)
        totals[rid]["reports"] += 1
        if r.get("verified"):
            totals[rid]["verified"] += 1
    ranked = sorted(totals.values(), key=lambda x: (-x["points"], -x["reports"]))
    for r in ranked:
        r["badge"] = _badge_for(r["points"])
    return ranked[:top_n]


def _badge_for(points: int) -> str:
    badge = "Newcomer"
    for threshold, name in _BADGE_TIERS:
        if points >= threshold:
            badge = name
    return badge


def stats() -> dict:
    reports = _read_reports()
    return {
        "total_reports": len(reports),
        "unique_reporters": len({r.get("reporter_id") for r in reports}),
        "verified_count": sum(1 for r in reports if r.get("verified")),
        "routed_count": sum(1 for r in reports if r.get("routed_to_commander")),
        "points_awarded_total": sum(r.get("points_awarded", 0) for r in reports),
    }
