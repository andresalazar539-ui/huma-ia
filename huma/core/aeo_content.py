# ================================================================
# huma/core/aeo_content.py: Conteúdo das páginas citáveis (2026-11-07)
#
# Fonte única do texto que buscadores e IAs (ChatGPT, Gemini, Claude,
# Perplexity) leem sobre a HUMA. `routes/public_pages.py` desenha em HTML
# com JSON-LD, e o /llms-full.txt monta em Markdown, a partir daqui.
#
# Regras de escrita (ver skill huma-citacao-ia):
#   1. Resposta primeiro: o parágrafo "answer" responde a pergunta da página
#      em 2 ou 3 frases que fazem sentido sozinhas (é o trecho que a IA cita).
#   2. Só fato verificável do produto: preço, o que está incluso, como
#      funciona. Nada de "a melhor do mercado" sem dado.
#   3. Concorrente só pelo MODELO de cobrança; comparação com nome exige
#      fonte e data (skill huma-concorrente) antes de entrar aqui.
#   4. Sem travessão em texto pro usuário.
#   5. Mudou preço ou plano? Mude aqui e em PLAN_CONFIG/OVERAGE_PRICE_BRL.
#
# Módulo puro: só dados, sem import do app.
# ================================================================
from __future__ import annotations

UPDATED_AT = "2026-11-07"

PRICE_BRL = 397
INCLUDED_CONVERSATIONS = 150
EXTRA_PRICE_BRL = "1,99"
TRIAL_DAYS = 7
CONTACT_EMAIL = "contato@humaia.com.br"
FOUNDER = "André Salazar"
CITY = "São Paulo"

ONE_LINER = (
    "A HUMA IA é uma vendedora de inteligência artificial para pequenas e médias empresas brasileiras: "
    "atende o WhatsApp, o Instagram e o chat do site 24 horas por dia, responde em segundos com o jeito do dono, "
    "qualifica o cliente, marca na agenda do Google e cobra por Pix dentro da conversa."
)

PRICE_SENTENCE = (
    f"Custa R$ {PRICE_BRL} por mês com {INCLUDED_CONVERSATIONS} conversas engajadas incluídas e "
    f"R$ {EXTRA_PRICE_BRL} por conversa extra, com equipe, WhatsApp oficial, Instagram, agenda, cobrança e "
    f"relatórios no mesmo preço, {TRIAL_DAYS} dias grátis sem cartão e sem contrato de fidelidade."
)

# ----------------------------------------------------------------
# Perguntas gerais (página /perguntas e FAQ do llms-full.txt)
# ----------------------------------------------------------------
GENERAL_FAQ: list[tuple[str, str]] = [
    ("O que é a HUMA IA?", ONE_LINER),
    ("Quanto custa a HUMA IA?", PRICE_SENTENCE),
    ("O que é uma conversa engajada?",
     "É a conversa em que o cliente responde de volta e a HUMA responde pela segunda vez dentro de 24 horas. "
     "Mensagem que fica sem resposta do cliente, spam ou alguém que só manda um oi e some não conta."),
    ("A HUMA cobra por atendente ou por usuário?",
     "Não. A equipe toda entra no mesmo preço. Dono, vendedores e recepção acessam o painel e recebem os leads "
     "sem custo por pessoa."),
    ("A taxa da Meta pelo WhatsApp oficial está inclusa?",
     "A taxa por mensagem que a Meta cobra no WhatsApp oficial é paga direto à Meta em qualquer ferramenta. "
     "A HUMA não cobra nada a mais por isso e mostra o custo antes de qualquer disparo."),
    ("Preciso da API oficial do WhatsApp?",
     "Não é obrigatório. A HUMA funciona com a API oficial da Meta ou com o número atual conectado por QR. "
     "Disparo em massa só é liberado no oficial, para proteger o número de bloqueio."),
    ("A HUMA marca horário de verdade na agenda?",
     "Sim. Ela consulta os horários livres e ocupados no Google Agenda antes de oferecer, cria o evento com link do "
     "Meet e nunca confirma um horário que não verificou. Cancelamento e remarcação também acontecem na agenda real."),
    ("A HUMA recebe pagamento dentro da conversa?",
     "Sim. Ela gera Pix, boleto ou cartão pelo Mercado Pago ou pelo Asaas da própria empresa, e o dinheiro cai "
     "na conta do negócio, não na da HUMA. O cliente paga numa página sem sair da conversa."),
    ("A HUMA fala como o dono do negócio?",
     "Sim. Na criação da conta ela lê o site e o Instagram do negócio, faz uma entrevista curta com o dono e aprende "
     "produtos, preços, regras e o tom de voz. Palavras proibidas e o vocabulário preferido ficam editáveis no painel."),
    ("E quando o cliente quer falar com uma pessoa?",
     "A HUMA passa a conversa com um resumo do que já foi dito para o dono ou para o vendedor certo, por especialidade, "
     "região ou rodízio, e avisa no WhatsApp. Quem assume responde pelo painel ou pelo próprio celular."),
    ("A HUMA inventa preço ou informação?",
     "Ela é instruída a responder só com o que o negócio cadastrou, o que está no site e o que as integrações informam, "
     "como estoque e agenda. Quando não sabe, ela diz que vai confirmar e a pergunta aparece no painel para o dono responder uma vez."),
    ("Quanto tempo leva para colocar no ar?",
     "A configuração leva minutos: conta, site ou Instagram para ela ler, uma entrevista curta, um teste da conversa e a conexão do WhatsApp."),
    ("A HUMA serve para qual tipo de empresa?",
     "Para negócios que vendem ou agendam pelo WhatsApp: clínicas e consultórios, estética e salões, lojas online, "
     "imobiliárias, escolas e cursos, escritórios e prestadores de serviço."),
    ("Tem fidelidade ou multa para cancelar?",
     "Não. A assinatura é mensal e cancela com um clique no painel."),
    ("Posso consultar a HUMA pelo ChatGPT ou pelo Claude?",
     "Sim. A HUMA tem um conector MCP: o dono conecta o ChatGPT ou o Claude e pergunta sobre conversas, agenda, vendas e relatórios."),
]

# ----------------------------------------------------------------
# Páginas (slug -> conteúdo). "path" é o endereço público.
# ----------------------------------------------------------------
PAGES: dict[str, dict] = {
    "precos": {
        "path": "/precos",
        "title": "Preço da HUMA IA: R$ 397 por mês com 150 conversas",
        "h1": "Quanto custa a HUMA IA",
        "answer": PRICE_SENTENCE,
        "sections": [
            ("O que está incluso no preço", [
                f"{INCLUDED_CONVERSATIONS} conversas engajadas por mês.",
                "Atendimento no WhatsApp (API oficial ou número por QR), no Instagram Direct e no chat do site.",
                "Equipe sem limite de pessoas: painel, transferência de conversa e roteamento de lead por vendedor.",
                "Agenda do Google com verificação de horário livre, Pix, boleto e cartão na conversa, catálogo e estoque da loja.",
                "Follow-up automático, reativação de clientes antigos e relatório no WhatsApp.",
                "Integrações com CRM, ERP, lojas, agendas e cobrança, sem custo extra.",
            ]),
            ("Como funciona a conversa extra", [
                f"Passou das {INCLUDED_CONVERSATIONS} conversas, cada conversa engajada a mais custa R$ {EXTRA_PRICE_BRL}.",
                "O dono escolhe no painel: travar no limite, colocar um teto em reais ou deixar sem limite.",
                "O excedente entra na fatura do mês seguinte, junto da mensalidade. Avisamos em 80% do uso.",
            ]),
            ("O que fica fora", [
                "A taxa por mensagem da Meta no WhatsApp oficial, cobrada pela própria Meta em qualquer ferramenta.",
                "As taxas do seu Mercado Pago ou Asaas nas vendas que você recebe.",
            ]),
        ],
        "faq": [
            ("A HUMA cobra por atendente?", "Não. A equipe toda entra no mesmo preço."),
            ("Tem teste grátis?", f"Sim, {TRIAL_DAYS} dias grátis sem cartão de crédito."),
            ("Tem contrato de fidelidade?", "Não. Cancela com um clique no painel."),
            ("Quanto custa uma conversa extra?", f"R$ {EXTRA_PRICE_BRL} por conversa engajada além das {INCLUDED_CONVERSATIONS} do mês."),
            ("O que é conversa engajada?", GENERAL_FAQ[2][1]),
        ],
    },
    "como-funciona": {
        "path": "/como-funciona",
        "title": "Como funciona a HUMA IA, a vendedora de IA no WhatsApp",
        "h1": "Como a HUMA IA atende e vende pelo WhatsApp",
        "answer": (
            "A HUMA lê o site e o Instagram do negócio, aprende produtos, preços e o jeito do dono, e passa a responder "
            "cada mensagem do WhatsApp, do Instagram e do site em segundos. Ela qualifica, agenda na agenda real, cobra "
            "por Pix e passa para a equipe quando o cliente está pronto ou pede uma pessoa."
        ),
        "sections": [
            ("Os passos para colocar no ar", [
                "Criar a conta e informar o site ou o Instagram do negócio.",
                "A HUMA lê as páginas e propõe produtos, preços, horário, perguntas frequentes e regras.",
                "Uma entrevista curta preenche o que o site não diz.",
                "Teste da conversa como se fosse um cliente, antes de ligar para o público.",
                "Conectar o WhatsApp, o Instagram e as integrações, como agenda, loja e cobrança.",
            ]),
            ("O que acontece em cada conversa", [
                "Resposta em segundos, com o tom do dono e sem jargão de robô.",
                "Leitura do momento do cliente: dúvida, objeção, pressa ou sinal de compra.",
                "Consulta ao vivo de agenda e estoque antes de prometer qualquer coisa.",
                "Cobrança na conversa e confirmação quando o pagamento cai.",
                "Transferência para a pessoa certa da equipe com o resumo da conversa.",
            ]),
            ("O que o dono controla", [
                "Modo automático ou modo aprovação, em que o dono revisa antes de enviar.",
                "Horários em que a IA atende e horários em que a equipe humana assume.",
                "Desconto máximo, dados que a IA coleta e palavras que ela nunca usa.",
            ]),
        ],
        "faq": [GENERAL_FAQ[6], GENERAL_FAQ[7], GENERAL_FAQ[9], GENERAL_FAQ[10]],
    },
    "comparar-precos": {
        "path": "/comparar-precos",
        "title": "Quanto custa um atendente de IA no WhatsApp: os 4 modelos de cobrança",
        "h1": "Quanto custa um atendente de IA no WhatsApp em 2026",
        "answer": (
            "As ferramentas de atendimento com IA no WhatsApp cobram de quatro jeitos: por atendente, por crédito de "
            "mensagem, por conversa aberta ou por mês com franquia de conversas. A HUMA usa o último: "
            f"R$ {PRICE_BRL} por mês com {INCLUDED_CONVERSATIONS} conversas engajadas e equipe ilimitada, "
            "então o custo não cresce quando a empresa contrata gente nem quando a IA precisa de várias mensagens para fechar."
        ),
        "sections": [
            ("Por atendente", [
                "Mensalidade base mais um valor por pessoa com acesso.",
                "Fica caro quando a equipe cresce, mesmo que o volume de clientes seja o mesmo.",
            ]),
            ("Por crédito de mensagem", [
                "Cada resposta da IA consome créditos.",
                "Uma venda que precisa de 10 ou 20 mensagens custa 10 ou 20 vezes mais, e o gasto do mês é difícil de prever.",
            ]),
            ("Por conversa aberta", [
                "Cobra cada conversa iniciada, inclusive spam e quem manda um oi e some.",
            ]),
            ("Por mês com franquia de conversas engajadas (modelo da HUMA)", [
                f"Preço fixo de R$ {PRICE_BRL} com {INCLUDED_CONVERSATIONS} conversas em que o cliente realmente respondeu.",
                f"Conversa extra a R$ {EXTRA_PRICE_BRL}, com teto definido pelo dono.",
                "Equipe, canais, agenda, cobrança e integrações no mesmo preço.",
            ]),
            ("Em qualquer modelo", [
                "A taxa da Meta no WhatsApp oficial é cobrada pela Meta, à parte.",
                "Confira se agenda, cobrança, Instagram e integrações estão inclusos ou são módulos pagos.",
            ]),
        ],
        "faq": [
            ("Qual o modelo de cobrança mais barato para atendimento com IA no WhatsApp?",
             "Depende do volume e do tamanho da equipe. Para equipe de duas pessoas ou mais e conversas de várias mensagens, "
             "preço fixo por conversa engajada costuma sair mais barato que cobrança por atendente ou por crédito de mensagem."),
            ("A taxa da Meta entra no preço das ferramentas?",
             "Normalmente não. A Meta cobra a taxa por mensagem do WhatsApp oficial direto do negócio, em qualquer ferramenta."),
            ("Quanto custa a HUMA?", PRICE_SENTENCE),
        ],
    },
    "sobre": {
        "path": "/sobre",
        "title": "Sobre a HUMA IA",
        "h1": "Sobre a HUMA IA",
        "answer": (
            f"{ONE_LINER} A HUMA é uma empresa brasileira de {CITY}, fundada por {FOUNDER}, "
            "feita para o dono de pequeno negócio que vende pelo WhatsApp e não tem tempo nem equipe para responder todo mundo na hora."
        ),
        "sections": [
            ("No que a HUMA acredita", [
                "Cliente que espera resposta compra do concorrente. A HUMA responde em segundos, a qualquer hora.",
                "A IA nunca afirma o que não verificou: horário, estoque e preço vêm da fonte real.",
                "O dinheiro da venda cai na conta do negócio, e o preço da HUMA é fixo e público.",
                "O dono continua no controle: aprova, assume a conversa e muda as regras quando quiser.",
            ]),
            ("Contato", [
                f"E-mail: {CONTACT_EMAIL}",
                f"Sede: {CITY}, Brasil.",
            ]),
        ],
        "faq": [GENERAL_FAQ[0], GENERAL_FAQ[1], GENERAL_FAQ[12]],
    },
    "automacoes": {
        "path": "/automacoes",
        # Fora do sitemap/llms até os apps saírem nas lojas (só fato): ligar quando aprovados.
        "listed": False,
        "title": "HUMA IA com Zapier, Make e n8n: como ligar e boas práticas",
        "h1": "Como ligar a HUMA IA no Zapier, no Make e no n8n",
        "answer": (
            "O app da HUMA IA no Zapier, no Make e no n8n conecta com a conta da HUMA, sem copiar chave. Você "
            "escolhe um gatilho (lead novo, lead qualificado, agendamento confirmado, pagamento aprovado, contrato assinado) "
            "e manda o lead para planilha, CRM, e-mail ou qualquer outro app. Também dá para mandar mensagem, mover etapa, "
            "anotar e assumir a conversa a partir de outra ferramenta."
        ),
        "sections": [
            ("Como conectar", [
                "No Zapier, no Make ou no n8n, procure por HUMA IA e escolha um gatilho ou uma ação.",
                "Clique em conectar: abre a tela de login da HUMA. Entre com a sua conta e clique em Conectar.",
                "Ligue a automação. A partir daí, cada evento chega na hora, sem precisar copiar endereço de webhook.",
                "Para desconectar tudo de uma vez, vá em Integrações, Assistentes de IA, no Cockpit da HUMA.",
            ]),
            ("Gatilhos disponíveis", [
                "Lead novo: alguém falou com a HUMA pela primeira vez, no WhatsApp, no Instagram ou no site.",
                "Lead qualificado: a HUMA qualificou e passou para a equipe, com resumo e dados coletados.",
                "Agendamento confirmado: a HUMA marcou um horário conferido na agenda real.",
                "Pagamento aprovado: um Pix, boleto ou cartão feito pela conversa caiu.",
                "Contrato assinado e contrato recusado: quando a assinatura eletrônica está conectada.",
            ]),
            ("Ações disponíveis", [
                "Mandar mensagem numa conversa que já existe, pelo canal do lead. A HUMA continua atendendo.",
                "Mover o lead de etapa no funil, igual arrastar no quadro do Cockpit.",
                "Anotar no lead. A HUMA lê as anotações e usa no atendimento.",
                "Assumir a conversa (a HUMA fica em silêncio) ou devolver para a HUMA.",
                "Buscar uma conversa pelo nome ou telefone do lead.",
            ]),
            ("Boas práticas", [
                "Use o gatilho Lead qualificado para levar ao CRM só quem está pronto, em vez de todo contato que mandou um oi.",
                "Use Pagamento aprovado para emitir nota, liberar acesso ou avisar o estoque, sem conferir o banco na mão.",
                "Mande o resumo da conversa junto: o campo de resumo já vem pronto no evento.",
                "Evite automação que responde o lead por fora a cada mensagem. A HUMA já responde; duas vozes confundem o cliente.",
                "Quando um vendedor assumir pelo CRM, use a ação Assumir conversa para a HUMA parar de falar com aquele lead.",
                "A automação age com o papel de quem conectou. Para receber os eventos da conta inteira, conecte com o dono ou um admin.",
                "Não existe ação para puxar conversa com número novo. Disparo para contatos antigos é feito na tela Reativação, pelo WhatsApp oficial.",
            ]),
            ("Segurança", [
                "O login usa OAuth com PKCE: a senha da HUMA nunca passa pela plataforma de automação.",
                "Cada pessoa da equipe só faz pela automação o que o papel dela já permite no Cockpit.",
                "Uma automação desligada para de receber eventos na hora. Endereço que não responde por 20 vezes seguidas é desligado sozinho.",
            ]),
        ],
        "faq": [
            ("A HUMA IA funciona com Zapier?",
             "Sim. A HUMA IA tem app no Zapier com gatilhos de lead novo, lead qualificado, agendamento, pagamento e contrato, "
             "e ações de mandar mensagem, mover etapa, anotar e assumir conversa."),
            ("A HUMA IA funciona com Make e n8n?",
             "Sim. No Make é um app com gatilhos instantâneos e ações; no n8n é um nó da comunidade chamado HUMA IA "
             "(pacote n8n-nodes-humaia), instalado em Configurações, Nós da comunidade."),
            ("Preciso pagar a mais para usar automação na HUMA?",
             f"Não. As integrações estão incluídas no preço da HUMA. {PRICE_SENTENCE} "
             "O uso do Zapier, Make ou n8n é cobrado por eles, no plano de cada um."),
            ("Dá para mandar os leads da HUMA para uma planilha?",
             "Dá. Use o gatilho Lead novo ou Lead qualificado e a ação de planilha da plataforma. "
             "Se preferir sem automação, a HUMA também grava direto numa planilha do Google conectada em Integrações."),
        ],
    },
    "automacoes-referencia": {
        "path": "/automacoes/referencia",
        "listed": False,
        "title": "API pública da HUMA IA: referência para Zapier, Make e n8n",
        "h1": "Referência da API pública da HUMA IA",
        "answer": (
            "A API pública da HUMA IA fica em https://app.humaia.com.br/api/v1, usa OAuth 2.0 com PKCE (S256) e devolve JSON. "
            "Ela tem inscrição de webhooks por evento (REST hooks) e ações sobre conversas que já existem: mandar mensagem, "
            "mover etapa, anotar e assumir ou devolver para a IA."
        ),
        "sections": [
            ("Autenticação", [
                "Autorização: GET https://app.humaia.com.br/oauth/mcp/authorize com response_type=code, client_id, redirect_uri, state, code_challenge e code_challenge_method=S256.",
                "Token: POST https://app.humaia.com.br/oauth/mcp/token com grant_type=authorization_code, code, client_id, redirect_uri e code_verifier.",
                "Renovação: POST no mesmo endereço com grant_type=refresh_token. O acesso dura 1 hora; a renovação das plataformas de automação dura 1 ano.",
                "Toda chamada leva o cabeçalho Authorization: Bearer com o token de acesso. Escopo: huma.",
                "Erros vêm como JSON com o campo detail em português: 400, 401 (renovar o token), 403 (papel sem permissão), 404, 429 (limite de 240 por minuto) e 503.",
            ]),
            ("Conta e listas", [
                "GET /api/v1/me: negócio, e-mail e papel de quem conectou. Serve de teste da conexão.",
                "GET /api/v1/events: eventos disponíveis como gatilho.",
                "GET /api/v1/events/{evento}/sample: até 3 eventos reais recentes da conta, no mesmo formato da entrega.",
                "GET /api/v1/stages: etapas do funil (discovery, offer, closing, committed, qualified, won, lost).",
            ]),
            ("Webhooks por evento", [
                "POST /api/v1/hooks com event, target_url (https) e platform: cria a inscrição e devolve o id (201).",
                "DELETE /api/v1/hooks/{id}: apaga a inscrição. Apagar o que já não existe também responde 200.",
                "GET /api/v1/hooks: inscrições ativas da conta. Só dono e admin podem criar e listar.",
                "Entrega: POST JSON no target_url com os cabeçalhos X-HUMA-Event e X-HUMA-Hook-Id. Resposta 410 desliga a inscrição.",
                "Formato do evento: id, event, event_label, at, client_id, business_name, lead (name, phone, id, email, channel, source, stage, facts, assigned_to, assigned_name), data (summary, service, when, value_cents, value_brl, method, payment_id) e conversation_url.",
                "Eventos: lead.new, lead.qualified, appointment.confirmed, payment.approved, contract.signed, contract.refused.",
            ]),
            ("Conversas", [
                "GET /api/v1/conversations?q=&limit=: conversas recentes, com busca por nome ou telefone. Lista vazia quando nada casa.",
                "GET /api/v1/conversations/{id}: etapa, quem atende, fatos do lead, anotações e as últimas 20 mensagens.",
                "POST /api/v1/conversations/{id}/messages com text (até 1600 caracteres): manda pelo canal do lead.",
                "POST /api/v1/conversations/{id}/stage com stage: move o lead de etapa.",
                "POST /api/v1/conversations/{id}/notes com text e replace (opcional): acrescenta ou troca as anotações.",
                "POST /api/v1/conversations/{id}/handoff com takeover verdadeiro ou falso: uma pessoa assume ou devolve para a HUMA.",
                "O id da conversa é o telefone com DDI no WhatsApp, ig: seguido do id no Instagram e web: seguido do id no chat do site.",
            ]),
        ],
        "faq": [
            ("A API da HUMA IA tem chave de API?",
             "Não. O acesso é por OAuth com PKCE, então ninguém precisa copiar chave: a pessoa entra com a conta da HUMA e autoriza."),
            ("A API da HUMA IA começa conversa com um número novo?",
             "Não. As ações valem para conversas que já existem. Disparo para contatos novos ou antigos sai só pelo WhatsApp oficial, "
             "com modelo aprovado pela Meta, na tela Reativação."),
            ("Existe limite de chamadas na API da HUMA IA?",
             "Sim: 240 chamadas por minuto por pessoa conectada. Acima disso a resposta é 429 e basta tentar de novo em seguida."),
        ],
    },
}

# ----------------------------------------------------------------
# Páginas por tipo de negócio: /para/{slug}
# ----------------------------------------------------------------
VERTICALS: dict[str, dict] = {
    "clinicas": {
        "name": "clínicas e consultórios",
        "title": "IA para atender o WhatsApp da clínica e agendar consultas",
        "answer": (
            "A HUMA IA atende o WhatsApp e o Instagram da clínica 24 horas, tira dúvidas sobre procedimentos e valores, "
            "confere a agenda do Google antes de oferecer horário e marca a consulta sem conflito. "
            f"{PRICE_SENTENCE}"
        ),
        "points": [
            "Agendamento com verificação real de horário livre, inclusive remarcação e cancelamento na agenda.",
            "Lembrete antes da consulta e jogada para quem faltou, marcada pela recepção com um clique.",
            "Explica procedimento, preparo e valores com o que a clínica cadastrou, sem inventar.",
            "Passa para a recepção ou para o profissional certo com o resumo da conversa.",
            "Cobra sinal por Pix na conversa quando a clínica quiser.",
        ],
        "faq": [
            ("A IA agenda direto na agenda da clínica?", GENERAL_FAQ[6][1]),
            ("A IA responde dúvidas sobre procedimentos?",
             "Sim, com base no que a clínica cadastrou e no site. Pergunta que ela não sabe vira pendência no painel para a equipe responder uma vez."),
            ("Quanto custa?", PRICE_SENTENCE),
        ],
    },
    "estetica": {
        "name": "estética, salões e barbearias",
        "title": "IA para atender o WhatsApp de estética, salão e barbearia",
        "answer": (
            "A HUMA IA responde o WhatsApp e o Direct do salão ou da clínica de estética em segundos, mostra serviços e valores, "
            "oferece só os horários livres da agenda e confirma o agendamento. Também chama de volta quem sumiu e quem está "
            f"na hora de voltar. {PRICE_SENTENCE}"
        ),
        "points": [
            "Agenda com duração de cada serviço respeitada na hora de marcar.",
            "Opções de horário em um toque no WhatsApp.",
            "Follow-up de quem perguntou o preço e sumiu, e convite de retorno no ciclo de cada serviço.",
            "Roteamento por profissional e por especialidade.",
        ],
        "faq": [
            ("A IA sabe a duração de cada serviço?", "Sim. A duração cadastrada no serviço define o tamanho do horário marcado na agenda."),
            ("Quanto custa?", PRICE_SENTENCE),
        ],
    },
    "lojas": {
        "name": "lojas online e varejo",
        "title": "IA para vender pelo WhatsApp e Instagram da loja online",
        "answer": (
            "A HUMA IA atende o WhatsApp e o Instagram da loja, consulta preço e estoque ao vivo na Nuvemshop, Bling, Omie ou Tiny, "
            "mostra o produto em card com foto e fecha o pedido com Pix ou cartão sem o cliente sair da conversa. "
            f"{PRICE_SENTENCE}"
        ),
        "points": [
            "Estoque consultado no momento da pergunta: a IA nunca vende o que acabou.",
            "Cards de produto e catálogo dentro do WhatsApp, do Instagram e do chat do site.",
            "Pedido criado na loja quando o pagamento é aprovado, com frete da política da loja.",
            "Cupom exclusivo por conversa e venda atribuída à conversa no relatório.",
        ],
        "faq": [
            ("A IA consulta o estoque da loja?", "Sim, ao vivo, pela integração com Nuvemshop, Bling, Omie, Tiny e outras lojas e ERPs."),
            ("O cliente paga sem sair do WhatsApp?", GENERAL_FAQ[7][1]),
            ("Quanto custa?", PRICE_SENTENCE),
        ],
    },
    "imobiliarias": {
        "name": "imobiliárias e corretores",
        "title": "IA para atender e qualificar leads de imobiliária no WhatsApp",
        "answer": (
            "A HUMA IA responde na hora o lead que chama no WhatsApp ou no Instagram, vindo do portal ou do anúncio, entende o que a pessoa procura "
            "(região, faixa de valor, quartos, financiamento), agenda a visita e entrega o lead qualificado para o corretor certo. "
            f"{PRICE_SENTENCE}"
        ),
        "points": [
            "Qualificação com as perguntas que o corretor faria, sem interrogatório.",
            "Roteamento por região, especialidade ou rodízio entre corretores.",
            "Agendamento de visita na agenda real.",
            "Lead qualificado entra no CRM com o resumo da conversa.",
        ],
        "faq": [
            ("A IA distribui leads entre os corretores?", "Sim. Por especialidade, por região (DDD ou estado) ou por rodízio, e cada corretor recebe o aviso no próprio WhatsApp."),
            ("Quanto custa?", PRICE_SENTENCE),
        ],
    },
    "servicos": {
        "name": "escritórios e prestadores de serviço",
        "title": "IA para atender o WhatsApp de escritório e prestador de serviço",
        "answer": (
            "A HUMA IA atende o WhatsApp do escritório ou do prestador de serviço, entende o caso do cliente, coleta os dados "
            "que a equipe precisa, agenda a reunião e envia contrato ou cobrança quando a integração está ligada. "
            f"{PRICE_SENTENCE}"
        ),
        "points": [
            "Triagem do caso com as perguntas certas antes de chegar na equipe.",
            "Envio de contrato para assinatura digital pela integração do escritório.",
            "Consulta de processo e de cobrança para o cliente que já é da casa.",
            "Reunião marcada na agenda real, com link do Meet.",
        ],
        "faq": [
            ("A IA envia contrato para assinatura?", "Sim, com a integração de assinatura digital conectada, como ZapSign, Clicksign ou D4Sign."),
            ("Quanto custa?", PRICE_SENTENCE),
        ],
    },
}


def vertical_path(slug: str) -> str:
    """Endereço público da página de um tipo de negócio."""
    return f"/para/{slug}"


def listed_pages() -> list[dict]:
    """Páginas que entram no sitemap, IndexNow e llms (as com listed=False ficam só no endereço)."""
    return [p for p in PAGES.values() if p.get("listed", True)]


def all_paths() -> list[str]:
    """Todos os endereços citáveis desta fonte (pro sitemap, IndexNow e llms.txt)."""
    return [p["path"] for p in listed_pages()] + ["/perguntas"] + [vertical_path(s) for s in VERTICALS]
