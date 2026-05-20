#!/usr/bin/env python3
"""
Envia a mensagem inicial de proposta de agenda pro contato via Evolution.
Lê meeting-requests/<id>.json. Lê slots-json (output de check_calendar_slots.py).
Atualiza last_outbound_*, slots_offered, expires_at_utc, location_text (almoço), history.

NÃO envia se --dry-run.

Uso:
  set -a && . /root/.openclaw/.env && set +a
  python3 send_initial_whatsapp.py --id <request_id> --slots-json <file_or_-> [--dry-run]
"""
import os
import argparse, json, os, random, re, sys
from datetime import datetime, timezone, timedelta
from urllib import request, parse, error

BASE = os.environ.get("EVOLUTION_BASE_URL", "")
INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")
LUNCH_VENUES = [  # Adapte aos seus locais preferidos
    ("Local A", "Nome do Local A"),
    ("Local B", "Nome do Local B"),
]


def first_name(s):
    return (s or "").split()[0].strip() if s else "tudo bem"


def recent_outbound_texts(jid, api_key, limit=12):
    try:
        url = f"{BASE}/chat/findMessages/{parse.quote(INSTANCE)}"
        body = {"where": {"key": {"remoteJid": jid, "fromMe": True}}, "limit": limit}
        req = request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "apikey": api_key,
            "Content-Type": "application/json",
            "User-Agent": "russ-meeting-spawn-context/1.0",
        })
        with request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode())
    except Exception:
        return []
    out = []
    for m in resp.get("messages", {}).get("records", []):
        msg = m.get("message") or {}
        text = (
            msg.get("conversation")
            or (msg.get("extendedTextMessage") or {}).get("text")
            or (msg.get("imageMessage") or {}).get("caption")
            or ""
        )
        if text:
            out.append(text)
    return out


def infer_greeting_from_history(texts):
    for text in texts:
        lower = (text or "").lower()
        if "bora marcar" in lower and "tenho essas opcoes" in lower:
            continue
        m = re.match(r"\s*(ei|fala|oi|ol[aá])\b", text or "", re.IGNORECASE)
        if m:
            token = m.group(1).lower()
            return "Olá" if token in ("ola", "olá") else token.capitalize()
    return None


def greeting_for(contact, jid=None, api_key=None):
    name = first_name(contact)
    if jid and api_key:
        inferred = infer_greeting_from_history(recent_outbound_texts(jid, api_key))
        if inferred:
            return f"{inferred} {name}"
    female_first_names = {
        "rafa", "rafaela", "ana", "maria", "julia", "juliana", "mariana", "carolina",
        "carla", "fernanda", "laura", "luiza", "beatriz", "bianca", "camila", "thais",
        "bruna", "renata", "patricia", "leticia", "gabriela", "giovanna",
    }
    return f"Ei {name}" if name.lower() in female_first_names else f"Fala {name}"


def email_suffix(email_known):
    return "Te mando o invite." if email_known else "Me passa teu email pra eu te mandar invite com link."


def compose(contact, modality, slots, location_text=None, location_short=None, email_known=False, greeting=None):
    greet = greeting or f"Fala {first_name(contact)}"
    slot_lines = "\n".join(f"- {s['label']}" for s in slots)

    if modality == "almoco":
        local = location_short or location_text or LUNCH_VENUES[0][0]
        return (
            f"{greet},\n\n"
            f"Bora almocar. Tenho essas opcoes:\n"
            f"{slot_lines}\n\n"
            f"Alguma funciona? Sugiro {local}. Me confirma qual dia e te mando o invite."
        )
    if modality == "presencial_office":
        return (
            f"{greet},\n\n"
            f"Bora marcar. Tenho essas opcoes:\n"
            f"{slot_lines}\n\n"
            f"Funciona aqui no escritorio? {email_suffix(email_known)}"
        )
    if modality == "presencial_outro":
        if location_text:
            return (
                f"{greet},\n\n"
                f"Bora marcar. Tenho essas opcoes:\n"
                f"{slot_lines}\n\n"
                f"Alguma funciona? Sugiro {location_text}. {email_suffix(email_known)}"
            )
        return (
            f"{greet},\n\n"
            f"Bora marcar. Tenho essas opcoes:\n"
            f"{slot_lines}\n\n"
            f"Alguma funciona? Me diz onde vc prefere. {email_suffix(email_known)}"
        )
    if modality == "tbd":
        suffix = "Te mando o invite." if email_known else "Me manda teu email tambem pra eu te enviar o invite."
        return (
            f"{greet},\n\n"
            f"Bora marcar em junho. Tenho essas opcoes:\n"
            f"{slot_lines}\n\n"
            f"Alguma funciona? Pode ser online ou presencial; se for presencial, me diz onde vc prefere. "
            f"{suffix}"
        )
    # meet (default)
    return (
        f"{greet},\n\n"
        f"Bora marcar. Tenho essas opcoes:\n"
        f"{slot_lines}\n\n"
        f"Alguma funciona? Te mando invite com link."
    )




def compose_brokered_confirm(broker_contact, target_contact, chosen_slot_label):
    """Modo A + sim_N: Russ confirma slot escolhido pelo broker."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    return (
        f"Fala {name},\n\n"
        f"Fechado, fica {chosen_slot_label} com o {target}. Manda invite por favor."
    )


def compose_brokered_counter(broker_contact, target_contact, slots):
    """Modo A + outra: Russ propõe alternativas pro broker."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    slot_lines = "\n".join(f"- {s['label']}" for s in slots)
    return (
        f"Fala {name},\n\n"
        f"Os horarios que vc me passou nao deram. Tenho essas opcoes pro {target}:\n"
        f"{slot_lines}\n\n"
        f"Olha com ele qual prefere?"
    )


def compose_brokered_options(broker_contact, target_contact, slots):
    """Modo B: Russ manda opções iniciais pro broker repassar."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    slot_lines = "\n".join(f"- {s['label']}" for s in slots)
    return (
        f"Fala {name},\n\n"
        f"Tenho essas opcoes pro {target}:\n"
        f"{slot_lines}\n\n"
        f"Manda pra ele e me confirma qual fica. Pode marcar com o invite por favor."
    )




def compose_brokered_confirm(broker_contact, target_contact, chosen_slot_label):
    """Modo A + sim_N: Russ confirma slot escolhido pelo broker."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    return (
        f"Fala {name},\n\n"
        f"Fechado, fica {chosen_slot_label} com o {target}. Manda invite por favor."
    )


def compose_brokered_counter(broker_contact, target_contact, slots):
    """Modo A + outra: Russ propõe alternativas pro broker."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    slot_lines = "\n".join(f"- {s['label']}" for s in slots)
    return (
        f"Fala {name},\n\n"
        f"Os horarios que vc me passou nao deram. Tenho essas opcoes pro {target}:\n"
        f"{slot_lines}\n\n"
        f"Olha com ele qual prefere?"
    )


def compose_brokered_options(broker_contact, target_contact, slots):
    """Modo B: Russ manda opções iniciais pro broker repassar."""
    name = first_name(broker_contact)
    target = target_contact or "ele"
    slot_lines = "\n".join(f"- {s['label']}" for s in slots)
    return (
        f"Fala {name},\n\n"
        f"Tenho essas opcoes pro {target}:\n"
        f"{slot_lines}\n\n"
        f"Manda pra ele e me confirma qual fica. Pode marcar com o invite por favor."
    )


def send_text(jid, text, api_key):
    url = f"{BASE}/message/sendText/{parse.quote(INSTANCE)}"
    body = {"number": jid, "text": text}
    req = request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "apikey": api_key,
        "Content-Type": "application/json",
        "User-Agent": "russ-meeting-spawn/1.0",
    })
    with request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--slots-json", required=True, help="path ou '-' para stdin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = os.path.join(REQUESTS_DIR, f"{args.id}.json")
    if not os.path.exists(path):
        print(json.dumps({"error": f"request {args.id} nao existe"})); sys.exit(2)
    req = json.load(open(path))

    if args.slots_json == "-":
        slots_data = json.load(sys.stdin)
    else:
        slots_data = json.load(open(args.slots_json))
    slots = slots_data.get("slots") or []
    if not slots:
        print(json.dumps({"error": "nenhum slot recebido"})); sys.exit(3)

    # Para almoço: escolher local random uma única vez (idempotente após gravar)
    if req.get("modality") == "almoco" and not req.get("location_text"):
        short, full = random.choice(LUNCH_VENUES)
        req["location_short"] = short
        req["location_text"] = full

    text = compose(
        contact=req.get("contact", ""),
        modality=req.get("modality", "meet"),
        slots=slots,
        location_text=req.get("location_text"),
        location_short=req.get("location_short"),
        email_known=bool(req.get("email")),
        greeting=greeting_for(req.get("contact", ""), req.get("jid"), os.environ.get("EVOLUTION_API_KEY")),
    )

    result = {
        "id": args.id,
        "contact": req.get("contact"),
        "jid": req.get("jid"),
        "modality": req.get("modality"),
        "duration_min": req.get("duration_min"),
        "location_text": req.get("location_text"),
        "composed_text": text,
        "dry_run": args.dry_run,
    }

    if args.dry_run:
        print(json.dumps(result, indent=2, ensure_ascii=False)); return

    api_key = os.environ.get("EVOLUTION_API_KEY")
    if not api_key:
        print(json.dumps({"error": "EVOLUTION_API_KEY ausente"})); sys.exit(4)

    try:
        evo_resp = send_text(req["jid"], text, api_key)
    except error.HTTPError as e:
        print(json.dumps({"error": f"Evolution HTTP {e.code}: {e.reason}", "id": args.id})); sys.exit(5)
    except Exception as e:
        print(json.dumps({"error": f"Evolution send: {type(e).__name__}: {e}", "id": args.id})); sys.exit(6)

    now_utc = datetime.now(timezone.utc).isoformat()
    msg_id = (evo_resp.get("key") or {}).get("id")
    last_slot_iso = slots[-1].get("end_utc") or slots[-1].get("end_brt")
    if last_slot_iso:
        last_dt = datetime.fromisoformat(last_slot_iso.replace("Z","+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        expires_at = (last_dt + timedelta(hours=36)).isoformat()
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

    req["last_outbound_at_utc"] = now_utc
    req["last_outbound_message_id"] = msg_id
    req["slots_offered"] = slots
    req["expires_at_utc"] = expires_at
    req["status"] = "monitoring"
    req.setdefault("history", []).append({
        "at_utc": now_utc,
        "note": f"send_initial enviado msgId={msg_id} slots={len(slots)} expira={expires_at}",
    })
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    result.update({"sent": True, "evolution_message_id": msg_id, "expires_at_utc": expires_at, "status_after": "monitoring"})
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
