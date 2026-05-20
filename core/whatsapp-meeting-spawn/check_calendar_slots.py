#!/usr/bin/env python3
"""
Encontra slots livres no Calendar do Rodrigo seguindo regras de horário comercial.

Calendars considerados (busy = qualquer um deles ocupado):
  rodrigo@hiker.ventures, rodrigo@smartalk.com.br, ri@hiker.ventures, Runna

Regras:
- Janela: a partir de amanhã 9h BRT até +<days-ahead> dias úteis 19h
- Horário comercial: seg-sex 9h-19h BRT
- Excluir 12h-14h (almoço) por padrão; --include-lunch desliga
- Buffer 15min antes e depois de eventos existentes
- Slots = blocos de duration_min minutos
- Priorizar manhãs (10-12) > tarde (14-17) > 9h/17-19h
- 1 slot por dia (default); --multi-per-day permite múltiplos
- Devolver até --n-slots; menos se não houver

Uso:
  set -a && . /root/.openclaw/.env && set +a
  python3 check_calendar_slots.py [--duration 60] [--n-slots 3] [--days-ahead 7] [--not-before-date YYYY-MM-DD] [--include-lunch] [--multi-per-day]
"""
import argparse, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
CALENDARS = [
    os.environ.get("OWNER_EMAIL", ""),
    # alias removido — adapte ao seu OWNER_EMAIL
    # alias removido — adapte ao seu OWNER_EMAIL
    "c_af37d66fe0a41d4b2acd58f213d8685d77281ae86aff9e1d4f5275caebc6690a@group.calendar.google.com",  # Runna
    # TrainingPeaks deliberadamente fora: marca semana inteira como busy (plano), nao compromisso real.
]
COMMERCIAL_START = time(9, 0)   # 9h BRT
COMMERCIAL_END = time(19, 0)    # 19h BRT
LUNCH_START = time(12, 0)
LUNCH_END = time(14, 0)
BUFFER_MIN = 15

# Slots candidatos por ordem de preferência dentro do dia
PREFERRED_START_TIMES_BY_DURATION = {
    60: [time(10, 0), time(11, 0), time(14, 0), time(15, 0), time(16, 0), time(9, 0), time(17, 0), time(18, 0)],
    30: [time(10, 0), time(10, 30), time(11, 0), time(11, 30), time(14, 0), time(14, 30), time(15, 0), time(15, 30),
         time(16, 0), time(16, 30), time(17, 0), time(17, 30), time(9, 0), time(9, 30), time(18, 0), time(18, 30)],
}


def fetch_busy(start_brt, end_brt):
    """Retorna lista de (start_utc, end_utc) já mergeada de todos os calendários."""
    cmd = ["gog", "calendar", "freebusy", "--json", "--from", start_brt.isoformat(), "--to", end_brt.isoformat()]
    for c in CALENDARS:
        cmd += ["--cal", c]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"gog freebusy falhou: {res.stderr.strip()[:300]}")
    data = json.loads(res.stdout)
    busy = []
    for cal_data in (data.get("calendars") or {}).values():
        for b in cal_data.get("busy", []):
            s = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
            busy.append((s, e))
    busy.sort()
    # merge intervals
    merged = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def is_free(slot_start_utc, slot_end_utc, busy_merged):
    pad = timedelta(minutes=BUFFER_MIN)
    for bs, be in busy_merged:
        if slot_start_utc < be + pad and slot_end_utc > bs - pad:
            return False
    return True


def collect_slots(days_ahead, duration, include_lunch, multi_per_day, n_slots, not_before_date=None):
    now_brt = datetime.now(BRT)
    tomorrow = now_brt.date() + timedelta(days=1)
    first_day = max(tomorrow, not_before_date) if not_before_date else tomorrow
    horizon = first_day + timedelta(days=days_ahead)
    start_window = datetime.combine(first_day, COMMERCIAL_START, BRT)
    end_window = datetime.combine(horizon, COMMERCIAL_END, BRT)

    busy = fetch_busy(start_window, end_window)
    preferred = PREFERRED_START_TIMES_BY_DURATION.get(duration, PREFERRED_START_TIMES_BY_DURATION[60])
    weekdays_pt = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]

    found = []
    used_days = set()
    cursor = first_day
    while cursor <= horizon and len(found) < n_slots:
        if cursor.weekday() >= 5:  # sat/sun
            cursor += timedelta(days=1); continue
        if not multi_per_day and cursor in used_days:
            cursor += timedelta(days=1); continue
        for tstart in preferred:
            slot_start_brt = datetime.combine(cursor, tstart, BRT)
            slot_end_brt = slot_start_brt + timedelta(minutes=duration)
            if slot_end_brt.time() > COMMERCIAL_END:
                continue
            if not include_lunch:
                if not (slot_end_brt.time() <= LUNCH_START or slot_start_brt.time() >= LUNCH_END):
                    continue
            slot_start_utc = slot_start_brt.astimezone(timezone.utc)
            slot_end_utc = slot_end_brt.astimezone(timezone.utc)
            if is_free(slot_start_utc, slot_end_utc, busy):
                found.append({
                    "label": f"{weekdays_pt[cursor.weekday()]} {cursor.strftime('%d/%m')} às {slot_start_brt.strftime('%Hh%M').replace('h00','h')}",
                    "weekday": weekdays_pt[cursor.weekday()],
                    "date": cursor.isoformat(),
                    "start_brt": slot_start_brt.isoformat(),
                    "end_brt": slot_end_brt.isoformat(),
                    "start_utc": slot_start_utc.isoformat(),
                    "end_utc": slot_end_utc.isoformat(),
                })
                used_days.add(cursor)
                if not multi_per_day:
                    break
                if len(found) >= n_slots:
                    break
        cursor += timedelta(days=1)
    return found, busy


def collect_slots_lunch(days_ahead, n_slots, not_before_date=None):
    """Almoco: sempre 12h30-13h30, dias uteis distintos."""
    now_brt = datetime.now(BRT)
    tomorrow = now_brt.date() + timedelta(days=1)
    first_day = max(tomorrow, not_before_date) if not_before_date else tomorrow
    horizon = first_day + timedelta(days=days_ahead)
    start_window = datetime.combine(first_day, time(9, 0), BRT)
    end_window = datetime.combine(horizon, COMMERCIAL_END, BRT)
    busy = fetch_busy(start_window, end_window)
    weekdays_pt = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
    found = []
    cursor = first_day
    while cursor <= horizon and len(found) < n_slots:
        if cursor.weekday() >= 5:
            cursor += timedelta(days=1); continue
        slot_start_brt = datetime.combine(cursor, time(12, 30), BRT)
        slot_end_brt = slot_start_brt + timedelta(minutes=60)
        slot_start_utc = slot_start_brt.astimezone(timezone.utc)
        slot_end_utc = slot_end_brt.astimezone(timezone.utc)
        if is_free(slot_start_utc, slot_end_utc, busy):
            found.append({
                "label": f"{weekdays_pt[cursor.weekday()]} {cursor.strftime('%d/%m')} às {slot_start_brt.strftime('%Hh%M').replace('h00','h')}",
                "weekday": weekdays_pt[cursor.weekday()],
                "date": cursor.isoformat(),
                "start_brt": slot_start_brt.isoformat(),
                "end_brt": slot_end_brt.isoformat(),
                "start_utc": slot_start_utc.isoformat(),
                "end_utc": slot_end_utc.isoformat(),
            })
        cursor += timedelta(days=1)
    return found, busy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=60, choices=[30, 60])
    ap.add_argument("--n-slots", type=int, default=3)
    ap.add_argument("--days-ahead", type=int, default=7)
    ap.add_argument("--not-before-date", default=None, help="primeiro dia permitido, YYYY-MM-DD")
    ap.add_argument("--include-lunch", action="store_true")
    ap.add_argument("--multi-per-day", action="store_true")
    ap.add_argument("--lunch", action="store_true", help="modo almoco: forca 12h30, 60min")
    args = ap.parse_args()
    not_before_date = None
    if args.not_before_date:
        try:
            not_before_date = datetime.strptime(args.not_before_date, "%Y-%m-%d").date()
        except ValueError:
            print(json.dumps({"error": "--not-before-date deve estar em YYYY-MM-DD"}))
            sys.exit(2)

    if not os.environ.get("GOG_KEYRING_PASSWORD"):
        print(json.dumps({"error": "GOG_KEYRING_PASSWORD ausente — source /root/.openclaw/.env"}))
        sys.exit(2)

    try:
        if args.lunch:
            slots, busy = collect_slots_lunch(args.days_ahead, args.n_slots, not_before_date)
        else:
            slots, busy = collect_slots(args.days_ahead, args.duration, args.include_lunch, args.multi_per_day, args.n_slots, not_before_date)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(3)

    print(json.dumps({
        "error": None,
        "duration_min": args.duration,
        "days_ahead": args.days_ahead,
        "not_before_date": args.not_before_date,
        "include_lunch": args.include_lunch,
        "calendars_checked": CALENDARS,
        "busy_intervals_count": len(busy),
        "slots": slots,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
