# skill-whatsapp-meeting-monitor

Skill que detecta pedidos de reunião no WhatsApp, te notifica no Slack, e fecha o invite no Google Calendar automaticamente quando você aprova.

Construído como parte do **Russ** (CoS pessoal do Rodrigo Moreira) rodando em **OpenClaw**. Este repo é a versão portável das skills — você pode usar como **referência de design**, como **base de código adaptável**, ou instalar como **drop-in completo** no seu próprio agente.

---

## Três caminhos pra adotar

Escolhe um. Não precisa fazer todos.

### Caminho A — Só leitura (entender o design)
Você quer entender como funciona, mas vai implementar do seu jeito (talvez em outra stack — n8n, Make, código próprio).

**Comece por:**
- [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) — as 3 camadas (VPS + GitHub + cliente), por que separar
- [`core/whatsapp-meeting-monitor/PLAYBOOK.md`](core/whatsapp-meeting-monitor/PLAYBOOK.md) — regras, invariantes, casos negativos reais (bug do `sessionTarget`, regras em prosa que envelhecem, normalização de input)
- [`docs/BRANCHES.md`](docs/BRANCHES.md) — as 6 branches que o monitor decide (A invite, B pede email, C só email, D fora dos slots, E cancelou, F ambíguo)

Tempo: 30-45 min de leitura.

### Caminho B — Core (instalar o mínimo viável)
Você roda OpenClaw (ou shell-runner equivalente) e quer só o detector global + monitor por contato.

**Use:**
- [`core/whatsapp-meeting-monitor/`](core/whatsapp-meeting-monitor/) — `run_detector.py` (cron 5x/dia) + `run_monitor.py` (cron 15min/contato) + `morning_reminder.py` (cron 8h)
- [`core/whatsapp-meeting-spawn/`](core/whatsapp-meeting-spawn/) — `check_replies_generic.py`, `update_request.py`, `create_calendar_invite.py`, `spawn_monitor_cron.py`, helpers

**Não precisa:** approval via reaction Slack (você aprova manualmente editando o JSON ou rodando `approve_request.py`).

Tempo: 1-2h pra subir.

### Caminho C — Tudo (drop-in com approval automático)
Quer o fluxo end-to-end: você reage com 👍 no Slack e o resto acontece sozinho.

**Use:** `core/` + `extras/meeting-request-approve/`.

**Pré-requisito extra:** seu agente precisa ouvir eventos `reaction_added`/`message` do Slack e disparar a skill (no OpenClaw isso é uma skill carregada por trigger; em outros stacks você precisa de subscription Events API).

Tempo: 2-4h pra subir.

---

## Pré-requisitos comuns (caminhos B e C)

1. **WhatsApp via Evolution API** — instância dedicada com seu número, exposta numa URL HTTPS. Veja [Evolution API docs](https://doc.evolution-api.com/). Variáveis: `EVOLUTION_BASE_URL`, `EVOLUTION_INSTANCE`, `EVOLUTION_API_KEY`.

2. **Slack bot** com scopes mínimos: `chat:write`, `chat:write.public`, `channels:read`, `channels:join`, `files:write`, `reactions:read`, `users:read`, `groups:read`. Variável: `SLACK_BOT_TOKEN`.

3. **Google Calendar** — acesso programático. O Russ usa [gog](https://github.com/anthropics/) (Google CLI). Você pode substituir por google-api-python-client + service account ou OAuth2. Variável: `OWNER_EMAIL`, `GOG_KEYRING_PASSWORD` (se gog).

4. **Runtime de cron** — qualquer um (crontab, OpenClaw cron, GitHub Actions schedule, Vercel Cron, etc). Os scripts são CLIs Python; o que dispara é com você.

5. **Python 3.10+** com stdlib (sem dependências externas — usa só `urllib`, `json`, `subprocess`, `argparse`, `zoneinfo`).

---

## Instalação rápida (caminho B/C, OpenClaw assumido)

```bash
# 1. Clone
git clone https://github.com/rodrigocmoreira81/skill-whatsapp-meeting-monitor.git
cd skill-whatsapp-meeting-monitor

# 2. Configura env
cp .env.example .env
# edita .env com seus tokens, channel IDs, instance name

# 3. Copia pra suas skills/
cp -r core/whatsapp-meeting-monitor /seu/workspace/skills/
cp -r core/whatsapp-meeting-spawn   /seu/workspace/skills/
# opcional (caminho C):
cp -r extras/meeting-request-approve /seu/workspace/skills/

# 4. Cria pasta de estado
mkdir -p /seu/workspace/memory/meeting-requests

# 5. Sourcia env e testa dry-run do detector
set -a && . .env && set +a
python3 /seu/workspace/skills/whatsapp-meeting-monitor/detect_meeting_requests.py --floor-hours 2

# 6. Configura crons (exemplo OpenClaw — adapte ao seu runner)
# Ver scripts/cron-setup.sh
```

Detalhes completos em [`INSTALL.md`](INSTALL.md).

---

## Como funciona, em uma cena

1. **9h15** — Alguém te manda no WhatsApp "vamos marcar um café semana que vem?"
2. **9h17** — Detector global roda (cron) e identifica pedido via regex. Cria `memory/meeting-requests/<id>.json` com status `pending_approval`. Posta no Slack: *"📨 Fulano pediu agenda. Modalidade: meet. Responda na thread: sim / não / eu marco"*.
3. **9h18** — Você reage com 👍. (Caminho C: skill `meeting-request-approve` muda status pra `approved`.)
4. **9h19** — `spawn_monitor_cron.py` consulta seu Calendar via gog, acha 3 slots livres, manda WhatsApp pra Fulano com os slots, cria cron individual rodando a cada 15min.
5. **Próximas horas** — Cron do monitor checa replies. Detecta confirmação de slot + email → cria invite no Calendar, manda link, self-disable.

Detalhes em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

---

## O que está aqui

```
skill-whatsapp-meeting-monitor/
├── README.md                          ← este arquivo
├── INSTALL.md                         ← guia detalhado de instalação
├── .env.example                       ← variáveis necessárias
├── docs/
│   ├── ARQUITETURA.md                 ← 3 camadas, fontes de verdade
│   └── BRANCHES.md                    ← classificação A-F do monitor
├── core/
│   ├── whatsapp-meeting-monitor/      ← detector global + monitor por contato + lembrete matinal
│   └── whatsapp-meeting-spawn/        ← scripts auxiliares (replies, update, calendar, send)
├── extras/
│   └── meeting-request-approve/       ← approval automático via reaction Slack (opcional)
└── scripts/
    ├── _patch.py                      ← script que gerou esta versão portável (ignore)
    └── cron-setup.sh                  ← exemplos de comandos pra criar os crons
```

---

## Limitações conhecidas

- **Regex do detector é português-BR e específico:** vocabulário como "vamos marcar", "topa um café", "marca com fulano". Você vai querer expandir conforme seu uso. Veja `REQUEST_PATTERNS` em `core/whatsapp-meeting-monitor/detect_meeting_requests.py`.
- **Bot Slack precisa estar no canal** pra postar arquivos. `chat:write.public` cobre mensagens, mas não cobre `files.completeUploadExternal` — chama `conversations.join` antes.
- **Evolution API tem latência** — mensagens podem aparecer 10-30s após chegarem ao WhatsApp. Considere isso ao ajustar `--floor-hours` do detector.
- **Calendar via gog é Hiker-específico** — você provavelmente vai trocar por google-api-python-client. A skill já isola isso em `create_calendar_invite.py` e `check_calendar_slots.py`.

---

## Licença

MIT. Use, modifique, compartilhe.

---

## Crédito

Construído pelo **Rodrigo Moreira** com **Claude Code** e o agente **Russ** (OpenClaw). Maio/2026.
