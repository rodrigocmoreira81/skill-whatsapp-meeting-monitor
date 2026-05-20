#!/usr/bin/env python3
"""Patch in-place: replace hardcoded Hiker/Rodrigo values with env-var lookups."""
import re
from pathlib import Path

ROOT = Path("/tmp/skill-repo")

# Mappings: regex pattern → replacement (raw text, Python source-friendly)
# Strings only — avoid touching docstrings/comments that are purely informational.
REPLACEMENTS = [
    # Workspace + paths
    (r'"/root/\.openclaw/workspace/memory/meeting-requests"',
     'os.path.join(os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace"), "memory/meeting-requests")'),
    (r'"/root/\.openclaw/workspace/memory/meeting-detector-state\.json"',
     'os.path.join(os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace"), "memory/meeting-detector-state.json")'),
    (r'"/root/\.openclaw/workspace"',
     'os.environ.get("WORKSPACE_DIR", "/root/.openclaw/workspace")'),
    (r'"/root/\.openclaw/openclaw\.json"',
     'os.environ.get("OPENCLAW_CONFIG_PATH", "/root/.openclaw/openclaw.json")'),

    # Evolution API
    (r'"https://atendimento-backend\.hiker\.ventures"',
     'os.environ.get("EVOLUTION_BASE_URL", "")'),
    (r'INSTANCE = "Rodrigo Moreira"',
     'INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")'),

    # Slack channel/user (kept default placeholders so import doesn't break)
    (r'"C0B0JKTM547"',
     'os.environ.get("SLACK_CHANNEL_ID", "C0XXXXXXXXX")'),
    (r'"U0AURK0QFT9"',
     'os.environ.get("SLACK_OWNER_USER_ID", "U0XXXXXXXXX")'),

    # Calendar / org-specific
    (r'PRIMARY = "rodrigo@hiker\.ventures"',
     'PRIMARY = os.environ.get("OWNER_EMAIL", "")'),
    (r'RECEPCAO_HIKER = "recepcao@afs\.com\.br"',
     'RECEPCAO_HIKER = os.environ.get("OFFICE_RECEPTION_EMAIL", "")'),
    (r'HIKER_LOCATION = "Hiker Ventures, Rua Sergipe 1440 - Savassi, Belo Horizonte - MG"',
     'HIKER_LOCATION = os.environ.get("OFFICE_ADDRESS", "")'),
]

# Ensure os import exists in files we patch
def ensure_os_import(text):
    if "import os" in text or "from os " in text:
        return text
    # Insert after first shebang/docstring block
    lines = text.split("\n")
    insert_at = 0
    for i, l in enumerate(lines[:30]):
        if l.startswith("import ") or l.startswith("from "):
            insert_at = i
            break
    lines.insert(insert_at, "import os")
    return "\n".join(lines)


def patch_file(path: Path):
    text = path.read_text(encoding="utf-8")
    original = text
    for pat, repl in REPLACEMENTS:
        text = re.sub(pat, repl, text)
    if text != original:
        text = ensure_os_import(text)
        path.write_text(text, encoding="utf-8")
        print(f"patched: {path.relative_to(ROOT)}")


for p in ROOT.rglob("*.py"):
    if "_patch.py" in str(p) or "__pycache__" in str(p):
        continue
    patch_file(p)

# also patch templates (txt) - just substitute the path references
for p in ROOT.rglob("*.txt"):
    text = p.read_text(encoding="utf-8")
    original = text
    text = text.replace("/root/.openclaw/workspace", "${WORKSPACE_DIR}")
    text = text.replace("/root/.openclaw/.env", "${ENV_FILE}")
    if text != original:
        p.write_text(text, encoding="utf-8")
        print(f"patched template: {p.relative_to(ROOT)}")
print("done.")
