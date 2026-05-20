#!/usr/bin/env python3
"""
Detector de pedidos de agenda no WhatsApp.

Lê meeting-detector-state.json (last_scan_at_utc, floor 12h se ausente).
Lê meeting-requests/*.json pra saber quais JIDs já têm request ativo (skip).
Consulta Evolution API por mensagens recebidas (fromMe=false) desde o watermark.
Exclui grupos (@g.us) e JIDs com request ativo.
Agrupa por JID e devolve as últimas N msgs de cada candidato pra o LLM do cron classificar.

Saída em stdout: JSON com {error, since_iso, scan_at_iso, candidates[], skipped_active_jids[]}.
NÃO atualiza state nem cria arquivos — quem decide é o cron payload.

Uso:
  set -a && . /root/.openclaw/.env && set +a
  python3 detect_meeting_requests.py [--max-msgs-per-jid 8] [--max-pages 30] [--limit-per-page 200]
"""
import os
import argparse, glob, json, os, sys, time
from datetime import datetime, timezone, timedelta
from urllib import request, parse, error

BASE = os.environ.get("EVOLUTION_BASE_URL", "")
INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
STATE_PATH = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-detector-state.json")
REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")
DEFAULT_FLOOR_HOURS = 12
ACTIVE_STATUSES = {"pending_approval", "approved", "monitoring"}


def post(path, body, api_key, timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode()
    req = request.Request(url, data=data, method="POST", headers={
        "apikey": api_key,
        "Content-Type": "application/json",
        "User-Agent": "russ-meeting-detector/1.0",
    })
    with request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse_iso(s):
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return int(datetime.fromisoformat(s).timestamp())


def read_state():
    try:
        return json.load(open(STATE_PATH))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def active_jids():
    skipped = set()
    for path in glob.glob(os.path.join(REQUESTS_DIR, "*.json")):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            req = json.load(open(path))
        except Exception:
            continue
        if req.get("status") in ACTIVE_STATUSES:
            for j in [req.get("jid")] + list(req.get("jid_alt") or []):
                if j:
                    skipped.add(j)
    return sorted(skipped)


def extract_text(msg_obj):
    msg = msg_obj.get("message") or {}
    if not msg:
        return ""
    return (
        msg.get("conversation")
        or (msg.get("extendedTextMessage") or {}).get("text")
        or (msg.get("imageMessage") or {}).get("caption")
        or (msg.get("videoMessage") or {}).get("caption")
        or ""
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-msgs-per-jid", type=int, default=8)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--limit-per-page", type=int, default=200)
    ap.add_argument("--floor-hours", type=int, default=DEFAULT_FLOOR_HOURS,
                    help="Limite maximo de lookback quando watermark estiver velho")
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    api_key = os.environ.get("EVOLUTION_API_KEY")
    if not api_key:
        print(json.dumps({"error": "EVOLUTION_API_KEY ausente", "candidates": []}))
        sys.exit(2)

    state = read_state()
    floor_dt = datetime.now(timezone.utc) - timedelta(hours=args.floor_hours)
    since_ts = parse_iso(state.get("last_scan_at_utc")) or int(floor_dt.timestamp())
    # Segurança: nunca olhar pra trás além do floor (pra primeira run não explodir)
    floor_ts = int(floor_dt.timestamp())
    if since_ts < floor_ts:
        since_ts = floor_ts

    scan_at = datetime.now(timezone.utc)
    skip = set(active_jids())

    path = f"/chat/findMessages/{parse.quote(INSTANCE)}"
    per_jid = {}  # jid -> {pushName, messages[]}
    pages_read = 0
    msgs_seen = 0
    stopped = "max-pages"

    for page in range(1, args.max_pages + 1):
        try:
            resp = post(path, {
                "where": {"key": {"fromMe": False}},
                "limit": args.limit_per_page,
                "page": page,
            }, api_key)
        except error.HTTPError as e:
            print(json.dumps({"error": f"HTTP {e.code} {e.reason} page={page}", "candidates": []}))
            sys.exit(4)
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}", "candidates": []}))
            sys.exit(5)

        recs = resp.get("messages", {}).get("records", [])
        if not recs:
            stopped = "no-more-records"
            break
        pages_read += 1
        msgs_seen += len(recs)

        last_ts_on_page = None
        for m in recs:
            ts = m.get("messageTimestamp") or 0
            last_ts_on_page = ts
            if ts <= since_ts:
                continue
            key = m.get("key") or {}
            jid = key.get("remoteJid") or ""
            if not jid or jid.endswith("@g.us"):  # excluir grupos
                continue
            if jid in skip:
                continue
            text = extract_text(m)
            if not text.strip():
                continue
            entry = per_jid.setdefault(jid, {"pushName": None, "messages": []})
            if not entry["pushName"]:
                entry["pushName"] = m.get("pushName")
            if len(entry["messages"]) < args.max_msgs_per_jid:
                entry["messages"].append({
                    "ts": ts,
                    "iso": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                    "text": text[:600],
                    "messageId": key.get("id"),
                })

        if last_ts_on_page is not None and last_ts_on_page < since_ts:
            stopped = "cutoff-reached"
            break
        time.sleep(args.sleep)

    candidates = []
    for jid, data in per_jid.items():
        # ordenar msgs do mais antigo pro mais novo
        msgs = sorted(data["messages"], key=lambda x: x["ts"])
        candidates.append({
            "jid": jid,
            "pushName": data["pushName"],
            "lastInboundIso": msgs[-1]["iso"] if msgs else None,
            "messageCount": len(msgs),
            "messages": msgs,
        })
    candidates.sort(key=lambda c: c["lastInboundIso"] or "", reverse=True)

    print(json.dumps({
        "error": None,
        "since_iso": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(),
        "scan_at_iso": scan_at.isoformat(),
        "stoppedReason": stopped,
        "pagesRead": pages_read,
        "messagesScanned": msgs_seen,
        "skippedActiveJids": sorted(skip),
        "candidateCount": len(candidates),
        "candidates": candidates,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
