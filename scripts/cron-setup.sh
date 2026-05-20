#!/usr/bin/env bash
# Exemplos de comandos pra criar os crons necessários.
# Edite conforme seu runtime (OpenClaw, crontab, GitHub Actions, Vercel Cron...).
#
# Pré-requisito: variáveis em .env carregadas. Faça:
#   set -a && . .env && set +a
# antes de rodar este script.

set -e

if [ -z "$WORKSPACE_DIR" ]; then
  echo "ERRO: WORKSPACE_DIR não setado. source .env antes."
  exit 1
fi

ENV_FILE="${ENV_FILE:-$PWD/.env}"

echo "→ Detector global (cron 5x/dia)"
openclaw cron add \
  --name "whatsapp-meeting-detector" \
  --cron "0 8,11,14,17,20 * * *" \
  --tz "America/Sao_Paulo" \
  --message "set -a && . $ENV_FILE && set +a && python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/run_detector.py --floor-hours 12 --max-pages 30 --limit-per-page 200" \
  --tools exec \
  --timeout-seconds 180

echo "→ Lembrete matinal (cron 8h, dias úteis)"
openclaw cron add \
  --name "meeting-requests-morning-reminder" \
  --cron "0 8 * * 1-5" \
  --tz "America/Sao_Paulo" \
  --message "python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/morning_reminder.py" \
  --tools exec \
  --timeout-seconds 60

echo "OK — monitor por contato é criado dinamicamente por spawn_monitor_cron.py"
