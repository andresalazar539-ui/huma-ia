# ================================================================
# huma/config.py — Configuração central v8
# Meta Cloud API direto. Sem Z-API. Sem 360dialog.
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
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mercadopago")

# ── Agendamento ──
GOOGLE_CALENDAR_CREDENTIALS = os.getenv("GOOGLE_CALENDAR_CREDENTIALS", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DEFAULT_MEETING_PLATFORM = os.getenv("DEFAULT_MEETING_PLATFORM", "google_meet")
ZOOM_API_KEY = os.getenv("ZOOM_API_KEY", "")

# ── Segurança ──
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
SAFE_MODE = os.getenv("SAFE_MODE", "false").lower() == "true"

# ── Modelos de IA ──
AI_MODEL_PRIMARY = os.getenv("AI_MODEL_PRIMARY", "claude-sonnet-4-5-20250929")  # Sonnet (complexo)
AI_MODEL_FAST = os.getenv("AI_MODEL_FAST", "claude-haiku-4-5-20251001")        # Haiku (simples)

# ── Rate Limiting ──
RATE_LIMIT_MAX_MSGS = int(os.getenv("RATE_LIMIT_MAX_MSGS", "10"))
RATE_LIMIT_WINDOW_SEC = int(os.getenv("RATE_LIMIT_WINDOW_SEC", "60"))

# ── Histórico ──
HISTORY_WINDOW = 10
HISTORY_MAX_BEFORE_COMPRESS = 14
DEDUP_WINDOW_SEC = 30

# ── App ──
APP_VERSION = "8.0.0"
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
