#!/usr/bin/env python3
"""Procura meeting-request com slack_thread_id == thread_ts.
Devolve JSON do request (com _path) ou {error}."""
import os
import argparse, glob, json, os, sys

REQUESTS_DIR = os.path.join(os.environ.get("WORKSPACE_DIR", os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")), "memory/meeting-requests")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread-ts", required=True)
    args = ap.parse_args()

    needle = args.thread_ts.strip()
    found = None
    for path in glob.glob(os.path.join(REQUESTS_DIR, "*.json")):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            req = json.load(open(path))
        except Exception:
            continue
        # tolerância: compara com e sem ".000000" no fim
        st = (req.get("slack_thread_id") or "").strip()
        if st == needle or st.split(".")[0] == needle.split(".")[0]:
            found = req
            found["_path"] = path
            break

    if found:
        print(json.dumps(found, ensure_ascii=False))
    else:
        print(json.dumps({"error": "no pending request matches thread_ts", "thread_ts": needle}))
        sys.exit(0)


if __name__ == "__main__":
    main()
