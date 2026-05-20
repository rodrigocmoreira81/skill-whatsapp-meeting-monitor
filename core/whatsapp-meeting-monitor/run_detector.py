#!/usr/bin/env python3
"""Deterministic WhatsApp meeting-request detector."""
import argparse
import json
import os
import re
import subprocess
import sys
from urllib import request

WORKSPACE = os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")
DETECT = f"{WORKSPACE}/skills/whatsapp-meeting-monitor/detect_meeting_requests.py"
CREATE = f"{WORKSPACE}/skills/whatsapp-meeting-monitor/create_request.py"
UPDATE = f"{WORKSPACE}/skills/whatsapp-meeting-monitor/update_detector_state.py"
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "C0XXXXXXXXX")

NEGATIVE_PATTERNS = [r"\bobrigad[oa]\b", r"\bvaleu\b", r"\bshow\b", r"\bentendi\b", r"\bok\b", r"\bbeleza\b"]
PERSONAL_PATTERNS = [r"\bamor\b", r"\bantonio\b", r"\btiago\b", r"\bmanuela\b", r"\bfamilia\b", r"\bfam[ií]lia\b", r"\bapto\b", r"\bapartamento\b", r"\bportaria\b"]
REQUEST_PATTERNS = [
    r"\bcomo est[aá] sua agenda\b", r"\bquando (?:vc|você) tem agenda\b", r"\btem agenda\b",
    r"\btem hor[aá]rio\b", r"\bqual hor[aá]rio\b", r"\bme passa .*op[cç][oõ]es\b",
    r"\bconsegue (?:uma )?(?:call|reuni[aã]o|video|vídeo)\b", r"\bpodemos (?:conversar|falar|marcar)\b",
    r"\bbora (?:um )?(?:caf[eé]|call|papo|almo[cç]o)\b", r"\bvamos (?:marcar|conversar|falar|almo[cç]ar)\b",
    r"\b(?:tomar|marcar|combinar|fazer) (?:um )?caf[eé]\b",
    r"\bcaf[eé]\b.*\b(?:conselho|conselhos|agenda|marcar|conversar|papo)\b",
    r"\bconselhos?\b.*\bcaf[eé]\b",
    r"\btopa (?:uma )?(?:call|conversa|caf[eé]|almo[cç]o)\b", r"\bmarcar (?:uma )?(?:call|reuni[aã]o|conversa|caf[eé]|almo[cç]o)\b",
    r"\bagendar (?:uma )?(?:call|reuni[aã]o|conversa|caf[eé]|almo[cç]o)\b", r"\balmo[cç]o\b.*\brola\b", r"\brola\b.*\balmo[cç]o\b",
]
BROKER_PATTERNS = [r"\bte apresentar\b", r"\bapresentar (?:o|a)\b", r"\bmarca(?:r)? com\b", r"\bagenda (?:pro|para o|com o|com a|pra)\b", r"\bquer te conhecer\b"]
SLOT_PATTERNS = [r"\bsegunda\b|\bterça\b|\bter[cç]a\b|\bquarta\b|\bquinta\b|\bsexta\b", r"\bamanh[aã]\b|\bhoje\b|\bsemana que vem\b", r"\b\d{1,2}h(?:\d{2})?\b", r"\b\d{1,2}:\d{2}\b", r"\bdia \d{1,2}\b"]
OUTBOUND_INVITE_CONFIRMATION_PATTERNS = [
    r"^\s*(?:almo[cç]o|caf[eé]|jantar|call|reuni[aã]o)\b.*\b(?:hoje|amanh[aã]|segunda|terça|ter[cç]a|quarta|quinta|sexta|\d{1,2}h|\d{1,2}:\d{2})\b",
]


def has_any(text, patterns):
    t = (text or "").lower()
    return any(re.search(p, t) for p in patterns)


def slack_tokens():
    tokens = []
    env_token = os.environ.get("SLACK_BOT_TOKEN")
    if env_token:
        tokens.append(env_token)
    try:
        with open(os.environ.get("OPENCLAW_CONFIG_PATH", "/root/.openclaw/openclaw.json")) as f:
            cfg = json.load(f)
    except Exception:
        return tokens

    stack = [cfg]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            candidate = cur.get("botToken")
            if isinstance(candidate, str) and candidate.startswith("xoxb-") and candidate not in tokens:
                tokens.append(candidate)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return tokens


def classify(candidate):
    msgs = [m for m in candidate.get("messages") or [] if (m.get("text") or "").strip()]
    if not msgs:
        return None
    joined = "\n".join(m["text"] for m in msgs)
    latest_text = msgs[-1]["text"].strip().replace("\n", " ")
    if "?" not in latest_text and has_any(latest_text, OUTBOUND_INVITE_CONFIRMATION_PATTERNS):
        return None
    hits = [m for m in msgs if has_any(m["text"], REQUEST_PATTERNS)]
    if not hits or has_any(joined, PERSONAL_PATTERNS):
        return None
    if len(msgs) == 1 and has_any(msgs[-1]["text"], NEGATIVE_PATTERNS):
        return None
    hit = hits[-1]
    snippet = hit["text"].strip().replace("\n", " ")
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    low = joined.lower()
    modality = "tbd"
    if any(x in low for x in ["almoço", "almoco", "almoçar", "almocar"]):
        modality = "almoco"
    elif any(x in low for x in ["call", "meet", "zoom", "online", "vídeo", "video"]):
        modality = "meet"
    elif any(x in low for x in ["savassi", "escritório", "escritorio", "hiker"]):
        modality = "presencial_hiker"
    broker_mode = None
    target_contact = None
    proposed_slots_text = None
    if has_any(joined, BROKER_PATTERNS):
        modality = "brokered"
        broker_mode = "proposes" if has_any(joined, SLOT_PATTERNS) else "requests_options"
        proposed_slots_text = snippet if broker_mode == "proposes" else ""
        m = re.search(r"(?:com|pro|para o|para a|pra)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç.-]*(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç.-]*){0,3})", joined)
        target_contact = m.group(1).strip() if m else "terceiro"
    return {
        "contact": candidate.get("pushName") or f"Contato {candidate.get('jid', '')[:4]}",
        "jid": candidate["jid"],
        "snippet": snippet,
        "modality": modality,
        "lastInboundIso": candidate.get("lastInboundIso") or hit.get("iso"),
        "target_contact": target_contact,
        "broker_mode": broker_mode,
        "proposed_slots_text": proposed_slots_text,
    }


def slack_post(text, dry_run=False):
    if dry_run:
        return "dry-run"
    tokens = slack_tokens()
    if not tokens:
        raise RuntimeError("SLACK_BOT_TOKEN ausente")
    body = json.dumps({"channel": SLACK_CHANNEL, "text": text}).encode()
    last_error = None
    for token in tokens:
        req = request.Request("https://slack.com/api/chat.postMessage", data=body, method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
        with request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        if data.get("ok"):
            return data["ts"]
        last_error = data
    raise RuntimeError(f"slack_post_failed: {last_error}")


def slack_message(item):
    if item["modality"] == "brokered":
        target = item.get("target_contact") or "terceiro"
        if item.get("broker_mode") == "proposes":
            return f"📨 *{item['contact']}* pediu agenda pra você com *{target}*.\n> _{item['snippet']}_\nHorários propostos pelo broker: {item.get('proposed_slots_text') or item['snippet']}\n\nResponda na thread:\n• sim ou sim 1 ou 👍 — confirma 1º horário proposto\n• sim 2 — confirma 2º horário\n• sim 3 — confirma 3º horário\n• outra ou 🔄 — Russ propõe alternativas pro broker\n• não ou 👎 — recusa"
        return f"📨 *{item['contact']}* pediu opções de agenda pra você com *{target}*.\n> _{item['snippet']}_\nBroker pede opções pra repassar.\n\nResponda na thread:\n• sim ou 👍 — Russ manda 3 slots pro broker repassar\n• não ou 👎 — recusa"
    return f"📨 *{item['contact']}* pediu agenda.\n> _{item['snippet']}_\nModalidade detectada: {item['modality']}\n\nResponda na thread (texto OU reação na mensagem):\n• sim ou 👍 — Meet 1h\n• sim hiker ou 🏢 — presencial Savassi 1h\n• sim 30min ou ⏱️ — Meet 30min\n• sim almoco ou 🍴 — almoço Moema/Maru\n• não ou 👎 — arquiva\n• eu marco ou 🙋 — você assume"


def create_request(item, slack_ts, dry_run=False):
    cmd = ["python3", CREATE, "--jid", item["jid"], "--name", item["contact"], "--snippet", item["snippet"], "--modality", item["modality"], "--slack-ts", slack_ts, "--last-inbound-iso", item["lastInboundIso"]]
    if item["modality"] == "brokered":
        cmd += ["--target-contact", item.get("target_contact") or "terceiro", "--broker-mode", item.get("broker_mode") or "requests_options", "--proposed-slots-text", item.get("proposed_slots_text") or ""]
    if dry_run:
        return {"dry_run": True, "cmd": cmd}
    return json.loads(subprocess.check_output(cmd, text=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor-hours", type=int, default=12)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--limit-per-page", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    det = subprocess.check_output(["python3", DETECT, "--floor-hours", str(args.floor_hours), "--max-pages", str(args.max_pages), "--limit-per-page", str(args.limit_per_page)], text=True)
    data = json.loads(det)
    if data.get("error"):
        print(json.dumps({"error": data["error"], "created": 0}, ensure_ascii=False))
        return 2
    positives = [item for item in (classify(c) for c in data.get("candidates") or []) if item]
    created, errors = [], []
    for item in positives:
        try:
            ts = slack_post(slack_message(item), dry_run=args.dry_run)
            created.append(create_request(item, ts, dry_run=args.dry_run))
        except Exception as exc:
            errors.append({"contact": item.get("contact"), "error": str(exc)})
    if not args.dry_run:
        subprocess.check_call(["python3", UPDATE, "--scan-at-iso", data["scan_at_iso"]])
    print(json.dumps({"ok": not errors, "scan_at_iso": data["scan_at_iso"], "candidate_count": data.get("candidateCount"), "positive_count": len(positives), "created_count": len(created), "created": created, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
