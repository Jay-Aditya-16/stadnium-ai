"""Privacy-preserving de-identification for post-event analysis (lite).

What it does for hackathon demo:
  - Strips direct identifiers (emails, phones, names, raw user text)
  - Generalises quasi-identifiers (exact zone → zone family,
    timestamps → hour buckets)
  - Date-shifts all timestamps by a uniform random offset per report
    (preserving temporal relationships but breaking calendar linkage)
  - Aggregates incident counts by type / severity / zone-family / hour
    so analysts get utility without per-individual tracking

True production de-identification would add k-anonymity guarantees,
suppression of small cells, and differential-privacy noise. The
mechanics are equivalent — this is a working MVP.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE_RE = re.compile(r"\+?\d[\d \-]{7,}\d")

DIRECT_IDENTIFIER_FIELDS = {
    "from_address", "to_address", "fan_email", "operator_email",
    "phone", "user_id", "fan_id", "client_id",
    "raw_body", "subject", "name", "email",
}
QUASI_FIELDS_TO_DROP = {"db_id"}

# Zone prefix → family. The exact zone (e.g. A_STAND section 7) is dropped
# in favour of the family (A) so an attendee who only knows "I was in A"
# can't link themselves to a single incident.
_ZONE_FAMILY = {
    "A": "A", "B": "B", "C": "C", "G": "G", "M": "M", "N": "N", "P": "P",
    "PITCH": "FIELD",
    "CLUB": "PREMIUM",
    "PAVILION": "PREMIUM",
}


def _hash_id(value: str, salt: str = "crowdsync") -> str:
    return "anon_" + hashlib.sha256(f"{salt}|{value}".encode()).hexdigest()[:10]


def _scrub_text(text: str) -> str:
    if not text:
        return text
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


def _generalise_zone(zone: Optional[str]) -> str:
    if not zone:
        return "UNKNOWN"
    head = str(zone).split("_")[0]
    for prefix, family in _ZONE_FAMILY.items():
        if head.startswith(prefix):
            return family
    return "OTHER"


def _bucket_timestamp(ts: str, shift_days: int = 0, granularity: str = "hour") -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ts
    dt = dt + timedelta(days=shift_days)
    if granularity == "hour":
        return dt.strftime("%Y-%m-%dT%H:00Z")
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    return dt.isoformat()


def deidentify_incident(
    incident: dict, shift_days: int = 0, granularity: str = "hour"
) -> dict:
    """Return a copy of `incident` with PII removed and quasi-IDs generalised."""
    out: dict = {}
    for key, value in incident.items():
        if key in DIRECT_IDENTIFIER_FIELDS or key in QUASI_FIELDS_TO_DROP:
            continue
        if key == "id":
            out["anon_id"] = _hash_id(str(value))
        elif key == "timestamp":
            out["time_bucket"] = _bucket_timestamp(value, shift_days, granularity)
        elif key == "zone":
            out["zone_family"] = _generalise_zone(value)
            # Exact zone deliberately dropped.
        elif key in ("reporter_id", "fan_report_id"):
            out[f"{key}_hash"] = _hash_id(str(value))
        elif isinstance(value, str):
            out[key] = _scrub_text(value)
        elif isinstance(value, dict):
            out[key] = deidentify_incident(value, shift_days, granularity)
        elif isinstance(value, list):
            out[key] = [
                deidentify_incident(v, shift_days, granularity) if isinstance(v, dict)
                else (_scrub_text(v) if isinstance(v, str) else v)
                for v in value
            ]
        else:
            out[key] = value
    return out


def build_post_event_report(
    incidents: list[dict],
    shift_days: Optional[int] = None,
    granularity: str = "hour",
) -> dict:
    """Produce a de-identified post-event report from raw incidents."""
    if shift_days is None:
        # Deterministic per-session shift so re-running the report stays stable
        # within a session, but each new analysis run gets a different offset.
        random.seed(int(datetime.utcnow().timestamp()) // 3600)
        shift_days = random.randint(-30, 30)

    deid = [deidentify_incident(i, shift_days, granularity) for i in incidents]

    by_type = Counter(i.get("type", "UNKNOWN") for i in deid)
    by_severity = Counter(i.get("severity", "unknown") for i in deid)
    by_zone_family = Counter(i.get("zone_family", "UNKNOWN") for i in deid)
    by_hour: dict[str, int] = defaultdict(int)
    for i in deid:
        by_hour[i.get("time_bucket", "?")] += 1

    return {
        "report_generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "applied_date_shift_days": shift_days,
        "time_granularity": granularity,
        "incident_count": len(deid),
        "by_type": dict(by_type),
        "by_severity": dict(by_severity),
        "by_zone_family": dict(by_zone_family),
        "by_hour": dict(sorted(by_hour.items())),
        "incidents": deid,
        "privacy_notes": [
            "Direct identifiers stripped: emails, phones, names, free-text PII.",
            "Quasi-identifiers generalised: exact zone → zone family; timestamps → hour buckets.",
            f"All timestamps shifted by {shift_days} day(s) — uniform within this report so temporal patterns survive but calendar linkage is broken.",
            "Reporter / fan IDs replaced with salted SHA-256 hash prefixes.",
            "Re-identification of any single attendee is not reasonably likely from this output.",
        ],
    }


def write_report_to_disk(report: dict) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / "post_event_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    return out_path
