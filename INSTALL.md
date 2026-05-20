# Instalação detalhada

## 1. Setup de infra

### Evolution API (WhatsApp)

Você precisa de uma instância da [Evolution API](https://doc.evolution-api.com/) com seu número conectado. Opções:

- **Self-host:** Docker compose, deploy num VPS. ~30min se for de boa com Docker.
- **Hospedagem terceirizada:** alguns players brasileiros vendem instância pronta.

Após conectar seu WhatsApp, anote:
- `EVOLUTION_BASE_URL` (ex: `https://evolution.seudominio.com.br`)
- `EVOLUTION_INSTANCE` (nome da instância — geralmente seu nome)
- `EVOLUTION_API_KEY` (gerada pelo Evolution)

### Slack bot

1. Vai em https://api.slack.com/apps → "Create New App" → from scratch
2. Sidebar → OAuth & Permissions → Bot Token Scopes:
   ```
   chat:write
   chat:write.public
   chat:write.customize    (opcional)
   channels:read
   channels:join
   channels:history
   files:read
   files:write
   reactions:read
   reactions:write
   users:read
   users:read.email
   groups:read
   groups:write
   im:read
   im:write
   im:history
   mpim:read
   mpim:write
   mpim:history
   ```
3. Install to Workspace → autoriza
4. Anota `SLACK_BOT_TOKEN` (xoxb-...)
5. Cria um canal pra notificações (ex: `#meetings-inbox`) e copia o channel ID

### Google Calendar

Duas rotas:

**Rota A — gog (CLI Anthropic):** [github.com/anthropics/...] — usado pelo Russ. Auth via OAuth, simples de setar.

**Rota B — google-api-python-client + service account:** mais portável. Você cria service account, compartilha seu calendar com o email da service account, usa OAuth2 token. Veja [Google Calendar API quickstart](https://developers.google.com/calendar/api/quickstart/python).

Se for rota B, você vai precisar **adaptar** os scripts `create_calendar_invite.py` e `check_calendar_slots.py` — eles hoje chamam `gog calendar create-event ...` via subprocess. Substitua pela lib Python.

## 2. Setup de scripts

```bash
# Clone
git clone https://github.com/rodrigocmoreira81/skill-whatsapp-meeting-monitor.git
cd skill-whatsapp-meeting-monitor

# Copy .env
cp .env.example .env
$EDITOR .env  # preenche tokens, IDs, paths

# Source env e teste smoke
set -a && . .env && set +a

# Teste 1: Evolution alcançável?
python3 core/whatsapp-meeting-monitor/detect_meeting_requests.py --floor-hours 2
# Deve retornar JSON com candidateCount e candidates (ou erro claro)

# Teste 2: Slack alcançável?
python3 -c "
import os, json
from urllib import request
token = os.environ['SLACK_BOT_TOKEN']
req = request.Request('https://slack.com/api/auth.test', headers={'Authorization': f'Bearer {token}'})
print(json.loads(request.urlopen(req).read()))
"
# Deve retornar {ok: True, ...}
```

## 3. Instalar nas suas skills/

```bash
mkdir -p $WORKSPACE_DIR/skills $WORKSPACE_DIR/memory/meeting-requests

cp -r core/whatsapp-meeting-monitor $WORKSPACE_DIR/skills/
cp -r core/whatsapp-meeting-spawn   $WORKSPACE_DIR/skills/

# Opcional (caminho C):
cp -r extras/meeting-request-approve $WORKSPACE_DIR/skills/
```

## 4. Criar os crons

### Detector global (5x/dia)

OpenClaw:
```bash
openclaw cron add \
  --name "whatsapp-meeting-detector" \
  --cron "0 8,11,14,17,20 * * *" \
  --tz "America/Sao_Paulo" \
  --message "set -a && . $ENV_FILE && set +a && python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/run_detector.py --floor-hours 12 --max-pages 30 --limit-per-page 200" \
  --tools exec \
  --timeout-seconds 180
```

Crontab equivalente:
```cron
0 8,11,14,17,20 * * * cd /path && set -a && . .env && set +a && python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/run_detector.py --floor-hours 12 >> /var/log/meeting-detector.log 2>&1
```

### Lembrete matinal (8h)

```bash
openclaw cron add \
  --name "meeting-requests-morning-reminder" \
  --cron "0 8 * * 1-5" \
  --tz "America/Sao_Paulo" \
  --message "python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/morning_reminder.py" \
  --tools exec \
  --timeout-seconds 60
```

### Monitor por contato (criado dinamicamente)

Você NÃO cria isso manualmente — `spawn_monitor_cron.py` cria automaticamente quando você aprova um pedido. Mas o template usado pra criar tá em `core/whatsapp-meeting-spawn/monitor_payload_template.txt` — você pode customizar se quiser.

## 5. Aprovação (caminho B vs C)

**Caminho B (manual):** quando o detector posta no Slack, você abre o JSON em `memory/meeting-requests/<id>.json` e edita o status, OU roda:
```bash
python3 extras/meeting-request-approve/approve_request.py --id <id> --action sim --modality meet --duration 60
```

**Caminho C (auto via Slack):** instale `extras/meeting-request-approve/` e configure seu agente pra disparar a skill em eventos `reaction_added` ou `message` na thread do canal. No OpenClaw isso é uma skill registrada como handler de Slack events.

## 6. Smoke test end-to-end

Manda pra você mesmo no WhatsApp (ou pede pra um colega) algo tipo: *"bora marcar um café semana que vem?"*. Depois:

```bash
# Dispara detector na mão
python3 $WORKSPACE_DIR/skills/whatsapp-meeting-monitor/run_detector.py --floor-hours 1

# Verifica criação
ls $WORKSPACE_DIR/memory/meeting-requests/

# Verifica post no Slack do canal configurado
```

Se aparecer no Slack, está OK. Se não, veja:
- Stdout do detector — `candidateCount` deve ser >= 1
- `classify()` em `run_detector.py` — pode ser que seu pedido não bata no regex (vocabulário pt-BR)

## Troubleshooting

**"missing_scope" no Slack:** confere os scopes no app + reinstall.

**"channels: []" no upload de file:** chama `conversations.join` antes — bot precisa ser membro do canal.

**Detector retorna 0 candidates mas você sabe que mandou:** verifica `meeting-detector-state.json` — `last_scan_at_utc` pode estar à frente da sua msg (o cron pode ter rodado antes).

**Cron com `durationMs < 100ms`:** sinal de payload mal configurado (era pra ser `agentTurn` + `isolated`, não `systemEvent` + `main`). Olha a doc do seu cron runner.

**Status com whitespace passou silencioso:** garante que está usando `morning_reminder.py` com `.strip().lower()`. Versão antiga tinha esse bug.
