#!/usr/bin/env python3
"""
Cria um meeting-request JSON em memory/meeting-requests/.
Idempotente: se id existe, retorna existente sem sobrescrever.

Uso:
  python3 create_request.py --jid <jid> --name <contact> --snippet <snippet> \
    --modality <meet|presencial_office|presencial_outro|tbd> \
    --slack-ts <ts> --last-inbound-iso <iso>
"""
import os
import argparse, json, os, re, sys
from datetime import datetime, timezone

REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")


def slugify(s):
    s = (s or "x").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:30] or "x"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jid", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--snippet", required=True)
    ap.add_argument("--modality", default="meet",
                    choices=["meet", "presencial_office", "presencial_outro", "almoco", "brokered", "tbd"])
    ap.add_argument("--target-contact", default=None,
                    help="Em brokered: nome do terceiro com quem se quer reunir")
    ap.add_argument("--broker-mode", default=None,
                    choices=["proposes", "requests_options", None],
                    help="proposes = broker já propôs slots; requests_options = broker pede opções")
    ap.add_argument("--proposed-slots-text", default=None,
                    help="Em brokered+proposes: texto literal dos slots propostos pelo broker")
    ap.add_argument("--slack-channel", default=os.environ.get("SLACK_CHANNEL_ID", "C0XXXXXXXXX"))
    ap.add_argument("--slack-ts", default=None)
    ap.add_argument("--last-inbound-iso", required=True)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    date_slug = now.strftime("%Y%m%d")
    jid_tail = args.jid.split("@")[0][-12:]
    rid = f"{date_slug}-{slugify(args.name)}-{jid_tail}"
    path = os.path.join(REQUESTS_DIR, f"{rid}.json")

    if os.path.exists(path):
        print(json.dumps({"id": rid, "path": path, "created": False, "existing": True}))
        return

    req = {
        "id": rid,
        "contact": args.name,
        "jid": args.jid,
        "jid_alt": [],
        "phone_e164": None,
        "email": None,
        "detected_at_utc": now.isoformat(),
        "snippet_original": args.snippet[:500],
        "status": "pending_approval",
        "modality": args.modality,
        "target_contact": args.target_contact,
        "broker_mode": args.broker_mode,
        "proposed_slots_text": args.proposed_slots_text,
        "target_contact": args.target_contact,
        "broker_mode": args.broker_mode,
        "proposed_slots_text": args.proposed_slots_text,
        "duration_min": 60,
        "location_text": None,
        "last_outbound_message_id": None,
        "last_outbound_at_utc": None,
        "last_seen_reply_at_utc": args.last_inbound_iso,
        "processed_message_ids": [],
        "slots_offered": [],
        "expires_at_utc": None,
        "monitor_cron_id": None,
        "slack_thread_id": args.slack_ts,
        "slack_channel_id": args.slack_channel,
        "history": [{"at_utc": now.isoformat(), "note": "detector criou pending_approval"}],
    }

    os.makedirs(REQUESTS_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    print(json.dumps({"id": rid, "path": path, "created": True}))


if __name__ == "__main__":
    main()
