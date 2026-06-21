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
# Versão do Graph API + base. O token de envio é POR CLIENTE
# (ClientIdentity.meta_access_token); aqui só ficam os knobs globais.
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")
META_GRAPH_BASE_URL = os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com")

# ── Evolution API (WhatsApp não-oficial, self-hosted) ──
# Modelo PLG: UM servidor Evolution da HUMA, muitos clientes. Cada cliente
# é uma "instance" (ClientIdentity.evolution_instance). URL + apikey global
# do servidor ficam aqui; o nome da instância vive no ClientIdentity.
# Vazio = canal Evolution indisponível (envio degrada gracioso, webhook 503).
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

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
# ── Estoque + Frete (Bling — Fase 2A + 2B) ──
# Token global = Fase 2A (dev/testes). Em produção, cada cliente HUMA
# tem o seu próprio token no ClientIdentity (preenchido via OAuth na
# Fase 2B). Vazio = capability SELL_PHYSICAL indisponível pra clientes
# que ainda não conectaram o Bling (modo no_credentials).
BLING_ACCESS_TOKEN = os.getenv("BLING_ACCESS_TOKEN", "")
BLING_BASE_URL = os.getenv("BLING_BASE_URL", "https://www.bling.com.br/Api/v3")

# Credenciais do APP HUMA cadastrado no Bling (developer.bling.com.br).
# Client ID é público (vai em URL OAuth); secret só no servidor.
# Redirect URI tem que bater LETRA POR LETRA com o cadastrado no app
# Bling — divergência = "redirect_uri_mismatch" e o callback nunca chega.
BLING_CLIENT_ID = os.getenv("BLING_CLIENT_ID", "")
BLING_CLIENT_SECRET = os.getenv("BLING_CLIENT_SECRET", "")
BLING_REDIRECT_URI = os.getenv("BLING_REDIRECT_URI", "")

# Endpoints OAuth do Bling V3. Confirmados via "Link de convite" do
# painel dev: https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id=...
# Permitimos override por env caso o Bling mude endpoints no futuro.
BLING_OAUTH_AUTHORIZE_URL = os.getenv(
    "BLING_OAUTH_AUTHORIZE_URL",
    "https://www.bling.com.br/Api/v3/oauth/authorize",
)
BLING_OAUTH_TOKEN_URL = os.getenv(
    "BLING_OAUTH_TOKEN_URL",
    "https://www.bling.com.br/Api/v3/oauth/token",
)

# State CSRF: TTL no Redis pra validar callback (10 min é folgado pra
# OAuth típico que leva 30-60s; protege contra replays tardios).
BLING_OAUTH_STATE_TTL_SEC = int(os.getenv("BLING_OAUTH_STATE_TTL_SEC", "600"))

# Margem de segurança pra refresh: refaz token quando faltar menos
# que isso pra expirar. 5 min cobre latência de rede + clock skew.
BLING_TOKEN_REFRESH_MARGIN_SEC = int(
    os.getenv("BLING_TOKEN_REFRESH_MARGIN_SEC", "300")
)

# ── CRM (espelhamento de pipeline — Fase CRM) ──
# Cada CRM é um app OAuth separado (client_id/secret próprios). O token
# por cliente vive no ClientIdentity (preenchido no callback do OAuth).
# Knobs compartilhados entre providers; credenciais de app são por
# provider. Vazio = OAuth daquele CRM indisponível no servidor.

# TTL do state CSRF no Redis pra validar callback OAuth do CRM.
CRM_OAUTH_STATE_TTL_SEC = int(os.getenv("CRM_OAUTH_STATE_TTL_SEC", "600"))

# Margem pra refresh do token de CRM (mesmo racional do Bling).
CRM_TOKEN_REFRESH_MARGIN_SEC = int(
    os.getenv("CRM_TOKEN_REFRESH_MARGIN_SEC", "300")
)

# Pipedrive (developer.pipedrive.com). Client ID público (vai na URL
# OAuth); secret só no servidor. Redirect URI tem que bater letra por
# letra com o cadastrado no app Pipedrive. base_url da API é por conta
# (company domain), descoberto no callback e guardado por cliente.
PIPEDRIVE_CLIENT_ID = os.getenv("PIPEDRIVE_CLIENT_ID", "")
PIPEDRIVE_CLIENT_SECRET = os.getenv("PIPEDRIVE_CLIENT_SECRET", "")
PIPEDRIVE_REDIRECT_URI = os.getenv("PIPEDRIVE_REDIRECT_URI", "")
PIPEDRIVE_OAUTH_AUTHORIZE_URL = os.getenv(
    "PIPEDRIVE_OAUTH_AUTHORIZE_URL",
    "https://oauth.pipedrive.com/oauth/authorize",
)
PIPEDRIVE_OAUTH_TOKEN_URL = os.getenv(
    "PIPEDRIVE_OAUTH_TOKEN_URL",
    "https://oauth.pipedrive.com/oauth/token",
)

# Webhook de atribuição (Pipedrive manda Basic auth se configurado na
# criação do webhook). Se ambos setados, a rota /webhook/crm/pipedrive
# exige Basic auth batendo; vazios = aceita sem auth (dev/sandbox).
PIPEDRIVE_WEBHOOK_USER = os.getenv("PIPEDRIVE_WEBHOOK_USER", "")
PIPEDRIVE_WEBHOOK_PASSWORD = os.getenv("PIPEDRIVE_WEBHOOK_PASSWORD", "")

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

# ── Factual Judge (regex determinístico) ──
# Detecta alucinação de entrega: IA fala "tá aqui o pix" / "olha o áudio"
# sem ter emitido a action correspondente (generate_payment / send_media /
# audio_text). Em mismatch → regenera com Sonnet (mesmo fluxo do pt_judge).
# Custo: zero (regex). Custo do retry: ~R$0,003 por turn que dispara.

# Liga/desliga a camada. Em emergência: FACTUAL_JUDGE_ENABLED=false
FACTUAL_JUDGE_ENABLED = os.getenv("FACTUAL_JUDGE_ENABLED", "true").lower() == "true"

# Timeout do retry com Sonnet quando juiz aponta mismatch.
# Default 8s — alinhado com PT_JUDGE_RETRY_TIMEOUT_SEC.
FACTUAL_JUDGE_RETRY_TIMEOUT_SEC = float(
    os.getenv("FACTUAL_JUDGE_RETRY_TIMEOUT_SEC", "8.0")
)
