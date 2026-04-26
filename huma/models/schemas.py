# ================================================================
# huma/models/schemas.py — Todos os modelos de dados
#
# Pydantic models pra validação de entrada/saída.
# Inclui autonomia do dono (v6.1.2).
# ================================================================

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ================================================================
# ENUMS
# ================================================================

class CloneMode(str, Enum):
    """Modo de operação do clone."""
    AUTO = "auto"          # Envia respostas automaticamente
    APPROVAL = "approval"  # Dono aprova antes de enviar


class MessagingStyle(str, Enum):
    """Estilo de envio de mensagens no WhatsApp."""
    SPLIT = "split"    # Quebra em várias msgs curtas (recomendado)
    SINGLE = "single"  # Uma msg só


class OnboardingStatus(str, Enum):
    """Status do onboarding do cliente."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SANDBOX = "sandbox"  # Testando antes de ativar
    ACTIVE = "active"    # Produção


class BusinessCategory(str, Enum):
    """Categoria do negócio — define perguntas de onboarding e base vertical."""
    CLINICA = "clinica"
    ECOMMERCE = "ecommerce"
    IMOBILIARIA = "imobiliaria"
    SERVICOS = "servicos"
    EDUCACAO = "educacao"
    RESTAURANTE = "restaurante"
    SALAO_BARBEARIA = "salao_barbearia"
    ADVOCACIA_FINANCEIRO = "advocacia_financeiro"
    ACADEMIA_PERSONAL = "academia_personal"
    PET = "pet"
    AUTOMOTIVO = "automotivo"
    OUTROS = "outros"  # IA pesquisa e aprende o mercado dinamicamente


class Intent(str, Enum):
    """Intenção detectada na mensagem do lead."""
    PRICE = "price"
    BUY = "buy"
    OBJECTION = "objection"
    SCHEDULE = "schedule"
    SUPPORT = "support"
    NEUTRAL = "neutral"


class Sentiment(str, Enum):
    """Sentimento detectado no lead."""
    FRUSTRATED = "frustrated"
    ANXIOUS = "anxious"
    EXCITED = "excited"
    COLD = "cold"
    NEUTRAL = "neutral"


class OutboundStatus(str, Enum):
    """Status de um lead em campanha outbound."""
    PENDING = "pending"
    SENT = "sent"
    REPLIED = "replied"
    FOLLOW_UP = "follow_up"
    CONVERTED = "converted"
    STOPPED = "stopped"


# ================================================================
# PAYLOADS DE ENTRADA (API)
# ================================================================

class MessagePayload(BaseModel):
    """Mensagem recebida do WhatsApp via webhook."""
    client_id: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=8)
    text: str = Field(default="", max_length=800)
    image_url: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        return v.replace("+", "").replace(" ", "").replace("-", "")

    def has_content(self) -> bool:
        return bool(self.text.strip()) or bool(self.image_url)


class MessageResponse(BaseModel):
    """Resposta do endpoint de mensagem."""
    status: str = "ok"
    message_id: Optional[str] = None


class ApprovalPayload(BaseModel):
    """Payload pra aprovar/rejeitar resposta pendente."""
    client_id: str
    phone: str
    approved: bool = True
    edited_text: Optional[str] = None  # Se editou, salva como correção


class WhatsAppImportPayload(BaseModel):
    """Texto exportado do WhatsApp pra análise de padrões."""
    chat_text: str = Field(..., min_length=100, max_length=500_000)


# ================================================================
# FUNIL
# ================================================================

class FunnelStageConfig(BaseModel):
    """
    Configuração de um estágio do funil.

    required_qualifications (v6.0.2):
        Lista de informações que a IA DEVE coletar antes de avançar.
        Se o dono não quer que pergunte nada, deixa vazio.
    """
    name: str
    goal: str = ""
    instructions: str = ""
    triggers_to_advance: str = ""
    triggers_to_stop: str = ""
    forbidden_actions: str = ""
    max_messages_in_stage: int = 0
    required_qualifications: list[str] = Field(default_factory=list)


class FollowUpRule(BaseModel):
    """Regra de follow-up automático."""
    name: str = ""
    hours_after_silence: int = 24
    message_template: str = ""
    max_attempts: int = 2
    stop_if: str = ""


class FunnelConfig(BaseModel):
    """Configuração completa do funil customizado pelo dono."""
    stages: list[FunnelStageConfig] = Field(default_factory=list)
    follow_up_rules: list[FollowUpRule] = Field(default_factory=list)
    global_stop_triggers: list[str] = Field(default_factory=list)


# ================================================================
# OUTBOUND
# ================================================================

class OutboundLead(BaseModel):
    """Lead pra campanha de prospecção ativa."""
    phone: str
    name: str = ""
    business_name: str = ""
    business_type: str = ""
    website: str = ""
    estimated_revenue: str = ""
    notes: str = ""
    status: OutboundStatus = OutboundStatus.PENDING
    attempts: int = 0
    last_attempt_at: Optional[datetime] = None

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        return v.replace("+", "").replace(" ", "").replace("-", "")


class OutboundCampaign(BaseModel):
    """Campanha de prospecção outbound."""
    campaign_id: str = ""
    client_id: str = ""
    name: str = ""
    message_template: str = ""
    follow_up_template: str = ""
    follow_up_hours: int = 48
    max_follow_ups: int = 2
    leads: list[OutboundLead] = Field(default_factory=list)
    daily_send_limit: int = 50
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ================================================================
# MÍDIA (v6.1.0)
# ================================================================

class MediaAsset(BaseModel):
    """
    Foto ou vídeo que o dono faz upload pra HUMA enviar.

    Tags descritivas permitem a IA buscar o criativo certo.
    Ex: tags=["antes e depois", "laser"] → IA envia quando lead
    perguntar sobre resultados.
    """
    asset_id: str = ""
    client_id: str = ""
    name: str = ""
    url: str = ""
    media_type: str = "image"  # "image" ou "video"
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ================================================================
# HORÁRIO DE FUNCIONAMENTO (v12 / fix 7.6)
# ================================================================

class TimeWindow(BaseModel):
    """Janela de atendimento. Ex: start='08:00', end='12:00'."""
    start: str  # "HH:MM"
    end: str    # "HH:MM"


class HolidayRule(BaseModel):
    """
    Exceção a um dia específico (feriado, férias, dia especial).
    - closed=True + windows vazio: dia totalmente fechado.
    - closed=False + windows preenchido: dia tem janelas diferentes (ex: meio-período).
    """
    date: str                              # "YYYY-MM-DD"
    closed: bool = True
    reason: str = ""                       # Ex: "Tiradentes"
    windows: list[TimeWindow] = Field(default_factory=list)


class BusinessScheduleConfig(BaseModel):
    """
    Horário semanal + feriados. Todos os campos opcionais.

    weekly: lista de 7 posições (0=seg ... 6=dom); cada posição é uma lista
    de janelas abertas. Lista vazia = fechado naquele dia-da-semana.
    Ex (seg-sex 8-12 / 14-18, sáb 8-12, dom fechado):
      [
        [{"start":"08:00","end":"12:00"},{"start":"14:00","end":"18:00"}],  # seg
        [...], [...], [...], [...],
        [{"start":"08:00","end":"12:00"}],                                   # sáb
        []                                                                   # dom
      ]

    Se weekly não tem 7 itens, sistema usa fallback seg-sex 8h-18h.
    """
    weekly: list[list[TimeWindow]] = Field(default_factory=list)
    holidays: list[HolidayRule] = Field(default_factory=list)
    appointment_duration_minutes: int = 60


# ================================================================
# AGENDAMENTO (v6.1.0)
# ================================================================

class SchedulingRequest(BaseModel):
    """
    Dados pra confirmar agendamento.
    Quais campos são obrigatórios depende do scheduling_required_fields
    do ClientIdentity — o dono decide.
    """
    client_id: str = ""
    phone: str = ""
    lead_name: str = ""
    lead_email: str = ""
    lead_phone_confirmed: bool = False
    service: str = ""
    date_time: str = ""
    meeting_platform: str = ""
    notes: str = ""
    location: str = ""  # Endereço presencial (clínica, salão, etc). Vai pro Google Calendar.
    lead_context: str = ""  # Resumo do que o lead quer / dor / perfil. Vai pra descrição do evento.
    schedule_config: Optional[BusinessScheduleConfig] = None  # v12 (fix 7.6) — passa config do dono pra validação


# ================================================================
# PAGAMENTO (v6.1.1)
# ================================================================

class PaymentRequest(BaseModel):
    """
    Dados pra gerar cobrança.
    payment_method: "pix", "boleto", ou "credit_card"
    amount_cents: valor em centavos (35000 = R$350,00)
    lead_cpf: obrigatório pra boleto. IA coleta na conversa.
    """
    client_id: str = ""
    phone: str = ""
    lead_name: str = ""
    description: str = ""
    amount_cents: int = 0
    payment_method: str = "pix"
    installments: int = 1
    lead_cpf: str = ""  # Obrigatório pra boleto


# ================================================================
# IDENTIDADE DO CLIENTE (com autonomia v6.1.2)
# ================================================================

class ClientIdentity(BaseModel):
    """
    Identidade completa do cliente (dono do negócio).

    Este é o modelo mais importante do sistema. Cada campo é
    configurável pelo dono no onboarding ou via API.

    Seções:
        - Básico: nome, categoria, descrição
        - Identidade: tom, saudação, palavras proibidas
        - Produtos: lista com nome, descrição, preço
        - Inteligência: FAQ, regras, competidores
        - Clone: modo de operação, estilo de msg
        - Voz: ElevenLabs voice_id
        - Aprendizado: correções do dono, padrões de fala
        - Funil: configuração customizada
        - Pagamento: métodos aceitos, parcelamento
        - Agendamento: plataforma, campos obrigatórios
        - Autonomia: personalidade, coleta, emojis, horário
    """
    # ── Básico ──
    client_id: str
    business_name: str = ""
    category: Optional[BusinessCategory] = None
    business_description: str = ""
    website: str = ""

    # ── Identidade ──
    tone_of_voice: str = ""
    greeting_style: str = ""
    forbidden_words: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    working_hours: str = ""
    custom_rules: str = ""

    # ── Produtos ──
    products_or_services: list[dict] = Field(default_factory=list)
    max_discount_percent: float = 0.0

    # ── Inteligência ──
    faq: list[dict] = Field(default_factory=list)
    enable_product_recommendations: bool = True
    enable_smart_recommendations: bool = True
    fallback_message: str = "Vou confirmar essa informação e já te retorno, ok?"

    # ── Clone ──
    clone_mode: CloneMode = CloneMode.APPROVAL
    messaging_style: MessagingStyle = MessagingStyle.SPLIT
    api_key: str = ""
    onboarding_status: OnboardingStatus = OnboardingStatus.PENDING
    onboarding_answers: dict = Field(default_factory=dict)

    # ── Voz ──
    enable_audio: bool = True
    voice_id: str = ""

    # ── Aprendizado ──
    correction_examples: list[dict] = Field(default_factory=list)
    speech_patterns: str = ""

    # ── Funil ──
    funnel_config: Optional[FunnelConfig] = None

    # ── Pagamento (v6.1.0) ──
    enable_payments: bool = False
    enable_scheduling: bool = False
    scheduling_platform: str = "google_meet"

    # ── WhatsApp Meta Cloud API (v8) ──
    waba_id: str = Field(
        default="",
        description="WhatsApp Business Account ID (criado via Embedded Signup).",
    )
    phone_number_id: str = Field(
        default="",
        description="Phone Number ID no Meta (gerado no Embedded Signup).",
    )
    owner_phone: str = Field(
        default="",
        description="Telefone do dono pra notificações (saldo, alertas).",
    )
    # Sprint 5 — opt-in por tipo de notificação. Defaults true: dono recebe
    # tudo até desligar conscientemente. notify_on_payment já era enviado.
    notify_owner_on_appointment: bool = Field(
        default=True,
        description="Sprint 5 / item 20 — notificar dono quando lead agenda.",
    )
    notify_owner_on_payment: bool = Field(
        default=True,
        description="Sprint 5 / item 21 — notificar dono quando lead paga.",
    )
    notify_owner_on_cancellation: bool = Field(
        default=True,
        description="Sprint 5 / item 22 — notificar dono quando lead cancela.",
    )
    notify_owner_on_stuck_lead: bool = Field(
        default=True,
        description=(
            "Sprint 6 / item 23 — notificar dono quando lead 'quente travado': "
            "stage offer/closing, 8+ msgs, parado há 2h+, sem agendar. "
            "Janela pra dono intervir manualmente antes do lead esfriar."
        ),
    )
    plan: str = Field(
        default="starter",
        description="Plano: starter, pro, scale, elite.",
    )

    # ── Autonomia do dono (v6.1.2) ──
    lead_collection_fields: list[str] = Field(
        default_factory=lambda: ["nome"],
        description="Dados que a IA deve coletar. Vazio = não pergunta nada.",
    )
    collect_before_offer: bool = Field(
        default=True,
        description="True = coleta antes de falar preço. False = coleta quando natural.",
    )
    accepted_payment_methods: list[str] = Field(
        default_factory=lambda: ["pix", "boleto", "credit_card"],
        description="Métodos aceitos. Vazio = não processa pagamento.",
    )
    max_installments: int = Field(
        default=10,
        description="Máximo de parcelas no cartão.",
    )
    scheduling_required_fields: list[str] = Field(
        default_factory=lambda: ["nome_completo", "email", "telefone_confirmado"],
        description="Dados obrigatórios pra agendar. Vazio = agenda direto.",
    )
    personality_traits: list[str] = Field(
        default_factory=list,
        description="Traços: ['engraçado'], ['sério', 'técnico'], ['acolhedor'].",
    )
    use_emojis: bool = Field(
        default=False,
        description="True = pode usar emojis. False = nunca.",
    )
    audio_trigger_stages: list[str] = Field(
        default_factory=lambda: ["closing", "won"],
        description="Estágios em que envia áudio clonado.",
    )
    regional_voices: dict = Field(
        default_factory=dict,
        description=(
            "Vozes regionais (plano Scale+). Mapa região→voice_id. "
            "Ex: {'nordeste': 'voice_123', 'sul': 'voice_456', 'default': 'voice_789'}. "
            "Se vazio, usa voice_id principal pra todo mundo."
        ),
    )
    silent_hours_start: str = Field(
        default="",
        description="Início do silêncio. Ex: '22:00'. Vazio = sem limite.",
    )
    silent_hours_end: str = Field(
        default="",
        description="Fim do silêncio. Ex: '07:00'. Vazio = sem limite.",
    )
    silent_hours_message: str = Field(
        default="Oi! Recebi sua mensagem. Te respondo em breve!",
        description="Mensagem automática fora do horário.",
    )
    market_analysis: dict = Field(
        default_factory=dict,
        description=(
            "Análise de mercado gerada pela IA no onboarding. "
            "Contém: contexto de mercado, público-alvo, perfis de cliente, "
            "objeções, argumentos, estratégia de vendas."
        ),
    )

    # v12 / fix 7.6 — estrutura completa de horário (opcional)
    # Se None, sistema usa fallback seg-sex 8h-18h (comportamento atual).
    # Dono preenche via UI depois com weekly[7] + holidays.
    business_schedule: Optional[BusinessScheduleConfig] = Field(
        default=None,
        description=(
            "Horário estruturado: weekly[7] (janelas por dia-da-semana) + "
            "holidays (exceções por data) + appointment_duration_minutes. "
            "None = fallback seg-sex 8h-18h. Dono preenche via API."
        ),
    )


# ================================================================
# CONVERSA
# ================================================================

class Conversation(BaseModel):
    """Estado de uma conversa entre HUMA e um lead."""
    client_id: str
    phone: str
    history: list[dict] = Field(default_factory=list)
    history_summary: str = ""
    stage: str = "discovery"
    lead_facts: list[str] = Field(default_factory=list)
    last_message_at: Optional[datetime] = None
    follow_up_count: int = 0
    is_outbound: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    active_appointment_event_id: str = ""  # event_id do Google Calendar do agendamento ativo. Vazio = nenhum ativo.
    active_appointment_datetime: str = ""  # Data/hora do agendamento ativo (string ISO). Pra logs e prompts.
    active_appointment_service: str = ""   # Nome do serviço agendado. Pra logs e prompts.
    cancel_attempts: int = 0               # v12 (6.B) — policy anti-churn. Conta tentativas do lead de cancelar. 0=nenhuma, 1-2=sinalizações, 3+=insistência. Reset em cancelamento executado ou reagendamento bem-sucedido.

    # v12 (fix 8) — dados do lead que NUNCA podem ser perdidos por compressão.
    # Populados quando Claude confirma create_appointment ou generate_payment.
    # Injetados no prompt como VERDADE — resolve alucinação de email/nome.
    lead_email: str = ""
    lead_name_canonical: str = ""
    lead_cpf: str = ""


# ================================================================
# APROVAÇÃO PENDENTE
# ================================================================

class PendingApproval(BaseModel):
    """Resposta da IA aguardando aprovação do dono."""
    client_id: str
    phone: str
    lead_message: str
    ai_response: str
    stage: str = "discovery"
    created_at: datetime = Field(default_factory=datetime.utcnow)
