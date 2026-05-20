---
name: whatsapp-meeting-spawn
description: Dado um meeting-request com status=approved, busca slots no Calendar, envia mensagem inicial via WhatsApp, e cria cron de monitor */15min seguindo o PLAYBOOK. Chamado pela skill meeting-request-approve.
---

# whatsapp-meeting-spawn

## WHEN

Chame esta skill **apenas** quando a `meeting-request-approve` aprovou um pedido (`status=approved`) e precisa disparar o ciclo de marcação.

Você recebe um `--id <request_id>`. Nada mais.

## ACTION (1 chamada só)

```
exec(command="set -a && . /root/.openclaw/.env && set +a && python3 /root/.openclaw/workspace/skills/whatsapp-meeting-spawn/spawn_monitor_cron.py --id <request_id>")
```

Esse script faz tudo:
1. `check_calendar_slots.py` (busca 3 slots reais; usa `--lunch` se modality=almoco)
2. `send_initial_whatsapp.py` (envia mensagem real pro contato; grava slots_offered, last_outbound_*, expires_at_utc, status=monitoring; sorteia um local de almoço se almoço)
3. `openclaw cron add` com payload de monitor genérico (`monitor_payload_template.txt`), cadência `*/15 8-22 * * *`
4. Grava `monitor_cron_id` no JSON

**Saída esperada (stdout JSON):**
```json
{"ok": true, "id": "...", "monitor_cron_id": "...", "slots_count": 3, "evolution_message_id": "...", "expires_at_utc": "...", "status": "monitoring"}
```

**Se `error` retornado:** reporte literal no thread do Slack do request (`slack_thread_id`) e pare. Não tente segunda chamada — o script é idempotente em alguns passos (Evolution não), mas é mais seguro pedir intervenção humana.

## Pós-execução

Reply no thread do Slack do request (target=`slack_channel_id`, threadId=`slack_thread_id`):

> "🟢 Marcação iniciada com **<contact>**. Enviei {N} opções pelo WhatsApp. Monitor cron `<id_curto>` ativo (a cada 15min, expira em <expires_at>). Atualizo aqui quando ela responder."

## Tom e contexto do contato

- Antes de enviar opções, `spawn_monitor_cron.py` tenta encontrar email existente do contato via `gog gmail search`. Se achar, grava no JSON e a mensagem não pede email de novo.
- `send_initial_whatsapp.py` consulta mensagens recentes enviadas por Rodrigo para o JID e reaproveita a saudação usada ali (`Ei`, `Fala`, `Oi`, `Olá`). Se não houver histórico claro, usa `Ei` para primeiros nomes femininos conhecidos e `Fala` como fallback.
- Para pedidos `tbd`, pergunte preferência online vs presencial; se presencial, peça o local. Só crie invite quando houver slot + email conhecido/recebido + modalidade/local resolvidos.

## Guardrails

- NUNCA chame esta skill com `status != approved` — o script aborta com erro.
- NUNCA edite os arquivos auxiliares (check_calendar_slots/send_initial/etc) sem revisar o PLAYBOOK.
- O monitor cron criado é responsabilidade dele self-disable — não tente desabilitar manualmente daqui.
