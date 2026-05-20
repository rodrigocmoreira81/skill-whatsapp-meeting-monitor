# Branches do monitor por contato

O `run_monitor.py` (rodando a cada 15min pra cada pedido ativo) decide **uma** branch por execução. Cada branch tem condições de entrada e ações.

## Curto-circuito (antes das branches)

| Condição | Ação |
|---|---|
| `status` já terminal (`invite_created`, `cancelled`, `declined`, `expired`, `user_handles`) | Cron disable + exit |
| `now > expires_at_utc` | Mark `expired` + cron disable + Slack aviso + exit |
| `check_replies_generic.py` falhou | Slack aviso + exit (sem mudar estado) |
| `newReplies` vazio | Exit (status `no_replies`) |

Watermark é avançado ANTES de classificar replies — garante idempotência.

## As 6 branches (em ordem de prioridade)

### Pré-passo: resolver `modality=tbd`

Se `modality` ainda é `tbd` (pedido não disse se quer presencial ou online):

- Reply contém `online|meet|video|call|chamada` → `set-modality meet`
- Reply contém `savassi|escritório|hiker|<office>` → `set-modality presencial_hiker`
- Reply contém `presencial` mas sem local claro → followup "Onde?"
- Reply confirma slot/email mas sem indicar preferência → followup "Online ou presencial?"

### Branch E — Cancelou

**Pattern:** `não vai dar|deixa pra próxima|desisti|cancela|não consigo|fica pra outra`

**Ação:**
1. `update_request.py mark-terminal --status cancelled`
2. `openclaw cron disable <monitor_cron_id>`
3. Slack: "🗑️ Fulano cancelou: _<snippet>_"

### Branch A — Slot confirmado + email disponível

**Pattern:** match em `slots_offered` (via `sim 1`, "quinta 10h", ou label completa) E (email no JSON existente OU email regex bate na reply)

**Ação:**
1. `update_request.py set-chosen-slot --index <N>`
2. `update_request.py set-email --email <novo>` (se veio na reply)
3. `create_calendar_invite.py` → cria evento no Calendar
4. `update_request.py mark-terminal --status invite_created`
5. `openclaw cron disable`
6. Slack: "✅ Invite criado com Fulano (qui 10h30). Link: ..."

### Branch B — Slot confirmado, sem email

**Ação:**
1. `update_request.py set-chosen-slot --index <N>`
2. `update_request.py send-followup --text "Perfeito. Me manda teu email pra eu te mandar o invite."`
3. Slack: "💬 Fulano confirmou qui 10h30. Pedi o email."
4. Monitor continua ativo (esperando email)

### Branch C — Email enviado, sem slot

**Ação:**
1. `update_request.py set-email --email <email>`
2. Slack: "💬 Fulano mandou email mas ainda não confirmou slot. Aguardando."
3. Monitor continua ativo

### Branch D — Pediu fora dos slots

**Pattern:** `outra (opção|data|hora)|outro (dia|horário)|não posso (nesses|nessas)|daria (segunda|terça...)`

**Ação:**
1. Slack: "⚠️ Fulano pediu fora dos slots ofertados: _<snippet>_. Quer que eu proponha alternativas?"
2. Status inalterado (monitor continua, espera você decidir)

**Nota:** essa branch é onde o sistema NÃO é autônomo de fato — espera decisão humana. Possível evolução: detectar branch D e propor 3 novos slots automaticamente (ver "Limitações" no README).

### Branch F — Ambíguo (fallback)

Qualquer reply que não bate nas anteriores.

**Pattern típico:** "vou ver", "talvez", "lembra de me cobrar", "depois te falo"

**Ação:**
1. Slack: "💬 Fulano respondeu ambíguo: _<snippet>_. Quer que eu cobre ou aguarda?"
2. Status inalterado

## Tabela de saída JSON

Cada run retorna stdout JSON:

```json
{
  "ok": true,
  "branch": "A_invite_created" | "B_slot_no_email" | "C_email_no_slot"
          | "D_outro_horario" | "E_cancelled" | "F_ambiguo"
          | "no_replies" | "terminal_short_circuit" | "expired",
  "request_id": "20260520-fulano-...",
  "slot_index": 0,            // se aplicável
  "dry_run": false,
  "errors": []
}
```

Auditável: cron de monitoria pode contar branches por dia, alertar se algum cron está sempre em F, etc.
