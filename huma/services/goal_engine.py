# ================================================================
# huma/services/goal_engine.py — Motor de meta + leitura viva do lead
# (F4 do "Devorador de Metas")
#
# Três peças, todas baratas (zero chamada de API):
#
#   1. merge_lead_state — a tool send_reply devolve `lead_read` (modo,
#      pressa, humor, confiança, perfil, objeção ativa, sinal de compra)
#      a cada turno. Aqui isso vira ESTADO persistido em
#      Conversation.lead_state, com histórico curto de confiança e o
#      micro-objetivo anterior. Antes, emotional_reading/micro_objective
#      eram gerados e jogados fora: a IA recomeçava do zero todo turno.
#
#   2. build_lead_state_prompt / build_goal_prompt — entram no bloco
#      DINÂMICO: a leitura do turno anterior com regras CONDICIONAIS
#      (SE pressa alta → ...), e a META explícita derivada das
#      capabilities com o checklist determinístico do que ainda falta
#      pra bater ("PRA BATER A META FALTA: email, data/hora").
#
#   3. build_objection_plan — SÓ quando há objeção ativa: injeta a
#      técnica + molde do cérebro da vertical e a resposta instanciada
#      do playbook do negócio pra AQUELA objeção (~200 tokens, e só
#      quando precisa — memória: instrução nova é SE/QUANDO, nunca
#      SEMPRE).
#
# Contratos:
#   - Nenhuma função levanta exceção por dado malformado: lead_read
#     vem de LLM, lead_state vem do banco — tudo é saneado.
#   - Retorno "" quando não há o que dizer (não gasta token à toa).
# ================================================================

from __future__ import annotations

from datetime import datetime

from huma.core.capabilities import Capability, has_any_sell
from huma.models.schemas import ClientIdentity, Conversation
from huma.utils.logger import get_logger
from huma.verticals import find_objection, tokens

log = get_logger("goal_engine")

_MODOS = {"direto", "consultivo", "explorando"}
_PRESSA = {"alta", "normal", "baixa"}
_CONFIANCA = {"subindo", "estavel", "caindo"}
_HIST_CONFIANCA_MAX = 5
_STR_MAX = 80
_MICRO_OBJETIVO_MAX = 120


def _norm_enum(value, allowed: set[str], fallback: str) -> str:
    """Normaliza valor de enum vindo do LLM (acentos, caixa, lixo)."""
    v = str(value or "").strip().lower()
    v = v.replace("á", "a").replace("é", "e").replace("ê", "e").replace("í", "i")
    return v if v in allowed else fallback


# ================================================================
# 1. ESTADO DO LEAD
# ================================================================


def merge_lead_state(prev: dict | None, read: dict | None, micro_objective: str = "") -> dict:
    """
    Funde a leitura deste turno (`read`, vinda da tool) no estado anterior.

    Regras:
      - modo/pressa/confianca: enum saneado; valor inválido mantém o anterior.
      - humor e perfil: "sticky" (só mudam quando a IA manda algo).
      - objecao_ativa e sinal_de_compra: são DO TURNO — ausência = nenhuma.
      - micro_objective: vira `micro_objetivo`; o anterior vai pra
        `micro_objetivo_anterior` (continuidade de plano entre turnos).
      - historico_confianca: últimos 5 valores (detecta "caindo 2x").

    Args:
        prev: Conversation.lead_state atual (pode ser None/vazio/lixo).
        read: dict `lead_read` devolvido pela tool (pode ser None/lixo).
        micro_objective: campo micro_objective da mesma resposta.

    Returns:
        Novo dict de estado (sempre um dict válido).
    """
    state: dict = dict(prev) if isinstance(prev, dict) else {}
    read = read if isinstance(read, dict) else {}

    state["modo"] = _norm_enum(read.get("modo"), _MODOS, state.get("modo") or "explorando")
    state["pressa"] = _norm_enum(read.get("pressa"), _PRESSA, state.get("pressa") or "normal")
    state["confianca"] = _norm_enum(read.get("confianca"), _CONFIANCA, state.get("confianca") or "estavel")

    for key in ("humor", "perfil"):
        novo = str(read.get(key) or "").strip()[:_STR_MAX]
        if novo:
            state[key] = novo
        else:
            state.setdefault(key, "")

    state["objecao_ativa"] = str(read.get("objecao_ativa") or "").strip()[:_STR_MAX]
    state["sinal_de_compra"] = bool(read.get("sinal_de_compra")) if isinstance(read.get("sinal_de_compra"), bool) else False

    mo = str(micro_objective or "").strip()[:_MICRO_OBJETIVO_MAX]
    if mo:
        anterior = str(state.get("micro_objetivo") or "")
        if anterior and anterior != mo:
            state["micro_objetivo_anterior"] = anterior
        state["micro_objetivo"] = mo

    hist = state.get("historico_confianca")
    hist = [h for h in hist if isinstance(h, str)] if isinstance(hist, list) else []
    hist.append(state["confianca"])
    state["historico_confianca"] = hist[-_HIST_CONFIANCA_MAX:]

    try:
        state["turnos"] = int(state.get("turnos") or 0) + 1
    except (TypeError, ValueError):
        state["turnos"] = 1
    state["atualizado_em"] = datetime.utcnow().isoformat()

    return state


def _confianca_caindo_2x(state: dict) -> bool:
    hist = state.get("historico_confianca") or []
    return len(hist) >= 2 and hist[-1] == "caindo" and hist[-2] == "caindo"


def build_lead_state_prompt(conv: Conversation) -> str:
    """
    Bloco dinâmico com a leitura do turno anterior + regras condicionais.

    "" quando ainda não há leitura (primeiro turno) — custo zero.
    """
    state = conv.lead_state if isinstance(conv.lead_state, dict) else {}
    if not state.get("turnos"):
        return ""

    modo = state.get("modo") or "explorando"
    pressa = state.get("pressa") or "normal"
    humor = state.get("humor") or "?"
    confianca = state.get("confianca") or "estavel"
    perfil = state.get("perfil") or "não detectado"
    objecao = state.get("objecao_ativa") or ""
    sinal = bool(state.get("sinal_de_compra"))

    linhas = [
        "\nLEITURA DO LEAD (sua leitura no turno anterior; atualize no lead_read se mudou):",
        f"  modo {modo} | pressa {pressa} | humor {humor} | confiança {confianca} | perfil: {perfil} | "
        f"objeção ativa: {objecao or 'nenhuma'} | sinal de compra: {'sim' if sinal else 'não'}",
    ]

    mo = state.get("micro_objetivo") or ""
    if mo:
        linhas.append(
            f"  Seu micro-objetivo anterior: \"{mo}\". Conseguiu? Se sim, defina o próximo; "
            "se não, insista por outro ângulo (nunca repetindo a mesma frase)."
        )

    # Regras CONDICIONAIS — só as que se aplicam ao estado atual.
    if pressa == "alta":
        linhas.append("  SE pressa alta (é o caso) → 1 balão, resolva e ofereça o próximo passo. Sem pergunta extra.")
    if modo == "consultivo":
        linhas.append("  SE modo consultivo (é o caso) → explique UMA coisa por vez, sem empurrar; ele está se educando, não enrolando.")
    if modo == "direto":
        linhas.append("  SE modo direto (é o caso) → responda o que ele pediu e conduza; zero preâmbulo.")
    if confianca == "caindo" or _confianca_caindo_2x(state):
        linhas.append(
            "  SE confiança caindo (é o caso) → pare de vender agora. Reconheça, pergunte o que incomodou, "
            "recupere a relação antes de qualquer próximo passo."
        )
    if sinal:
        linhas.append("  SE sinal de compra (é o caso) → não explique mais; conduza pro fechamento com opção concreta.")
    if objecao:
        linhas.append("  SE objeção ativa (é o caso) → siga o PLANO PRA OBJEÇÃO ATIVA abaixo; não repita argumento já usado.")

    return "\n".join(linhas) + "\n"


# ================================================================
# 2. META EXPLÍCITA + CHECKLIST DO QUE FALTA
# ================================================================


def _fold(text: str) -> str:
    """Minúsculas sem acento — 'região' e 'regiao' são o mesmo campo."""
    import unicodedata

    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()


def _has_field(field: str, conv: Conversation) -> bool:
    """Heurística determinística: o dado já está na conversa?"""
    f = _fold(field).strip()
    if not f:
        return True
    facts = [_fold(x) for x in (conv.lead_facts or [])]

    if "email" in f or "e-mail" in f:
        return bool(conv.lead_email) or any("@" in x for x in facts)
    if "nome" in f:
        return bool(conv.lead_name_canonical) or any("nome" in x for x in facts)
    if "telefone" in f or "whatsapp" in f or "celular" in f:
        return True  # o número já é o WhatsApp da conversa
    if "cpf" in f:
        return bool(conv.lead_cpf) or any("cpf" in x for x in facts)
    if f in ("data/hora", "horário", "horario", "data"):
        return bool(conv.active_appointment_event_id)

    chave = f.replace("_", " ").split()[0]
    return any(chave in x for x in facts)


def _goal_and_fields(identity: ClientIdentity, conv: Conversation) -> tuple[str, list[str]]:
    """Meta principal (texto) + campos necessários pra batê-la."""
    caps = identity.capabilities_resolved
    metas: list[str] = []
    campos: list[str] = []

    if Capability.QUALIFY in caps:
        metas.append("lead QUALIFICADO entregue ao humano (action handoff_to_human com todos os campos obrigatórios)")
        campos.extend(identity.lead_collection_fields or ["nome", "interesse"])
    if Capability.SCHEDULE in caps:
        metas.append("agendamento CONFIRMADO pelo sistema (action create_appointment)")
        campos.extend(identity.scheduling_required_fields or ["nome", "email"])
        campos.append("data/hora")
    if has_any_sell(caps):
        metas.append("pagamento GERADO pelo sistema e pago (action generate_payment)")
        if not campos:
            campos.extend(identity.lead_collection_fields or [])

    # dedup preservando ordem
    vistos: set[str] = set()
    campos_unicos = [c for c in campos if not (c.lower() in vistos or vistos.add(c.lower()))]
    return " OU ".join(metas), campos_unicos


def build_goal_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Bloco dinâmico com a META da conversa e o checklist do que falta.

    "" quando o clone não tem capability de meta (SUPPORT puro) ou a
    conversa está em lost.
    """
    if conv.stage == "lost":
        return ""

    meta, campos = _goal_and_fields(identity, conv)
    if not meta:
        return ""

    if conv.stage in ("committed", "won"):
        return (
            "\nMETA DA CONVERSA: BATIDA. Agora é pós: confirme detalhes, acolha dúvidas, "
            "zero re-venda, zero novo link.\n"
        )

    faltando = [c for c in campos if not _has_field(c, conv)]
    linhas = [
        f"\nMETA DA CONVERSA: {meta}.",
        "  Toda mensagem encurta a distância até ela, sem atropelar o lead: o caminho é rapport → dado → próximo passo.",
    ]
    if faltando:
        linhas.append(f"  PRA BATER A META FALTA: {', '.join(faltando)}. Colete no momento natural, um por vez.")
    else:
        linhas.append("  Dados pra meta: completos. Conduza pro próximo passo concreto (horário, pagamento ou handoff).")
    return "\n".join(linhas) + "\n"


# ================================================================
# 3. PLANO PRA OBJEÇÃO ATIVA (condicional)
# ================================================================


def _best_playbook_objection(objecao: str, playbook: dict | None) -> tuple[str, str] | None:
    """Objeção do playbook do negócio que mais casa com a ativa (overlap de tokens)."""
    if not isinstance(playbook, dict):
        return None
    alvo = tokens(objecao)
    if not alvo:
        return None
    melhor: tuple[int, str, str] | None = None
    for item in playbook.get("objecoes") or []:
        if not isinstance(item, dict):
            continue
        texto = str(item.get("objecao") or "")
        resposta = str(item.get("resposta_exemplo") or "")
        if not texto or not resposta:
            continue
        score = len(alvo & tokens(texto))
        if score and (melhor is None or score > melhor[0]):
            melhor = (score, texto, resposta)
    return (melhor[1], melhor[2]) if melhor else None


def build_objection_plan(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Bloco dinâmico com técnica + molde (vertical) e resposta instanciada
    (playbook) pra objeção ativa. "" se não há objeção ou nenhum match.
    """
    state = conv.lead_state if isinstance(conv.lead_state, dict) else {}
    objecao = str(state.get("objecao_ativa") or "").strip()
    if not objecao:
        return ""

    partes: list[str] = []

    obj_vertical = find_objection(identity.category, objecao)
    if obj_vertical:
        partes.append(f"  Técnica (vertical): {obj_vertical.tecnica}")
        partes.append(f"  Molde: {obj_vertical.molde}")

    playbook = (identity.market_analysis or {}).get("playbook") if identity.market_analysis else None
    match = _best_playbook_objection(objecao, playbook)
    if match:
        partes.append(f"  Deste negócio: \"{match[0]}\" → {match[1]}")

    if not partes:
        return ""

    return (
        f"\nPLANO PRA OBJEÇÃO ATIVA (\"{objecao}\"): VALIDAR (\"entendo\") → ENTENDER (é real ou desculpa?) "
        "→ REFRAME com fato real → próximo passo.\n"
        + "\n".join(partes)
        + "\n  Use do seu jeito, uma vez. SE o lead repetir a objeção, mude o ângulo em vez de repetir o argumento.\n"
    )
