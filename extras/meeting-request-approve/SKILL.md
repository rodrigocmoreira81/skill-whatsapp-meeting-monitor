---
name: meeting-request-approve
description: Processa a resposta do <owner> (texto em thread OU reação) sobre um pedido de agenda detectado pelo whatsapp-meeting-detector no #russ-main. Cobre pedidos diretos E brokered (alguém pedindo agenda em nome de terceiro).
---

# meeting-request-approve

## WHEN — esta skill se aplica em DOIS casos:

### Caso A — texto em thread
1. Msg nova do <owner> (Slack user `$SLACK_OWNER_USER_ID`)
2. Canal `$SLACK_CHANNEL_ID` (#russ-main)
3. Numa **thread** (tem `thread_ts` ≠ `ts`)
4. A msg-pai da thread começa com `📨 *` (postada pelo Russ via detector)

### Caso B — reaction_added
1. Evento `reaction_added` do <owner> (`$SLACK_OWNER_USER_ID`)
2. Em msg do canal `$SLACK_CHANNEL_ID`
3. A msg reagida começa com `📨 *`

Em dúvida: rode PASSO 1 (match) primeiro. Se não casar, pare silenciosamente.

## ACTION

### PASSO 1 — Casar com pedido

```
exec(command="python3 /root/.openclaw/workspace/skills/meeting-request-approve/match_pending_thread.py --thread-ts '<thread_ts_ou_item_ts>'")
```

- Não casou → pare silenciosamente.
- Casou mas `status != pending_approval` → reply "Esse pedido já foi processado: <status>." e pare.

### PASSO 2 — Parsear resposta

Inspecione `modality` do request. Existem 2 caminhos de parsing.

#### CAMINHO 1 — modality ∈ {meet, presencial_hiker, presencial_outro, almoco, tbd}

Normalize (trim, lowercase, sem acento). Mapeie para `action` + `modality` + `duration`:

| Resposta | action | modality | duration |
|---|---|---|---|
| `sim`, `ok`, `marca`, `pode`, `vai`, `👍`, `✅` | sim | meet | 60 |
| `sim hiker`, `hiker`, `presencial`, `escritorio`, `🏢` | sim | presencial_hiker | 60 |
| `sim 30`, `sim 30min`, `30min`, `30`, `⏱️` | sim | meet | 30 |
| `sim almoco`, `sim almoço`, `almoco`, `almoço`, `🍴`, `🍽️` | sim | almoco | 60 |
| `não`, `nao`, `n`, `👎`, `❌` | nao | — | — |
| `eu marco`, `eu`, `🙋`, `✋` | eu_marco | — | — |

Texto livre com confirmação ("sim mas só dia 28 à tarde"): action=`sim`, modality preservada do detector, capture restante em `--free-text`.

Ambiguidade ("talvez"): reply "Não entendi — responde `sim`, `sim hiker`, `sim 30min`, `sim almoco`, `não`, ou `eu marco`. Ou reaja: 👍 / 🏢 / ⏱️ / 🍴 / 👎 / 🙋." e pare.

→ exec approve_request.py com `--action <act> --modality <mod> --duration <dur>` (sem `--broker-choice`).

#### CAMINHO 2 — modality == "brokered"

Mapeie para `broker_choice`:

| Resposta | broker_choice |
|---|---|
| `sim`, `sim 1`, `1`, `👍`, `✅` | sim_1 |
| `sim 2`, `2` | sim_2 |
| `sim 3`, `3` | sim_3 |
| `outra`, `outras`, `proponha`, `🔄` | outra |
| `não`, `nao`, `n`, `👎` | (action=nao, sem broker_choice) |
| `sim` em broker_mode=`requests_options` (sem número) | sim (não sim_1 — modo B só tem sim/não) |

Como saber broker_mode: vem do JSON do request (`req.broker_mode`).

- Se broker_mode=`proposes`: aceite `sim_1`, `sim_2`, `sim_3`, `outra`, ou `nao`. `sim` solto = `sim_1`.
- Se broker_mode=`requests_options`: só `sim` (igual a `sim_1`) ou `nao`. Ignore números.

→ exec approve_request.py com `--action sim --modality brokered --broker-choice <choice>` (ou `--action nao` se recusou).

### PASSO 3 — Confirmação visual (Slack)

```
message(action="react", channel="slack", target="$SLACK_CHANNEL_ID", messageId="<thread_ts>", reaction="eyes")
```

Reply no thread:

| Caso | reply |
|---|---|
| sim (meet, 60) | "✅ Anotado. Vou marcar Meet 1h com **<contact>**. Disparando agora." |
| sim (presencial_hiker, 60) | "✅ Anotado. Vou marcar 1h presencial na Hiker com **<contact>** (incluo recepção pra sala). Disparando agora." |
| sim (meet, 30) | "✅ Anotado. Vou marcar Meet 30min com **<contact>**. Disparando agora." |
| sim (almoco, 60) | "✅ Anotado. Vou marcar almoço com **<contact>**. Disparando agora." |
| brokered + sim_N + proposes | "✅ Anotado. Vou confirmar opção <N> com **<contact>** (marcação com **<target_contact>**)." |
| brokered + outra + proposes | "✅ Anotado. Vou propor alternativas pro **<contact>** repassar pro **<target_contact>**." |
| brokered + sim + requests_options | "✅ Anotado. Vou mandar 3 opções pro **<contact>** repassar pro **<target_contact>**." |
| nao | "🗑️ Arquivado." |
| eu_marco | "👌 Beleza, fica com você. Não vou mexer." |

### PASSO 4 — Disparar spawn (se action == sim)

```
exec(command="set -a && . /root/.openclaw/.env && set +a && python3 /root/.openclaw/workspace/skills/whatsapp-meeting-spawn/spawn_monitor_cron.py --id '<request.id>'")
```

O spawn detecta `modality=brokered` automaticamente e roda o fluxo brokered (sem invite, sem monitor pesado).

Saída JSON: `{ok, modality, broker_mode?, status, monitor_cron_id, evolution_message_id}`.

Reply no thread:
- **Direto (não-brokered)**: "🟢 Marcação iniciada. Enviei <slots_count> opções pelo WhatsApp. Monitor `<cron_id_curto>` ativo. Atualizo aqui quando responder."
- **Brokered + status=user_handles** (modo A sim_N OU modo B sim): "🟢 Mandei mensagem pro **<contact>**. Ele cuida do invite agora — vou parar de monitorar."
- **Brokered + status=monitoring** (modo A outra): "🟢 Mandei alternativas pro **<contact>**. Monitor `<cron_id_curto>` ativo até ele decidir."

Se `error`: reply literal o erro, NÃO retry.

## Guardrails

- Confirme `user == $SLACK_OWNER_USER_ID`. Nunca processe outros.
- Não processe se `thread_ts == ts` (Caso A: msg-pai não é reply).
- Reactions chegam independentemente — sempre cheque `status` antes de agir.
- Se exec falhar: poste erro no thread e pare. Não retry.
