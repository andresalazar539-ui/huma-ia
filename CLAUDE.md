# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Sobre este arquivo (ler primeiro):** O Claude Code lê este `CLAUDE.md` automaticamente no início de toda sessão. Ele é **guia, não fonte de verdade — quando este arquivo e o código divergirem, o código vence.** Por isso ele evita cravar dados voláteis (números de versão, contagens exatas de token, listas que mudam a cada deploy) e prefere apontar para o arquivo onde a verdade vive. Atualize-o sempre que fizer uma mudança *estrutural* que uma sessão futura precise saber (matar um tier, novo arquivo sensível, novo contrato de retorno).
> _Última revisão de freshness: 2026-05-23._

## Project Overview

HUMA IA is a WhatsApp-based AI sales clone platform. Each "client" (business owner) gets an AI clone that handles WhatsApp conversations with leads through a configurable sales funnel. The system uses Anthropic Claude as the AI backbone, Supabase for persistence, Redis for caching/rate-limiting, and integrates with WhatsApp (Meta Cloud API + Twilio sandbox), Mercado Pago payments, Google Calendar scheduling, and ElevenLabs voice cloning.

The codebase is written in **Brazilian Portuguese** (comments, variable names, UI strings, log messages). All user-facing text must remain in Portuguese.

## Commands

```bash
# Run locally
uvicorn huma.app:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest huma/tests/ -v

# Run a single test class or method
pytest huma/tests/test_huma.py::TestFunnel -v
pytest huma/tests/test_huma.py::TestFunnel::test_committed_stage_exists -v

# Install dependencies
pip install -r requirements.txt
```

## Architecture

### Request Flow

1. WhatsApp message arrives at `/api/message` or `/webhook/twilio`
2. `routes/api.py` validates and delegates to `core/orchestrator.py`
3. Orchestrator buffers rapid-fire messages (8s window via `message_buffer`), then processes as one
4. Orchestrator loads client identity from Supabase, checks rate limits/dedup/silent hours
5. `services/ai_service.py` builds a system prompt from client identity + funnel state + conversation history, calls Anthropic Claude
6. AI response is parsed for structured output (reply text, intent, sentiment, stage_action, lead_facts, actions)
7. Orchestrator applies stage transitions, runs PRE-FLIGHT, dispatches actions, sends reply via WhatsApp, optionally generates cloned audio

### Sales Funnel (`core/funnel.py`)

Stages: **discovery -> offer -> closing -> committed -> won / lost**

- Claude (the AI) can advance leads up to `committed`. The `won` transition is **system-only** (triggered by confirmed payment via Mercado Pago webhook).
- `lost` is terminal but allows reactivation.
- Each stage has psychology-driven instructions, required qualifications, and forbidden actions.
- Business owners can override the default funnel with a custom `FunnelConfig`.

### Tiered Intelligence

The system uses cost/complexity tiers to balance latency and quality. **A fonte de verdade da seleção é `_select_tier()` em `orchestrator.py` — consulte-o antes de assumir quais tiers existem.** Snapshot atual:

- **Tier 0** (no LLM): Deterministic responses for greetings, FAQ, price queries, hours — resolved in `conversation_intelligence.py`.
- **Tier 2** (Haiku): Standard conversation — `build_static_prompt + build_dynamic_prompt`.
- **Tier 3** (Sonnet): Full intelligence + learned insights + lead profiling + image intelligence — for objections, complex closing, images.

> **Tier 1 foi DESCONTINUADO na v11.2.** Permanece apenas como fallback defensivo no `ai_service.py` (se alguém chamar com `tier=1`, degrada para single-string). Não trate o Tier 1 como caminho ativo nem adicione lógica nova nele.

Prompt caching (`cache_control: ephemeral`, ttl `1h`) está ativo no bloco estático dos tiers 2 e 3. Os mínimos de elegibilidade de cache são **específicos por modelo** — ver regra #4 e `min_chars_for_cache` no `ai_service.py`.

### Camada de qualidade de português (PT judge)

Há um LLM-as-judge que avalia a saída do Haiku em busca de erro ortográfico (logger `huma.pt_judge`). Em veredito de erro, regenera com Sonnet dentro de um timeout curto; em falha dupla, degrada graciosamente mantendo a resposta original. Relevante porque adiciona uma possível segunda chamada de IA ao fluxo do orchestrator. _(Confirmar o módulo exato no repo antes de editar.)_

### Key Models (`models/schemas.py`)

- **ClientIdentity**: The central configuration model. Controls everything: tone, products, funnel, payment methods, scheduling, emoji usage, lead collection fields, silent hours, personality traits, voice cloning settings.
- **Conversation**: Per-lead state including history, stage, lead_facts, follow-up count, and `active_appointment_*` (event id / data do agendamento ativo) usado pelo PRE-FLIGHT e pelo cancelamento real.
- **MessagePayload/MessageResponse**: Webhook input/output.

### Services Layer (`services/`)

- `ai_service.py` — System prompt construction, Claude API calls (Sonnet for complex, Haiku for simple), history compression, tool definition, `generate_response`
- `whatsapp_service.py` — Message sending via Meta Cloud API and Twilio
- `db_service.py` — Supabase operations (clients, conversations, campaigns)
- `redis_service.py` — Rate limiting, dedup, pending approvals, message locking
- `payment_service.py` — Mercado Pago integration (Pix, boleto, credit card)
- `scheduling_service.py` — Google Calendar appointment creation, FreeBusy checks, cancel/update
- `audio_service.py` — ElevenLabs voice cloning
- `transcription_service.py` — Audio-to-text (Groq/OpenAI fallback)
- `billing_service.py` — Credit/plan middleware
- `message_buffer.py` — Aggregates rapid messages before processing
- `attribution_service.py` — Origem do lead (first-touch): referral CTWA (Meta/Evolution), código `#h` de link rastreável, `utm_*`. Alimenta a seção "Origem" dos relatórios. Captura disparada pelos webhooks em `routes/api.py` (gate barato `has_signal`), grava via `db_service.set_lead_source` (nunca sobrescreve origem existente)
- `learning_engine.py` — Analyzes completed conversations for insights
- `sales_intelligence.py` / `conversation_intelligence.py` / `image_intelligence.py` — Specialized AI analysis

> Outras peças vistas em produção (confirmar caminho exato no repo): detector de loop (`huma.loop_detector`, registra acionamentos da safety net do `check_availability`) e resolvedor de datas (`huma.date_resolver`, normaliza datas estruturadas antes do agendamento).

### Deployment

Deployed on **Railway** via Nixpacks. Config in `railway.toml` and `nixpacks.toml`. Health check at `/health`. Auto-deploy a partir da branch `main`.

## Key Design Decisions

- **Approval mode**: Clients can run in `auto` (AI sends directly) or `approval` (owner reviews before sending). Corrections in approval mode feed back into the AI as learning examples.
- **Two AI models**: `AI_MODEL_PRIMARY` (Sonnet, complex reasoning) e `AI_MODEL_FAST` (Haiku, simple tasks), configurados via env var. A string do modelo **não** deve ser hardcoded — trocar de versão (ex.: Sonnet 4.5 → 4.6) é mudança de env var no Railway, não de código.
- **PT judge**: a qualidade ortográfica do Haiku é garantida por um juiz LLM com regeneração via Sonnet, não por regex/hardcode. Preferir resiliência estrutural a remendos.
- **Message buffer**: Leads often send multiple short WhatsApp messages in sequence. The buffer waits 8s of silence before combining and processing as one message.
- **Atribuição de origem é FIRST-TOUCH**: `lead_source/lead_source_detail/lead_source_ref` na Conversation são gravados uma vez (na chegada do lead) e nunca sobrescritos. Contrato importante: `save_conversation` só inclui esses campos no upsert quando `lead_source` está preenchido — incluir `""` apagaria uma origem gravada em paralelo pelo `attribution_service.capture` (background task do webhook). Não "simplificar" isso.
- **Required env vars**: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`. Redis and other services are optional (features degrade gracefully).
- **Tests are unit-only**: Tests mock external services. No integration tests requiring live Supabase/Redis. `conftest.py` sets fake env vars.

---

## RULES FOR CLAUDE CODE (READ BEFORE EVERY TASK)

These rules are not suggestions. They come from real production incidents in this codebase. Follow them literally.

### 1. Inviolable contracts — never change these shapes

**`ai.generate_response()` return dict** must always have these exact keys:
```
reply, reply_parts, intent, sentiment, stage_action, confidence,
lead_facts, actions, micro_objective, emotional_reading, audio_text
```
Any caller (`orchestrator.py`, tests, future features) depends on this. Never remove a key. If adding a key, default it to empty/neutral so old callers don't break.

**`_build_reply_tool_compact()` — the `actions` field description is STRUCTURAL, not decorative.** The description tells Claude that each action must have a `type` field plus specific keys. Even if a SPEC says "compress all descriptions to save tokens", **NEVER** strip or shorten the description of the `actions` field. Dropping the `type` instruction silently breaks appointments, payments, and media — Claude returns actions without `type`, and `action.get("type", "")` returns empty, so the action is dropped into `remaining_actions` with no error.

Rule of thumb: **description = decoration** for scalar enums like `intent`, `sentiment`. **description = structural instruction** for `actions` arrays and anything where the shape isn't inferable from the field name.

### 2. Sensitive files — extra caution required

Touching any of these requires mapping the full impact before editing:

- `huma/core/orchestrator.py` — controls message flow, stage transitions, PRE-FLIGHT scheduling, action dispatch, check_availability marker + turn-1 suppression + safety net
- `huma/services/ai_service.py` — prompt builders, tool definition, `generate_response`, tier selection inputs, prompt caching
- `huma/services/scheduling_service.py` — Google Calendar integration, FreeBusy checks
- `huma/services/payment_service.py` — Mercado Pago, Pix, boleto
- `huma/services/conversation_intelligence.py` — deterministic classification (Tier 0)
- `huma/core/funnel.py` — stage graph and transition rules
- `huma/core/orchestrator.py::_handle_cancel_appointment_action` — v12 (6.C): executa delete REAL no Google Calendar via `sched.cancel_appointment(event_id)`. Limpa `active_appointment_*` + reset `cancel_attempts` + stage=lost APENAS em sucesso do delete. Em falha de rede: mantém estado intacto pra permitir retry, retorna mensagem de instabilidade. 404/410 são idempotentes (tratados como sucesso).
- `huma/services/scheduling_service.py::cancel_appointment` — entry point pro delete. Nunca propaga exception: sempre retorna dict `{status, detail}`. Erros HTTP 404/410 são considerados sucesso (evento já não existe = estado final desejado).

Before editing any of these:
1. Read the full file first
2. `grep` for every caller of any function you plan to change
3. Verify Redis keys, Supabase columns, webhook shapes still match

### 3. The PRE-FLIGHT of scheduling is sacred

In `_send_with_human_delay` inside `orchestrator.py`, `_preflight_appointment` runs **before** the reply is sent. If it detects a conflict, the Claude reply is **discarded** and a conflict message is sent instead. Do not:
- Move the PRE-FLIGHT call after `wa.send_text`
- Skip the PRE-FLIGHT for "performance"
- Trust Claude's `"vou verificar"` reply as proof of availability

A IA **nunca** afirma disponibilidade ou ocupação que não foi verificada pela ferramenta. Quem decide livre/ocupado é o Google Calendar (FreeBusy via `scheduling_service`), nunca a prosa do modelo. The PRE-FLIGHT adds ~300ms, which is <3% of total latency. The value is preventing false confirmations to customers.

### 4. Prompt caching (tiers 2 and 3)

The `system` parameter for tiers 2 and 3 is structured as two blocks:
```python
system_blocks = [
    {"type": "text", "text": static, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    {"type": "text", "text": dynamic},
]
```
**Never** convert this back to a single string for those tiers.

**O mínimo de tokens pra cache é específico do modelo — não existe um número único.** A verdade vive em `min_chars_for_cache` no `ai_service.py`. Snapshot atual: **Haiku exige ~4096 tokens (~9000 chars)**; **Sonnet exige ~1024 tokens (~2400 chars)**. Se o bloco estático ficar abaixo do mínimo do modelo em uso, o cache não é criado e cada mensagem paga input cheio. Antes de encurtar qualquer coisa no bloco estático, confirme o mínimo do modelo-alvo no código — não confie em número decorado aqui.

### 5. Prompt compression — what is negotiable and what is not

**Never compress or remove** (these are non-negotiable quality rules):
- The 14 absolute rules (anti-em-dash, no English, no markdown, no robotic phrases)
- Tone of voice rules per vertical
- Funnel stage instructions (current stage + neighbors)
- `build_autonomy_prompt` content (scheduling, discount, data collection rules)
- Anti-hallucination rules ("NUNCA invente preço", "NUNCA confirme horário", "NUNCA invente disponibilidade")
- The structural description of `actions` in the tool

**Safe to compress or make conditional**:
- Image intelligence (only when `image_url` is present)
- Learned insights (only on Tier 3)
- Lead profiling (only on Tier 3)
- Market analysis verbosity (can be compressed to 1-line format)
- Speech patterns (can be trimmed to top 5)

### 6. Bug fixes must be surgical

When fixing a bug:
1. Identify the root cause (not the symptom)
2. Change only what's needed to fix it
3. Do **not** refactor adjacent code, rename variables, reorder imports, or "clean up" the file
4. If you notice an improvement opportunity, add it as a "MELHORIA SUGERIDA" note at the end of the response — do not implement it in the same commit

Refactoring during a bug fix is how regressions happen.

### 7. Backwards compatibility on function signatures

Never add a required parameter to an existing function. Always default new parameters:
```python
# WRONG
def process(phone, text, client_id, bsuid):  # breaks all callers
    ...

# RIGHT
def process(phone, text, client_id, bsuid: str | None = None):
    ...
```
Before changing any function signature, `grep -r "function_name(" huma/` and verify every caller.

### 8. Database changes are additive only

Supabase migrations in production must be non-blocking:
```sql
-- CORRECT — additive, idempotent, non-blocking
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS new_field TEXT;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x ON conversations(new_field) WHERE new_field IS NOT NULL;

-- WRONG — blocking, non-idempotent
ALTER TABLE conversations ADD COLUMN new_field TEXT NOT NULL;
```
Never drop a column in the same deploy that removes its usage — deprecate first, drop in the next deploy.

### 9. Code completeness

- Never deliver code with `...` or `# resto do código igual`. André does not write code — he copies and pastes exactly what you deliver. Partial files cause errors.
- When modifying a function, deliver the entire function. When modifying a file meaningfully, deliver the entire file.
- Type hints are required on all function signatures.
- Docstrings required on public/exported functions.
- No hardcoded credentials — use env vars from `huma/config.py`.
- Logs use structured format: `log.info(f"Category | phone={phone} | key=value | ...")`.

### 10. Error handling specificity

Never `try/except Exception` without logging context. Prefer:
```python
try:
    result = await api_call()
except httpx.TimeoutException:
    log.error(f"Timeout | service=elevenlabs | phone={phone}")
    return None
except httpx.HTTPStatusError as e:
    log.error(f"HTTP {e.response.status_code} | service=elevenlabs | phone={phone}")
    raise
except Exception as e:
    log.critical(f"Unexpected | service=elevenlabs | phone={phone} | {type(e).__name__}: {e}")
    raise
```
Generic `except Exception: pass` is forbidden.

### 11. When a SPEC instruction feels wrong, stop

SPECs are written by humans and can contain dangerous generalizations. If you read a SPEC rule that seems to violate one of the rules above, **stop and ask before executing**. Example: a SPEC saying "compress all tool descriptions" is wrong if applied to the `actions` field (rule #1).

When in doubt, preserve current behavior.

### 12. Verification before commit

Before considering any task complete:
- [ ] `pytest huma/tests/ -v` passes
- [ ] `grep -r "renamed_function" huma/` shows no stale references
- [ ] No new `TODO` or `FIXME` added without a corresponding issue
- [ ] Redis keys used follow existing patterns (`category:id:field`)
- [ ] Logs added for any new external API call
- [ ] The change was scoped to the files explicitly requested — no drive-by edits