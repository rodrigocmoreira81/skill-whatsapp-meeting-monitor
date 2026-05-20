#!/usr/bin/env python3
"""Resumo matinal de meeting-requests pra cron `meeting-requests-morning-reminder`.

Lê memory/meeting-requests/*.json e emite JSON em stdout com:
  - stale_pending[]: status=pending_approval com detected_at_utc > 12h atrás
  - monitoring[]:    status em {approved, monitoring}
  - terminal_recent[]: status terminal com completed_at_utc nas últimas 24h
  - counts: contadores dos três grupos
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REQUESTS_DIR = Path(os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests"))
STALE_AFTER_HOURS = 12
TERMINAL_LOOKBACK_HOURS = 24
TERMINAL_STATUSES = {"invite_created", "cancelled", "declined", "expired"}
ACTIVE_STATUSES = {"approved", "monitoring"}


def parse_iso(value):
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def load_requests():
    out = []
    if not REQUESTS_DIR.is_dir():
        return out
    for path in sorted(REQUESTS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            print(f"warn: failed to read {path.name}: {e}", file=sys.stderr)
    return out


def classify(now, req):
    raw_status = req.get("status")
    status = raw_status.strip().lower() if isinstance(raw_status, str) else raw_status
    contact = req.get("contact") or "?"
    modality = req.get("modality") or "meet"

    if status == "pending_approval":
        detected = parse_iso(req.get("detected_at_utc"))
        if detected is None:
            return None, None
        age = now - detected
        if age < timedelta(hours=STALE_AFTER_HOURS):
            return None, None
        snippet = (req.get("snippet_original") or "").strip().replace("\n", " ")
        if len(snippet) > 140:
            snippet = snippet[:137] + "..."
        return "stale_pending", {
            "contact": contact,
            "modality": modality,
            "snippet": snippet,
            "age_hours": round(age.total_seconds() / 3600, 1),
            "slack_thread_id": req.get("slack_thread_id"),
        }

    if status in ACTIVE_STATUSES:
        return "monitoring", {
            "contact": contact,
            "modality": modality,
            "expires_at_utc": req.get("expires_at_utc"),
        }

    if status in TERMINAL_STATUSES:
        completed = parse_iso(req.get("completed_at_utc"))
        if completed is None:
            return None, None
        if (now - completed) > timedelta(hours=TERMINAL_LOOKBACK_HOURS):
            return None, None
        return "terminal_recent", {
            "contact": contact,
            "status": status,
        }

    return None, None


def main():
    now = datetime.now(timezone.utc)
    buckets = {"stale_pending": [], "monitoring": [], "terminal_recent": []}
    for req in load_requests():
        bucket, item = classify(now, req)
        if bucket and item is not None:
            buckets[bucket].append(item)

    buckets["stale_pending"].sort(key=lambda x: -x["age_hours"])
    buckets["monitoring"].sort(key=lambda x: x.get("expires_at_utc") or "")
    buckets["terminal_recent"].sort(key=lambda x: x["contact"])

    out = {
        **buckets,
        "counts": {
            "stale_count": len(buckets["stale_pending"]),
            "monitoring_count": len(buckets["monitoring"]),
            "terminal_recent_count": len(buckets["terminal_recent"]),
        },
        "generated_at_utc": now.isoformat(),
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
