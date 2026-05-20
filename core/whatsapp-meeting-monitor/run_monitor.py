#!/usr/bin/env python3
"""Monitor determinístico por contato — segue PLAYBOOK whatsapp-meeting-monitor.

Substitui o payload LLM-driven dos crons `monitor-meeting-<id>`. Sem LLM no loop:
lê o JSON, faz short-circuit em terminal/expired, chama check_replies_generic,
avança watermark, classifica replies via regex e dispara UMA das branches A-F.

Uso (no payload do cron):
  set -a && . /root/.openclaw/.env && set +a && \
  python3 /root/.openclaw/workspace/skills/whatsapp-meeting-monitor/run_monitor.py --id <request_id>

Saída em stdout: JSON com {ok, action, branch, request_id, dry_run, errors[]}.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib import request as urlrequest

WORKSPACE = os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")
SPAWN = f"{WORKSPACE}/skills/whatsapp-meeting-spawn"
REQUESTS_DIR = f"{WORKSPACE}/memory/meeting-requests"
CHECK_REPLIES = f"{SPAWN}/check_replies_generic.py"
UPDATE = f"{SPAWN}/update_request.py"
CREATE_INVITE = f"{SPAWN}/create_calendar_invite.py"

TERMINAL_STATUSES = {
    "invite_created",
    "cancelled",
    "declined",
    "expired",
    "user_handles",
}

CANCEL_PATTERNS = [
    r"\bn[aã]o vai dar\b",
    r"\bdeixa pra (?:pr[oó]xima|outra)\b",
    r"\bdesisti\b",
    r"\bcancela(?:r|do|ndo)?\b",
    r"\bn[aã]o consigo\b",
    r"\bn[aã]o vou conseguir\b",
    r"\bfica pra (?:outra|depois|pr[oó]xima)\b",
]

AFFIRM_PATTERNS = [
    r"^\s*(?:sim|topa|topo|pode|ok|okay|fechado|combinado|perfeito|beleza|show|isso|por mim|fechou|bora|vamos)\b",
]

SLOT_PICK_REGEX = [
    (re.compile(r"\b(?:sim\s+)?1\b|\bprimeira?\b|\bo\s*1\b|\bopt(?:ion|cao)\s*1\b", re.I), 0),
    (re.compile(r"\b(?:sim\s+)?2\b|\bsegunda?\b|\bo\s*2\b|\bopt(?:ion|cao)\s*2\b", re.I), 1),
    (re.compile(r"\b(?:sim\s+)?3\b|\bterceira?\b|\bo\s*3\b|\bopt(?:ion|cao)\s*3\b", re.I), 2),
]

EMAIL_REGEX = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")

ONLINE_PATTERNS = [r"\b(?:online|meet|video|v[ií]deo|call|chamada|remoto|zoom|teams)\b"]
PRESENCIAL_HIKER_PATTERNS = [r"\b(?:savassi|escrit[oó]rio|hiker)\b"]
PRESENCIAL_GENERIC_PATTERNS = [r"\bpresencial\b", r"\bpessoalmente\b"]

OUTRO_HORARIO_PATTERNS = [
    r"\boutra (?:op[cç][aã]o|data|hora|semana|possibilidade)\b",
    r"\boutro (?:dia|hor[aá]rio|momento)\b",
    r"\bnão (?:posso|consigo) (?:nesses|nessas|nesse|nessa)\b",
    r"\bn[aã]o (?:posso|consigo) nesses\b",
    r"\bdaria (?:amanh[aã]|segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo|\d{1,2}h)\b",
]

AMBIGUOUS_PATTERNS = [
    r"\bvou ver\b",
    r"\btalvez\b",
    r"\blembra de me cobrar\b",
    r"\bme cobra\b",
    r"\bmais tarde\b",
    r"\bdepois (?:te|eu) (?:falo|aviso|confirmo|retorno)\b",
    r"\bconfirmo depois\b",
]


def has_any(text: str, patterns) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in patterns)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value):
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def load_request(rid: str):
    path = os.path.join(REQUESTS_DIR, f"{rid}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def slack_post(channel: str, text: str, thread_ts=None, dry_run=False):
    if dry_run:
        return f"dry-run:{thread_ts or 'new'}"
    tokens = slack_tokens()
    if not tokens:
        raise RuntimeError("SLACK_BOT_TOKEN ausente")
    body = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = json.dumps(body).encode()
    last_error = None
    for token in tokens:
        req = urlrequest.Request(
            "https://slack.com/api/chat.postMessage",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        with urlrequest.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("ok"):
            return payload["ts"]
        last_error = payload
    raise RuntimeError(f"slack_post_failed: {last_error}")


def run_cmd(args, dry_run=False):
    """Wrapper subprocess. Em dry_run, só logga e retorna stub."""
    if dry_run:
        return {"dry_run": True, "cmd": args, "stdout": ""}
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"cmd_failed rc={out.returncode}: {' '.join(args)} :: stderr={out.stderr.strip()[:400]}")
    try:
        parsed = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        parsed = {"stdout": out.stdout.strip()}
    return parsed


def disable_cron(cron_id: str, dry_run=False):
    return run_cmd(["openclaw", "cron", "disable", cron_id], dry_run=dry_run)


def detect_slot_index(text: str, slots_offered):
    """Tenta achar qual slot (índice 0-based) o usuário escolheu."""
    if not slots_offered:
        return None
    lower = text.lower()
    for regex, idx in SLOT_PICK_REGEX:
        if idx >= len(slots_offered):
            continue
        if regex.search(lower):
            return idx
    for idx, slot in enumerate(slots_offered):
        label = (slot.get("label") or "").lower().strip()
        if not label:
            continue
        if label in lower:
            return idx
        parts = [p for p in re.split(r"[,\s]+", label) if len(p) > 2]
        if len(parts) >= 2 and all(p in lower for p in parts):
            return idx
    if len(slots_offered) == 1 and has_any(text, AFFIRM_PATTERNS):
        return 0
    return None


def detect_modality(text: str):
    """Retorna 'meet', 'presencial_hiker', 'presencial_outro' ou None."""
    if has_any(text, ONLINE_PATTERNS):
        return "meet", None
    if has_any(text, PRESENCIAL_HIKER_PATTERNS):
        return "presencial_hiker", None
    if has_any(text, PRESENCIAL_GENERIC_PATTERNS):
        loc_match = re.search(r"presencial\s+(?:em|no|na|aqui no|ali no|l[aá] no)\s+([\w\s\-]{3,40})", text.lower())
        return "presencial_outro", loc_match.group(1).strip() if loc_match else None
    return None, None


def truncate(text: str, n: int = 140) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 3] + "..."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="meeting-request id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    req = load_request(args.id)
    if req is None:
        print(json.dumps({"ok": False, "error": f"request {args.id} não encontrado", "branch": "missing"}))
        sys.exit(2)

    rid = args.id
    cron_id = req.get("monitor_cron_id")
    slack_channel = req.get("slack_channel_id") or os.environ.get("SLACK_CHANNEL_ID", "C0XXXXXXXXX")
    slack_thread = req.get("slack_thread_id")
    contact = req.get("contact", "?")
    status = (req.get("status") or "").strip().lower()
    expires_at = parse_iso(req.get("expires_at_utc"))
    modality = (req.get("modality") or "tbd").strip().lower()
    slots_offered = req.get("slots_offered") or []
    existing_email = req.get("email")

    errors = []

    # ───── SHORT-CIRCUIT TERMINAL ─────
    if status in TERMINAL_STATUSES:
        if cron_id:
            try:
                disable_cron(cron_id, dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"disable_cron failed: {e}")
        print(json.dumps({"ok": True, "branch": "terminal_short_circuit", "status": status, "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── EXPIRED ─────
    if expires_at and now_utc() > expires_at:
        try:
            run_cmd(["python3", UPDATE, "mark-terminal", "--id", rid, "--status", "expired", "--note", "past expires_at"], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"mark-terminal expired failed: {e}")
        if cron_id:
            try:
                disable_cron(cron_id, dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"disable_cron failed: {e}")
        try:
            slack_post(slack_channel, f"🔵 Monitor de *{contact}* expirou sem fechar. Self-disabled.", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack expired failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "expired", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── CHECK REPLIES ─────
    try:
        check_out = run_cmd(["python3", CHECK_REPLIES, "--id", rid], dry_run=False)  # sempre real: read-only
    except Exception as e:
        try:
            slack_post(slack_channel, f"⚠️ check_replies falhou para *{contact}*: {e}", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception:
            pass
        print(json.dumps({"ok": False, "branch": "check_replies_error", "request_id": rid, "errors": [str(e)]}))
        return 3

    if check_out.get("error"):
        try:
            slack_post(slack_channel, f"⚠️ check_replies falhou para *{contact}*: {check_out['error']}", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception:
            pass
        print(json.dumps({"ok": False, "branch": "check_replies_error", "request_id": rid, "errors": [check_out["error"]]}))
        return 3

    new_replies = check_out.get("newReplies") or []
    if not new_replies:
        print(json.dumps({"ok": True, "branch": "no_replies", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── ADVANCE WATERMARK ANTES DE QUALQUER ACTION ─────
    new_replies_sorted = sorted(new_replies, key=lambda r: r.get("iso") or "")
    latest_iso = new_replies_sorted[-1].get("iso")
    msg_ids = ",".join(r.get("messageId") for r in new_replies_sorted if r.get("messageId"))
    try:
        run_cmd(["python3", UPDATE, "advance-watermark", "--id", rid, "--reply-ts", latest_iso, "--message-ids", msg_ids], dry_run=args.dry_run)
    except Exception as e:
        errors.append(f"advance-watermark failed: {e}")

    combined_text = "\n".join((r.get("text") or "") for r in new_replies_sorted)
    snippet = truncate(combined_text, 200)

    # ───── BRANCH E — CANCELOU ─────
    if has_any(combined_text, CANCEL_PATTERNS):
        try:
            run_cmd(["python3", UPDATE, "mark-terminal", "--id", rid, "--status", "cancelled", "--note", snippet[:200]], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"mark-terminal cancelled failed: {e}")
        if cron_id:
            try:
                disable_cron(cron_id, dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"disable_cron failed: {e}")
        try:
            slack_post(slack_channel, f"🗑️ *{contact}* cancelou: _{snippet}_", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack cancelled failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "E_cancelled", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── RESOLVER MODALITY TBD ─────
    if modality == "tbd":
        new_modality, location = detect_modality(combined_text)
        if new_modality:
            cmd = ["python3", UPDATE, "set-modality", "--id", rid, "--modality", new_modality]
            if location:
                cmd += ["--location", location]
            try:
                run_cmd(cmd, dry_run=args.dry_run)
                modality = new_modality
            except Exception as e:
                errors.append(f"set-modality failed: {e}")
        elif EMAIL_REGEX.search(combined_text) or detect_slot_index(combined_text, slots_offered) is not None:
            try:
                run_cmd(["python3", UPDATE, "send-followup", "--id", rid, "--text", "Perfeito. Prefere online ou presencial? Se for presencial, onde?"], dry_run=args.dry_run)
                slack_post(slack_channel, f"💬 *{contact}* confirmou slot/email, pedi preferência online vs presencial.", thread_ts=slack_thread, dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"followup modality failed: {e}")
            print(json.dumps({"ok": not errors, "branch": "modality_followup", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
            return 0

    # ───── DETECTAR SLOT E EMAIL ─────
    slot_idx = detect_slot_index(combined_text, slots_offered)
    email_match = EMAIL_REGEX.search(combined_text)
    new_email = email_match.group(0) if email_match else None
    effective_email = existing_email or new_email

    # ───── BRANCH A — SLOT + EMAIL → INVITE ─────
    if slot_idx is not None and effective_email and modality != "tbd":
        try:
            run_cmd(["python3", UPDATE, "set-chosen-slot", "--id", rid, "--index", str(slot_idx)], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"set-chosen-slot failed: {e}")
        if new_email and new_email != existing_email:
            try:
                run_cmd(["python3", UPDATE, "set-email", "--id", rid, "--email", new_email], dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"set-email failed: {e}")
        try:
            invite_out = run_cmd(["python3", CREATE_INVITE, "--id", rid], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"create_invite failed: {e}")
            invite_out = {}
        if not errors:
            try:
                run_cmd(["python3", UPDATE, "mark-terminal", "--id", rid, "--status", "invite_created"], dry_run=args.dry_run)
            except Exception as e:
                errors.append(f"mark-terminal invite_created failed: {e}")
            if cron_id:
                try:
                    disable_cron(cron_id, dry_run=args.dry_run)
                except Exception as e:
                    errors.append(f"disable_cron failed: {e}")
        slot_label = slots_offered[slot_idx].get("label", f"slot {slot_idx + 1}") if slot_idx < len(slots_offered) else f"slot {slot_idx + 1}"
        invite_link = (invite_out or {}).get("invite_link") or "(link em meeting-requests JSON)"
        try:
            slack_post(slack_channel, f"✅ Invite criado com *{contact}* ({slot_label}). Link: {invite_link}", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack invite failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "A_invite_created", "request_id": rid, "slot_index": slot_idx, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── BRANCH B — SLOT SEM EMAIL ─────
    if slot_idx is not None and not effective_email:
        try:
            run_cmd(["python3", UPDATE, "set-chosen-slot", "--id", rid, "--index", str(slot_idx)], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"set-chosen-slot failed: {e}")
        try:
            run_cmd(["python3", UPDATE, "send-followup", "--id", rid, "--text", "Perfeito. Me manda teu email pra eu te mandar o invite."], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"send-followup email failed: {e}")
        slot_label = slots_offered[slot_idx].get("label", f"slot {slot_idx + 1}") if slot_idx < len(slots_offered) else f"slot {slot_idx + 1}"
        try:
            slack_post(slack_channel, f"💬 *{contact}* confirmou {slot_label}. Pedi o email.", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack B failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "B_slot_no_email", "request_id": rid, "slot_index": slot_idx, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── BRANCH C — EMAIL SEM SLOT ─────
    if new_email and slot_idx is None:
        try:
            run_cmd(["python3", UPDATE, "set-email", "--id", rid, "--email", new_email], dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"set-email failed: {e}")
        try:
            slack_post(slack_channel, f"💬 *{contact}* mandou email ({new_email}) mas ainda sem confirmar slot. Aguardando.", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack C failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "C_email_no_slot", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── BRANCH D — PEDIU OUTRO HORÁRIO ─────
    if has_any(combined_text, OUTRO_HORARIO_PATTERNS):
        try:
            slack_post(slack_channel, f"⚠️ *{contact}* pediu fora dos slots ofertados: _{snippet}_. Quer que eu proponha alternativas?", thread_ts=slack_thread, dry_run=args.dry_run)
        except Exception as e:
            errors.append(f"slack D failed: {e}")
        print(json.dumps({"ok": not errors, "branch": "D_outro_horario", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
        return 0

    # ───── BRANCH F — AMBÍGUO (fallback) ─────
    try:
        slack_post(slack_channel, f"💬 *{contact}* respondeu ambíguo: _{snippet}_. Quer que eu cobre ou aguarda?", thread_ts=slack_thread, dry_run=args.dry_run)
    except Exception as e:
        errors.append(f"slack F failed: {e}")
    print(json.dumps({"ok": not errors, "branch": "F_ambiguo", "request_id": rid, "dry_run": args.dry_run, "errors": errors}))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
