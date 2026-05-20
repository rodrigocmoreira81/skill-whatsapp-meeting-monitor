#!/usr/bin/env python3
"""
Check replies genérico — parametrizado por --id <request_id>.
Segue o PLAYBOOK: since=last_seen_reply_at_utc, filtra processed_message_ids, jids = jid + jid_alt.

Saída: JSON {error, id, since_iso, jids_checked, count, newReplies[]}.
NÃO atualiza estado — quem decide é o cron payload (chama update_request.py advance-watermark depois).
"""
import os
import argparse, json, os, sys
from datetime import datetime, timezone, timedelta
from urllib import request, parse, error

BASE = os.environ.get("EVOLUTION_BASE_URL", "")
INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")
FLOOR_HOURS = 24  # safety se last_seen_reply_at_utc estiver vazio


def post(path, body, api_key, timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode()
    req = request.Request(url, data=data, method="POST", headers={
        "apikey": api_key,
        "Content-Type": "application/json",
        "User-Agent": "russ-meeting-monitor/1.0",
    })
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def extract_text(msg_obj):
    msg = msg_obj.get("message") or {}
    return (
        msg.get("conversation")
        or (msg.get("extendedTextMessage") or {}).get("text")
        or (msg.get("imageMessage") or {}).get("caption")
        or (msg.get("videoMessage") or {}).get("caption")
        or ""
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("EVOLUTION_API_KEY")
    if not api_key:
        print(json.dumps({"error": "EVOLUTION_API_KEY ausente", "newReplies": [], "count": 0}))
        sys.exit(2)

    path = os.path.join(REQUESTS_DIR, f"{args.id}.json")
    try:
        req = json.load(open(path))
    except Exception as e:
        print(json.dumps({"error": f"falha lendo {path}: {e}", "newReplies": [], "count": 0}))
        sys.exit(3)

    jids = [req.get("jid")] + list(req.get("jid_alt") or [])
    jids = [j for j in jids if j]
    processed = set(req.get("processed_message_ids") or [])

    last_seen = req.get("last_seen_reply_at_utc") or req.get("last_outbound_at_utc")
    if last_seen:
        if last_seen.endswith("Z"):
            last_seen = last_seen[:-1] + "+00:00"
        since_dt = datetime.fromisoformat(last_seen)
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(hours=FLOOR_HOURS)
    since_ts = int(since_dt.timestamp())

    api_path = f"/chat/findMessages/{parse.quote(INSTANCE)}"
    replies = []
    errors = []
    for jid in jids:
        try:
            resp = post(api_path, {
                "where": {"key": {"remoteJid": jid, "fromMe": False}},
                "limit": 50,
            }, api_key)
        except error.HTTPError as e:
            errors.append(f"{jid}: HTTP {e.code} {e.reason}")
            continue
        except Exception as e:
            errors.append(f"{jid}: {type(e).__name__}: {e}")
            continue
        for m in resp.get("messages", {}).get("records", []):
            ts = m.get("messageTimestamp") or 0
            if ts <= since_ts:
                continue
            key = m.get("key") or {}
            mid = key.get("id")
            if mid and mid in processed:
                continue
            replies.append({
                "jid": jid,
                "timestamp": ts,
                "iso": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "messageId": mid,
                "text": extract_text(m),
            })

    # dedupe por messageId
    seen, deduped = set(), []
    for r in sorted(replies, key=lambda x: x["timestamp"]):
        if r["messageId"] in seen:
            continue
        seen.add(r["messageId"])
        deduped.append(r)

    print(json.dumps({
        "error": "; ".join(errors) if errors else None,
        "id": args.id,
        "since_iso": since_dt.isoformat(),
        "jids_checked": jids,
        "count": len(deduped),
        "newReplies": deduped,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
