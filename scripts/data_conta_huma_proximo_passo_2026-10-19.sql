-- ================================================================
-- Cadastro da conta da própria HUMA (cli_c64a03005f16) — 2026-10-19
--
-- Ajuste de DADOS, não de coluna. Rode DEPOIS de
-- scripts/migration_self_serve_link.sql (as três últimas colunas).
--
-- O que muda: descrição enxuta (a antiga era um manifesto de 2 mil
-- caracteres que fazia a IA despejar tudo na primeira resposta),
-- produto e FAQ cadastrados (estavam vazios) e o "Próximo passo por
-- link" apontando pro cadastro da HUMA. O cache do cliente expira em
-- 5 minutos; pra valer na hora, salve qualquer campo em Ajustes.
-- ================================================================

update clients set
  business_description = $d$A HUMA é uma vendedora de inteligência artificial que atende o WhatsApp, o Instagram e o site de um negócio como se fosse alguém da equipe: responde na hora, 24 horas por dia, tira dúvidas, qualifica quem chega, marca horário no Google Agenda e cobra por Pix, boleto e cartão dentro da própria conversa. No cadastro ela aprende o jeito do negócio (tom, produtos, regras) e vai melhorando com as conversas. Feita pra negócios pequenos e médios do Brasil: clínicas, lojas, e-commerce, prestadores de serviço. Preço fixo, sem cobrar por atendente. Quem quer começar cria a conta sozinho, em poucos minutos, e testa 7 dias grátis sem cartão.$d$,
  products_or_services = $j$[{"name": "HUMA", "price": "397,00/mês", "description": "150 conversas engajadas por mês inclusas, tudo incluso: WhatsApp oficial, Instagram e chat do site, Google Agenda, Pix/boleto/cartão na conversa, follow-up automático, voz clonada, equipe com carteira de leads e Cockpit com conversas, agenda, relatórios e placar. Passou de 150 conversas: R$ 1,99 por conversa, só se o dono liberar. Teste grátis de 7 dias, sem cartão e sem contrato; cancela com um clique."}]$j$::jsonb,
  faq = $f$[
    {"question": "Como começo o teste grátis?", "answer": "Criando sua conta em https://app.humaia.com.br/login. Leva poucos minutos: você entra com o Google ou com e-mail, a HUMA lê seu site ou Instagram, faz uma entrevista curta sobre o seu negócio e já começa a atender. Sem cartão."},
    {"question": "Precisa de cartão pra testar?", "answer": "Não. O teste de 7 dias é sem cartão e sem contrato. Só assina quem quiser continuar depois."},
    {"question": "Funciona no WhatsApp que eu já uso?", "answer": "Sim. Você conecta o número que já usa lendo um QR code, ou conecta o WhatsApp oficial da Meta pela própria HUMA. O Instagram e o chat do site entram na mesma conta."},
    {"question": "Quanto custa?", "answer": "R$ 397 por mês com 150 conversas engajadas inclusas e tudo liberado. Passou disso, R$ 1,99 por conversa, e só se você liberar. Não cobra por atendente."},
    {"question": "E se a IA errar ou o cliente quiser falar com uma pessoa?", "answer": "A HUMA resume a conversa e avisa você ou alguém da equipe no WhatsApp e no Cockpit. A pessoa assume quando quiser e devolve pra HUMA depois. Ela nunca inventa preço nem confirma horário sem conferir na agenda."},
    {"question": "Serve pra e-commerce?", "answer": "Sim. Integra com Nuvemshop, Bling, Omie e Tiny: consulta estoque ao vivo, mostra produto com foto, fecha o pedido e recebe Pix ou cartão dentro da conversa, com o dinheiro caindo na conta do dono."},
    {"question": "Serve pra clínica ou quem trabalha com agenda?", "answer": "Sim. Conecta no Google Agenda, confere horário livre antes de marcar, nunca marca em cima de outro compromisso, lembra o cliente e remarca ou cancela se precisar."},
    {"question": "Tem contrato ou fidelidade?", "answer": "Não. Mensal, cancela com um clique e as conversas já pagas continuam valendo."}
  ]$f$::jsonb,
  self_serve_url   = 'https://app.humaia.com.br/login',
  self_serve_label = 'Criar conta e testar 7 dias grátis',
  self_serve_when  = 'quiser testar, começar, criar a conta ou saber como começa',
  updated_at = now()
where client_id = 'cli_c64a03005f16';
