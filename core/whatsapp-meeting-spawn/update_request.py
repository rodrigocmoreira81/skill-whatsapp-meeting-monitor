#!/usr/bin/env python3
"""
Mutações controladas em meeting-requests/<id>.json. Sub-comandos:

  advance-watermark --id X --reply-ts <iso> --message-ids id1,id2
      Avança last_seen_reply_at_utc + adiciona ids em processed_message_ids.

  set-email --id X --email Y
  set-modality --id X --modality meet|presencial_office|presencial_outro [--location "..."]
  set-chosen-slot --id X --index N
  mark-terminal --id X --status <invite_created|cancelled|declined|expired|user_handles> --note "..."
  send-followup --id X --text "..."  (envia WhatsApp + grava last_outbound_*)

Todos idempotentes-friendly: registram entry em history.
"""
import os
import argparse, json, os, sys
from datetime import datetime, timezone
from urllib import request, parse, error

BASE = os.environ.get("EVOLUTION_BASE_URL", "")
INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")


def load(rid):
    path = os.path.join(REQUESTS_DIR, f"{rid}.json")
    if not os.path.exists(path):
        print(json.dumps({"error": f"id {rid} não existe"}))
        sys.exit(2)
    return path, json.load(open(path))


def save(path, req):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def hist(req, note):
    req.setdefault("history", []).append({"at_utc": now_iso(), "note": note})


def cmd_advance(args):
    path, req = load(args.id)
    if args.reply_ts:
        prev = req.get("last_seen_reply_at_utc") or ""
        if not prev or args.reply_ts > prev:
            req["last_seen_reply_at_utc"] = args.reply_ts
    if args.message_ids:
        ids = [x.strip() for x in args.message_ids.split(",") if x.strip()]
        existing = set(req.get("processed_message_ids") or [])
        new = [i for i in ids if i not in existing]
        req["processed_message_ids"] = list(existing) + new
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "last_seen_reply_at_utc": req.get("last_seen_reply_at_utc"), "processed_count": len(req.get("processed_message_ids") or [])}))


def cmd_set_email(args):
    path, req = load(args.id)
    req["email"] = args.email
    hist(req, f"email setado: {args.email}")
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "email": args.email}))


def cmd_set_modality(args):
    path, req = load(args.id)
    req["modality"] = args.modality
    if args.location:
        req["location_text"] = args.location
    hist(req, f"modality setada: {args.modality}" + (f" location={args.location}" if args.location else ""))
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "modality": args.modality, "location_text": req.get("location_text")}))


def cmd_set_chosen_slot(args):
    path, req = load(args.id)
    slots = req.get("slots_offered") or []
    if args.index < 0 or args.index >= len(slots):
        print(json.dumps({"error": f"index {args.index} fora dos {len(slots)} slots"}))
        sys.exit(3)
    req["chosen_slot_index"] = args.index
    req["chosen_slot"] = slots[args.index]
    hist(req, f"slot escolhido: {slots[args.index]['label']}")
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "chosen_slot": req["chosen_slot"]}))


def cmd_mark_terminal(args):
    path, req = load(args.id)
    req["status"] = args.status
    req["completed_at_utc"] = now_iso()
    hist(req, f"status terminal: {args.status}" + (f" — {args.note}" if args.note else ""))
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "status": args.status}))


def cmd_send_followup(args):
    path, req = load(args.id)
    api_key = os.environ.get("EVOLUTION_API_KEY")
    if not api_key:
        print(json.dumps({"error": "EVOLUTION_API_KEY ausente"}))
        sys.exit(4)
    url = f"{BASE}/message/sendText/{parse.quote(INSTANCE)}"
    body = {"number": req["jid"], "text": args.text}
    rq = request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "apikey": api_key, "Content-Type": "application/json",
        "User-Agent": "russ-meeting-monitor/1.0",
    })
    try:
        with request.urlopen(rq, timeout=30) as r:
            evo = json.loads(r.read().decode())
    except Exception as e:
        print(json.dumps({"error": f"Evolution send: {type(e).__name__}: {e}"}))
        sys.exit(5)
    mid = (evo.get("key") or {}).get("id")
    req["last_outbound_at_utc"] = now_iso()
    req["last_outbound_message_id"] = mid
    hist(req, f"followup enviado msgId={mid}: '{args.text[:80]}'")
    save(path, req)
    print(json.dumps({"ok": True, "id": args.id, "message_id": mid}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("advance-watermark")
    s1.add_argument("--id", required=True)
    s1.add_argument("--reply-ts", default=None, help="ISO da reply mais recente processada")
    s1.add_argument("--message-ids", default="", help="csv de IDs já tratados")
    s1.set_defaults(fn=cmd_advance)

    s2 = sub.add_parser("set-email")
    s2.add_argument("--id", required=True)
    s2.add_argument("--email", required=True)
    s2.set_defaults(fn=cmd_set_email)

    sm = sub.add_parser("set-modality")
    sm.add_argument("--id", required=True)
    sm.add_argument("--modality", required=True, choices=["meet", "presencial_office", "presencial_outro", "almoco", "tbd"])
    sm.add_argument("--location", default="")
    sm.set_defaults(fn=cmd_set_modality)

    s3 = sub.add_parser("set-chosen-slot")
    s3.add_argument("--id", required=True)
    s3.add_argument("--index", type=int, required=True)
    s3.set_defaults(fn=cmd_set_chosen_slot)

    s4 = sub.add_parser("mark-terminal")
    s4.add_argument("--id", required=True)
    s4.add_argument("--status", required=True,
                    choices=["invite_created", "cancelled", "declined", "expired", "user_handles"])
    s4.add_argument("--note", default="")
    s4.set_defaults(fn=cmd_mark_terminal)

    s5 = sub.add_parser("send-followup")
    s5.add_argument("--id", required=True)
    s5.add_argument("--text", required=True)
    s5.set_defaults(fn=cmd_send_followup)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
