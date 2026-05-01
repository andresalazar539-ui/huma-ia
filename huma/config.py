# ================================================================
# huma/config.py — Configuração central v8.1
# v8.1: HISTORY_WINDOW 10→6, HISTORY_MAX_BEFORE_COMPRESS 14→10
#   Compressão já preserva contexto via lead_facts e summary.
#   6 mensagens recentes são suficientes pro Claude manter o fio.
#   Economia: ~1000 tokens/chamada de histórico.
# ================================================================

import os
from dotenv import load_dotenv

load_dotenv()

# ── APIs Principais ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# ── Meta WhatsApp Cloud API ──
META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "huma_verify_2026")

# ── Twilio (teste via Sandbox) ──
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "")

# ── Voz (ElevenLabs) ──
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
# ── Transcrição de áudio ──
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# ── Pagamentos ──
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
# Sprint 1 / item 2 — webhook secret pra validar HMAC do MP (diferente do access_token)
# Configurar no painel MP: Webhooks → Configurações → "Sua chave secreta"
MERCADOPAGO_WEBHOOK_SECRET = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mercadopago")

# ── Agendamento ──
GOOGLE_CALENDAR_CREDENTIALS = os.getenv("GOOGLE_CALENDAR_CREDENTIALS", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DEFAULT_MEETING_PLATFORM = os.getenv("DEFAULT_MEETING_PLATFORM", "google_meet")
ZOOM_API_KEY = os.getenv("ZOOM_API_KEY", "")

# ── Segurança ──
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
SAFE_MODE = os.getenv("SAFE_MODE", "false").lower() == "true"

# Sprint 1 / item 8 — playground protegido em produção
# Em prod: PLAYGROUND_ENABLED=false (default) → endpoint trancado
# Em dev: PLAYGROUND_ENABLED=true + PLAYGROUND_TOKEN setado → exige header X-Playground-Token
PLAYGROUND_ENABLED = os.getenv("PLAYGROUND_ENABLED", "false").lower() == "true"
PLAYGROUND_TOKEN = os.getenv("PLAYGROUND_TOKEN", "")

# ── Modelos de IA ──
AI_MODEL_PRIMARY = os.getenv("AI_MODEL_PRIMARY", "claude-sonnet-4-5-20250929")  # Sonnet (complexo)
AI_MODEL_FAST = os.getenv("AI_MODEL_FAST", "claude-haiku-4-5-20251001")        # Haiku (simples)

# ── Rate Limiting ──
RATE_LIMIT_MAX_MSGS = int(os.getenv("RATE_LIMIT_MAX_MSGS", "10"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))

# ── Histórico ──
# v8.2 (memória pré-GA):
#   - HISTORY_WINDOW 4 → 8: mantém ~4 trocas completas após compressão
#     (lead pergunta → IA responde × 4). Antes 4 cobria só 2 trocas.
#   - HISTORY_MAX_BEFORE_COMPRESS 6 → 14: conversas até 14 msgs ficam
#     INTEIRAS sem compressão. Cobre ~95% das vendas WhatsApp típicas.
#
# Motivação: dono reportou IA esquecendo info depois de 10-12 msgs.
# Diagnóstico identificou compressão precoce + summary não-acumulativo.
# Trade-off de custo: +200-300 tokens por chamada Haiku (~R$0.001 extra
# por turn). Vale ouro vs. churn por "vendedor que esqueceu o lead".
HISTORY_WINDOW = 8
HISTORY_MAX_BEFORE_COMPRESS = 14
DEDUP_WINDOW_SEC = 30

# ── App ──
APP_VERSION = "8.1.0"
APP_TITLE = "HUMA IA"
APP_DESCRIPTION = "Clone inteligente para vendas e atendimento"

# ── Message Buffer ──
BUFFER_WAIT_SECONDS = int(os.getenv("BUFFER_WAIT_SECONDS", "8"))
BUFFER_MAX_WAIT_SECONDS = int(os.getenv("BUFFER_MAX_WAIT_SECONDS", "60"))

# ── Validação ──
# Só crasha se faltar o essencial. Redis e ElevenLabs são opcionais pra teste.
REQUIRED_VARS = {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
}

_missing = [k for k, v in REQUIRED_VARS.items() if not v]
if _missing:
    import logging
    logging.warning(f"Variáveis faltando (app pode ter funcionalidade limitada): {', '.join(_missing)}")

# Redis é opcional — se não tiver, features de cache/rate limit ficam desabilitadas
if not REDIS_URL:
    REDIS_URL = ""
    import logging
    logging.warning("REDIS_URL não configurado. Cache e rate limit desabilitados.")
    # ── ElevenLabs ──
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

# ── PT-BR Judge (LLM-as-judge) ──
# Avaliador de português que roda APÓS cada resposta do Haiku.
# Se detectar erro → orchestrator regenera com Sonnet.
# Em qualquer falha (timeout, parse) → mantém Haiku (degrade gracioso).

# Liga/desliga a camada inteira. Em emergência: PT_JUDGE_ENABLED=false
PT_JUDGE_ENABLED = os.getenv("PT_JUDGE_ENABLED", "true").lower() == "true"

# Timeout do juiz (Haiku avaliando). Default 3s — Haiku responde em <1s típico.
PT_JUDGE_TIMEOUT_SEC = float(os.getenv("PT_JUDGE_TIMEOUT_SEC", "3.0"))

# Timeout do retry com Sonnet quando juiz aponta erro.
# Default 8s — Sonnet leva 3-5s típico, 8s dá margem.
PT_JUDGE_RETRY_TIMEOUT_SEC = float(os.getenv("PT_JUDGE_RETRY_TIMEOUT_SEC", "8.0"))
