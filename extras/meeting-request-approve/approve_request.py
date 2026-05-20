#!/usr/bin/env python3
"""
Atualiza status de um meeting-request baseado na decisão do Rodrigo.
Idempotente: se status já não é pending_approval, retorna ok sem alterar.

Uso:
  python3 approve_request.py --id <id> --action <sim|nao|eu_marco> \
    [--modality meet|presencial_hiker|presencial_outro|tbd] \
    [--duration 30|60] [--user-msg "..."] [--free-text "..."]
"""
import os
import argparse, json, os, sys
from datetime import datetime, timezone

REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")

STATUS_MAP = {
    "sim": "approved",
    "nao": "declined",
    "eu_marco": "user_handles",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--action", required=True, choices=list(STATUS_MAP.keys()))
    ap.add_argument("--modality", default=None,
                    choices=["meet", "presencial_hiker", "presencial_outro", "almoco", "tbd"])
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--user-msg", default="")
    ap.add_argument("--free-text", default="",
                    help="texto livre adicional do Rodrigo (ex: 'só dia 28 à tarde')")
    ap.add_argument("--broker-choice", default=None,
                    help="Em modality=brokered: sim_1|sim_2|sim_3|sim|outra|nao")
    args = ap.parse_args()

    path = os.path.join(REQUESTS_DIR, f"{args.id}.json")
    if not os.path.exists(path):
        print(json.dumps({"error": f"id {args.id} não encontrado", "path": path}))
        sys.exit(2)

    req = json.load(open(path))
    current = req.get("status")
    if current != "pending_approval":
        print(json.dumps({
            "ok": True,
            "id": args.id,
            "status": current,
            "note": "status já não era pending_approval, nada alterado",
        }))
        return

    now = datetime.now(timezone.utc).isoformat()
    new_status = STATUS_MAP[args.action]
    req["status"] = new_status
    req["approved_at_utc"] = now

    if args.action == "sim":
        if args.modality:
            req["modality"] = args.modality
        req["duration_min"] = args.duration
        if args.free_text:
            req["user_constraints"] = args.free_text[:500]
            if args.modality == "presencial_outro":
                req["location_text"] = args.free_text[:500]
        if args.broker_choice:
            req["broker_choice"] = args.broker_choice
        if args.broker_choice:
            req["broker_choice"] = args.broker_choice

    history_note = (
        f"Rodrigo respondeu: action={args.action}"
        + (f" modality={args.modality}" if args.modality else "")
        + (f" duration={args.duration}min" if args.action == "sim" else "")
        + (f" free='{args.free_text[:80]}'" if args.free_text else "")
        + (f" msg='{args.user_msg[:120]}'" if args.user_msg else "")
    )
    req.setdefault("history", []).append({"at_utc": now, "note": history_note})

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    print(json.dumps({
        "ok": True,
        "id": args.id,
        "status": new_status,
        "contact": req.get("contact"),
        "jid": req.get("jid"),
        "modality": req.get("modality"),
        "duration_min": req.get("duration_min"),
        "user_constraints": req.get("user_constraints"),
    }))


if __name__ == "__main__":
    main()
