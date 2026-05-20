#!/usr/bin/env python3
"""Atualiza last_scan_at_utc do detector."""
import argparse, json
from datetime import datetime, timezone

STATE = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-detector-state.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-at-iso", required=True)
    args = ap.parse_args()

    try:
        state = json.load(open(STATE))
    except FileNotFoundError:
        state = {"version": 1, "notes": "watermark do detector"}
    state["last_scan_at_utc"] = args.scan_at_iso
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    import os; os.replace(tmp, STATE)
    print(json.dumps({"ok": True, "last_scan_at_utc": args.scan_at_iso}))


if __name__ == "__main__":
    main()
