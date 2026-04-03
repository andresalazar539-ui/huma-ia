# ================================================================
# huma/services/ai_service.py — Cérebro da HUMA
#
# v9.3 — Actions tipadas + instruções obrigatórias:
#   - Tool definition com schema explícito pra actions
#   - create_appointment, generate_payment, send_media tipados
#   - Instruções obrigatórias no autonomy prompt
#   - Sales Intelligence Engine integrado ao system prompt
#   - Contexto temporal, ritmo, persuasão, subtexto
#
# Mantido (zero breaking changes):
#   - Lazy init, generate_response, validate_response
#   - compress_history, analyze_speech_patterns
#   - Formato de saída idêntico (reply_parts, intent, etc)
# ================================================================

import json

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

_client = None


def _get_ai_client():
    """Lazy init do Anthropic client."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            log.warning("ANTHROPIC_API_KEY não configurada")
            return None
        _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _client

# Cache de insights aprendidos (não muda a cada mensagem)
_insights_cache: dict[str, tuple] = {}  # {client_id: (text, timestamp)}
INSIGHTS_CACHE_TTL = 600  # 10 minutos


async def _get_insights_cached(client_id: str) -> str:
    """Busca insights com cache de 10 min."""
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
# SYSTEM PROMPT
# ================================================================

def build_autonomy_prompt(identity: ClientIdentity) -> str:
    """Gera bloco de autonomia baseado nas configs do dono."""
    prompt = ""

    if identity.personality_traits:
        traits = ", ".join(identity.personality_traits)
        prompt += f"\nPERSONALIDADE: Você é {traits}.\n"

    if identity.use_emojis:
        prompt += (
            "EMOJIS (regra rígida):\n"
            "  - Máximo 1 emoji a cada 3-4 mensagens. NÃO em toda mensagem.\n"
            "  - NUNCA no início da mensagem.\n"
            "  - NUNCA junto com informação séria (preço, horário, endereço, dados).\n"
            "  - OK em: celebração ('fechou! 🎉'), humor leve, saudação casual.\n"
            "  - Se o lead NÃO usou emoji, você também NÃO usa.\n"
            "  - Prefira: 😊 👍 🙏 — evite emojis obscuros ou infantis.\n"
            "  - Na dúvida: NÃO use.\n"
        )
    else:
        prompt += "NUNCA use emojis. Zero. Em nenhuma mensagem.\n"

    fields = identity.lead_collection_fields
    if not fields:
        prompt += "\nCOLETA: NÃO pergunte dados pessoais. Apenas escute e responda.\n"
    else:
        field_names = ", ".join(fields)
        prompt += f"\nCOLETA: Você DEVE coletar do lead: {field_names}.\n"
        if identity.collect_before_offer:
            prompt += "Colete ANTES de falar de produto/preço.\n"
        else:
            prompt += "Colete quando for natural na conversa, pode falar de produto antes.\n"

    methods = identity.accepted_payment_methods
    if not methods:
        prompt += "\nPAGAMENTO: Você NÃO processa pagamento. Diga que vai passar pro responsável.\n"
    else:
        method_map = {
            "pix": "Pix (QR code no chat)",
            "boleto": "Boleto (código de barras no chat — PRECISA do CPF do lead)",
            "credit_card": f"Cartão (link seguro, até {identity.max_installments}x)",
        }
        accepted = [method_map.get(m, m) for m in methods]
        prompt += f"\nPAGAMENTO ACEITO: {', '.join(accepted)}.\n"
        if "boleto" in methods:
            prompt += "BOLETO: Antes de gerar, PERGUNTE o CPF do lead. Inclua 'lead_cpf' no action.\n"
        for m in ["pix", "boleto", "credit_card"]:
            if m not in methods:
                prompt += f"NÃO ofereça {m}.\n"

    if identity.enable_scheduling:
        sched_fields = identity.scheduling_required_fields
        if sched_fields:
            prompt += f"\nAGENDAMENTO: Colete {', '.join(sched_fields)} antes de confirmar.\n"
        else:
            prompt += "\nAGENDAMENTO: Confirme direto sem coletar dados extras.\n"

    if identity.max_discount_percent > 0:
        prompt += f"\nDESCONTO: Máximo {identity.max_discount_percent}%. Só ofereça se o lead pedir.\n"
    else:
        prompt += "\nDESCONTO: NUNCA ofereça desconto.\n"

    if identity.enable_scheduling:
        prompt += (
            "\n\nACTION DE AGENDAMENTO (OBRIGATÓRIO):\n"
            "  Quando o lead CONFIRMAR que quer agendar E você tiver nome + email + data/hora + serviço:\n"
            "  INCLUA na resposta uma action com type 'create_appointment'.\n"
            "  O SISTEMA cria evento no Google Calendar, gera link Google Meet, envia convite por email.\n"
            "  Se você NÃO incluir a action, o lead NÃO recebe confirmação real — só texto.\n"
            "  Formato da data: DD/MM/YYYY HH:MM (ex: 04/04/2026 10:00)\n"
            "  NUNCA 'confirme' agendamento só por texto. SEMPRE dispare a action.\n"
        )

    if identity.enable_payments and identity.accepted_payment_methods:
        prompt += (
            "\nACTION DE PAGAMENTO (OBRIGATÓRIO):\n"
            "  Quando o lead CONFIRMAR que quer pagar:\n"
            "  INCLUA na resposta uma action com type 'generate_payment'.\n"
            "  O SISTEMA gera QR code Pix / boleto / link de checkout e envia pro lead.\n"
            "  Se você NÃO incluir a action, o lead NÃO recebe forma de pagamento.\n"
            "  NUNCA diga 'vou gerar o pix' sem incluir a action. A action É a geração.\n"
        )

    return prompt


def build_system_prompt(identity: ClientIdentity, conv: Conversation) -> str:
    """
    Monta o system prompt completo.

    v9.0: integra sales_intelligence pra transformar a IA
    de chatbot em closer de elite.

    Ordem de prioridade no prompt:
      1. Identidade (quem a IA é)
      2. Contexto temporal (quando está falando)
      3. Dados do negócio (produtos, FAQ, regras)
      4. Autonomia do dono (personalidade, coleta, pagamento)
      5. Funil (estágios, posição atual)
      6. Inteligência de vendas (ritmo, micro-objetivos, persuasão, emoção)
      7. Fatos do lead
      8. Regras absolutas (anti-alucinação, proibições)
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

    # ── Bloco 1: Identidade core ──
    prompt = f"""Você é clone do "{identity.business_name}". WhatsApp. HUMANO, BRASILEIRO.
Você é um CLOSER DE VENDAS — não um atendente que responde perguntas.
Cada mensagem sua tem um objetivo. Cada palavra avança a conversa.

RAPPORT:
  - Você é pessoa real, não robô.
  - Mensagens CURTAS (1-2 frases cada). Separadas.
  - Crie conexão antes de vender.
  - Brasileiro de verdade: mensagem picada, informal, sem bloco de texto.

ANTI-PADRÕES DE IA (NUNCA faça isso — se fizer, o lead sabe que é robô):
  - NUNCA mande 2+ mensagens dizendo a mesma coisa com palavras diferentes
  - NUNCA avise o que vai fazer antes de fazer. NÃO diga "vou te explicar", "vou te mandar". FAÇA.
  - NUNCA use: "te gravei", "gravei aqui", "direitinho", "explicadinho", "certinho", "viu" no final de frase
  - NUNCA comece com "Claro!" ou "Com certeza!" em toda resposta. Varie: "opa", "então", "olha", "po"
  - NUNCA repita o nome do lead em toda mensagem. Use 1 a cada 3-4 mensagens no máximo.
  - Se o lead perguntou algo: RESPONDA na hora. Não prometa que vai responder depois.
  - NUNCA use linguagem que brasileiro não usa no WhatsApp. Teste: "eu mandaria isso pra um amigo?" Se não, reescreva.

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

    # ── Bloco 2: Autonomia do dono ──
    prompt += build_autonomy_prompt(identity)

    # ── Bloco 3: Funil ──
    prompt += "\n" + build_funnel_prompt(identity, conv.stage)

    # ── Bloco 4: Inteligência de vendas (NOVO v9.0) ──
    from huma.services.sales_intelligence import build_sales_intelligence_prompt
    sales_prompt = build_sales_intelligence_prompt(identity, conv)
    if sales_prompt:
        prompt += "\n" + sales_prompt

    # ── Bloco 5: Fatos do lead ──
    prompt += f"""

FATOS DO LEAD:
{chr(10).join(f'  - {f}' for f in conv.lead_facts) if conv.lead_facts else '  Nenhum.'}"""

    # ── Bloco 6: Mídias e áudio ──
    prompt += """

MÍDIAS: Se o lead pedir foto/vídeo, use action send_media com tags relevantes.

ÁUDIO — COMO FUNCIONA:
  Você preenche o campo audio_text. O sistema converte em voice note e envia no WhatsApp.

  QUANDO MANDAR ÁUDIO:
    - SOMENTE se o lead PEDIR ("manda áudio", "tô dirigindo", "prefiro ouvir", "me explica por áudio")
    - Ou em momentos estratégicos após 3+ trocas de mensagem (complemento emocional, nunca informacional)
    - NO INÍCIO DA CONVERSA: SÓ texto. Áudio só se o lead pedir explicitamente.

  SE O LEAD PEDIU ÁUDIO:
    - reply_parts: frase CURTA de ponte. Exemplos REAIS:
      "opa, já te mando aqui"
      "minutinho"
      "já tô mandando"
      "segura aí que já vai"
      NÃO use: "te gravei", "gravei aqui pra você", "vou te mandar um áudio explicando"
    - audio_text: resposta COMPLETA (40-70 palavras). Preço, condições, explicação, tudo que ele pediu.
      Fale como brasileiro gravando voice note: direto, natural, com emoção.
      Se ele perguntou preço, FALE o preço no áudio. Se perguntou como funciona, EXPLIQUE no áudio.
      TERMINE o áudio com convite: "qualquer dúvida me fala, tá?" ou "o que achou?" ou "bora?"

  SE O LEAD NÃO PEDIU ÁUDIO (complemento estratégico):
    - reply_parts: resposta completa normal por texto.
    - audio_text: CURTO (20-35 palavras). Só emoção, confiança, experiência. NUNCA repete o texto.
    - Se não faz sentido: audio_text vazio ("").

  DEPOIS DO ÁUDIO:
    Se o audio_text NÃO terminou com pergunta ou convite, o sistema manda um texto curto depois.
    Você não precisa se preocupar com isso — o sistema cuida.

  PROIBIÇÕES ABSOLUTAS:
    - NUNCA escreva "te gravei", "gravei aqui", "te mando o áudio"
    - NUNCA diga que não pode mandar áudio ou que o sistema só permite texto
    - NUNCA repita no audio_text o que já está no reply_parts
    - NUNCA mande áudio sem o lead ter pedido nas primeiras mensagens da conversa"""

    # ── Bloco 7: Regras absolutas (último — maior peso no Claude) ──
    prompt += f"""

REGRAS ABSOLUTAS:
  1. NUNCA invente preços, produtos, prazos ou garantias
  2. NUNCA mencione concorrentes
  3. NUNCA use palavras proibidas
  4. Na dúvida: "{identity.fallback_message}"
  5. FORMATAÇÃO PROIBIDA: sem markdown, sem asteriscos, sem negrito, sem itálico, sem travessão (—), sem meia-risca (–), sem bullet points, sem listas numeradas. Escreva como brasileiro escreve no WhatsApp: texto corrido, simples, sem formatação nenhuma.
  6. NÃO avance no funil sem dados obrigatórios coletados
  7. FOCO NO NEGÓCIO: Se o lead perguntar sobre assuntos sem relação com {identity.business_name}, redirecione educadamente
  8. Cada mensagem sua tem UM micro-objetivo. Se não sabe o que quer alcançar, NÃO responda no automático
  9. ESPELHE o ritmo do lead. Curto com curto. Detalhado com detalhado
  10. NUNCA termine sem pergunta ou convite (exceto em "won" e "lost")
  11. ANTI-REPETIÇÃO (CRÍTICO):
      - Releia o histórico INTEIRO antes de responder. Incluindo [áudio enviado: ...].
      - Se você já disse algo (por texto OU por áudio), NÃO diga de novo. Nem reformulado.
      - Repetir a mesma informação com palavras diferentes AINDA É REPETIÇÃO.
      - Se já explicou o procedimento, NÃO explique de novo a menos que o lead PEÇA de novo com pergunta clara.
      - Se já falou preço, NÃO repita. Se já falou condições, NÃO repita.
      - A única exceção: se o lead faz uma PERGUNTA NOVA e CLARA que pede informação específica que você já deu. Aí responde de forma diferente.
      - "hmm entendi" ou "ok" NÃO são perguntas. NÃO justificam reenviar informação.
  12. SOM DE HUMANO: use contrações (tá, pra, né, pro, tô). Varie o comprimento. Comece frases de formas diferentes. Misture frases curtas com longas.
  13. DADOS JÁ COLETADOS: verifique os FATOS DO LEAD acima. Se o dado já está lá, NÃO pergunte de novo.
  14. OBJETIVO SEMPRE ATIVO: você NUNCA desiste. Seu objetivo é agendamento, venda, ou resolver a dúvida até o fim. Se o lead esfria, você muda o ângulo. Se ele resiste, você tenta de outro jeito. Você SÓ para quando o lead EXPLICITAMENTE disser que não quer. "Ok" NÃO é desistência — é desinteresse. Reengaje.
  15. SENTIMENTO REAL: se o lead ri (kkkk, haha, rsrs), você ri junto ("kkk" ou "haha" no texto). Se tem medo e descontrai, você descontrai junto E DEPOIS tranquiliza com fato concreto. Espelhe a emoção antes de resolver.
  16. PALAVRAS PROIBIDAS DA IA: NUNCA use "te gravei", "gravei aqui", "direitinho" (como muleta), "viu" no final de toda frase, "explicadinho". Essas palavras denunciam IA instantaneamente.

ANTI-ALUCINAÇÃO: Só afirme fatos listados acima. Inventar = falha grave."""

    # ── Blocos condicionais (vertical, market, speech, corrections) ──
    if identity.category:
        from huma.services.learning_engine import build_vertical_prompt
        vertical_prompt = build_vertical_prompt(identity.category)
        if vertical_prompt:
            prompt += vertical_prompt

    if identity.market_analysis:
        ma = identity.market_analysis
        prompt += "\n\nANÁLISE DE MERCADO (use pra adaptar abordagem):\n"
        if ma.get("market_context"):
            prompt += f"  Mercado: {ma['market_context']}\n"
        if ma.get("target_audience"):
            prompt += f"  Público: {ma['target_audience']}\n"
        if ma.get("local_context"):
            prompt += f"  Contexto local: {ma['local_context']}\n"
        if ma.get("top_arguments"):
            prompt += f"  Argumentos fortes: {', '.join(ma['top_arguments'])}\n"
        if ma.get("top_objections"):
            prompt += f"  Objeções comuns: {', '.join(ma['top_objections'])}\n"
        if ma.get("closing_triggers"):
            prompt += f"  Gatilhos de fechamento: {', '.join(ma['closing_triggers'])}\n"
        if ma.get("profiles"):
            prompt += "  Perfis analisados:\n"
            for p in ma["profiles"][:4]:
                prompt += f"    [{p.get('name','')}] {p.get('description','')}\n"
                prompt += f"      Tom: {p.get('ideal_tone','')}\n"
                prompt += f"      Fluxo: {p.get('conversation_flow','')}\n"

    if identity.speech_patterns:
        prompt += f"\n\nPADRÕES DE FALA DO DONO:\n{identity.speech_patterns}"

    if identity.correction_examples:
        prompt += "\n\nCORREÇÕES DO DONO (aprenda com estas):"
        for i, c in enumerate(identity.correction_examples[-10:], 1):
            prompt += f"\n  {i}. IA disse: \"{c.get('ai_said', '')}\" → Dono corrigiu: \"{c.get('owner_corrected', '')}\""

    if conv.history_summary:
        prompt += f"\n\nCONTEXTO ANTERIOR:\n{conv.history_summary}"

    return prompt


# ================================================================
# TOOL DEFINITION — força JSON válido sempre
#
# v9.3: actions tipadas com schema explícito.
#   O Claude sabe exatamente a estrutura de create_appointment,
#   generate_payment e send_media. Sem ambiguidade.
# ================================================================

def _build_reply_tool(messaging_style: MessagingStyle) -> dict:
    """Define a tool que força o Claude a retornar JSON estruturado."""
    if messaging_style == MessagingStyle.SPLIT:
        reply_property = {
            "reply_parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2 a 4 mensagens CURTAS e SEPARADAS. Máximo 1-2 frases cada. "
                    "Parte 1: conexão ou resposta direta. "
                    "Última parte: pergunta ou convite de ação. "
                    "Cada parte tem uma FUNÇÃO — não é só quebrar texto."
                ),
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
        "description": "Envia a resposta para o lead no WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                **reply_property,
                "audio_text": {
                    "type": "string",
                    "description": (
                        "Voice note pro WhatsApp. DUAS SITUAÇÕES:\n\n"
                        "LEAD PEDIU ÁUDIO:\n"
                        "  Resposta COMPLETA no áudio (40-70 palavras).\n"
                        "  RESPONDA O QUE ELE PERGUNTOU. Se pediu preço, FALE O PREÇO. Se pediu endereço, DÊ O ENDEREÇO.\n"
                        "  Se pediu explicação, EXPLIQUE. Se pediu condições, DÊ AS CONDIÇÕES.\n"
                        "  O áudio responde QUALQUER pergunta do lead, não só procedimento.\n"
                        "  Termine com convite: 'qualquer dúvida me fala, tá?' ou 'o que achou?'\n"
                        "  Fale como brasileiro gravando voice note de verdade.\n\n"
                        "LEAD NÃO PEDIU ÁUDIO (complemento):\n"
                        "  CURTO (20-35 palavras). Só emoção, confiança, experiência.\n"
                        "  NUNCA repete o que já tá no texto ou no áudio anterior [áudio enviado: ...].\n"
                        "  Se não faz sentido, string vazia ''.\n"
                        "  NO INÍCIO DA CONVERSA: deixe vazio. Só texto.\n\n"
                        "REGRAS:\n"
                        "  - NUNCA use 'te gravei', 'gravei aqui', 'direitinho'\n"
                        "  - Brasileiro real: 'olha só', 'sério', 'pode confiar', 'tá?'\n"
                        "  - Sem formatação, sem emoji, sem travessão\n"
                        "  - Se o lead ri (kkk, haha), pode rir junto\n"
                        "  - NUNCA repita informação que já foi no texto ou em áudio anterior"
                    ),
                },
                "intent": {
                    "type": "string",
                    "enum": ["price", "buy", "objection", "schedule", "support", "neutral"],
                    "description": "Intenção detectada na mensagem do lead.",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["frustrated", "anxious", "excited", "cold", "neutral"],
                    "description": "Sentimento detectado no lead.",
                },
                "stage_action": {
                    "type": "string",
                    "enum": ["advance", "hold", "stop"],
                    "description": "advance = avançar no funil, hold = manter, stop = encerrar.",
                },
                "confidence": {
                    "type": "number",
                    "description": "Confiança da resposta entre 0.0 e 1.0.",
                },
                "micro_objective": {
                    "type": "string",
                    "description": (
                        "O que esta resposta quer alcançar. Ex: 'descobrir a dor do lead', "
                        "'plantar semente de preço', 'criar urgência', 'acolher frustração'."
                    ),
                },
                "emotional_reading": {
                    "type": "string",
                    "description": (
                        "Leitura emocional detalhada do lead neste momento."
                    ),
                },
                "new_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Novos fatos descobertos sobre o lead.",
                },
                "actions": {
                    "type": "array",
                    "description": (
                        "AÇÕES QUE O SISTEMA EXECUTA AUTOMATICAMENTE. "
                        "VOCÊ DEVE USAR ACTIONS — o sistema depende disso.\n\n"
                        "REGRA CRÍTICA: quando o lead confirmar agendamento (tem nome + email + data/hora + serviço), "
                        "você DEVE incluir uma action create_appointment. O sistema cria o evento no Google Calendar, "
                        "gera link do Google Meet, envia convite por email, e manda tudo pro lead no WhatsApp. "
                        "Se você NÃO incluir a action, NADA disso acontece e o lead fica sem confirmação real.\n\n"
                        "REGRA CRÍTICA: quando o lead quiser pagar (Pix, boleto ou cartão), "
                        "você DEVE incluir uma action generate_payment. O sistema gera QR code Pix, boleto, "
                        "ou link de checkout e envia pro lead. Se você NÃO incluir a action, o lead não recebe nada.\n\n"
                        "TIPOS DISPONÍVEIS:\n\n"
                        "1. AGENDAMENTO — use quando tiver TODOS os dados:\n"
                        "   {\"type\": \"create_appointment\", \"lead_name\": \"nome completo\", "
                        "\"lead_email\": \"email@exemplo.com\", \"service\": \"nome do serviço\", "
                        "\"date_time\": \"DD/MM/YYYY HH:MM\"}\n"
                        "   IMPORTANTE: date_time pode ser texto natural como o lead falou. "
                        "Exemplos: 'terça às 10h', 'amanhã 14h', 'segunda 10:00'. "
                        "O sistema calcula a data exata automaticamente. NÃO tente calcular o dia/mês.\n"
                        "   O sistema cria evento no Google Calendar + link Google Meet + envia convite por email.\n\n"
                        "2. PAGAMENTO — use quando o lead confirmar que quer pagar:\n"
                        "   {\"type\": \"generate_payment\", \"lead_name\": \"nome\", "
                        "\"description\": \"descrição do serviço\", \"amount_cents\": 35000, "
                        "\"payment_method\": \"pix\"}\n"
                        "   payment_method: \"pix\" | \"boleto\" | \"credit_card\"\n"
                        "   amount_cents: valor em centavos (350 reais = 35000)\n"
                        "   Se boleto: inclua \"lead_cpf\": \"12345678900\"\n"
                        "   O sistema gera QR code / boleto / link de checkout e manda pro lead.\n\n"
                        "3. MÍDIA — use quando quiser enviar foto ou vídeo:\n"
                        "   {\"type\": \"send_media\", \"tags\": [\"antes e depois\", \"clareamento\"]}\n"
                        "   O sistema busca fotos/vídeos cadastrados com essas tags.\n\n"
                        "Se não tem action pra disparar, mande array vazio []."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["create_appointment", "generate_payment", "send_media"],
                                "description": "Tipo da ação.",
                            },
                            "lead_name": {
                                "type": "string",
                                "description": "Nome completo do lead.",
                            },
                            "lead_email": {
                                "type": "string",
                                "description": "Email do lead (obrigatório pra agendamento).",
                            },
                            "service": {
                                "type": "string",
                                "description": "Nome do serviço (agendamento).",
                            },
                            "date_time": {
                                "type": "string",
                                "description": (
                                    "Data e hora do agendamento. Pode ser texto natural OU formato estruturado.\n"
                                    "Exemplos válidos: 'terça às 10h', 'amanhã 14h', 'segunda 10:00', "
                                    "'depois de amanhã às 15h', '07/04/2026 10:00', 'dia 10 às 14h'.\n"
                                    "O sistema converte automaticamente pra data exata. "
                                    "NÃO calcule o dia da semana — mande o texto como o lead falou."
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": "Descrição do pagamento.",
                            },
                            "amount_cents": {
                                "type": "integer",
                                "description": "Valor em centavos. 350 reais = 35000.",
                            },
                            "payment_method": {
                                "type": "string",
                                "enum": ["pix", "boleto", "credit_card"],
                                "description": "Método de pagamento.",
                            },
                            "lead_cpf": {
                                "type": "string",
                                "description": "CPF do lead (obrigatório pra boleto).",
                            },
                            "installments": {
                                "type": "integer",
                                "description": "Número de parcelas (cartão). Default: 1.",
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tags pra buscar mídia (send_media).",
                            },
                        },
                        "required": ["type"],
                    },
                },
            },
            "required": required_reply + ["intent", "sentiment", "stage_action", "confidence"],
        },
    }


# ================================================================
# GERAÇÃO DE RESPOSTA
# ================================================================

async def generate_response(identity, conv, user_text, image_url=None, use_fast_model=False):
    """
    Gera resposta da IA usando tool_use para garantir JSON válido sempre.
    """
    model = AI_MODEL_FAST if use_fast_model else AI_MODEL_PRIMARY
    system = build_system_prompt(identity, conv)

    from huma.services.learning_engine import get_learned_insights, profile_lead, build_profile_prompt
    try:
        learned = await _get_insights_cached(identity.client_id)
        if learned:
            system += learned
    except Exception:
        pass

    try:
        hour = conv.last_message_at.hour if conv.last_message_at else None
        lead_profile = profile_lead(conv.phone, user_text, conv.lead_facts, hour)
        profile_prompt = build_profile_prompt(lead_profile)
        if profile_prompt:
            system += profile_prompt
    except Exception:
        pass

    # Monta mensagens
    messages = [{"role": m["role"], "content": m["content"]} for m in conv.history]

    if image_url:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": image_url}},
                {"type": "text", "text": user_text.strip() or "Lead enviou imagem."},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    # Tool que força JSON válido
    reply_tool = _build_reply_tool(identity.messaging_style)

    try:
        response = await _get_ai_client().messages.create(
            model=model,
            max_tokens=800,
            system=system,
            tools=[reply_tool],
            tool_choice={"type": "tool", "name": "send_reply"},
            messages=messages,
        )

        # Extrai o tool_use block
        parsed = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "send_reply":
                parsed = block.input
                break

        if not parsed:
            log.warning("Tool use não retornou dados")
            return _fallback_result(identity.fallback_message)

        # Extrai campos
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
            # v9.2 — audio_text gerado na mesma chamada
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
        return result

    except Exception as e:
        log.error(f"Erro na IA | {e}")
        return _fallback_result(identity.fallback_message)


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
# VALIDAÇÃO (anti-alucinação) — modo soft
# ================================================================

async def validate_response(identity, reply, confidence):
    """Verifica se a IA inventou informação. Modo soft: avisa mas não bloqueia."""
    if confidence >= 0.90:
        return {"is_safe": True}

    products = [
        f"{p.get('name', '')}: R${p.get('price', '')}"
        for p in identity.products_or_services
        if p.get("name")
    ]

    prompt = (
        f"Verifique se a resposta inventou informação.\n"
        f"Produtos reais: {chr(10).join(products) if products else 'Nenhum'}\n"
        f"Desconto máximo: {identity.max_discount_percent}%\n"
        f"Resposta: \"{reply}\"\n"
        f"JSON: {{\"is_safe\": true/false, \"reason\": \"\"}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )

        if not parsed.get("is_safe", True):
            log.warning(f"Alucinação detectada | reason={parsed.get('reason', '')}")

        # Sempre retorna is_safe=True (modo soft)
        return {"is_safe": True}

    except Exception:
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
    """Comprime histórico quando fica muito grande."""
    if len(history) <= HISTORY_MAX_BEFORE_COMPRESS:
        return history, summary, facts

    to_compress = history[:-HISTORY_WINDOW]
    recent = history[-HISTORY_WINDOW:]

    messages_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in to_compress
    )

    prompt = (
        f"Resumo anterior: {summary or 'Nenhum.'}\n"
        f"Fatos: {json.dumps(facts, ensure_ascii=False) if facts else 'Nenhum.'}\n"
        f"Mensagens:\n{messages_text}\n"
        f"JSON: {{\"summary\": \"resumo em 5 linhas\", \"facts\": [\"todos os fatos\"]}}"
    )

    try:
        response = await _get_ai_client().messages.create(
            model=AI_MODEL_FAST,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = json.loads(
            response.content[0].text.strip().replace("```json", "").replace("```", "")
        )
        return recent, parsed.get("summary", summary), parsed.get("facts", facts)
    except Exception:
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
