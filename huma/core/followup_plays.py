# ================================================================
# huma/core/followup_plays.py — Jogadas de follow-up (catálogo + regras)
#
# Módulo PURO (só stdlib). O follow-up deixou de ser "uma situação só"
# (lead sumiu, 2 tentativas, 72h) e virou um conjunto de JOGADAS, uma
# por situação real do negócio. O dono liga e desliga cada jogada,
# escolhe a intensidade e, nas jogadas de ciclo, de quantos em quantos
# dias o cliente costuma voltar.
#
# Guardado em clients.followup_config (JSONB, DEFAULT '{}'; migration
# scripts/migration_followup_v2.sql):
#
#   {
#     "intensity": "padrao",            # leve | padrao | persistente
#     "hour_start": 9, "hour_end": 20,  # janela de envio, Brasília
#     "weekend": true,                  # manda sábado e domingo?
#     "paid_ok": false,                 # oficial: aceita follow-up pago
#                                       # (template) depois das 24h?
#     "plays": {
#       "sumiu_na_conversa": {"on": true},
#       "hora_de_voltar": {"on": true, "cycle_days": 30, "intensity": "leve"}
#     }
#   }
#
# CONTRATO DE COMPATIBILIDADE: config vazio ({}) = só a jogada
# "sumiu_na_conversa" ligada, intensidade "padrao" (2 tentativas). É o
# comportamento de antes da feature. Nenhuma jogada nova manda mensagem
# pra lead de cliente existente sem o dono ligar.
#
# Quem decide o TEXTO é a IA (ai_service), com o objetivo da jogada e o
# tom do dono. Aqui mora só o que é regra: quais jogadas existem, pra
# quem fazem sentido, quando cada passo vence e quando a jogada para.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_BR_TZ = timezone(timedelta(hours=-3))

INTENSITY_LEVE = "leve"
INTENSITY_PADRAO = "padrao"
INTENSITY_PERSISTENTE = "persistente"
VALID_INTENSITIES: tuple[str, ...] = (INTENSITY_LEVE, INTENSITY_PADRAO, INTENSITY_PERSISTENTE)

INTENSITY_LABELS: dict[str, str] = {
    INTENSITY_LEVE: "Leve",
    INTENSITY_PADRAO: "Padrão",
    INTENSITY_PERSISTENTE: "Persistente",
}

DEFAULT_HOUR_START = 9
DEFAULT_HOUR_END = 20

# Janela de texto livre do WhatsApp oficial: depois de 24h da última
# mensagem DO LEAD só sai template (pago). A margem evita mandar no
# minuto em que a janela fecha.
META_FREE_WINDOW_HOURS = 24.0
META_SAFE_WINDOW_HOURS = 23.0
# Primeiro passo que cairia em cima ou depois do fechamento da janela é
# puxado pra cá no oficial: sai de graça em vez de virar template.
META_LAST_FREE_STEP_HOURS = 20.0

MIN_CYCLE_DAYS = 7
MAX_CYCLE_DAYS = 730

# Ids das jogadas (estáveis: vão pro banco e pro relatório).
PLAY_SUMIU = "sumiu_na_conversa"
PLAY_PRECO = "sumiu_no_preco"
PLAY_CHAMAR_DEPOIS = "pediu_pra_chamar_depois"
PLAY_PAGAMENTO = "pagamento_pendente"
PLAY_CANCELOU = "cancelou_horario"
PLAY_VOLTAR = "hora_de_voltar"
PLAY_PERDIDO = "quem_desistiu"

# Silêncio mínimo (horas) antes do primeiro passo, por tipo de negócio.
# Mesmos valores que o scheduler usava (_VERTICAL_FOLLOWUP_MIN_HOURS).
VERTICAL_BASE_HOURS: dict[str, float] = {
    "ecommerce": 1, "restaurante": 1, "salao_barbearia": 2, "pet": 3,
    "clinica": 4, "automotivo": 4, "academia_personal": 6, "servicos": 6,
    "outros": 6, "advocacia_financeiro": 12, "educacao": 12, "imobiliaria": 24,
}
DEFAULT_BASE_HOURS = 4.0

# De quantos em quantos dias o cliente costuma voltar, por tipo de
# negócio (só sugestão inicial; o dono ajusta). 0 = não faz sentido
# sugerir ciclo pra esse tipo de negócio.
VERTICAL_CYCLE_DAYS: dict[str, int] = {
    "salao_barbearia": 30, "pet": 30, "clinica": 180, "academia_personal": 0,
    "ecommerce": 60, "restaurante": 21, "automotivo": 180, "servicos": 0,
    "educacao": 0, "advocacia_financeiro": 0, "imobiliaria": 0, "outros": 0,
}


@dataclass(frozen=True)
class Play:
    """Uma jogada de follow-up: a situação, pra quem vale e o ritmo."""
    id: str
    name: str                 # título na tela
    situation: str            # "quando acontece", em linguagem do dono
    what_huma_does: str       # o que a HUMA faz, em uma frase
    # Passos em HORAS depois do gatilho, por intensidade. Lista vazia =
    # a jogada calcula os passos de outro jeito (ver steps_for).
    steps: dict[str, tuple[float, ...]] = field(default_factory=dict)
    # Precisa de pelo menos UMA destas capabilities ("" = qualquer conta).
    needs_any: tuple[str, ...] = ()
    # Fala com quem já é cliente (True) ou com lead (False).
    for_customers: bool = False
    uses_cycle: bool = False
    # Objetivo que vai pro prompt da IA (instrução SE/QUANDO).
    objective: str = ""
    # Objetivo da ÚLTIMA tentativa (despedida com porta aberta).
    last_objective: str = ""


_DESPEDIDA = (
    "Esta é a ÚLTIMA tentativa. Despedida ELEGANTE: mostre que percebeu o "
    "silêncio, deixe a porta aberta ('qualquer coisa é só me chamar') e "
    "NÃO cobre resposta nem faça pergunta que exija decisão."
)

PLAYS: tuple[Play, ...] = (
    Play(
        id=PLAY_SUMIU,
        name="Parou de responder",
        situation="O lead estava conversando e sumiu no meio.",
        what_huma_does="Retoma o assunto exato em que a conversa parou.",
        objective=(
            "Reengajar sem parecer insistente. SE houver pendência ou assunto concreto "
            "na memória/conversa, retome ELE naturalmente. SE não houver, mande uma "
            "mensagem leve perguntando se ficou alguma dúvida. Termine com UMA pergunta leve."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_PRECO,
        name="Sumiu depois do preço",
        situation="O lead ouviu o valor e parou de responder.",
        what_huma_does="Volta mostrando o que está incluso e as formas de pagar, sem baixar preço por conta própria.",
        objective=(
            "O lead sumiu depois de saber o valor. NÃO repita o preço nem peça desculpa por ele. "
            "Retome pelo RESULTADO que ele queria. SE o negócio tiver parcelamento ou forma de "
            "pagamento listada acima, mencione UMA opção. SÓ ofereça desconto SE houver desconto "
            "autorizado acima, e nunca acima do limite. Termine com UMA pergunta leve."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_CHAMAR_DEPOIS,
        name="Pediu pra chamar depois",
        situation="O lead disse \"me chama semana que vem\" ou \"depois do dia 5\".",
        what_huma_does="Espera a data que ele pediu e chama no dia, lembrando o combinado.",
        steps={
            INTENSITY_LEVE: (0.0,),
            INTENSITY_PADRAO: (0.0, 48.0),
            INTENSITY_PERSISTENTE: (0.0, 48.0, 120.0),
        },
        objective=(
            "O lead PEDIU pra ser chamado nesta data. Abra lembrando disso de forma natural "
            "('você pediu pra eu te chamar essa semana') e retome o assunto que ficou combinado. "
            "NÃO peça desculpa por chamar. Termine com UMA pergunta leve."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_PAGAMENTO,
        name="Gerou o pagamento e não pagou",
        situation="O Pix, boleto ou link foi gerado e o pagamento não entrou.",
        what_huma_does="Lembra com jeito e se oferece pra gerar de novo ou tirar a dúvida que travou.",
        needs_any=("sell_digital", "sell_physical"),
        steps={
            INTENSITY_LEVE: (1.0,),
            INTENSITY_PADRAO: (1.0, 24.0),
            INTENSITY_PERSISTENTE: (1.0, 24.0, 72.0),
        },
        objective=(
            "O lead recebeu o pagamento e ainda não pagou. Pergunte se deu algum problema ou se "
            "ficou dúvida, e se ofereça pra ajudar a concluir. NÃO invente prazo, estoque acabando "
            "nem desconto. NÃO diga que o pagamento venceu SE isso não estiver escrito acima."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_CANCELOU,
        name="Cancelou o horário",
        situation="O cliente cancelou o horário que tinha marcado.",
        what_huma_does="Oferece remarcar sem cobrar explicação.",
        needs_any=("schedule",),
        steps={
            INTENSITY_LEVE: (24.0,),
            INTENSITY_PADRAO: (24.0, 120.0),
            INTENSITY_PERSISTENTE: (24.0, 120.0, 336.0),
        },
        objective=(
            "O lead tinha um horário e cancelou. NÃO cobre explicação nem demonstre chateação. "
            "Ofereça remarcar. NUNCA afirme que um horário está livre: pergunte qual dia ou período "
            "fica melhor pra ele."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_VOLTAR,
        name="Hora de voltar",
        situation="Já é cliente e passou o tempo em que ele costuma voltar (por exemplo, 3 meses depois da compra).",
        what_huma_does="Chama o cliente de volta no ciclo certo e se oferece pra resolver ali mesmo. É a reativação por ciclo de compra.",
        for_customers=True,
        uses_cycle=True,
        steps={
            INTENSITY_LEVE: (0.0,),
            INTENSITY_PADRAO: (0.0, 168.0),
            INTENSITY_PERSISTENTE: (0.0, 168.0, 504.0),
        },
        objective=(
            "Esta pessoa JÁ É CLIENTE e faz um tempo que não volta. Cumprimente como quem conhece. "
            "SE a memória disser o que ela comprou ou fez, cite isso. Lembre que está na época de "
            "voltar e pergunte se quer resolver. NÃO invente promoção nem data."
        ),
        last_objective=_DESPEDIDA,
    ),
    Play(
        id=PLAY_PERDIDO,
        name="Quem desistiu",
        situation="O lead foi dado como perdido há mais de um mês.",
        what_huma_does="Faz uma única tentativa, leve, de reabrir a conversa.",
        steps={
            INTENSITY_LEVE: (720.0,),
            INTENSITY_PADRAO: (720.0,),
            INTENSITY_PERSISTENTE: (720.0, 2160.0),
        },
        objective=(
            "Este lead desistiu há semanas. Mensagem CURTA e leve, sem cobrar nada. SE a memória "
            "disser o que ele procurava, cite. Pergunte se ainda faz sentido conversar. "
            "NÃO invente novidade, promoção nem mudança de preço."
        ),
        last_objective=_DESPEDIDA,
    ),
)

_PLAYS_BY_ID: dict[str, Play] = {p.id: p for p in PLAYS}

# O que vem sugerido (botão "Ligar o recomendado") por tipo de negócio.
# PLAY_SUMIU está sempre ligada: é o follow-up que já existia.
_RECOMMENDED: dict[str, tuple[str, ...]] = {
    "ecommerce": (PLAY_SUMIU, PLAY_PRECO, PLAY_PAGAMENTO, PLAY_CHAMAR_DEPOIS, PLAY_VOLTAR),
    "restaurante": (PLAY_SUMIU, PLAY_PAGAMENTO, PLAY_VOLTAR),
    "salao_barbearia": (PLAY_SUMIU, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS, PLAY_VOLTAR),
    "pet": (PLAY_SUMIU, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS, PLAY_VOLTAR),
    "clinica": (PLAY_SUMIU, PLAY_PRECO, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS, PLAY_VOLTAR),
    "automotivo": (PLAY_SUMIU, PLAY_PRECO, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS),
    "academia_personal": (PLAY_SUMIU, PLAY_PRECO, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS, PLAY_PERDIDO),
    "servicos": (PLAY_SUMIU, PLAY_PRECO, PLAY_CHAMAR_DEPOIS, PLAY_PERDIDO),
    "educacao": (PLAY_SUMIU, PLAY_PRECO, PLAY_PAGAMENTO, PLAY_CHAMAR_DEPOIS, PLAY_PERDIDO),
    "advocacia_financeiro": (PLAY_SUMIU, PLAY_CHAMAR_DEPOIS),
    "imobiliaria": (PLAY_SUMIU, PLAY_CHAMAR_DEPOIS, PLAY_PERDIDO),
    "outros": (PLAY_SUMIU, PLAY_PRECO, PLAY_CHAMAR_DEPOIS),
}


# ----------------------------------------------------------------
# Leitura do catálogo
# ----------------------------------------------------------------

def get_play(play_id: str) -> Optional[Play]:
    """Jogada pelo id (None se não existe)."""
    return _PLAYS_BY_ID.get(str(play_id or ""))


def _category_key(category: Any) -> str:
    """Slug da categoria a partir do enum, string ou None ("outros" no vazio)."""
    value = getattr(category, "value", category)
    key = str(value or "").strip().lower()
    return key if key in VERTICAL_BASE_HOURS else "outros"


def _caps_set(capabilities: Any) -> set[str]:
    """Capabilities como conjunto de slugs (aceita enum, string, lista, None)."""
    out: set[str] = set()
    for c in capabilities or []:
        value = getattr(c, "value", c)
        if isinstance(value, str) and value.strip():
            out.add(value.strip().lower())
    return out


def base_hours(category: Any) -> float:
    """Silêncio mínimo (horas) antes do primeiro follow-up, pelo tipo de negócio."""
    return float(VERTICAL_BASE_HOURS.get(_category_key(category), DEFAULT_BASE_HOURS))


def default_cycle_days(category: Any) -> int:
    """Ciclo sugerido (dias) pro tipo de negócio; 0 quando não há sugestão."""
    return int(VERTICAL_CYCLE_DAYS.get(_category_key(category), 0))


def play_available(play: Play, capabilities: Any) -> bool:
    """A jogada faz sentido pra essa conta? (depende do que a HUMA faz nela)"""
    if not play.needs_any:
        return True
    return bool(_caps_set(capabilities) & set(play.needs_any))


def recommended_plays(category: Any, capabilities: Any = None) -> list[str]:
    """
    Ids das jogadas sugeridas pro tipo de negócio, já sem as que a conta
    não consegue usar (ex.: "cancelou o horário" sem agenda ligada).
    """
    ids = _RECOMMENDED.get(_category_key(category), _RECOMMENDED["outros"])
    out: list[str] = []
    for pid in ids:
        play = _PLAYS_BY_ID[pid]
        if play.uses_cycle and default_cycle_days(category) <= 0:
            continue
        if play_available(play, capabilities):
            out.append(pid)
    return out


# ----------------------------------------------------------------
# Configuração do dono
# ----------------------------------------------------------------

def _clean_intensity(raw: Any, fallback: str = INTENSITY_PADRAO) -> str:
    value = str(raw or "").strip().lower()
    return value if value in VALID_INTENSITIES else fallback


def _clean_hour(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    hour = int(raw)
    return hour if 0 <= hour <= 23 else fallback


def _clean_cycle(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    days = int(raw)
    if days <= 0:
        return fallback
    return max(MIN_CYCLE_DAYS, min(MAX_CYCLE_DAYS, days))


def normalize_config(raw: Any, category: Any = None) -> dict:
    """
    Valida e completa a configuração que vem da tela (ou do banco).

    Tolerante por desenho: qualquer coisa que não seja dict vira o
    padrão; campo inválido cai no padrão; jogada desconhecida é
    descartada. Config vazio = só "sumiu_na_conversa" ligada, que é o
    follow-up de antes da feature. Idempotente.

    Args:
        raw: valor cru de clients.followup_config.
        category: tipo de negócio (define o ciclo sugerido).

    Returns:
        Dict completo: intensity, hour_start, hour_end, weekend, paid_ok
        e plays (todas as jogadas do catálogo, cada uma com on,
        intensity e, nas de ciclo, cycle_days).
    """
    data = raw if isinstance(raw, dict) else {}
    intensity = _clean_intensity(data.get("intensity"))
    hour_start = _clean_hour(data.get("hour_start"), DEFAULT_HOUR_START)
    hour_end = _clean_hour(data.get("hour_end"), DEFAULT_HOUR_END)
    if hour_start >= hour_end:
        hour_start, hour_end = DEFAULT_HOUR_START, DEFAULT_HOUR_END

    raw_plays = data.get("plays") if isinstance(data.get("plays"), dict) else {}
    plays: dict[str, dict] = {}
    for play in PLAYS:
        item = raw_plays.get(play.id) if isinstance(raw_plays.get(play.id), dict) else {}
        # Só a jogada que já existia nasce ligada.
        default_on = play.id == PLAY_SUMIU
        on = item.get("on") if isinstance(item.get("on"), bool) else default_on
        entry: dict[str, Any] = {
            "on": on,
            "intensity": _clean_intensity(item.get("intensity"), intensity),
        }
        if play.uses_cycle:
            entry["cycle_days"] = _clean_cycle(item.get("cycle_days"), default_cycle_days(category))
            if entry["cycle_days"] <= 0:
                entry["on"] = False
        plays[play.id] = entry

    return {
        "intensity": intensity,
        "hour_start": hour_start,
        "hour_end": hour_end,
        "weekend": data.get("weekend") if isinstance(data.get("weekend"), bool) else True,
        "paid_ok": data.get("paid_ok") is True,
        "plays": plays,
    }


def with_recommended(raw: Any, category: Any, capabilities: Any = None) -> dict:
    """Config com as jogadas sugeridas pro tipo de negócio LIGADAS (botão da tela)."""
    config = normalize_config(raw, category)
    for pid in recommended_plays(category, capabilities):
        config["plays"][pid]["on"] = True
    return config


def play_is_on(config: Any, play_id: str, category: Any = None, capabilities: Any = None) -> bool:
    """A jogada está ligada E a conta consegue usar?"""
    play = get_play(play_id)
    if play is None:
        return False
    if not play_available(play, capabilities):
        return False
    normalized = normalize_config(config, category)
    return bool(normalized["plays"].get(play_id, {}).get("on"))


# ----------------------------------------------------------------
# Ritmo: quando cada passo vence
# ----------------------------------------------------------------

_SUMIU_LADDER: tuple[float, ...] = (24.0, 72.0, 168.0, 336.0)
_SUMIU_STEPS_BY_INTENSITY: dict[str, int] = {
    INTENSITY_LEVE: 1, INTENSITY_PADRAO: 2, INTENSITY_PERSISTENTE: 4,
}


def steps_for(
    play_id: str,
    intensity: str = INTENSITY_PADRAO,
    category: Any = None,
    official: bool = False,
) -> list[float]:
    """
    Passos da jogada, em HORAS depois do gatilho, em ordem crescente.

    "Parou de responder" e "Sumiu depois do preço" começam no silêncio
    mínimo do tipo de negócio (loja 1h, clínica 4h, imobiliária 24h) e
    seguem a escada 1 dia, 3 dias, 7 dias, 14 dias.

    No WhatsApp oficial, o primeiro passo que cairia em cima do
    fechamento da janela de 24h é puxado pra 20h: sai de graça em vez
    de virar template pago.

    Args:
        play_id: id da jogada.
        intensity: leve | padrao | persistente.
        category: tipo de negócio.
        official: True quando a conta usa o WhatsApp oficial (Meta).

    Returns:
        Lista de horas ([] se a jogada não existe).
    """
    play = get_play(play_id)
    if play is None:
        return []
    level = _clean_intensity(intensity)

    if play.steps:
        return [float(h) for h in play.steps.get(level, play.steps[INTENSITY_PADRAO])]

    first = base_hours(category)
    if official and first >= META_SAFE_WINDOW_HOURS:
        first = META_LAST_FREE_STEP_HOURS
    total = _SUMIU_STEPS_BY_INTENSITY[level]
    out = [first]
    for hours in _SUMIU_LADDER:
        if len(out) >= total:
            break
        # Passo colado no anterior (menos de 12h) não é follow-up, é insistência.
        if hours >= out[-1] + 12.0:
            out.append(hours)
    return out


def is_last_step(play_id: str, step: int, intensity: str, category: Any = None) -> bool:
    """True quando `step` (começa em 0) é a última tentativa da jogada."""
    total = len(steps_for(play_id, intensity, category))
    return total > 0 and step >= total - 1


def objective_for(play_id: str, step: int, intensity: str, category: Any = None) -> str:
    """
    Objetivo que vai pro prompt da IA neste passo. Jogada de uma
    tentativa só usa o objetivo normal (não abre com despedida).
    """
    play = get_play(play_id)
    if play is None:
        return ""
    total = len(steps_for(play_id, intensity, category))
    if total > 1 and step >= total - 1 and play.last_objective:
        return play.last_objective
    return play.objective


# Depois que o passo vence, por quanto tempo ele ainda pode sair. Passou
# disso, a HUMA não manda mensagem atrasada (evita rajada em conversa
# velha quando o dono muda a intensidade).
STEP_GRACE_HOURS = 48.0
LEGACY_MAX_SILENCE_HOURS = 72.0


def step_is_due(hours_silent: Optional[float], steps: list[float], attempt: int) -> bool:
    """
    O passo `attempt` (começa em 0) da jogada "Parou de responder" pode
    sair agora? Sai quando o silêncio já chegou no passo e ainda está
    dentro da tolerância. Sem saber o tempo de silêncio, sai (mesma
    postura do job antigo).
    """
    if attempt < 0 or attempt >= len(steps):
        return False
    if hours_silent is None:
        return True
    start = steps[attempt]
    limit = max(LEGACY_MAX_SILENCE_HOURS, start + STEP_GRACE_HOURS)
    return start <= hours_silent <= limit


def max_silence_hours() -> float:
    """Maior silêncio que ainda pode gerar follow-up de "Parou de responder"."""
    return _SUMIU_LADDER[-1] + STEP_GRACE_HOURS


def max_sumiu_steps() -> int:
    """Número de tentativas da intensidade mais alta."""
    return max(_SUMIU_STEPS_BY_INTENSITY.values())


_PRICE_STAGES = ("offer", "closing")


def looks_like_price_silence(stage: Any, history: Any) -> bool:
    """
    O lead sumiu depois de ouvir o preço? Verdadeiro quando a conversa
    está em oferta ou fechamento e a última fala da HUMA traz um valor
    em reais. Markers internos ("[PAGAMENTO ...]") não contam.
    """
    if str(stage or "") not in _PRICE_STAGES:
        return False
    for entry in reversed(history or []):
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        content = entry.get("content")
        if not isinstance(content, str) or content.lstrip().startswith("["):
            continue
        return "R$" in content
    return False


def has_custom_window(raw: Any) -> bool:
    """O dono escolheu horário de envio? (sem escolha, vale só o horário de silêncio)"""
    return isinstance(raw, dict) and ("hour_start" in raw or "hour_end" in raw or "weekend" in raw)


# ----------------------------------------------------------------
# Janela de envio (Brasília) e janela grátis do oficial
# ----------------------------------------------------------------

def _to_brt(moment: datetime) -> datetime:
    """Instante em Brasília. Naive é tratado como UTC (padrão do banco)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_BR_TZ)


def inside_send_window(config: Any, now: Optional[datetime] = None) -> bool:
    """Agora é hora de mandar follow-up? (janela do dono, em Brasília)"""
    cfg = normalize_config(config)
    local = _to_brt(now or datetime.now(timezone.utc))
    if not cfg["weekend"] and local.weekday() >= 5:
        return False
    return cfg["hour_start"] <= local.hour < cfg["hour_end"]


def next_send_time(config: Any, due: datetime) -> datetime:
    """
    Primeiro instante em que dá pra mandar, a partir de `due`. Dentro da
    janela devolve o próprio `due`; fora, o começo da próxima janela.
    Devolve datetime aware em UTC.
    """
    cfg = normalize_config(config)
    local = _to_brt(due)
    for _ in range(9):
        weekend_blocked = not cfg["weekend"] and local.weekday() >= 5
        if not weekend_blocked:
            if local.hour < cfg["hour_start"]:
                local = local.replace(hour=cfg["hour_start"], minute=0, second=0, microsecond=0)
            if cfg["hour_start"] <= local.hour < cfg["hour_end"]:
                return local.astimezone(timezone.utc)
        local = (local + timedelta(days=1)).replace(
            hour=cfg["hour_start"], minute=0, second=0, microsecond=0,
        )
    return local.astimezone(timezone.utc)


def free_text_allowed(
    official: bool,
    last_lead_message_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> bool:
    """
    Texto livre ainda entrega? Fora do oficial, sempre. No oficial, só
    até 24h depois da última mensagem DO LEAD (com margem). Sem saber
    quando o lead escreveu, no oficial a resposta é não.
    """
    if not official:
        return True
    if last_lead_message_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    last = last_lead_message_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours = (current - last).total_seconds() / 3600.0
    return 0 <= hours < META_SAFE_WINDOW_HOURS


# ----------------------------------------------------------------
# Tela: o catálogo já pronto pro Cockpit desenhar
# ----------------------------------------------------------------

def _describe_hours(hours: float) -> str:
    """'na hora' / '1 hora depois' / '3 dias depois' / '2 semanas depois'."""
    if hours <= 0:
        return "na hora"
    if hours < 24:
        n = int(round(hours))
        return "1 hora depois" if n <= 1 else f"{n} horas depois"
    days = int(round(hours / 24))
    if days < 14:
        return "1 dia depois" if days == 1 else f"{days} dias depois"
    if days < 28:
        weeks = int(round(days / 7))
        return f"{weeks} semanas depois"
    months = int(round(days / 30))
    return "1 mês depois" if months == 1 else f"{months} meses depois"


def describe_steps(play_id: str, intensity: str, category: Any = None, official: bool = False) -> list[str]:
    """Os passos em palavras, pra tela ("4 horas depois", "1 dia depois")."""
    return [_describe_hours(h) for h in steps_for(play_id, intensity, category, official)]


def catalog_for_screen(
    config: Any,
    category: Any = None,
    capabilities: Any = None,
    official: bool = False,
) -> list[dict]:
    """
    Lista de jogadas pro Cockpit: nome, situação, o que a HUMA faz, se
    está ligada, se é sugerida pro tipo de negócio, se a conta consegue
    usar, os passos em palavras e quantos passos saem como template
    pago no oficial.
    """
    normalized = normalize_config(config, category)
    suggested = set(recommended_plays(category, capabilities))
    out: list[dict] = []
    for play in PLAYS:
        entry = normalized["plays"][play.id]
        hours = steps_for(play.id, entry["intensity"], category, official)
        # Jogadas pra cliente e pra quem desistiu começam longe da última
        # mensagem do lead: no oficial, todo passo é template.
        always_paid = official and (play.for_customers or play.id in (PLAY_PERDIDO, PLAY_CANCELOU, PLAY_CHAMAR_DEPOIS))
        paid_steps = 0
        if official:
            paid_steps = len(hours) if always_paid else sum(1 for h in hours if h >= META_SAFE_WINDOW_HOURS)
        item = {
            "id": play.id,
            "name": play.name,
            "situation": play.situation,
            "what_huma_does": play.what_huma_does,
            "on": bool(entry["on"]),
            "intensity": entry["intensity"],
            "recommended": play.id in suggested,
            "available": play_available(play, capabilities),
            "needs_any": list(play.needs_any),
            "for_customers": play.for_customers,
            "uses_cycle": play.uses_cycle,
            "steps": describe_steps(play.id, entry["intensity"], category, official),
            "paid_steps": paid_steps,
        }
        if play.uses_cycle:
            item["cycle_days"] = entry.get("cycle_days", 0)
        out.append(item)
    return out
