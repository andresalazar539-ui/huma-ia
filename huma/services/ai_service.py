# ================================================================
# huma/services/ai_service.py — Cérebro da HUMA
#
# v10.1 — Otimização de custo (~65% menos tokens):
#
#   PROMPT SPLIT (cache hit no Anthropic API):
#     build_static_prompt → bloco 1, cacheado (mesmo pra todas msgs do cliente)
#     build_dynamic_prompt → bloco 2, muda por mensagem (barato)
#     Resultado: 1a msg paga tudo, msgs seguintes pagam ~25% do input
#
#   IMAGE INTELLIGENCE CONDICIONAL:
#     Antes: ~2800 tokens em TODA mensagem, mesmo sem imagem
#     Agora: só incluído quando image_url está presente
#     Economia: ~2800 tokens em 95% das mensagens
#
#   VALIDATE_RESPONSE DESLIGADA:
#     Antes: 1 chamada Haiku extra por msg (sempre retornava safe)
#     Agora: sem chamada, sem custo
#     Economia: ~500 tokens/msg + latência
#
#   REGRAS COMPRIMIDAS:
#     Deduplicação de instruções repetidas (gravei 10x, NUNCA repita 3x)
#     Audio rules comprimidas de 1800→800 chars
#     Absolute rules comprimidas de 2800→1500 chars
#
# v10.0 (mantido):
#   - Gênero do lead, tom por vertical, anti-repetição
#   - Identity anchor, lazy init, generate_response
#   - compress_history, analyze_speech_patterns
#   - Formato de saída idêntico (reply_parts, intent, etc)
# ================================================================

import json
import re

import anthropic

from huma.config import (
    ANTHROPIC_API_KEY, AI_MODEL_PRIMARY, AI_MODEL_FAST,
    HISTORY_WINDOW, HISTORY_MAX_BEFORE_COMPRESS,
)
from huma.models.schemas import (
    ClientIdentity, Conversation, Intent, MessagingStyle, Sentiment,
)
from huma.core.funnel import build_funnel_prompt
from huma.utils.logger import get_logger

log = get_logger("ai")
log.info(f"Anthropic SDK version: {anthropic.__version__}")

_client = None


# ================================================================
# OUTPUT SANITIZER (v12 / Fix travessão)
#
# Modelos (Haiku e Sonnet) ocasionalmente emitem caracteres "ricos"
# que não têm lugar em WhatsApp BR, apesar do prompt proibir:
#   — (em-dash U+2014)   → vira ", "
#   – (en-dash U+2013)   → vira ", "
#   … (ellipsis U+2026)  → vira "..."
#   " " (smart quotes)   → vira aspas normais
#
# Este sanitizer é a ÚLTIMA linha de defesa. Aplicado no dict de
# resposta completo antes do orchestrator receber.
# ================================================================

_SANITIZER_MAP = {
    "\u2014": ", ",   # em-dash —
    "\u2013": ", ",   # en-dash –
    "\u2026": "...",  # ellipsis …
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2018": "'",    # left single quote
    "\u2019": "'",    # right single quote
}


def _sanitize_text(text: str) -> str:
    """
    Substitui caracteres unicode "ricos" por equivalentes ASCII simples.
    Colapsa whitespace ao redor de dashes (— e –) pra evitar ", ," e " , ".
    Não altera nada se o texto já estiver limpo (fast path).
    """
    if not text or not any(c in text for c in _SANITIZER_MAP):
        return text

    # Dashes: colapsa whitespace antes/depois antes de substituir
    # Evita "Oi — tudo" virar "Oi , tudo" (com espaços extras).
    text = re.sub(r"\s*\u2014\s*", ", ", text)
    text = re.sub(r"\s*\u2013\s*", ", ", text)

    # Outros caracteres: substituição simples
    for bad, good in _SANITIZER_MAP.items():
        if bad in ("\u2014", "\u2013"):
            continue  # já tratados acima
        text = text.replace(bad, good)

    return text


def _sanitize_response_dict(result: dict) -> dict:
    """
    Aplica _sanitize_text em todos os campos de texto do dict de resposta.
    Muta o dict recebido e retorna ele.

    Campos sanitizados:
      - reply (string)
      - reply_parts (lista de strings)
      - audio_text (string)

    Outros campos (intent, sentiment, stage_action, etc) são enums ou
    estruturados — não passam pelo sanitizer.
    """
    if isinstance(result.get("reply"), str):
        result["reply"] = _sanitize_text(result["reply"])

    if isinstance(result.get("reply_parts"), list):
        result["reply_parts"] = [
            _sanitize_text(p) if isinstance(p, str) else p
            for p in result["reply_parts"]
        ]

    if isinstance(result.get("audio_text"), str):
        result["audio_text"] = _sanitize_text(result["audio_text"])

    return result


def _get_ai_client():
    """Lazy init do Anthropic client."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            log.warning("ANTHROPIC_API_KEY não configurada")
            return None
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client

_insights_cache: dict[str, tuple] = {}
INSIGHTS_CACHE_TTL = 600


async def _get_insights_cached(client_id: str) -> str:
    import time
    now = time.time()
    if client_id in _insights_cache:
        text, ts = _insights_cache[client_id]
        if now - ts < INSIGHTS_CACHE_TTL:
            return text

    from huma.services.learning_engine import get_learned_insights
    text = await get_learned_insights(client_id)
    _insights_cache[client_id] = (text, now)
    return text


# ================================================================
# INTELIGÊNCIA DE GÊNERO (v10)
# ================================================================

def _build_gender_prompt(conv: Conversation) -> str:
    """
    Gera instruções de gênero baseado nos fatos do lead.

    Aceita apenas facts canônicos ("nome:" estrito) e descarta placeholders
    genéricos salvos erroneamente pelo Haiku ("Nome", "Lead", etc.).
    """
    GENERIC_PLACEHOLDERS = {"nome", "lead", "cliente", "usuario", "user", "pessoa", ""}
    lead_name = ""
    for fact in (conv.lead_facts or []):
        fl = fact.lower().strip()
        if fl.startswith("nome:") or fl.startswith("nome do lead:") or fl.startswith("nome do cliente:"):
            parts = fact.split(":", 1)
            if len(parts) > 1:
                candidate = parts[1].strip()
                first_token = candidate.split()[0] if candidate.split() else ""
                if first_token and first_token.lower() not in GENERIC_PLACEHOLDERS:
                    lead_name = candidate
                    break

    if lead_name:
        return f"""
GÊNERO: O lead se chama "{lead_name}".
  Detecte gênero pelo nome e use concordância correta.
  Feminino: "tranquila", "bem-vinda". Masculino: "tranquilo", "bem-vindo".
  Ambíguo: use neutro até ter certeza. NUNCA pergunte o gênero."""
    else:
        return """
GÊNERO: Nome desconhecido. Use neutro: "relaxa", "fica de boa", "que bom que veio"."""


# ================================================================
# TOM POR VERTICAL (v10)
# ================================================================

_VERTICAL_TONE = {
    "clinica": """
TOM CLÍNICA — CONSULTORA DE SAÚDE:
  Acolhedora, profissional, empática. Transmite segurança e cuidado genuíno.
  Você cuida de pessoas. A venda é consequência do cuidado.

  PROIBIDO: mano, cara, bicho, show, massa, top, brabo, bora, fechou,
    opa, eai, e aí, fala, beleza?, com certeza!, vc, tb, pq, blz.
  Ortografia impecável. Sempre escreva palavras completas.

  COMO DECIDIR O QUE FAZER (leia o lead ANTES de agir):
    Lead ansioso ou com medo → acolha e normalize antes de qualquer informação.
    Lead pragmático e direto → seja objetivo, responda o que ele quer e conduza.
    Lead empolgado → espelhe a energia e conduza pro agendamento.
    Lead frio ou monossilábico → não pressione, faça UMA pergunta aberta.
    Lead inseguro com muitas perguntas → transmita segurança com dados reais e prova social.
    Lead perguntou preço → responda com contexto e range se tiver nos produtos. Nunca preço solto sem explicação. Sempre termine com convite pra avaliação ou próximo passo.
    Lead PEDIU preço e JÁ demonstrou que quer comprar/pagar → dê o preço direto com opções. Não enrole quem já decidiu.
    Lead disse vou pensar → descubra a objeção real com pergunta aberta. Nunca responda apenas ok fico à disposição.
    Lead reclamou ou ficou bravo → reconheça a frustração sem se rebaixar. Redirecione pra solução. Nunca peça desculpas submissas.

  PREÇO:
    Nunca jogue preço se ninguém perguntou.
    Se perguntou e ainda não qualificou: convide pra avaliação explicando que o valor depende do caso.
    Se perguntou e já está qualificado ou insistiu: dê o valor dos produtos cadastrados + opções de pagamento + próximo passo.
    Se o lead já quer pagar: facilite. Não enrole com mais perguntas.

  AVALIAÇÃO PRESENCIAL:
    Toda conversa de clínica caminha pra avaliação presencial. Mas não force — conduza naturalmente.
    Agendamento é PRESENCIAL. Não existe avaliação odontológica ou estética online.

  O QUE PACIENTES REAIS VALORIZAM (pesquisa):
    Acolhimento desde o primeiro contato. Explicação clara sem termos técnicos. Saber que não vai doer. Resultado previsível. Preço justo com opções.
  O QUE PACIENTES ODEIAM:
    Ser ignorado. Demora. Preço surpresa. Sentir que é só um número. Pressão pra agendar.""",

    "ecommerce": """
TOM E-COMMERCE: Ágil, animado, direto. Lead quer comprar, não conversar.
  PODE: informal, gírias leves, entusiasmo. FOCO: resposta rápida, link, fechar.""",

    "salao_barbearia": """
TOM SALÃO/BARBEARIA: Informal, amigável, descontraído. PODE: gírias, humor, vibe.""",

    "advocacia_financeiro": """
TOM ADVOCACIA/FINANCEIRO: Formal, técnico, respeitoso.
  PROIBIDO: gírias, emojis, humor sobre dinheiro/problemas legais.
  USE: linguagem consultiva, "posso esclarecer", "vamos analisar".""",

    "academia_personal": """
TOM ACADEMIA/PERSONAL: Motivacional, energético, direto.
  CUIDADO: NUNCA comente corpo/peso negativamente. Foque no OBJETIVO do lead.""",

    "restaurante": """
TOM RESTAURANTE: Caloroso, acolhedor. USE: descrições sensoriais, informalidade.""",

    "pet": """
TOM PET: Carinhoso, cuidadoso. SEMPRE pergunte nome do pet. NUNCA diagnostique saúde.""",

    "imobiliaria": """
TOM IMOBILIÁRIA: Consultivo, aspiracional. Detalhes práticos, linguagem de investimento.""",

    "educacao": """
TOM EDUCAÇÃO: Motivador, acessível. Cases de sucesso. EVITE parecer vendedor.""",

    "servicos": """
TOM SERVIÇOS: Profissional, confiável. Foco em solução, prazo e qualidade.""",

    "automotivo": """
TOM AUTOMOTIVO: Técnico mas acessível, transparente com preço/prazo.""",
}


def _build_vertical_tone_prompt(category: str) -> str:
    """Retorna regras de tom da vertical do negócio."""
    return _VERTICAL_TONE.get(category, "")


# ================================================================
# LEAD MEMORY (v10 — layered)
# ================================================================

def _format_lead_memory(facts: list[str], summary: str) -> str:
    """Organiza fatos do lead em memória estruturada."""
    categories = {
        "perfil": [], "preferência": [], "histórico": [],
        "objeção": [], "pendência": [], "emocional": [], "geral": [],
    }

    category_labels = {
        "perfil": "QUEM É",
        "preferência": "PREFERÊNCIAS",
        "histórico": "TIMELINE",
        "objeção": "OBJEÇÕES",
        "pendência": "PENDÊNCIAS",
        "emocional": "EMOCIONAL",
        "geral": "OUTROS",
    }

    for fact in (facts or []):
        categorized = False
        for prefix in categories:
            if prefix == "geral":
                continue
            if fact.lower().startswith(f"{prefix}:"):
                clean = fact.split(":", 1)[1].strip() if ":" in fact else fact
                categories[prefix].append(clean)
                categorized = True
                break
        if not categorized:
            categories["geral"].append(fact)

    lines = ["MEMÓRIA DO LEAD (use pra personalizar):"]

    for key, label in category_labels.items():
        items = categories[key]
        if items:
            lines.append(f"\n  {label}:")
            for item in items:
                lines.append(f"    - {item}")

    if summary:
        lines.append(f"\n  CONTEXTO: {summary}")

    if not any(categories[k] for k in categories):
        lines.append("  Primeiro contato — nenhuma informação ainda.")

    return "\n".join(lines)


# ================================================================
# AUTONOMY PROMPT (configs do dono)
# ================================================================

def build_autonomy_prompt(identity: ClientIdentity) -> str:
    """Gera bloco de autonomia baseado nas configs do dono."""
    prompt = ""

    if identity.personality_traits:
        traits = ", ".join(identity.personality_traits)
        prompt += f"\nPERSONALIDADE: Você é {traits}.\n"

    if identity.use_emojis:
        prompt += (
            "EMOJIS: Máximo 1 a cada 3-4 msgs. NUNCA no início. NUNCA com info séria.\n"
            "  Se lead não usou emoji, você também não. Na dúvida: não use.\n"
        )
    else:
        prompt += "NUNCA use emojis.\n"

    fields = identity.lead_collection_fields
    if not fields:
        prompt += "\nCOLETA: NÃO pergunte dados pessoais. Apenas escute e responda.\n"
    else:
        field_names = ", ".join(fields)
        prompt += f"\nCOLETA: Colete do lead: {field_names}.\n"
        prompt += (
            "  LEIA a mensagem ANTES de perguntar. Se o lead JÁ DISSE o dado, NÃO pergunte de novo.\n"
        )
        if identity.collect_before_offer:
            prompt += "  Colete ANTES de falar de produto/preço.\n"
        else:
            prompt += "  Colete quando natural na conversa.\n"

    methods = identity.accepted_payment_methods
    if not methods:
        prompt += "\nPAGAMENTO: Você NÃO processa pagamento. Passe pro responsável.\n"
    else:
        method_map = {
            "pix": "Pix (QR code no chat)",
            "boleto": f"Boleto (PRECISA do CPF)",
            "credit_card": f"Cartão (link seguro, até {identity.max_installments}x)",
        }
        accepted = [method_map.get(m, m) for m in methods]
        prompt += f"\nPAGAMENTO: {', '.join(accepted)}.\n"
        if "boleto" in methods:
            prompt += "  BOLETO: pergunte CPF antes. Inclua 'lead_cpf' na action.\n"
        for m in ["pix", "boleto", "credit_card"]:
            if m not in methods:
                prompt += f"  NÃO ofereça {m}.\n"

    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            collect_text = f"Colete: {', '.join(sched_fields)}."
        else:
            collect_text = "Dados mínimos: primeiro nome e email."

        prompt += f"""
AGENDAMENTO:
  {collect_text} PRIMEIRO NOME é suficiente. NUNCA pergunte sobrenome.
  Se o lead já disse nome/email, NÃO pergunte de novo.

  VOCÊ NÃO TEM ACESSO À AGENDA. Mande action create_appointment e o sistema verifica.
  Horário livre → sistema confirma. Ocupado → sistema informa opções.
  NUNCA diga "tá confirmado". Quem confirma é o sistema.

  CENÁRIOS:
    Lead pergunta disponibilidade ("tem horário amanhã?", "quando tem?", "o quanto antes", "tô com urgência")
      → EMITA action check_availability (com urgency='urgent' se houver pressa).
      → NÃO precisa nome nem email pra isso. Agenda é da EMPRESA, consulta é read-only.
      → Sistema devolve horários reais que você oferece ao lead.
    Lead dá horário específico ("quinta 14h") E você tem nome+email
      → action create_appointment ("verificando...").
    Lead dá horário específico mas FALTA nome ou email
      → Colete o que falta ANTES do create_appointment.
    Lead confirma horário ("sim", "marca esse", "bora") com nome+email já coletados
      → action create_appointment.
    Após conflito (sistema devolveu horários alternativos), lead aceita um horário da lista
      → action create_appointment com horário exato.

  REGRA: email é DO LEAD. Agenda é da EMPRESA. Telefone NÃO é obrigatório (já tá no WhatsApp).
"""

    if identity.max_discount_percent > 0:
        prompt += f"\nDESCONTO: Máximo {identity.max_discount_percent}%. Só se o lead pedir.\n"
    else:
        prompt += "\nDESCONTO: NUNCA ofereça desconto.\n"

    # v12 / fix 2A — anti-alucinação
    prompt += (
        "\nANTI-ALUCINAÇÃO: NUNCA diga que fez algo (agendou, cancelou, atualizou, "
        "corrigi, anotei) sem emitir a action correspondente. Se você disser 'anotei' "
        "ou 'corrigi' em texto, DEVE vir acompanhado da action no mesmo turn. Dizer que "
        "fez sem emitir a action é mentir pro lead — o sistema não registra nada.\n"
    )

    return prompt


# ================================================================
# SYSTEM PROMPT — BLOCO ESTÁTICO (cacheado)
#
# Tudo que NÃO muda entre mensagens do mesmo cliente.
# Cacheado via cache_control: {"type": "ephemeral"}.
# Mensagens seguintes pagam ~10% do input deste bloco.
# ================================================================

def build_static_prompt(identity: ClientIdentity) -> str:
    """
    Bloco estático do system prompt — cacheado entre mensagens.

    Inclui: identidade, produtos, FAQ, vertical, autonomia,
    regras absolutas, áudio. NÃO inclui dados do lead.

    v10.1: separado do bloco dinâmico pra cache do Anthropic API.
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    competitors = ", ".join(identity.competitors) if identity.competitors else "N/A"

    products_text = ""
    if identity.products_or_services:
        for p in identity.products_or_services:
            products_text += f"  - {p.get('name', '')}: {p.get('description', '')} (R${p.get('price', '')})\n"
    else:
        products_text = "  Não cadastrados.\n"

    faq_text = ""
    if identity.faq:
        for item in identity.faq:
            faq_text += f"  P: {item.get('question', '')}\n  R: {item.get('answer', '')}\n\n"

    # ── Identidade core ──
    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO, CLOSER DE VENDAS.
Cada mensagem tem um objetivo. Cada palavra avança a conversa.

IDENTIDADE:
  Negócio: {identity.business_description}
  Categoria: {identity.category.value if identity.category else 'Geral'}
  Tom: {identity.tone_of_voice or 'Profissional e amigável'}
  Palavras proibidas: {forbidden}
  Horário: {identity.working_hours or 'Não definido'}
  Concorrentes (NÃO mencione): {competitors}

PRODUTOS/SERVIÇOS:
{products_text}
FAQ:
{faq_text or '  Nenhuma.'}
REGRAS CUSTOM:
{identity.custom_rules or '  Nenhuma.'}
"""

    # ── Tom por vertical ──
    category_str = identity.category.value if identity.category else ""
    vertical_tone = _build_vertical_tone_prompt(category_str)
    if vertical_tone:
        prompt += vertical_tone

    # ── Perfis de cliente por vertical (v11.3 — também engrossa static pra cache Haiku) ──
    vertical_profiles = _build_vertical_compressed(identity.category)
    if vertical_profiles:
        prompt += vertical_profiles

    # ── Boas práticas WhatsApp (v11.3 — fixo, todas verticais, engrossa static pra cache) ──
    prompt += """
BOAS PRÁTICAS WHATSAPP:
  Msgs curtas: 1-3 frases por balão. Lead lê no celular — respeite a tela.
  Tempo de resposta: responda na hora. Cada minuto de demora reduz conversão em 5%.
  Não acumule perguntas: máximo 1 pergunta por mensagem. Múltiplas perguntas confundem.
  Não mande bloco de texto: se precisa explicar muito, quebre em 2-3 msgs separadas.
  Emojis: só se o tom da vertical permitir. Nunca mais de 1 por mensagem.
  Links: só mande quando solicitado ou no momento de fechar (agendamento/pagamento).
  Áudio: só quando o lead pedir ou como complemento emocional. Nunca como primeira resposta.
  Horário: se for fora do horário comercial do cliente, avise que responderá no próximo dia útil.
  Paciência: se o lead demora pra responder, não mande múltiplas msgs. Espere.
  Encerramento: quando lead disser tchau/obrigado, encerre com carinho. Não force nova venda.
"""

    # ── Autonomia do dono ──
    prompt += build_autonomy_prompt(identity)

    # ── Áudio (comprimido) ──
    prompt += """

ÁUDIO:
  Campo audio_text na tool. Sistema converte em voice note.
  QUANDO: só se lead PEDIR ("manda áudio", "tô dirigindo") ou como complemento após 3+ trocas.
  LEAD PEDIU: reply_parts = ponte curta ("segura aí"). audio_text = resposta COMPLETA (40-70 palavras).
  COMPLEMENTO: reply_parts = resposta normal. audio_text = CURTO (20-35 palavras, só emoção). Ou vazio.
  INÍCIO DA CONVERSA: só texto. Áudio vazio.
  NUNCA: "te gravei", "gravei aqui". NUNCA repita no áudio o que já tá no texto.
  NUNCA anuncie que vai mandar áudio. NUNCA diga 'segura aí', 'vou te mandar um áudio', 'gravando pra você'.
  Se o campo audio_text for preenchido, o SISTEMA decide se envia. Você NÃO sabe se o áudio vai chegar."""

    # ── Regras absolutas (comprimidas, deduplicadas) ──
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias. ANTI-ALUCINAÇÃO: só afirme fatos listados acima.
  2. NUNCA mencione concorrentes. NUNCA use palavras proibidas.
  3. Na dúvida: "{identity.fallback_message}"
  4. FORMATAÇÃO PROIBIDA: sem markdown, asteriscos, negrito, itálico, travessão, bullet points. Texto corrido.
  5. NÃO avance no funil sem dados obrigatórios.
  6. FOCO NO NEGÓCIO: off-topic → redirecione educadamente.
  7. Espelhe o ritmo do lead. Curto com curto. Detalhado com detalhado.
  8. NUNCA termine sem pergunta ou convite (exceto won/lost).
  9. ANTI-REPETIÇÃO: releia histórico INTEIRO (texto + [áudio enviado: ...]). Se já disse, NÃO repita.
     Repetir com palavras diferentes AINDA É REPETIÇÃO. "hmm"/"ok" NÃO justificam reenviar info.
     Se já mandou action (payment/appointment): NÃO mande de novo.
  10. SOM DE HUMANO: contrações (tá, pra, né). Varie comprimento. Comece frases diferente.
      NUNCA: "te gravei", "direitinho", "explicadinho", "certinho", "viu" no final.
      PROIBIDO começar msg com: "Claro!", "Com certeza!", "Opa!", "Eai!", "Beleza?", "Show!".
      Varie aberturas: "que bom", "entendo", "então", "olha", "legal".
      NUNCA repita nome do lead em toda msg. Máx 1 a cada 3-4 msgs.
  11. DADOS JÁ COLETADOS: verifique MEMÓRIA DO LEAD. Se já tem, NÃO pergunte de novo.
  12. VOCÊ É O NEGÓCIO: VOCÊ gera links, VOCÊ agenda. NUNCA peça pro lead fazer seu trabalho.
  13. RAPPORT: msgs CURTAS (1-2 frases). Crie conexão antes de vender. Brasileiro de verdade.
  14. GRAMÁTICA: revise concordância. "Eu manja" está ERRADO. Erros destroem credibilidade.
  15. CTA OBRIGATÓRIO: TODA resposta DEVE terminar com pergunta, convite ou próximo passo que avance a conversa.
      Mensagem informativa solta é PROIBIDA. Se informou algo, pergunte. Se respondeu dúvida, direcione.
      Exemplos de final PROIBIDO: "...te explica o que faz sentido pra você."
      Exemplos de final CORRETO: "...te explica o que faz sentido pra você. Qual dia fica melhor pra gente marcar?"
  16. PREÇO: NUNCA revele preço se o lead NÃO perguntou. Se perguntou, NUNCA mande valor solto.
      Sempre: valor + contexto + CTA de agendamento/avaliação. Preço sem valor percebido = objeção garantida.
  17. POSTURA: NUNCA peça desculpas nem se rebaixe ("você tem razão, eu errei", "peço desculpas").
      Lead reclamou? Reconheça a frustração + redirecione. "Entendo e quero resolver. Me conta mais."
      Closer mantém autoridade. Closer resolve. Closer NUNCA se rende.\""""

    # ── Vertical knowledge (learning engine) ──
    if identity.category:
        from huma.services.learning_engine import build_vertical_prompt
        vertical_prompt = build_vertical_prompt(identity.category)
        if vertical_prompt:
            prompt += vertical_prompt

    # ── Market analysis ──
    if identity.market_analysis:
        ma = identity.market_analysis
        prompt += "\n\nMERCADO:\n"
        if ma.get("market_context"):
            prompt += f"  {ma['market_context']}\n"
        if ma.get("target_audience"):
            prompt += f"  Público: {ma['target_audience']}\n"
        if ma.get("top_arguments"):
            prompt += f"  Argumentos: {', '.join(ma['top_arguments'])}\n"
        if ma.get("top_objections"):
            prompt += f"  Objeções comuns: {', '.join(ma['top_objections'])}\n"

    # ── Speech patterns ──
    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    # ── Correction examples ──
    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO:"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA: \"{c.get('ai_said', '')}\" → Dono: \"{c.get('owner_corrected', '')}\""

    return prompt


# ================================================================
# VERTICAL COMPRIMIDO (Tier 3) — tabela 1-linha por perfil
# Substitui learning_engine.build_vertical_prompt no Tier 3.
# Economia: ~600 tokens.
# ================================================================

_VERTICAL_COMPRESSED = {
    "clinica": (
        "\nPERFIS (vertical clínica):\n"
        "  Mulher 30+: acolhedor, foco resultado/segurança, medo de dor e resultado ruim.\n"
        "  Homem 40+: direto, foco custo-benefício e discrição.\n"
        "  Jovem 18-29: vibe leve, foco transformação e rede social.\n"
        "  OBJEÇÕES FREQUENTES: preço alto, medo de dor, tempo de recuperação, medo de ficar artificial, 'vou pensar', vergonha.\n"
        "  ERROS FATAIS: pressionar lead com medo, falar preço antes de construir valor, ignorar emoção do lead, usar juridiquês médico.\n"
        "  ABORDAGEM: acolha primeiro, explique depois. Resultado > procedimento. Segurança > velocidade. Avaliação gratuita remove barreira. Foto antes/depois é o argumento mais forte.\n"
        "  GATILHO DE URGÊNCIA: 'os horários da Dra. estão bem concorridos essa semana', 'agenda abre segunda e lota rápido', 'tem 2 vagas esse mês'.\n"
        "  FOLLOW-UP: se lead sumiu após 4-6h, tom acolhedor sem pressão: 'ficou alguma dúvida? tô aqui pra te ajudar'. Nunca cobrar decisão."
    ),
    "ecommerce": (
        "\nPERFIS (e-commerce):\n"
        "  Comprador rápido: quer link e fechar. Seja ágil, zero enrolação.\n"
        "  Pesquisador: compara preço. Destaque diferencial e prova social.\n"
        "  Caçador de desconto: compara com concorrente, quer cupom.\n"
        "  OBJEÇÕES FREQUENTES: frete caro, demora pra chegar, medo de golpe, produto diferente da foto, 'vi mais barato em outro lugar'.\n"
        "  ERROS FATAIS: demorar pra responder, não mostrar foto real, não oferecer garantia, esconder frete até o checkout.\n"
        "  ABORDAGEM: Pix com desconto fecha mais que promoção. Foto real > foto de catálogo. Frete grátis converte mais que desconto. Lead que pergunta sobre troca está perto de comprar.\n"
        "  GATILHO DE URGÊNCIA: 'últimas peças no estoque', 'promoção válida até hoje', 'frete grátis só essa semana'.\n"
        "  FOLLOW-UP: se lead sumiu após 1-2h, tom direto: 'separei aqui pra você, fecha no pix?'. Reativar rápido antes do interesse esfriar."
    ),
    "salao_barbearia": (
        "\nPERFIS (salão/barbearia):\n"
        "  Cliente recorrente: informal, quer horário. Vá direto ao ponto.\n"
        "  Novo: pergunta processo/preço. Acolha e mostre diferencial.\n"
        "  OBJEÇÕES FREQUENTES: mudança de horário, profissional indisponível, preço, 'não conheço', distância.\n"
        "  ERROS FATAIS: não confirmar horário com antecedência, tratar cliente fiel como novo, ignorar preferência por profissional, prometer horário sem checar agenda.\n"
        "  ABORDAGEM: horário é rei — confirme sempre. Foto de resultado (antes/depois de corte) converte muito. Cliente que pede 'o de sempre' quer rapidez, não explicação. Primeira vez tem desconto remove fricção.\n"
        "  GATILHO DE URGÊNCIA: 'os horários de sexta e sábado lotam cedo', 'tenho uma vaga às X, garante?', 'só sobrou esse horário'.\n"
        "  FOLLOW-UP: se lead sumiu após 2-3h, tom informal: 'fechou o horário ou quer que eu veja outro?'. Clientela rotativa, reativação rápida."
    ),
    "advocacia_financeiro": (
        "\nPERFIS (advocacia/financeiro):\n"
        "  Urgente: problema real, quer solução. Seja técnico e confiável.\n"
        "  Prevenção: dúvida aberta. Consulta diagnóstica.\n"
        "  OBJEÇÕES FREQUENTES: preço, medo de perder, 'não entendo nada de lei', 'preciso mesmo?', 'faço sozinho'.\n"
        "  ERROS FATAIS: dar conselho jurídico no WhatsApp, usar juridiquês, prometer resultado, minimizar o problema do lead.\n"
        "  ABORDAGEM: NUNCA dê parecer no chat — convide pra consulta. Sigilo e confiança > preço. Linguagem simples converte. Cliente que pergunta preço primeiro é o mais difícil. Primeira consulta gratuita remove barreira.\n"
        "  GATILHO DE URGÊNCIA: baseado em prazo processual real — 'o prazo pra contestar é X dias', 'quanto antes, menos complicado'. NUNCA fabricar urgência falsa.\n"
        "  FOLLOW-UP: se lead sumiu após 12-24h, tom consultivo e sóbrio: 'conseguiu pensar sobre? qualquer dúvida, posso esclarecer'. Respeitar o tempo de decisão."
    ),
    "academia_personal": (
        "\nPERFIS (academia/personal):\n"
        "  Iniciante: inseguro, quer acolhimento. Foque no objetivo, nunca no corpo.\n"
        "  Avançado: quer resultado. Técnico e direto.\n"
        "  OBJEÇÕES FREQUENTES: vergonha, não sei usar equipamentos, preço, tempo, fidelidade com a antiga academia, localização.\n"
        "  ERROS FATAIS: comentar corpo/peso negativamente, julgar sedentarismo, falar em 'perder peso' antes do lead, pressionar iniciante.\n"
        "  ABORDAGEM: NUNCA julgue o corpo. Foque no OBJETIVO do lead. Teste grátis 3-7 dias é o melhor funil. Foto do espaço > lista de modalidades. Janeiro e julho são picos — prepare-se.\n"
        "  GATILHO DE URGÊNCIA: 'matrícula da promoção acaba sexta', 'teste grátis de 7 dias começa quando você quiser', 'vagas do horário das 18h acabam rápido'.\n"
        "  FOLLOW-UP: se lead sumiu após 6-8h, tom motivacional: 'bora começar? posso te marcar o teste pra amanhã'. Reativar enquanto motivação tá alta."
    ),
    "restaurante": (
        "\nPERFIS (restaurante):\n"
        "  Reserva: quer data/horário/mesa. Direto.\n"
        "  Dúvida cardápio: caloroso, descrição sensorial.\n"
        "  Faminto agora: quer pedir delivery rápido.\n"
        "  OBJEÇÕES FREQUENTES: taxa de entrega, tempo de espera, preço por pessoa, cardápio limitado, indisponibilidade de horário.\n"
        "  ERROS FATAIS: demorar pra responder horário de pico, não confirmar reserva, não perguntar restrições alimentares, prometer prazo que não cumpre.\n"
        "  ABORDAGEM: foto do prato > descrição. Combo/promoção do dia converte mais. Reserva confirma-se com hora e nome. Descrição sensorial (crocante, cremoso) vende mais que ingredientes.\n"
        "  GATILHO DE URGÊNCIA: 'sábado já tá quase lotado', 'pizza do dia tem desconto só até 20h', 'última mesa pra 6 pessoas'.\n"
        "  FOLLOW-UP: se lead sumiu após 30-60min (delivery) ou 1-2h (reserva), tom direto: 'vai querer fechar o pedido?' ou 'confirmo sua reserva?'. Janela curta."
    ),
    "pet": (
        "\nPERFIS (pet):\n"
        "  Dono ansioso: quer cuidado. SEMPRE pergunte nome do pet.\n"
        "  Prático: quer banho/vacina/ração resolvidos rápido.\n"
        "  OBJEÇÕES FREQUENTES: medo de maus tratos, profissional desconhecido, preço, distância, horário.\n"
        "  ERROS FATAIS: diagnosticar saúde pelo chat, tratar pet como 'bicho' genérico, esquecer o nome do pet, prometer prazo irreal de banho/tosa.\n"
        "  ABORDAGEM: SEMPRE pergunte o nome do pet — gera conexão instantânea. Foto durante/depois do banho gera encantamento. Pacote mensal (4 banhos) converte mais que avulso. Leva-e-traz é diferencial enorme. NUNCA diagnostique saúde — encaminhe pro veterinário.\n"
        "  GATILHO DE URGÊNCIA: 'os horários de sábado lotam já na terça', 'promoção do pacote mensal acaba sexta', 'temos um horário de leva-e-traz livre amanhã'.\n"
        "  FOLLOW-UP: se lead sumiu após 3-4h, tom carinhoso usando nome do pet: 'como tá o Thor? marcamos o banho dele?'. Conexão emocional reativa."
    ),
    "imobiliaria": (
        "\nPERFIS (imobiliária):\n"
        "  Investidor: foco ROI/localização. Técnico.\n"
        "  Morador: foco família/rotina. Aspiracional.\n"
        "  Primeiro imóvel: inseguro, muitas dúvidas sobre financiamento.\n"
        "  OBJEÇÕES FREQUENTES: não tenho entrada, financiamento difícil, burocracia, liquidez, vacância, manutenção.\n"
        "  ERROS FATAIS: ignorar perfil do comprador, falar números com morador (ou aspiração com investidor), prometer aprovação de financiamento, minimizar burocracia.\n"
        "  ABORDAGEM: visita presencial é o momento de maior conversão. Lead que pede simulação está 70% decidido. FGTS como entrada abre portas. Mencionar cônjuge (se souber) aumenta confiança.\n"
        "  GATILHO DE URGÊNCIA: 'esse imóvel tem outra visita marcada essa semana', 'taxa de juros do financiamento pode subir', 'promoção da construtora válida até dia X'.\n"
        "  FOLLOW-UP: se lead sumiu após 24-48h, tom consultivo: 'conseguiu pensar? posso agendar uma visita no fim de semana?'. Decisão grande, tempo longo."
    ),
    "educacao": (
        "\nPERFIS (educação):\n"
        "  Indeciso: quer transformação. Use cases. NUNCA pareça vendedor.\n"
        "  Decidido: quer processo/preço. Direto.\n"
        "  Pai/mãe decidindo: quer segurança pro filho.\n"
        "  OBJEÇÕES FREQUENTES: preço, tempo, 'não sei se consigo', horários, 'meu filho tem idade?'.\n"
        "  ERROS FATAIS: pressionar como vendedor, prometer emprego, minimizar esforço necessário, ignorar dúvida sobre metodologia.\n"
        "  ABORDAGEM: depoimento de aluno > qualquer feature. Aula experimental gratuita é o melhor funil. Certificado reconhecido é argumento forte. Pra pai/mãe: segurança e metodologia > preço.\n"
        "  GATILHO DE URGÊNCIA: 'matrículas da próxima turma encerram sexta', 'desconto de primeira matrícula vai só até X', 'últimas vagas da turma da manhã'.\n"
        "  FOLLOW-UP: se lead sumiu após 12-24h, tom motivador sem pressão: 'conseguiu conversar em casa? posso tirar mais alguma dúvida?'. Decisão envolve família."
    ),
    "servicos": (
        "\nPERFIS (serviços):\n"
        "  Quer orçamento: foco prazo/qualidade. Seja confiável.\n"
        "  Quer tirar dúvida: consultivo.\n"
        "  Urgente: tem problema pra resolver agora.\n"
        "  OBJEÇÕES FREQUENTES: preço, prazo longo, 'já fui enganado antes', preço alto pra urgência.\n"
        "  ERROS FATAIS: não mostrar portfólio, prometer prazo irreal, não oferecer garantia, ser genérico demais na abordagem.\n"
        "  ABORDAGEM: lead que foi enganado precisa de MAIS garantias que o normal. Contrato com garantia de ajustes remove a principal barreira. Mostrar portfólio antes do preço aumenta percepção de valor. Orçamento sem compromisso remove fricção.\n"
        "  GATILHO DE URGÊNCIA: 'consigo encaixar você ainda essa semana', 'agenda da próxima semana já tá fechando', 'posso travar o preço desse orçamento por 48h'.\n"
        "  FOLLOW-UP: se lead sumiu após 6-12h, tom confiável: 'te mandei o orçamento, deu pra ver? qualquer ajuste a gente conversa'. Confiança > pressão."
    ),
    "automotivo": (
        "\nPERFIS (automotivo):\n"
        "  Emergência: quer rapidez. Direto.\n"
        "  Planejada: quer transparência de preço/prazo.\n"
        "  OBJEÇÕES FREQUENTES: preço alto, tempo de espera, 'concessionária é melhor?', 'peças originais?', desconfiança.\n"
        "  ERROS FATAIS: não perguntar modelo/ano, prometer prazo que não cumpre, esconder peças usadas, orçamento sem diagnóstico real.\n"
        "  ABORDAGEM: SEMPRE pergunte modelo e ano do carro — mostra profissionalismo. Foto/vídeo do problema gera confiança absurda. Orçamento sem compromisso remove barreira. Garantia nas peças e serviço é o argumento de fechamento.\n"
        "  GATILHO DE URGÊNCIA: 'se deixar piorar vai sair bem mais caro', 'consigo encaixar hoje ainda', 'peça disponível agora, se pedir amanhã só na semana que vem'.\n"
        "  FOLLOW-UP: se lead sumiu após 4-8h (emergência) ou 24h (preventiva), tom técnico: 'conseguiu decidir? o orçamento fica válido por 5 dias'. Prazo concreto reativa."
    ),
    "outros": (
        "\nPERFIS (geral):\n"
        "  Comprador decidido: quer solução rápida. Seja direto, sem enrolação.\n"
        "  Pesquisador: compara opções. Destaque diferencial e prova social.\n"
        "  Curioso: não sabe se precisa. Faça perguntas pra descobrir a dor.\n"
        "  OBJEÇÕES FREQUENTES: preço, 'vou pensar', 'já tenho fornecedor', desconfiança.\n"
        "  ERROS FATAIS: ser genérico, não personalizar, pressionar cedo demais.\n"
        "  ABORDAGEM: entenda a dor antes de oferecer solução. Escuta > fala.\n"
        "  GATILHO DE URGÊNCIA: só use quando for REAL — 'disponibilidade limitada', 'condição especial essa semana'. Nunca fabricar pressão falsa.\n"
        "  FOLLOW-UP: se lead sumiu após 6-12h, tom curioso e sem pressão: 'ficou alguma dúvida que eu posso esclarecer?'. Reativar com pergunta aberta."
    ),
}


def _build_vertical_compressed(category) -> str:
    """Retorna tabela comprimida por vertical (Tier 3)."""
    if not category:
        return ""
    key = category.value if hasattr(category, "value") else str(category)
    return _VERTICAL_COMPRESSED.get(key, "")


# ================================================================
# SYSTEM PROMPT — BLOCO DINÂMICO (muda por mensagem)
#
# Dados do lead, posição no funil, hora atual.
# NÃO é cacheado — mas é pequeno (~500-800 tokens).
# ================================================================

def build_dynamic_prompt(
    identity: ClientIdentity,
    conv: Conversation,
    image_url: str | None = None,
) -> str:
    """
    Bloco dinâmico do system prompt — muda a cada mensagem.

    Inclui: gênero, funil, vendas, memória do lead, imagem (se houver).
    """
    prompt = ""

    # ── Gênero do lead ──
    prompt += _build_gender_prompt(conv)

    # ── Funil (só stage atual + vizinhos) ──
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    # ── Inteligência de vendas (compacta) ──
    from huma.services.sales_intelligence import build_sales_intelligence_prompt
    sales_prompt = build_sales_intelligence_prompt(identity, conv)
    if sales_prompt:
        prompt += "\n" + sales_prompt

    # ── Memória do lead ──
    capped_facts = conv.lead_facts[-25:] if conv.lead_facts and len(conv.lead_facts) > 25 else conv.lead_facts
    prompt += "\n\n" + _format_lead_memory(capped_facts, conv.history_summary)

    # ── Image intelligence (SÓ quando tem imagem — economia ~2800 tokens) ──
    if image_url:
        from huma.services.image_intelligence import build_image_intelligence_prompt
        image_prompt = build_image_intelligence_prompt(identity)
        if image_prompt:
            prompt += "\n" + image_prompt

    # ── Mídias (sempre, é curto) ──
    prompt += "\nMÍDIAS: Se lead pedir foto/vídeo, use action send_media com tags relevantes."

    # ── Reforço de regras críticas (final do contexto = maior peso no modelo) ──
    prompt += """
REFORÇO (releia antes de responder):
  - Termine SEMPRE com pergunta ou convite. Mensagem solta é proibida.
  - Nunca jogue preço que o lead não pediu. Se pediu, dê com contexto.
  - Nunca peça desculpa submissa. Reconheça e redirecione.
  - Nunca anuncie áudio. O sistema decide.
  - Use o NOME do lead (da memória). Nunca escreva a palavra 'nome' como placeholder."""

    # ── Identity anchor (final = maior peso) ──
    prompt += f"""

LEMBRETE: Você é "{identity.business_name}". Você VENDE e ATENDE.
  Já disse isso antes? NÃO repita. O que o lead quer? Releia. Responda com propósito."""

    return prompt


# ================================================================
# TIERED PROMPTS (v11.0) — ajusta tamanho à complexidade da msg
# ================================================================

def _format_products_minimal(products: list) -> str:
    """Produtos só com nome+preço (sem descrição) — Tier 1."""
    if not products:
        return "  Não cadastrados.\n"
    lines = []
    for p in products:
        lines.append(f"  - {p.get('name', '')}: R${p.get('price', '')}")
    return "\n".join(lines) + "\n"


def _format_stage_minimal(identity: ClientIdentity, stage: str) -> str:
    """Stage atual com objetivo em 1 linha — Tier 1."""
    from huma.core.funnel import get_stages
    stages = get_stages(identity)
    for s in stages:
        if s.name == stage:
            objective = getattr(s, "objective", "") or getattr(s, "description", "") or ""
            return f"STAGE: {stage} — {objective[:120]}"
    return f"STAGE: {stage}"


def build_tier1_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Micro prompt (~1.500 tokens) — Tier 1.

    Para msgs simples: "sim", "ok", "meu nome é X", confirmações.
    SEM: FAQ, vertical, insights, sales intel, áudio, gender, profile.
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    category = identity.category.value if identity.category else "Geral"
    tone = identity.tone_of_voice or "Profissional e amigável"

    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. Closer brasileiro.

NEGÓCIO: {category}. Tom: {tone}.

PRODUTOS:
{_format_products_minimal(identity.products_or_services)}
{_format_stage_minimal(identity, conv.stage)}
  stage_action: advance=avança funil, hold=mantém, stop=encerra.
"""

    # Últimos 10 facts + summary
    facts = (conv.lead_facts or [])[-10:]
    if facts:
        prompt += "\nMEMÓRIA:\n"
        for f in facts:
            prompt += f"  - {f}\n"
    else:
        prompt += "\nMEMÓRIA: primeiro contato.\n"

    if conv.history_summary:
        prompt += f"\nCONTEXTO: {conv.history_summary}\n"

    prompt += f"""
REGRAS:
  - SEMPRE responda em português do Brasil. NUNCA use palavras em inglês.
  - Msgs curtas, sem markdown, sem emojis no início.
  - NUNCA invente preço. NUNCA confirme horário (sistema confirma).
  - Se já coletou dado, NÃO pergunte de novo.
  - Palavras proibidas: {forbidden}.
  - Na dúvida: "{identity.fallback_message}".

Responda usando a tool send_reply."""

    return prompt


def build_tier2_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Standard (~3.000 tokens) — Tier 2.

    Para discovery/offer normal. Tem FAQ, funil completo, autonomia.
    SEM: vertical detalhado, insights, sales intel, áudio, image, profile, speech.
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    competitors = ", ".join(identity.competitors) if identity.competitors else "N/A"

    products_text = ""
    if identity.products_or_services:
        for p in identity.products_or_services:
            products_text += f"  - {p.get('name', '')}: {p.get('description', '')} (R${p.get('price', '')})\n"
    else:
        products_text = "  Não cadastrados.\n"

    faq_text = ""
    if identity.faq:
        for item in identity.faq:
            faq_text += f"  P: {item.get('question', '')}\n  R: {item.get('answer', '')}\n"

    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. Closer brasileiro.

IDENTIDADE:
  Negócio: {identity.business_description}
  Categoria: {identity.category.value if identity.category else 'Geral'}
  Tom: {identity.tone_of_voice or 'Profissional e amigável'}
  Palavras proibidas: {forbidden}
  Horário: {identity.working_hours or 'Não definido'}
  Concorrentes (NÃO mencione): {competitors}

PRODUTOS/SERVIÇOS:
{products_text}
FAQ:
{faq_text or '  Nenhuma.'}
REGRAS CUSTOM:
{identity.custom_rules or '  Nenhuma.'}
"""

    # Autonomia (sem áudio — áudio é feature de Tier 3)
    prompt += build_autonomy_prompt(identity)

    # Gênero
    prompt += _build_gender_prompt(conv)

    # Funil (stage + vizinhos)
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    # Lead memory (até 25)
    capped = conv.lead_facts[-25:] if conv.lead_facts and len(conv.lead_facts) > 25 else conv.lead_facts
    prompt += "\n\n" + _format_lead_memory(capped, conv.history_summary)

    # Data/hora (sem isso o Claude inventa datas)
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=-3)))
    prompt += f"\nDATA/HORA ATUAL: {now.strftime('%A, %d/%m/%Y %H:%M')} (Brasília)\n"

    # Regras absolutas comprimidas (~200 tokens)
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preço/produto/prazo/garantia. Só afirme fatos listados.
  2. NUNCA mencione concorrentes. NUNCA use palavras proibidas.
  3. Na dúvida: "{identity.fallback_message}".
  4. Sem markdown, asteriscos, bullets. Texto corrido.
  5. Não avance no funil sem dados obrigatórios.
  6. Off-topic → redirecione educadamente.
  7. Espelhe o ritmo do lead (curto/longo).
  8. Termine com pergunta ou convite (exceto won/lost).
  9. ANTI-REPETIÇÃO: releia histórico. Se já disse, não repita.
  10. Humano: contrações (tá, pra, né). Nunca "te gravei", "certinho".
  11. Dados já coletados: não pergunte de novo.
  12. Você É o negócio: você gera links, você agenda.
  13. Rapport: 1-2 frases. Brasileiro real.
  14. Revise gramática — erros destroem credibilidade.

LEMBRETE: Você é "{identity.business_name}". Responda usando a tool send_reply."""

    return prompt


def build_tier3_prompt(
    identity: ClientIdentity,
    conv: Conversation,
    image_url: str | None = None,
) -> str:
    """
    Full (~5.000 tokens) — Tier 3.

    Replica build_static_prompt + build_dynamic_prompt, mas com
    vertical COMPRIMIDO (tabela em vez de prosa). Economia ~600 tokens.
    """
    forbidden = ", ".join(identity.forbidden_words) if identity.forbidden_words else "Nenhuma"
    competitors = ", ".join(identity.competitors) if identity.competitors else "N/A"

    products_text = ""
    if identity.products_or_services:
        for p in identity.products_or_services:
            products_text += f"  - {p.get('name', '')}: {p.get('description', '')} (R${p.get('price', '')})\n"
    else:
        products_text = "  Não cadastrados.\n"

    faq_text = ""
    if identity.faq:
        for item in identity.faq:
            faq_text += f"  P: {item.get('question', '')}\n  R: {item.get('answer', '')}\n\n"

    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO, CLOSER DE VENDAS.
Cada mensagem tem um objetivo. Cada palavra avança a conversa.

IDENTIDADE:
  Negócio: {identity.business_description}
  Categoria: {identity.category.value if identity.category else 'Geral'}
  Tom: {identity.tone_of_voice or 'Profissional e amigável'}
  Palavras proibidas: {forbidden}
  Horário: {identity.working_hours or 'Não definido'}
  Concorrentes (NÃO mencione): {competitors}

PRODUTOS/SERVIÇOS:
{products_text}
FAQ:
{faq_text or '  Nenhuma.'}
REGRAS CUSTOM:
{identity.custom_rules or '  Nenhuma.'}
"""

    # Tom por vertical
    category_str = identity.category.value if identity.category else ""
    vertical_tone = _build_vertical_tone_prompt(category_str)
    if vertical_tone:
        prompt += vertical_tone

    # Autonomia
    prompt += build_autonomy_prompt(identity)

    # Áudio
    prompt += """

ÁUDIO:
  Campo audio_text na tool. Sistema converte em voice note.
  QUANDO: só se lead PEDIR ("manda áudio", "tô dirigindo") ou como complemento após 3+ trocas.
  LEAD PEDIU: reply_parts = ponte curta ("segura aí"). audio_text = resposta COMPLETA (40-70 palavras).
  COMPLEMENTO: reply_parts = resposta normal. audio_text = CURTO (20-35 palavras, só emoção). Ou vazio.
  INÍCIO DA CONVERSA: só texto. Áudio vazio.
  NUNCA: "te gravei", "gravei aqui". NUNCA repita no áudio o que já tá no texto."""

    # 14 regras absolutas completas
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias. ANTI-ALUCINAÇÃO: só afirme fatos listados acima.
  2. NUNCA mencione concorrentes. NUNCA use palavras proibidas.
  3. Na dúvida: "{identity.fallback_message}"
  4. FORMATAÇÃO PROIBIDA: sem markdown, asteriscos, negrito, itálico, travessão, bullet points. Texto corrido.
  5. NÃO avance no funil sem dados obrigatórios.
  6. FOCO NO NEGÓCIO: off-topic → redirecione educadamente.
  7. Espelhe o ritmo do lead. Curto com curto. Detalhado com detalhado.
  8. NUNCA termine sem pergunta ou convite (exceto won/lost).
  9. ANTI-REPETIÇÃO: releia histórico INTEIRO (texto + [áudio enviado: ...]). Se já disse, NÃO repita.
     Repetir com palavras diferentes AINDA É REPETIÇÃO. "hmm"/"ok" NÃO justificam reenviar info.
     Se já mandou action (payment/appointment): NÃO mande de novo.
  10. SOM DE HUMANO: contrações (tá, pra, né). Varie comprimento. Comece frases diferente.
      NUNCA: "te gravei", "direitinho", "explicadinho", "certinho", "viu" no final.
      NUNCA comece toda resposta com "Claro!" ou "Com certeza!". Varie: "opa", "então", "olha".
      NUNCA repita nome do lead em toda msg. Máx 1 a cada 3-4 msgs.
  11. DADOS JÁ COLETADOS: verifique MEMÓRIA DO LEAD. Se já tem, NÃO pergunte de novo.
  12. VOCÊ É O NEGÓCIO: VOCÊ gera links, VOCÊ agenda. NUNCA peça pro lead fazer seu trabalho.
  13. RAPPORT: msgs CURTAS (1-2 frases). Crie conexão antes de vender. Brasileiro de verdade.
  14. GRAMÁTICA: revise concordância. "Eu manja" está ERRADO. Erros destroem credibilidade."""

    # Vertical COMPRIMIDO (em vez de learning_engine.build_vertical_prompt)
    vertical_comp = _build_vertical_compressed(identity.category)
    if vertical_comp:
        prompt += vertical_comp

    # Market analysis
    if identity.market_analysis:
        ma = identity.market_analysis
        prompt += "\n\nMERCADO:\n"
        if ma.get("market_context"):
            prompt += f"  {ma['market_context']}\n"
        if ma.get("target_audience"):
            prompt += f"  Público: {ma['target_audience']}\n"
        if ma.get("top_arguments"):
            prompt += f"  Argumentos: {', '.join(ma['top_arguments'])}\n"
        if ma.get("top_objections"):
            prompt += f"  Objeções comuns: {', '.join(ma['top_objections'])}\n"

    # Speech patterns
    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    # Correction examples
    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO:"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA: \"{c.get('ai_said', '')}\" → Dono: \"{c.get('owner_corrected', '')}\""

    # --- Bloco dinâmico ---
    prompt += _build_gender_prompt(conv)
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    try:
        from huma.services.sales_intelligence import build_sales_intelligence_prompt
        sales_prompt = build_sales_intelligence_prompt(identity, conv)
        if sales_prompt:
            prompt += "\n" + sales_prompt
    except Exception:
        pass

    capped = conv.lead_facts[-25:] if conv.lead_facts and len(conv.lead_facts) > 25 else conv.lead_facts
    prompt += "\n\n" + _format_lead_memory(capped, conv.history_summary)

    if image_url:
        try:
            from huma.services.image_intelligence import build_image_intelligence_prompt
            image_prompt = build_image_intelligence_prompt(identity)
            if image_prompt:
                prompt += "\n" + image_prompt
        except Exception:
            pass

    prompt += "\nMÍDIAS: Se lead pedir foto/vídeo, use action send_media com tags relevantes."

    prompt += f"""

LEMBRETE: Você é "{identity.business_name}". Você VENDE e ATENDE.
  Já disse isso antes? NÃO repita. O que o lead quer? Releia. Responda com propósito."""

    return prompt


# ================================================================
# LEGACY: build_system_prompt (retrocompatibilidade)
# ================================================================

def build_system_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Monta system prompt completo (legacy — usado por outros módulos).

    Para generate_response, usar build_static_prompt + build_dynamic_prompt
    com 2 system blocks pra cache.
    """
    return build_static_prompt(identity) + "\n" + build_dynamic_prompt(identity, conv)


# ================================================================
# TOOL DEFINITION — força JSON válido sempre
# ================================================================

def _build_reply_tool(messaging_style: MessagingStyle) -> dict:
    """Define a tool que força o Claude a retornar JSON estruturado."""
    if messaging_style == MessagingStyle.SPLIT:
        reply_property = {
            "reply_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 msgs curtas separadas. Cada uma 1-2 frases com função própria.",
                "minItems": 1,
                "maxItems": 4,
            }
        }
        required_reply = ["reply_parts"]
    else:
        reply_property = {
            "reply": {
                "type": "string",
                "description": "Mensagem única. Máximo 3 frases.",
            }
        }
        required_reply = ["reply"]

    return {
        "name": "send_reply",
        "description": "Envia resposta pro lead no WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                **reply_property,
                "audio_text": {
                    "type": "string",
                    "description": (
                        "Voice note. Lead PEDIU: resposta completa 40-70 palavras. "
                        "Complemento: curto 20-35 palavras, só emoção. Vazio se não faz sentido. "
                        "NUNCA repita o texto. NUNCA 'te gravei'."
                    ),
                },
                "intent": {
                    "type": "string",
                    "enum": ["price", "buy", "objection", "schedule", "support", "neutral"],
                    "description": "Intenção do lead.",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["frustrated", "anxious", "excited", "cold", "neutral"],
                    "description": "Sentimento do lead.",
                },
                "stage_action": {
                    "type": "string",
                    "enum": ["advance", "hold", "stop"],
                    "description": "advance=avançar funil, hold=manter, stop=encerrar.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confiança 0.0-1.0.",
                },
                "micro_objective": {
                    "type": "string",
                    "description": "O que esta resposta quer alcançar.",
                },
                "emotional_reading": {
                    "type": "string",
                    "description": "Leitura emocional do lead.",
                },
                "new_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fatos novos descobertos sobre o lead.",
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Ações especiais. Cada action tem 'type' + campos:\n"
                        "create_appointment: lead_name, lead_email, service, date_time\n"
                        "generate_payment: lead_name, description, amount_cents, payment_method, lead_cpf (boleto)\n"
                        "send_media: tags (lista)"
                    ),
                },
            },
            "required": required_reply + ["intent", "sentiment", "stage_action", "confidence"],
        },
    }


def _build_reply_tool_compact(messaging_style: MessagingStyle) -> dict:
    """
    Versão compacta de _build_reply_tool — sem descriptions nos campos.
    Preserva branching SPLIT/SINGLE. Economia ~400 tokens por call.

    v12 (Cenário 7): adiciona check_availability na description de actions
    (structural — ver CLAUDE.md §1).
    """
    if messaging_style == MessagingStyle.SPLIT:
        reply_property = {
            "reply_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 msgs curtas. A ÚLTIMA DEVE terminar com pergunta ou convite pro próximo passo. NUNCA termine com informação solta.",
                "minItems": 1,
                "maxItems": 4,
            }
        }
        required_reply = ["reply_parts"]
    else:
        reply_property = {
            "reply": {
                "type": "string",
                "description": "Mensagem única. DEVE terminar com pergunta ou convite pro próximo passo.",
            }
        }
        required_reply = ["reply"]

    return {
        "name": "send_reply",
        "description": "Envia resposta pro lead no WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                **reply_property,
                "audio_text": {"type": "string"},
                "intent": {
                    "type": "string",
                    "enum": ["price", "buy", "objection", "schedule", "support", "neutral"],
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["frustrated", "anxious", "excited", "cold", "neutral"],
                },
                "stage_action": {
                    "type": "string",
                    "enum": ["advance", "hold", "stop"],
                },
                "confidence": {"type": "number"},
                "micro_objective": {"type": "string"},
                "emotional_reading": {"type": "string"},
                "new_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Ações especiais. Cada item DEVE ter o campo 'type' obrigatório + campos específicos:\n"
                        "- type='check_availability': (campos opcionais: urgency='urgent'|'normal', slots_to_find=5). Emita quando o lead pedir pra saber horários disponíveis, demonstrar urgência, ou pedir o próximo horário livre. O sistema consulta o Calendar e injeta os horários reais no próximo turn — você NÃO precisa dizer 'vou verificar', apenas emita a action.\n"
                        "- type='create_appointment': lead_name, lead_email, service, date_time. Emita quando o lead escolher um horário específico e você tiver nome+email.\n"
                        "- type='cancel_appointment': (sem campos — só emita quando lead insistiu em cancelar após você oferecer alternativa E perguntar motivo; sistema deleta o evento no Calendar)\n"
                        "- type='generate_payment': lead_name, description, amount_cents, payment_method, lead_cpf (só boleto)\n"
                        "- type='send_media': tags (lista de strings)"
                    ),
                },
            },
            "required": required_reply + ["intent", "sentiment", "stage_action", "confidence"],
        },
    }


# ================================================================
# GERAÇÃO DE RESPOSTA
# ================================================================

async def generate_response(identity, conv, user_text, image_url=None, use_fast_model=False, tier: int = 3):
    """
    Gera resposta da IA usando tool_use para garantir JSON válido.

    v10.1: usa 2 system blocks pra cache do Anthropic API.
    Bloco 1 (estático): cacheado entre mensagens do mesmo cliente.
    Bloco 2 (dinâmico): muda por mensagem, pequeno.
    """
    model = AI_MODEL_FAST if use_fast_model else AI_MODEL_PRIMARY

    # ── Montagem do system prompt por tier ──
    if tier == 1:
        # Tier 1: micro prompt, sem cache — mantém como está
        static = build_tier1_prompt(identity, conv)
        dynamic = ""
    elif tier == 2:
        # Tier 2: prompt ORIGINAL completo, sem insights e sem profiling
        static = build_static_prompt(identity)
        dynamic = build_dynamic_prompt(identity, conv, image_url=image_url)
    else:
        # Tier 3: prompt ORIGINAL + insights + profiling (comportamento pré-tiers)
        static = build_static_prompt(identity)

        try:
            learned = await _get_insights_cached(identity.client_id)
            if learned:
                static += learned
        except Exception:
            pass

        dynamic = build_dynamic_prompt(identity, conv, image_url=image_url)

        try:
            from huma.services.learning_engine import profile_lead, build_profile_prompt
            hour = conv.last_message_at.hour if conv.last_message_at else None
            lead_profile = profile_lead(conv.phone, user_text, conv.lead_facts, hour)
            profile_prompt = build_profile_prompt(lead_profile)
            if profile_prompt:
                dynamic += profile_prompt
        except Exception:
            pass

    log.info(f"Prompt | tier={tier} | static_chars={len(static)} | dynamic_chars={len(dynamic)} | est_static_tokens={len(static)//4} | est_dynamic_tokens={len(dynamic)//4}")

    # Monta mensagens
    messages = [{"role": m["role"], "content": m["content"]} for m in conv.history]

    if image_url:
        if image_url.startswith("data:"):
            parts = image_url.split(",", 1)
            media_type = parts[0].replace("data:", "").replace(";base64", "")
            b64_data = parts[1] if len(parts) > 1 else ""
            image_block = {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            }
        else:
            image_block = {
                "type": "image",
                "source": {"type": "url", "url": image_url},
            }

        messages.append({
            "role": "user",
            "content": [
                image_block,
                {"type": "text", "text": user_text.strip() or "Lead enviou imagem."},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    reply_tool = _build_reply_tool_compact(identity.messaging_style)

    # ── System blocks por tier (v11.2 — cache defensivo) ──
    if tier == 1:
        # Tier 1 foi eliminado em v11.2. Mantido aqui como fallback defensivo:
        # se por algum motivo alguém chamar com tier=1, cai em single string.
        system_blocks = static
    else:
        # Tier 2/3: cache no estático.
        # Haiku 4.5 exige mínimo 4096 TOKENS (~9500 chars em PT-BR, ratio ~2.33 chars/token).
        # Sonnet exige mínimo 1024 tokens (~2400 chars).
        # Valores conservadores com margem de segurança.
        min_chars_for_cache = 9000 if "haiku" in model.lower() else 2400
        static_is_cacheable = len(static) >= min_chars_for_cache

        static_block = {"type": "text", "text": static}
        if static_is_cacheable:
            # TTL 1h: write custa 2x ($2/MTok Haiku) vs 1,25x do 5min, mas read
            # continua 0,1x ($0,10/MTok). Leads no WhatsApp costumam demorar
            # minutos entre mensagens — 1h garante cache hit em conversas longas.
            static_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            log.info(f"Cache | tier={tier} | static ELIGIBLE ttl=1h | chars={len(static)} | min={min_chars_for_cache}")
        else:
            log.warning(f"Cache | tier={tier} | static TOO SMALL | chars={len(static)} | min={min_chars_for_cache}")

        system_blocks = [static_block]
        if dynamic:
            system_blocks.append({"type": "text", "text": dynamic})

    # Diagnóstico de cache v11.2 — loga hash do static pra verificar se muda entre chamadas
    import hashlib
    if isinstance(system_blocks, list) and len(system_blocks) > 0:
        first_block = system_blocks[0]
        if isinstance(first_block, dict):
            static_text = first_block.get("text", "")
            static_hash = hashlib.sha256(static_text.encode()).hexdigest()[:16]
            log.info(f"Cache debug | tier={tier} | static_hash={static_hash} | static_len={len(static_text)} | blocks_count={len(system_blocks)} | has_cache_control={'cache_control' in first_block}")

    # Retry com backoff
    max_retries = 2
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = await _get_ai_client().messages.create(
                model=model,
                max_tokens=800,
                system=system_blocks,
                tools=[reply_tool],
                tool_choice={"type": "tool", "name": "send_reply"},
                messages=messages,
            )
            break
        except Exception as e:
            last_error = e
            error_str = str(e)
            if attempt < max_retries and ("529" in error_str or "overloaded" in error_str.lower() or "timeout" in error_str.lower() or "500" in error_str or "429" in error_str or "rate_limit" in error_str.lower()):
                wait = (attempt + 1) * 2
                log.warning(f"IA retry {attempt + 1}/{max_retries} | {type(e).__name__} | aguardando {wait}s")
                import asyncio as _aio
                await _aio.sleep(wait)
                continue
            log.error(f"Erro na IA | {e}")
            return _fallback_result(identity.fallback_message)
    else:
        log.error(f"IA falhou após {max_retries} retries | {last_error}")
        return _fallback_result(identity.fallback_message)

    # Log de cache (v11.1) — instrumenta prompt caching pra validar economia
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
            log.info(
                f"CACHE | tier={tier} | model={model} | "
                f"input={usage.input_tokens} | output={usage.output_tokens} | "
                f"cache_read={cache_read} | cache_creation={cache_creation}"
            )
    except Exception:
        pass  # log de métrica não pode quebrar a resposta

    # Extrai o tool_use block
    parsed = None
    for block in response.content:
        if block.type == "tool_use" and block.name == "send_reply":
            parsed = block.input
            break

    if not parsed:
        log.warning("Tool use não retornou dados")
        return _fallback_result(identity.fallback_message)

    try:
        intent = Intent(parsed.get("intent", "neutral").lower())
    except ValueError:
        intent = Intent.NEUTRAL

    try:
        sentiment = Sentiment(parsed.get("sentiment", "neutral").lower())
    except ValueError:
        sentiment = Sentiment.NEUTRAL

    confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.7))))

    result = {
        "reply": "",
        "reply_parts": [],
        "intent": intent,
        "sentiment": sentiment,
        "stage_action": parsed.get("stage_action", "hold"),
        "confidence": confidence,
        "lead_facts": parsed.get("new_facts", []),
        "actions": parsed.get("actions", []),
        "micro_objective": parsed.get("micro_objective", ""),
        "emotional_reading": parsed.get("emotional_reading", ""),
        "audio_text": parsed.get("audio_text", ""),
    }

    if "reply_parts" in parsed and isinstance(parsed["reply_parts"], list) and parsed["reply_parts"]:
        result["reply_parts"] = parsed["reply_parts"]
        result["reply"] = " ".join(parsed["reply_parts"])
    else:
        result["reply"] = parsed.get("reply", identity.fallback_message)
        result["reply_parts"] = [result["reply"]]

    log.info(
        f"Resposta | intent={intent.value} | conf={confidence:.2f} | "
        f"stage={parsed.get('stage_action','hold')} | actions={len(result['actions'])} | "
        f"objective={result['micro_objective'][:50]}"
    )

    # Sanitiza caracteres ricos antes de devolver (defesa em profundidade vs prompt)
    result = _sanitize_response_dict(result)

    return result


def _fallback_result(text):
    """Resultado seguro quando a IA falha."""
    return {
        "reply": text,
        "reply_parts": [text],
        "intent": Intent.NEUTRAL,
        "sentiment": Sentiment.NEUTRAL,
        "stage_action": "hold",
        "confidence": 0.0,
        "lead_facts": [],
        "actions": [],
        "micro_objective": "",
        "emotional_reading": "",
        "audio_text": "",
    }


# ================================================================
# VALIDAÇÃO (anti-alucinação) — DESLIGADA v10.1
#
# Antes: chamava Haiku a cada mensagem, sempre retornava is_safe=True.
# Custo puro sem benefício. Quando implementar enforcement real,
# reativar com bloqueio (não só warning).
# ================================================================

async def validate_response(identity, reply, confidence):
    """
    Anti-alucinação DESLIGADA (v10.1).

    Motivo: modo soft sempre retornava is_safe=True.
    Economia: 1 chamada Haiku (~500 tokens) por mensagem.

    TODO: reativar quando implementar enforcement real
    (bloquear resposta + regenerar com instrução mais restrita).
    """
    return {"is_safe": True}


# ================================================================
# UTILITÁRIOS
# ================================================================

async def generate_outbound_message(identity, lead, template=""):
    """Gera mensagem de prospecção outbound."""
    prompt = (
        f"Clone de \"{identity.business_name}\". Tom: {identity.tone_of_voice or 'Profissional'}.\n"
        f"Lead: {lead.name or 'N/A'}, empresa: {lead.business_name or 'N/A'}, "
        f"segmento: {lead.business_type or 'N/A'}.\n"
        f"{'Template: ' + template if template else ''}\n"
        f"Escreva 1 mensagem WhatsApp de prospecção. Max 4 frases. Humano. Termine com pergunta."
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


async def compress_history(history, summary, facts):
    """
    Comprime histórico preservando memória do lead.

    v10.1: HISTORY_MAX_BEFORE_COMPRESS reduzido de 14→10 (config.py).
    Comprime mais cedo, mantém histórico mais leve.
    """
    if len(history) <= HISTORY_MAX_BEFORE_COMPRESS:
        return history, summary, facts

    to_compress = history[:-HISTORY_WINDOW]
    recent = history[-HISTORY_WINDOW:]

    messages_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in to_compress
        if isinstance(m.get("content"), str)
    )

    existing_facts = json.dumps(facts[-20:], ensure_ascii=False) if facts else "[]"

    prompt = (
        f"Resuma esta conversa e extraia fatos do lead.\n\n"
        f"Fatos anteriores (MANTENHA todos, adicione novos): {existing_facts}\n\n"
        f"Mensagens:\n{messages_text}\n\n"
        f"Responda APENAS com JSON, sem texto antes ou depois:\n"
        f'{{"summary":"resumo de 3-5 linhas do estado da conversa",'
        f'"facts":["fato 1","fato 2","fato 3"]}}\n\n'
        f"Nos facts, inclua com prefixo:\n"
        f"- perfil: nome, gênero, como gosta de ser chamado\n"
        f"- preferência: pagamento, horário, comunicação\n"
        f"- histórico: o que já comprou, agendou, perguntou\n"
        f"- objeção: o que resistiu e como resolveu\n"
        f"- pendência: promessas abertas, follow-up\n"
        f"NUNCA remova fatos anteriores. Só adicione."
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        raw = raw.replace("```json", "").replace("```", "").strip()
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            raw = raw[brace_start:brace_end + 1]

        parsed = json.loads(raw)

        new_summary = parsed.get("summary", summary)
        new_facts = parsed.get("facts", facts)

        if isinstance(new_facts, list) and len(new_facts) > 25:
            new_facts = new_facts[:25]
            log.info(f"Compressão: fatos cortados pra 25 (tinha {len(parsed.get('facts', []))})")

        log.info(
            f"Compressão OK | msgs_comprimidas={len(to_compress)} | "
            f"msgs_mantidas={len(recent)} | fatos={len(new_facts)}"
        )
        return recent, new_summary, new_facts

    except Exception as e:
        log.error(f"Compressão falhou | {type(e).__name__}: {e} | mantendo original")
        return recent, summary, facts


async def analyze_speech_patterns(chat_text):
    """Analisa padrões de fala do dono a partir de export do WhatsApp."""
    lines = chat_text.strip().split("\n")[-500:]

    prompt = (
        f"Analise estas mensagens de WhatsApp e identifique padrões:\n"
        f"{chr(10).join(lines)}\n"
        f"JSON: {{\"greeting_style\": \"\", \"tone\": \"\", "
        f"\"common_expressions\": [], \"closing_style\": \"\"}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_PRIMARY,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )
        return (
            f"Saudação: {parsed.get('greeting_style', '')}\n"
            f"Tom: {parsed.get('tone', '')}\n"
            f"Expressões: {', '.join(parsed.get('common_expressions', []))}\n"
            f"Fechamento: {parsed.get('closing_style', '')}"
        )
    except Exception:
        return ""
