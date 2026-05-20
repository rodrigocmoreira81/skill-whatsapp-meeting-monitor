# Playbook — monitor de WhatsApp pra marcar reunião

**Escopo:** este playbook orienta o cron que monitora um único contato (`monitor-meeting-<id>`, cadência 15min) buscando respostas pra fechar reunião. Para o detector global de novos pedidos, ver `run_detector.py` no mesmo diretório.

**Padrão obrigatório:** payload do cron NÃO carrega regras em prosa pra LLM seguir. Roda `run_monitor.py --id <request_id>` direto (script Python determinístico). LLM só transcreve a última linha JSON. Justificativa: 2 falhas reais (<contato-X> e systemEvent abaixo) provaram que regras em prompt envelhecem e falham silenciosamente.

## Casos de falha (entrada → ação errada → correção)

**1. `monitor-sthella-xa-cana-cafe` (cron 55aac68b, 2026-05-14 a 16)** — payload LLM com `CONTEXTO ATUAL: ainda não confirmou` hardcoded; `check_replies.py` usava `last_outbound_at_utc` como `since`; sem self-disable em terminal.
   - Sintoma: "<contato-X> respondeu sem confirmar" cuspido no Slack a cada 2h por 24h+ DEPOIS do invite ter sido criado.
   - Correção: cron disable manual; PLAYBOOK criado com regras 1-7.

**2. `whatsapp-meeting-detector` (cron bc055783, 2026-05-17 a 19)** — cron com `sessionTarget=main` + `payload.kind=systemEvent`. Esse formato injeta o prompt no chat principal mas NÃO dispara um run com tools.
   - Sintoma: cada execução terminava em 1-2ms registrando "OK" + o prompt-bruto como summary. Watermark congelado por 2 dias. Nenhum pedido detectado.
   - Correção: cron migrado para `sessionTarget=isolated` + `payload.kind=agentTurn` + `command` Python; criado `run_detector.py` determinístico.
   - Sinal pra alarme: cron habilitado com `durationMs < 100ms` em runs consecutivos → payload mal configurado.

**3. `morning_reminder.classify()` (2026-05-18)** — comparava `req["status"]` cru contra sets `TERMINAL_STATUSES`/`ACTIVE_STATUSES`. Status com whitespace ou capitalização diferente caía em `None` silenciosamente.
   - Sintoma: cron 8h da manhã do meeting-requests-morning-reminder falhou; reportou no Slack ("morning_reminder falhou e avisei no Slack").
   - Correção: `.strip().lower()` no status + 6 testes unitários em `test_morning_reminder.py`.
   - Lição: normalizar input antes de comparar com vocabulário fechado.

## Cadência

- Monitor cron roda **a cada 15min** enquanto está ativo (não horário). Cadência rápida porque o objetivo é fechar agenda no mesmo dia.
- Quando o monitor termina (terminal/expired), self-disable. O detector global volta sozinho ao baseline (a cada 3h).

## Estado canônico

Arquivo `memory/meeting-requests/<id>.json` é a ÚNICA fonte de verdade. Campos obrigatórios:

- `id`: identificador único (slug com data e contato)
- `contact`: nome legível
- `jid`: WhatsApp JID principal
- `jid_alt`: JIDs alternativos (LID, telefone) se houver
- `status`: `pending_approval` | `approved` | `monitoring` | `invite_created` | `declined` | `expired`
- `modality`: `meet` (default) | `presencial_hiker` | `presencial_outro`
- `duration_min`: 60 (default)
- `last_outbound_at_utc`: timestamp da nossa última msg enviada
- `last_seen_reply_at_utc`: timestamp da última msg DELA que processamos
- `processed_message_ids`: array de IDs já tratados (idempotência dura)
- `slots_offered`: array dos slots propostos (label, start, end)
- `expires_at_utc`: deadline absoluto do monitor (ex: data do slot mais tarde + 1 dia)
- `monitor_cron_id`: id do cron de monitor (pra self-disable)
- `slack_thread_id`: thread no #russ-main onde tudo é reportado

## Como o cron executa (run_monitor.py determinístico)

O payload do cron deve ser **agentTurn** com `sessionTarget=isolated`, `toolsAllow=["exec"]` e UMA linha de mensagem:

```
Execute exatamente este comando e responda apenas com a última linha JSON resumida, sem postar mensagens adicionais: set -a && . /root/.openclaw/.env && set +a && python3 /root/.openclaw/workspace/skills/whatsapp-meeting-monitor/run_monitor.py --id <REQUEST_ID>
```

O `run_monitor.py` faz todo o trabalho em Python (sem LLM no loop): lê estado, short-circuita terminal/expired, chama `check_replies_generic.py`, avança watermark ANTES de classificar, decide UMA das 6 branches (A-F), executa actions via subprocess. Retorna `{ok, branch, request_id, errors[]}` em stdout. Saída JSON estruturada é auditável; prosa livre não é.

**Não escreva regras de classify em prosa no payload do cron.** Se a regra for nova, edita o `run_monitor.py` + commita teste. Regras em prompt envelhecem em silêncio.

## Invariantes técnicos (preservados pelo script)

1. **Estado primeiro.** Lê JSON antes de qualquer ação. Status terminal (`invite_created`, `cancelled`, `expired`, `declined`, `user_handles`) OU `now > expires_at_utc` → `openclaw cron disable` e termina. Sem Slack.

2. **`since` = `last_seen_reply_at_utc`** (NÃO `last_outbound_at_utc`). Senão a mesma resposta dela vira "nova" infinitamente.

3. **Avançar watermark ANTES de classify.** Após pegar replies novas, atualiza `last_seen_reply_at_utc` + adiciona IDs em `processed_message_ids` ANTES de decidir branch ou postar Slack. Sem isso o cron loopa em falha.

4. **Filtra por ID já processado.** Mesmo dentro da janela `since`, descarta IDs em `processed_message_ids` (belt-and-suspenders contra clock skew).

5. **Status normalizado.** Sempre comparar `status.strip().lower()` contra os sets — input pode vir com whitespace/case (lição do bug 2026-05-18 do morning_reminder).

6. **Self-disable em terminal.** Branch A (invite criado), E (cancelado) e expired: `openclaw cron disable` no mesmo run. Não esperar Rodrigo.

7. **Expiração dura.** `expires_at_utc` setado na criação (data do slot mais tarde + 1 dia). Past expiração → mark `expired` + disable.

8. **`sessionTarget=isolated` + `kind=agentTurn`.** NUNCA `main` + `systemEvent` — esse formato injeta o prompt mas não executa tools (durationMs 1-2ms = sinal forte de payload mal configurado).

## Regras de Calendar (criação do invite)

- **Duração default:** 60min (override só se Rodrigo especificou).
- **Modalidade default:** Google Meet (quando Rodrigo só disse "sim" sem especificar).
- **Presencial na Hiker:** location `"$OFFICE_ADDRESS"`. **OBRIGATÓRIO:** adicionar `$OFFICE_RECEPTION_EMAIL` como convidado — staff do prédio usa o invite pra reservar sala. Sem isso <owner> chega sem sala.
- Convidado principal: e-mail do contato (não da recepção).
- Timezone: `America/Sao_Paulo`.

## Checklist antes de subir um novo monitor

- [ ] JSON criado em `memory/meeting-requests/<id>.json` com schema completo incluindo `expires_at_utc` e `monitor_cron_id` placeholder
- [ ] Cron criado com `sessionTarget=isolated`, `payload.kind=agentTurn`, `toolsAllow=["exec"]`, mensagem invocando `run_monitor.py --id <id>`
- [ ] `monitor_cron_id` injetado no JSON depois de `cron add` retornar o id
- [ ] Cadência do cron = `*/15 8-22 * * *` (15min, horário de vigília)
- [ ] Dry-run validado: `python3 run_monitor.py --id <id> --dry-run` retorna branch esperada (no_replies / terminal_short_circuit)
- [ ] Primeiro run real validado: `openclaw cron run <id>` produz `durationMs > 5s` (sinal de que executou Python, não injeção vazia)
