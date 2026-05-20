#!/usr/bin/env python3
"""
Orquestrador: busca slots → envia inicial → cria monitor cron → grava monitor_cron_id.

Pré-requisitos: meeting-requests/<id>.json com status=approved.

Uso:
  set -a && . /root/.openclaw/.env && set +a
  python3 spawn_monitor_cron.py --id <request_id>
"""
import os
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SKILL_DIR = "/root/.openclaw/workspace/skills/whatsapp-meeting-spawn"
REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")
TEMPLATE_PATH = os.path.join(SKILL_DIR, "monitor_payload_template.txt")
BRT = ZoneInfo("America/Sao_Paulo")


def slot_args_for_constraints(req):
    text = " ".join(str(req.get(k) or "") for k in ("snippet_original", "user_constraints")).lower()
    args = []
    if "junho" in text:
        now_brt = datetime.now(BRT)
        year = now_brt.year if now_brt.month <= 6 else now_brt.year + 1
        args += ["--not-before-date", f"{year}-06-01", "--days-ahead", "30"]
    return args


def load_req(rid):
    path = os.path.join(REQUESTS_DIR, f"{rid}.json")
    if not os.path.exists(path):
        return None, None
    return path, json.load(open(path))


def save_req(path, req):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(req, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def run_capture(cmd, **kwargs):
    res = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return res.returncode, res.stdout, res.stderr


def lookup_existing_email(contact):
    if not contact:
        return None
    cmd = ["gog", "gmail", "search", f'"{contact}"', "--max", "10", "--json", "--account", os.environ.get("OWNER_EMAIL", "")]
    rc, out, err = run_capture(cmd, timeout=45)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", contact) if len(t) > 2]
    for thread in data.get("threads") or []:
        sender = thread.get("from") or ""
        if tokens and not any(t in sender.lower() for t in tokens):
            continue
        m = re.search(r"<([^<>\s]+@[^<>\s]+)>", sender)
        if m and not m.group(1).lower().endswith(os.environ.get("OWNER_EMAIL_DOMAIN", "")):
            return m.group(1)
    return None


def hydrate_known_contact_data(path, req):
    if req.get("email"):
        return req
    email = lookup_existing_email(req.get("contact", ""))
    if not email:
        return req
    req["email"] = email
    req.setdefault("history", []).append({
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "note": f"email existente encontrado via gog gmail: {email}",
    })
    save_req(path, req)
    return req


def handle_brokered(args, path, req):
    """Lida com brokered. Retorna dict de saída pra imprimir."""
    import sys
    from urllib import request as urlreq, parse as urlparse, error as urlerr
    BASE = os.environ.get("EVOLUTION_BASE_URL", "")
    INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
    api_key = os.environ.get("EVOLUTION_API_KEY")
    if not api_key:
        return {"error": "EVOLUTION_API_KEY ausente"}

    import importlib.util
    spec = importlib.util.spec_from_file_location("send_initial", os.path.join(SKILL_DIR, "send_initial_whatsapp.py"))
    si = importlib.util.module_from_spec(spec); spec.loader.exec_module(si)

    broker_contact = req.get("contact", "")
    target_contact = req.get("target_contact") or "ele"
    broker_mode = req.get("broker_mode")
    broker_choice = req.get("broker_choice")
    proposed_text = req.get("proposed_slots_text") or ""

    text = None
    spawn_cron = False
    terminal_status = None

    if broker_mode == "proposes" and broker_choice and broker_choice.startswith("sim"):
        # Modo A + sim_N: confirma slot N proposto pelo broker
        # Parse slot do texto do broker_choice (sim_1, sim_2, sim_3, sim)
        idx = 1
        if "_" in broker_choice:
            try: idx = int(broker_choice.split("_")[1])
            except: idx = 1
        # Quebra proposed_slots_text em opções (separador: " ou ", " | ", ",")
        opts = re.split(r"\s+ou\s+|\s*\|\s*|,", proposed_text, flags=re.IGNORECASE)
        opts = [o.strip() for o in opts if o.strip()]
        chosen_label = opts[idx-1] if 0 < idx <= len(opts) else (opts[0] if opts else proposed_text[:80])
        text = si.compose_brokered_confirm(broker_contact, target_contact, chosen_label)
        terminal_status = "user_handles"
        req["chosen_slot_label_text"] = chosen_label
    elif broker_mode == "proposes" and broker_choice == "outra":
        # Modo A + outra: Russ propõe alternativas, cria cron leve
        slots_cmd = ["python3", os.path.join(SKILL_DIR, "check_calendar_slots.py"), "--duration", str(req.get("duration_min", 60)), "--n-slots", "3"] + slot_args_for_constraints(req)
        rc, out, err = run_capture(slots_cmd, timeout=90)
        if rc != 0:
            return {"error": f"check_calendar_slots: {err.strip()[:200]}"}
        slots_data = json.loads(out)
        if not slots_data.get("slots"):
            return {"error": "sem slots disponíveis"}
        text = si.compose_brokered_counter(broker_contact, target_contact, slots_data["slots"])
        req["slots_offered"] = slots_data["slots"]
        spawn_cron = True
        terminal_status = None  # fica monitoring
    elif broker_mode == "requests_options":
        # Modo B: Russ manda 3 opções pro broker repassar
        slots_cmd = ["python3", os.path.join(SKILL_DIR, "check_calendar_slots.py"), "--duration", str(req.get("duration_min", 60)), "--n-slots", "3"] + slot_args_for_constraints(req)
        rc, out, err = run_capture(slots_cmd, timeout=90)
        if rc != 0:
            return {"error": f"check_calendar_slots: {err.strip()[:200]}"}
        slots_data = json.loads(out)
        if not slots_data.get("slots"):
            return {"error": "sem slots disponíveis"}
        text = si.compose_brokered_options(broker_contact, target_contact, slots_data["slots"])
        req["slots_offered"] = slots_data["slots"]
        terminal_status = "user_handles"
    else:
        return {"error": f"brokered sem broker_mode/broker_choice valido: mode={broker_mode} choice={broker_choice}"}

    # Envia WhatsApp pro broker
    url = f"{BASE}/message/sendText/{urlparse.quote(INSTANCE)}"
    body = {"number": req["jid"], "text": text}
    rq = urlreq.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "apikey": api_key, "Content-Type": "application/json",
        "User-Agent": "russ-brokered/1.0",
    })
    try:
        with urlreq.urlopen(rq, timeout=30) as r:
            evo = json.loads(r.read().decode())
    except Exception as e:
        return {"error": f"Evolution send: {type(e).__name__}: {e}"}

    msg_id = (evo.get("key") or {}).get("id")
    now_iso = datetime.now(timezone.utc).isoformat()
    req["last_outbound_at_utc"] = now_iso
    req["last_outbound_message_id"] = msg_id

    if terminal_status:
        req["status"] = terminal_status
        req["completed_at_utc"] = now_iso
        req.setdefault("history", []).append({"at_utc": now_iso, "note": f"brokered terminal: {terminal_status} msgId={msg_id}"})
        save_req(path, req)
        return {"ok": True, "id": args.id, "modality": "brokered", "broker_mode": broker_mode, "status": terminal_status, "monitor_cron_id": None, "evolution_message_id": msg_id, "text_sent_preview": text[:120]}

    # spawn_cron == True (modo A + outra): cria cron leve usando template brokered
    template_path = os.path.join(SKILL_DIR, "monitor_payload_brokered_template.txt")
    if not os.path.exists(template_path):
        return {"error": f"template brokered ausente: {template_path}"}
    payload = open(template_path).read().replace("{REQUEST_ID}", args.id)
    req["status"] = "monitoring"
    from datetime import timedelta as _td
    last_slot = req["slots_offered"][-1]
    last_dt = datetime.fromisoformat(last_slot["end_utc"].replace("Z","+00:00"))
    req["expires_at_utc"] = (last_dt + _td(hours=36)).isoformat()
    req.setdefault("history", []).append({"at_utc": now_iso, "note": f"brokered counter-proposal enviada msgId={msg_id}"})
    save_req(path, req)

    name = f"monitor-brokered-{args.id}"[:60]
    desc = f"Monitor brokered counter-proposal {broker_contact} ({target_contact})"
    cron_cmd = [
        "openclaw", "cron", "add", "--name", name, "--description", desc,
        "--cron", "*/15 8-22 * * *", "--tz", "America/Sao_Paulo", "--exact",
        "--session", "isolated", "--model", "openai/gpt-5.5",
        "--timeout-seconds", "180", "--no-deliver", "--message", payload, "--json",
    ]
    rc, out, err = run_capture(cron_cmd, timeout=60)
    if rc != 0:
        return {"error": f"cron add: {err.strip()[:200]}"}
    json_start = out.find("{")
    cron_obj = json.loads(out[json_start:])
    cron_id = cron_obj.get("id")
    _, req = load_req(args.id)
    req["monitor_cron_id"] = cron_id
    req.setdefault("history", []).append({"at_utc": datetime.now(timezone.utc).isoformat(), "note": f"brokered monitor cron criado: {cron_id}"})
    save_req(path, req)
    return {"ok": True, "id": args.id, "modality": "brokered", "broker_mode": broker_mode, "status": "monitoring", "monitor_cron_id": cron_id, "evolution_message_id": msg_id, "text_sent_preview": text[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    if not os.environ.get("EVOLUTION_API_KEY") or not os.environ.get("GOG_KEYRING_PASSWORD"):
        print(json.dumps({"error": "EVOLUTION_API_KEY ou GOG_KEYRING_PASSWORD ausentes"})); sys.exit(2)

    path, req = load_req(args.id)
    if not req:
        print(json.dumps({"error": f"request {args.id} não existe"})); sys.exit(3)

    if req.get("status") != "approved":
        print(json.dumps({"error": f"status atual = {req.get('status')}, esperado 'approved'"})); sys.exit(4)

    req = hydrate_known_contact_data(path, req)

    # RAMIFICA pra brokered
    if req.get("modality") == "brokered":
        result = handle_brokered(args, path, req)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    modality = req.get("modality", "meet")
    duration = req.get("duration_min", 60)

    slots_cmd = ["python3", os.path.join(SKILL_DIR, "check_calendar_slots.py"), "--n-slots", "3"] + slot_args_for_constraints(req)
    if modality == "almoco":
        slots_cmd += ["--lunch"]
    else:
        slots_cmd += ["--duration", str(duration)]
    rc, out, err = run_capture(slots_cmd, timeout=90)
    if rc != 0:
        print(json.dumps({"error": f"check_calendar_slots falhou: {err.strip()[:300]}"})); sys.exit(5)
    slots_data = json.loads(out)
    if slots_data.get("error") or not slots_data.get("slots"):
        print(json.dumps({"error": f"sem slots disponíveis: {slots_data.get('error') or 'lista vazia'}"})); sys.exit(6)

    slots_file = f"/tmp/spawn-slots-{args.id}.json"
    with open(slots_file, "w") as f:
        json.dump(slots_data, f)
    send_cmd = ["python3", os.path.join(SKILL_DIR, "send_initial_whatsapp.py"), "--id", args.id, "--slots-json", slots_file]
    rc, out, err = run_capture(send_cmd, timeout=60)
    if rc != 0:
        print(json.dumps({"error": f"send_initial falhou: {err.strip()[:300]} | out: {out[:200]}"})); sys.exit(7)
    send_result = json.loads(out)
    if send_result.get("error"):
        print(json.dumps({"error": f"send_initial: {send_result['error']}"})); sys.exit(8)

    template = open(TEMPLATE_PATH).read()
    payload = template.replace("{REQUEST_ID}", args.id)
    payload_file = f"/tmp/spawn-payload-{args.id}.txt"
    with open(payload_file, "w") as f:
        f.write(payload)

    name = f"monitor-meeting-{args.id}"[:60]
    desc = f"Monitor WhatsApp agenda {req.get('contact','?')} ({modality})"
    cron_cmd = [
        "openclaw", "cron", "add",
        "--name", name, "--description", desc,
        "--cron", "*/15 8-22 * * *", "--tz", "America/Sao_Paulo", "--exact",
        "--session", "isolated", "--model", "openai/gpt-5.5",
        "--timeout-seconds", "300", "--no-deliver",
        "--message", payload, "--json",
    ]
    rc, out, err = run_capture(cron_cmd, timeout=60)
    if rc != 0:
        print(json.dumps({"error": f"cron add falhou: {err.strip()[:300]}"})); sys.exit(9)
    json_start = out.find("{")
    cron_obj = json.loads(out[json_start:])
    cron_id = cron_obj.get("id")
    if not cron_id:
        print(json.dumps({"error": f"cron add não retornou id: {out[:300]}"})); sys.exit(10)

    _, req = load_req(args.id)
    req["monitor_cron_id"] = cron_id
    req.setdefault("history", []).append({
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "note": f"spawn_monitor_cron OK: cron_id={cron_id} slots={len(slots_data['slots'])} sent_msg_id={send_result.get('evolution_message_id')}",
    })
    save_req(path, req)

    try: os.unlink(slots_file)
    except: pass
    try: os.unlink(payload_file)
    except: pass

    print(json.dumps({
        "ok": True, "id": args.id,
        "monitor_cron_id": cron_id,
        "slots_count": len(slots_data["slots"]),
        "evolution_message_id": send_result.get("evolution_message_id"),
        "expires_at_utc": req.get("expires_at_utc"),
        "status": req.get("status"),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
