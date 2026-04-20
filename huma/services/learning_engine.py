# ================================================================
# huma/services/learning_engine.py — Motor de Aprendizado
#
# A HUMA começa inteligente (base vertical) e fica genial
# (aprendizado por conversas).
#
# 3 camadas:
#
#   1. VERTICAL KNOWLEDGE
#      Conhecimento embutido por categoria de negócio.
#      Dia 1, sem dados, a IA já sabe como cada perfil
#      de cliente se comporta naquela vertical.
#
#   2. CONVERSATION LEARNING
#      Analisa conversas finalizadas (won/lost).
#      Extrai padrões: qual perfil compra, qual argumento
#      funciona, qual tom converte mais.
#      Gera "insights" que alimentam o system prompt.
#
#   3. LEAD PROFILING
#      Infere perfil do lead automaticamente (DDD, horário,
#      vocabulário, perguntas) sem perguntar.
#      Adapta tom, argumentos, velocidade.
#
# Tabela Supabase: learning_insights
# ================================================================

import json
from datetime import datetime

from fastapi.concurrency import run_in_threadpool

from huma.models.schemas import BusinessCategory, Conversation
from huma.utils.logger import get_logger

log = get_logger("learning")


# ================================================================
# CAMADA 1: BASE DE CONHECIMENTO POR VERTICAL
#
# Cada categoria tem padrões conhecidos do mercado.
# Isso garante que no dia 1 a HUMA não é burra.
# O dono não precisa ensinar o óbvio.
# ================================================================

def get_vertical_knowledge(category: BusinessCategory) -> dict:
    """Retorna base de conhecimento da vertical.

    Fonte: huma.categories registry (Fase 1 — Category Packs).
    """
    from huma.categories import get_knowledge
    return get_knowledge(category)


def build_vertical_prompt(category: BusinessCategory) -> str:
    """
    Gera trecho do system prompt com conhecimento da vertical.
    Isso é o que faz a HUMA ser inteligente no dia 1.
    """
    knowledge = get_vertical_knowledge(category)
    if not knowledge:
        return ""

    prompt = "\nCONHECIMENTO DA VERTICAL:\n"

    # Perfis
    perfis = knowledge.get("perfis", {})
    if perfis:
        prompt += "\n  PERFIS DE CLIENTE (adapte tom e argumentos):\n"
        for pid, perfil in perfis.items():
            prompt += f"\n    [{perfil['descricao']}]\n"
            prompt += f"      Tom: {perfil['tom_ideal']}\n"
            prompt += f"      Sinais: {', '.join(perfil['sinais'])}\n"
            prompt += f"      Objeções: {', '.join(perfil['objecoes_comuns'])}\n"
            prompt += f"      Argumentos: {', '.join(perfil['argumentos_fortes'])}\n"
            prompt += f"      Fluxo ideal: {perfil['ordem_conversa']}\n"

    # Insights universais
    insights = knowledge.get("insights_universais", [])
    if insights:
        prompt += "\n  INSIGHTS DA VERTICAL:\n"
        for insight in insights:
            prompt += f"    - {insight}\n"

    return prompt


# ================================================================
# CAMADA 2: APRENDIZADO POR CONVERSAS
#
# Toda conversa que termina em "won" ou "lost" é analisada.
# Extrai padrões que alimentam o system prompt.
#
# Armazena insights na tabela learning_insights do Supabase.
# ================================================================

async def analyze_completed_conversation(client_id: str, conv: Conversation, outcome: str):
    """
    Analisa conversa finalizada e extrai aprendizados.

    Args:
        client_id: ID do cliente
        conv: conversa completa
        outcome: "won" ou "lost"

    Extrai:
        - Perfil inferido do lead
        - Argumentos que funcionaram (ou não)
        - Objeções encontradas
        - Tom usado
        - Tempo total da conversa
        - Estágio em que ganhou/perdeu
    """
    if not conv.history or len(conv.history) < 4:
        return  # Conversa muito curta pra analisar

    # Extrai dados da conversa
    lead_messages = [m["content"] for m in conv.history if m["role"] == "user"]
    huma_messages = [m["content"] for m in conv.history if m["role"] == "assistant"]

    lead_text = " ".join(lead_messages).lower()
    huma_text = " ".join(huma_messages).lower()

    # Infere perfil do lead
    profile = _infer_profile_from_text(lead_text)

    # Identifica objeções
    objections = _detect_objections(lead_text)

    # Identifica argumentos usados
    arguments = _detect_arguments(huma_text)

    # Calcula métricas
    total_messages = len(conv.history)
    lead_msg_count = len(lead_messages)
    stages_visited = _extract_stages(conv)

    insight = {
        "client_id": client_id,
        "phone": conv.phone,
        "outcome": outcome,
        "profile": profile,
        "objections": objections,
        "arguments_used": arguments,
        "total_messages": total_messages,
        "lead_messages": lead_msg_count,
        "stages": stages_visited,
        "lead_facts": conv.lead_facts,
        "created_at": datetime.utcnow().isoformat(),
    }

    # Salva no Supabase
    await _save_insight(insight)

    log.info(
        f"Insight salvo | {client_id} | outcome={outcome} | "
        f"profile={profile.get('inferred_segment', 'unknown')} | "
        f"objections={len(objections)} | msgs={total_messages}"
    )


async def get_learned_insights(client_id: str, limit: int = 50) -> str:
    """
    Retorna insights aprendidos formatados pro system prompt.

    Exemplo de saída:
        "Padrões aprendidos com 47 conversas:
         - 73% das mulheres 30+ que perguntam sobre resultado compram
         - Argumento 'avaliação gratuita' aparece em 80% das vendas
         - Principal objeção: preço (45% dos casos)
         - Tom acolhedor converte 2x mais que tom direto nessa vertical"
    """
    from huma.services.db_service import get_supabase
    supa = get_supabase()

    resp = await run_in_threadpool(
        lambda: supa.table("learning_insights").select("*")
            .eq("client_id", client_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
    )

    insights = resp.data or []
    if not insights:
        return ""

    # Calcula estatísticas
    total = len(insights)
    won = [i for i in insights if i.get("outcome") == "won"]
    lost = [i for i in insights if i.get("outcome") == "lost"]
    win_rate = len(won) / total * 100 if total > 0 else 0

    # Conta objeções mais comuns
    all_objections = []
    for i in insights:
        all_objections.extend(i.get("objections", []))
    top_objections = _count_top(all_objections, 3)

    # Conta argumentos que mais aparecem em vendas
    winning_arguments = []
    for i in won:
        winning_arguments.extend(i.get("arguments_used", []))
    top_arguments = _count_top(winning_arguments, 3)

    # Conta perfis que mais compram
    winning_profiles = [i.get("profile", {}).get("inferred_segment", "unknown") for i in won]
    top_profiles = _count_top(winning_profiles, 3)

    # Média de mensagens pra venda
    avg_msgs_won = (
        sum(i.get("total_messages", 0) for i in won) / len(won)
        if won else 0
    )

    # Monta prompt
    prompt = f"\nAPRENDIZADOS ({total} conversas analisadas, {win_rate:.0f}% taxa de conversão):\n"

    if top_profiles:
        prompt += f"  Perfis que mais compram: {', '.join(f'{p[0]} ({p[1]}x)' for p in top_profiles)}\n"

    if top_arguments:
        prompt += f"  Argumentos que mais vendem: {', '.join(f'{a[0]} ({a[1]}x)' for a in top_arguments)}\n"

    if top_objections:
        prompt += f"  Objeções mais comuns: {', '.join(f'{o[0]} ({o[1]}x)' for o in top_objections)}\n"

    if avg_msgs_won > 0:
        prompt += f"  Média de mensagens até venda: {avg_msgs_won:.0f}\n"

    # Insights específicos de perdas
    if lost:
        losing_stages = [i.get("stages", ["unknown"])[-1] for i in lost if i.get("stages")]
        top_losing_stages = _count_top(losing_stages, 2)
        if top_losing_stages:
            prompt += f"  Estágios onde mais perde: {', '.join(f'{s[0]} ({s[1]}x)' for s in top_losing_stages)}\n"

    prompt += "  USE esses dados pra adaptar tom e argumentos ao perfil do lead.\n"
    return prompt


# ================================================================
# CAMADA 3: PERFIL AUTOMÁTICO DO LEAD
#
# Sem perguntar, infere:
#   - Faixa etária aproximada (vocabulário + contexto)
#   - Gênero provável (nome se tiver, ou padrões de fala)
#   - Região (DDD)
#   - Urgência (horário + vocabulário)
#   - Poder aquisitivo (perguntas sobre preço/parcelamento)
#   - Canal de origem (se integrado com ads)
# ================================================================

DDD_REGIONS = {
    "11": "São Paulo (Capital)", "21": "Rio de Janeiro (Capital)",
    "31": "Belo Horizonte", "41": "Curitiba", "51": "Porto Alegre",
    "61": "Brasília", "71": "Salvador", "81": "Recife",
    "85": "Fortaleza", "91": "Belém", "92": "Manaus",
    "27": "Vitória", "48": "Florianópolis", "62": "Goiânia",
    "84": "Natal", "79": "Aracaju", "98": "São Luís",
    "86": "Teresina", "65": "Cuiabá", "67": "Campo Grande",
}

# Palavras que indicam faixa etária
YOUNG_SIGNALS = [
    "kk", "kkk", "haha", "tipo", "mano", "véi", "vei",
    "brabo", "top", "show", "dms", "pdc", "tmj",
    "preventivo", "tiktok", "insta", "reels",
]

MATURE_SIGNALS = [
    "senhora", "senhor", "bom dia", "boa tarde",
    "por gentileza", "poderia", "gostaria", "por favor",
    "rejuvenescimento", "flacidez", "rugas",
]


def profile_lead(phone: str, text: str, facts: list[str] = None, hour: int = None) -> dict:
    """
    Infere perfil do lead automaticamente.

    Args:
        phone: telefone (pra DDD)
        text: primeira mensagem (ou acumulado)
        facts: fatos já conhecidos
        hour: hora da mensagem

    Returns:
        {
            "inferred_segment": "mulher_30_plus",
            "region": "São Paulo (Capital)",
            "urgency": "medium",
            "price_sensitivity": "high",
            "formality": "informal",
            "estimated_age_range": "20-29",
            "signals": ["usou 'kkk'", "perguntou preço primeiro"],
        }
    """
    text_lower = text.lower()
    profile = {
        "inferred_segment": "unknown",
        "region": "",
        "urgency": "medium",
        "price_sensitivity": "medium",
        "formality": "informal",
        "estimated_age_range": "",
        "signals": [],
    }

    # Região por DDD
    if len(phone) >= 4:
        ddd = phone[2:4] if phone.startswith("55") else phone[:2]
        region = DDD_REGIONS.get(ddd, "")
        if region:
            profile["region"] = region
            profile["signals"].append(f"DDD {ddd}: {region}")

    # Faixa etária por vocabulário
    young_count = sum(1 for s in YOUNG_SIGNALS if s in text_lower)
    mature_count = sum(1 for s in MATURE_SIGNALS if s in text_lower)

    if young_count > mature_count:
        profile["estimated_age_range"] = "18-29"
        profile["formality"] = "informal"
        profile["signals"].append(f"Vocabulário jovem ({young_count} sinais)")
    elif mature_count > young_count:
        profile["estimated_age_range"] = "35+"
        profile["formality"] = "formal"
        profile["signals"].append(f"Vocabulário maduro ({mature_count} sinais)")
    else:
        profile["estimated_age_range"] = "30-39"

    # Urgência
    urgent_words = ["urgente", "agora", "hoje", "pra ontem", "emergência", "rápido"]
    if any(w in text_lower for w in urgent_words):
        profile["urgency"] = "high"
        profile["signals"].append("Vocabulário de urgência")
    elif hour and (hour >= 22 or hour <= 6):
        profile["urgency"] = "high"
        profile["signals"].append(f"Mensagem às {hour}h (fora do horário)")

    # Sensibilidade a preço
    price_words = ["barato", "desconto", "promoção", "parcelar", "caro", "mais barato", "cupom"]
    if any(w in text_lower for w in price_words):
        profile["price_sensitivity"] = "high"
        profile["signals"].append("Sensibilidade a preço detectada")
    elif any(w in text_lower for w in ["melhor", "premium", "exclusivo", "qualidade"]):
        profile["price_sensitivity"] = "low"
        profile["signals"].append("Busca qualidade sobre preço")

    # Gênero (se tiver nome nos fatos)
    if facts:
        for fact in facts:
            if "nome" in fact.lower():
                name = fact.split(":")[-1].strip().split()[0].lower()
                gender = _guess_gender(name)
                if gender:
                    profile["signals"].append(f"Nome '{name}' → provável {gender}")

                    # Combina gênero + idade pra segmento
                    if gender == "feminino":
                        if profile["estimated_age_range"] in ["35+", "30-39"]:
                            profile["inferred_segment"] = "mulher_30_plus"
                        else:
                            profile["inferred_segment"] = "jovem_20_29"
                    elif gender == "masculino":
                        profile["inferred_segment"] = "homem_qualquer_idade"
                break

    return profile


def build_profile_prompt(profile: dict) -> str:
    """Gera trecho do system prompt com o perfil inferido."""
    if not profile or profile.get("inferred_segment") == "unknown":
        return ""

    prompt = "\nPERFIL INFERIDO DO LEAD (adapte sua abordagem):\n"

    if profile.get("region"):
        prompt += f"  Região: {profile['region']}\n"
    if profile.get("estimated_age_range"):
        prompt += f"  Idade estimada: {profile['estimated_age_range']}\n"
    if profile.get("urgency") == "high":
        prompt += "  Urgência: ALTA — seja rápido e objetivo\n"
    if profile.get("price_sensitivity") == "high":
        prompt += "  Sensibilidade a preço: ALTA — destaque condições e parcelamento\n"
    elif profile.get("price_sensitivity") == "low":
        prompt += "  Sensibilidade a preço: BAIXA — destaque qualidade e exclusividade\n"
    if profile.get("formality") == "formal":
        prompt += "  Formalidade: Use tom mais respeitoso e formal\n"
    if profile.get("inferred_segment") != "unknown":
        prompt += f"  Segmento: {profile['inferred_segment']}\n"

    if profile.get("signals"):
        prompt += f"  Sinais detectados: {', '.join(profile['signals'][:5])}\n"

    return prompt


# ================================================================
# HELPERS INTERNOS
# ================================================================

def _infer_profile_from_text(text: str) -> dict:
    """Infere perfil básico do lead pelo texto das mensagens."""
    profile = {
        "inferred_segment": "unknown",
        "price_mentioned": "preço" in text or "quanto" in text or "valor" in text,
        "objection_detected": any(w in text for w in ["caro", "não sei", "medo", "receio"]),
    }

    young = sum(1 for s in YOUNG_SIGNALS if s in text)
    mature = sum(1 for s in MATURE_SIGNALS if s in text)

    if young > mature:
        profile["inferred_segment"] = "jovem_20_29"
    elif mature > young:
        profile["inferred_segment"] = "mulher_30_plus"

    return profile


def _detect_objections(text: str) -> list[str]:
    """Detecta objeções mencionadas pelo lead."""
    objection_map = {
        "preço": ["caro", "mais barato", "não tenho", "apertado", "puxado"],
        "confiança": ["golpe", "medo", "confiável", "seguro", "verdade"],
        "tempo": ["demora", "quanto tempo", "prazo", "rápido"],
        "dor": ["dói", "doer", "incômodo", "anestesia"],
        "necessidade": ["não sei se preciso", "será que", "preciso mesmo"],
    }

    found = []
    for objection, keywords in objection_map.items():
        if any(k in text for k in keywords):
            found.append(objection)

    return found


def _detect_arguments(text: str) -> list[str]:
    """Detecta argumentos usados pela HUMA."""
    argument_map = {
        "garantia": ["garantia", "troca", "devolvemos"],
        "avaliação_gratuita": ["avaliação gratuita", "sem compromisso", "grátis"],
        "desconto_pix": ["pix", "desconto", "à vista"],
        "parcelamento": ["parcela", "10x", "sem juros"],
        "prova_social": ["antes e depois", "clientes", "avaliações", "resultados"],
        "urgência": ["últimas unidades", "essa semana", "hoje"],
        "personalização": ["pra você", "no seu caso", "especial"],
    }

    found = []
    for argument, keywords in argument_map.items():
        if any(k in text for k in keywords):
            found.append(argument)

    return found


def _extract_stages(conv: Conversation) -> list[str]:
    """Extrai estágios visitados na conversa."""
    return [conv.stage]  # Simplificado — em produção, extrair do histórico


def _count_top(items: list, n: int = 3) -> list[tuple]:
    """Conta frequência e retorna top N."""
    counts = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:n]


def _guess_gender(name: str) -> str:
    """
    Inferência básica de gênero por nome.
    Funciona pra nomes brasileiros comuns.
    """
    name = name.lower().strip()

    feminine_endings = ["a", "ia", "na", "la", "ra", "sa", "ta", "da", "cia"]
    masculine_endings = ["o", "os", "io", "do", "lo", "ro", "so", "to"]

    # Exceções comuns
    feminine_names = {
        "alice", "beatriz", "isabel", "raquel", "carmen", "mabel",
        "ingrid", "miriam", "megan", "iris", "liz", "ruth",
    }
    masculine_names = {
        "luca", "issa", "josefa", "nikita", "andrea", "sacha",
        "joshua", "noah", "dana", "nikola",
    }

    if name in feminine_names:
        return "feminino"
    if name in masculine_names:
        return "masculino"

    for ending in feminine_endings:
        if name.endswith(ending):
            return "feminino"
    for ending in masculine_endings:
        if name.endswith(ending):
            return "masculino"

    return ""


async def _save_insight(insight: dict):
    """Salva insight no Supabase."""
    try:
        from huma.services.db_service import get_supabase
        supa = get_supabase()
        await run_in_threadpool(
            lambda: supa.table("learning_insights").insert(insight).execute()
        )
    except Exception as e:
        log.error(f"Erro salvando insight | {e}")
