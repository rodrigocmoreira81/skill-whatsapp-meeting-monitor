# Arquitetura

A skill foi desenhada pra rodar dentro do **Russ** (CoS pessoal do Rodrigo Moreira em OpenClaw), mas os princípios se aplicam a qualquer agente que tenha:

- Um **runtime de cron** (OpenClaw, crontab, GitHub Actions, Vercel Cron, etc)
- Um **estado canônico em arquivos** (JSON em disco — não banco)
- **Sincronização clara** entre quem edita e quem executa

## As três camadas (modelo Russ original)

```
┌────────────────────────────┐
│   GitHub (verdade)         │ ← versionamento + histórico
│   github.com/org/repo      │
└─────────────┬──────────────┘
              │  pull/push horário
              ▼
┌────────────────────────────┐
│   VPS (executor)           │ ← onde o agente lê/escreve em tempo real
│   /workspace/...           │
│   - skills/                │
│   - memory/                │
│   - crons rodam aqui       │
└─────────────┬──────────────┘
              │  pull horário
              ▼
┌────────────────────────────┐
│   Cliente (espelho)        │ ← read-only no dia-a-dia
│   ~/.client/workspace      │
│   - Claude Code consulta   │
│     sem precisar de SSH    │
└────────────────────────────┘
```

**Regra de ouro:** edita SEMPRE na VPS. Cliente é só leitura.

**Por que não direto no GitHub?** Latência. O agente edita estado dezenas de vezes por hora (atualiza watermark, marca processed_ids, etc). Cada commit por mudança seria caro. Resolve com VPS como fonte rápida + sync horário pro GitHub.

## Fluxo de uma reunião marcada

```
┌──────────────┐
│  WhatsApp    │  Fulano: "vamos marcar um café?"
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Detector global (cron · 5x/dia)     │
│  - varre últimas 12h via Evolution   │
│  - regex classify pedido de agenda   │
│  - cria meeting-requests/<id>.json   │
│  - posta no Slack com botões         │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Você (Slack)                        │
│  - reage 👍 / responde "sim hiker"   │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  approve_request.py (opcional auto)  │
│  - status: pending → approved        │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  spawn_monitor_cron.py               │
│  - busca slots livres (Calendar)     │
│  - manda WhatsApp com 1-3 slots      │
│  - cria cron individual (15min)      │
│  - status: approved → monitoring     │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Monitor por contato (cron · 15min)  │
│  - check_replies_generic.py          │
│  - classifica branch A-F             │
│  - executa ação                      │
└──────┬───────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Branch A (slot + email)             │
│  - create_calendar_invite.py         │
│  - status: monitoring → invite_created
│  - cron disable                      │
└──────────────────────────────────────┘
```

## Decisão arquitetural #1: Python no comando, não LLM

Os crons rodam **scripts Python puros**, não payloads LLM com regras em prosa.

**Por quê:**
- Regras em prosa envelhecem em silêncio. Caso real: payload com `CONTEXTO ATUAL: ainda não confirmou` hardcoded virou loop de 24h depois do invite ter sido criado.
- Código falha em voz alta. Python explode com stack trace; LLM "interpreta" e segue errado.
- Saída estruturada (JSON com branch + errors) é auditável. Prosa livre não.

LLM ainda tem espaço — mas pra parsing de linguagem natural ambíguo (não-classify clean), composição de followups naturais, etc. Não pro loop de controle.

## Decisão arquitetural #2: estado em JSON, não banco

`memory/meeting-requests/<id>.json` é a única fonte de verdade. Cada request tem schema completo: status, slots oferecidos, watermarks de reply, histórico de ações, monitor_cron_id.

**Por quê:**
- Inspecionável com `cat`/`jq`. Diagnóstico em segundos.
- Versionável no git (parte do sync horário). Você vê quem mudou o quê quando.
- Sem dependência de DB. Sem migrations. Sem ORM.

**Trade-off:** não escala pra milhares de pedidos simultâneos. Mas: você é uma pessoa, não 1000.

## Decisão arquitetural #3: cron individual por contato

Cada pedido aprovado vira **seu próprio cron** (`monitor-meeting-<id>`, rodando a cada 15min). Quando termina (invite criado / cancelado / expirou), o próprio cron se desliga (`openclaw cron disable`).

**Por quê:**
- Isola falhas: se monitor do Fulano trava, monitor da Maria continua.
- Cadência específica por contato sem complicar o detector global.
- Self-disable é simples: `if status terminal: cron disable; exit`.

**Trade-off:** explode em número de crons se você tem 50 pedidos abertos. Mas: você não tem 50 pedidos abertos. Se tiver, você tem outros problemas.

## Padrões importantes (preservados pelo `run_monitor.py`)

1. **Ler estado primeiro.** Short-circuit em terminal/expired antes de qualquer outra chamada.
2. **Watermark = `last_seen_reply_at_utc`** (NÃO outbound). Senão a mesma reply vira "nova" infinitamente.
3. **Avançar watermark ANTES de agir.** Se postar Slack antes do watermark, falha de Slack faz loop.
4. **Filtrar IDs já processados.** Belt-and-suspenders contra clock skew.
5. **Status normalizado.** `.strip().lower()` antes de comparar — input pode vir com whitespace/case.
6. **Self-disable em terminal.** Não espera ninguém.
7. **Expiração dura.** `expires_at_utc` setado na criação. Past → mark expired + disable.

Detalhes em `core/whatsapp-meeting-monitor/PLAYBOOK.md`.
