#!/usr/bin/env python3
"""
Cria evento no Google Calendar ($OWNER_EMAIL) pra um meeting-request confirmado.

Lê meeting-requests/<id>.json (precisa: contact, email, modality, chosen_slot, duration_min, location_text se aplicavel).
Inclui $OFFICE_RECEPTION_EMAIL como convidado se modality=presencial_office.
Cria conferência Google Meet se modality=meet.

Atualiza JSON: event_id, invite_link, status=invite_created, completed_at_utc.

Uso:
  set -a && . /root/.openclaw/.env && set +a
  python3 create_calendar_invite.py --id <request_id>
"""
import os
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone

REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")
PRIMARY = os.environ.get("OWNER_EMAIL", "")
OFFICE_RECEPTION = os.environ.get("OFFICE_RECEPTION_EMAIL", "")
OFFICE_LOCATION = os.environ.get("OFFICE_ADDRESS", "")


def summary_for(modality, contact):
    if modality == "almoco":
        return f"Almoço {os.environ.get('OWNER_NAME', 'Owner')} <> {contact}"
    if modality == "presencial_office":
        return f"{os.environ.get('OWNER_NAME', 'Owner')} <> {contact} (office)"
    if modality == "presencial_outro":
        return f"{os.environ.get('OWNER_NAME', 'Owner')} <> {contact}"
    return f"{os.environ.get('OWNER_NAME', 'Owner')} <> {contact}"


def location_for(modality, location_text):
    if modality == "presencial_office":
        return OFFICE_LOCATION
    if modality in ("almoco", "presencial_outro"):
        return location_text or ""
    return ""  # meet: location vazia, Meet via conference


def description_for(modality, contact):
    # Sem descricao no invite — Rodrigo prefere sem rastro de automacao
    return ""


def attendees_for(modality, contact_email):
    a = [contact_email]
    if modality == "presencial_office":
        a.append(OFFICE_RECEPTION)
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    if not os.environ.get("GOG_KEYRING_PASSWORD"):
        print(json.dumps({"error": "GOG_KEYRING_PASSWORD ausente"})); sys.exit(2)

    path = os.path.join(REQUESTS_DIR, f"{args.id}.json")
    if not os.path.exists(path):
        print(json.dumps({"error": f"id {args.id} não existe"})); sys.exit(3)
    req = json.load(open(path))

    chosen = req.get("chosen_slot")
    if not chosen:
        print(json.dumps({"error": "chosen_slot não definido — chame update_request.py set-chosen-slot primeiro"})); sys.exit(4)
    email = req.get("email")
    if not email or "@" not in email:
        print(json.dumps({"error": "email do contato ausente ou inválido"})); sys.exit(5)

    modality = req.get("modality", "meet")
    contact = req.get("contact", "")
    summary = summary_for(modality, contact)
    location = location_for(modality, req.get("location_text"))
    description = description_for(modality, contact)
    attendees = attendees_for(modality, email)

    cmd = [
        "gog", "calendar", "create",
        PRIMARY,
        "--summary", summary,
        "--from", chosen["start_brt"],
        "--to", chosen["end_brt"],
        "--send-updates", "all",
        "--attendees", ",".join(attendees),
        "--json",
    ]
    if description:
        cmd += ["--description", description]
    if location:
        cmd += ["--location", location]
    if modality == "meet":
        cmd += ["--with-meet"]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        print(json.dumps({"error": f"gog create falhou: {res.stderr.strip()[:400]}", "cmd": " ".join(cmd)}))
        sys.exit(6)
    try:
        event = json.loads(res.stdout)
    except Exception as e:
        print(json.dumps({"error": f"resposta gog não-JSON: {res.stdout[:200]}"})); sys.exit(7)

    event_id = event.get("id")
    html_link = event.get("htmlLink")
    meet_link = (event.get("hangoutLink") or
                 ((event.get("conferenceData") or {}).get("entryPoints") or [{}])[0].get("uri"))

    now_iso = datetime.now(timezone.utc).isoformat()
    req["event_id"] = event_id
    req["invite_link"] = html_link
    req["meet_link"] = meet_link
    req["status"] = "invite_created"
    req["completed_at_utc"] = now_iso
    req.setdefault("history", []).append({
        "at_utc": now_iso,
        "note": f"invite criado event_id={event_id} attendees={attendees} meet={meet_link or '-'}",
    })

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    print(json.dumps({
        "ok": True,
        "id": args.id,
        "event_id": event_id,
        "invite_link": html_link,
        "meet_link": meet_link,
        "attendees": attendees,
        "modality": modality,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
