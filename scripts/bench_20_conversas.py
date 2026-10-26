"""
Base de 20 conversas complexas (2026-10-26) — custo e qualidade MEDIDOS.

Roda 20 conversas de venda roteirizadas (10 mensagens do lead cada, com
objeção de preço, comparação com concorrente, "vou pensar", dúvida de
integração, pedido de desconto, pressa, desconfiança) contra a HUMA DE
PRODUÇÃO pelo chat do site, uma sessão nova por conversa. As respostas são
reais (prompt real, modelo real, cache real) e o custo de cada chamada cai
na tabela ai_usage com o phone "web:<sessão>", então o custo por conversa
é lido do banco depois, não estimado.

Uso:
  python scripts/bench_20_conversas.py --label sonnet            # com REPLY_SINGLE_MODEL=true no Railway
  python scripts/bench_20_conversas.py --label misto             # com REPLY_SINGLE_MODEL=false
  python scripts/bench_20_conversas.py --label x --count 3       # amostra
  python scripts/bench_20_conversas.py --dry-run                 # só valida os roteiros, sem rede

Saída: scripts/bench_out/<label>/NN-<cenario>.md (transcrições) e
scripts/bench_out/<label>/resumo.json (sessões, tempos, erros). O custo por
conversa vem depois com:

  SELECT phone, count(*) AS respostas, round(sum(cost_brl),4) AS custo_brl
  FROM ai_usage WHERE phone = ANY(ARRAY[...sessões do resumo.json...])
  GROUP BY phone ORDER BY custo_brl DESC;

Atenção: gasta crédito real da Anthropic (uns R$ 20 na configuração só
Sonnet, uns R$ 9 na mista) e consome 20 conversas do plano da conta usada.
Não coloque telefone nos roteiros: o canal web avisa o dono quando o lead
deixa número.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

DEFAULT_BASE = "https://app.humaia.com.br"
DEFAULT_CLIENT = "cli_c64a03005f16"   # conta da própria HUMA (o produto vendido é a HUMA)
OUT_DIR = Path(__file__).resolve().parent / "bench_out"

# 20 cenários de venda da HUMA, 10 mensagens cada. Lead reativo o suficiente
# pra uma conversa real (responde ao que uma vendedora normalmente diria),
# sem depender da resposta exata. Nenhum telefone.
SCENARIOS: list[tuple[str, list[str]]] = [
    ("ecommerce_bling_preco", [
        "Boa noite, vi um anúncio de vocês. Tenho uma loja virtual de roupas, uso o Bling. Isso serve pra mim?",
        "Me chamo Renata. Hoje eu mesma respondo o WhatsApp e o Instagram e perco venda de madrugada.",
        "Como funciona na prática quando um cliente pergunta se tem a camiseta no tamanho M?",
        "E o pagamento? O cliente paga dentro da conversa ou vocês mandam pro site?",
        "Quanto custa?",
        "397 por mês é caro pra mim. Eu vendo uns 15 mil por mês, isso é quase 3% do faturamento.",
        "A Octadesk me ofereceu um plano de 200 reais. Por que eu pagaria quase o dobro em vocês?",
        "Tá, e se der problema? A IA pode falar besteira pro meu cliente, prometer prazo errado?",
        "Vou pensar e converso com meu marido. Me manda um resumo do que a gente falou.",
        "Beleza, vou testar os 7 dias. Como eu começo?",
    ]),
    ("clinica_agenda_cetica", [
        "Oi. Sou da recepção de uma clínica de estética. Minha chefe pediu pra eu entender o que vocês fazem.",
        "A gente usa Google Agenda. A IA marca direto na agenda ou só anota?",
        "E se duas pessoas pedirem o mesmo horário ao mesmo tempo? Já tive problema com isso em outro sistema.",
        "Ela pergunta nome e telefone antes de marcar? A gente precisa do CPF também pra convênio.",
        "Minha chefe tem medo de a IA parecer robô. As clientes daqui são senhoras, gostam de atenção.",
        "Dá pra ela mandar áudio? As clientes mandam muito áudio.",
        "Quanto fica por mês? E tem fidelidade?",
        "Caro. A gente atende umas 80 mensagens por semana só. Compensa?",
        "Se a IA marcar errado, quem resolve? Vocês têm suporte por telefone?",
        "Vou mostrar pra minha chefe. Pode me mandar por escrito as integrações que vocês têm?",
    ]),
    ("imobiliaria_qualificacao", [
        "Olá, sou corretor. Trabalho com apartamentos na zona sul e recebo uns 40 leads por dia dos portais.",
        "Meu problema é que 80% desses leads é curioso. Vocês conseguem filtrar quem é comprador de verdade?",
        "Que perguntas a IA faz pra qualificar? Eu preciso saber renda, se tem entrada e prazo.",
        "E depois que qualifica, ela me passa como? Eu uso o Pipedrive.",
        "Tem como dividir os leads entre mim e meus dois sócios?",
        "Quanto custa pra três pessoas?",
        "Hum. Hoje eu uso uma secretária que ganha 2 mil. A IA substitui ela?",
        "Me preocupa a IA falar valor de imóvel errado. Ela inventa preço?",
        "E imóvel que já vendeu? Ela vai continuar oferecendo?",
        "Fechado, quero testar com meus leads de amanhã. O que eu preciso fazer hoje?",
    ]),
    ("agencia_revenda", [
        "Fala! Tenho uma agência de marketing, 12 clientes pequenos. Queria oferecer atendimento por IA pra eles.",
        "Vocês têm plano de agência ou revenda? Preciso de uma conta por cliente.",
        "E o cliente final vê a marca de vocês ou a minha?",
        "Quanto fica cada conta? Com 12 contas eu precisaria de desconto.",
        "Caramba, 12 vezes 397 é quase 5 mil por mês. Meus clientes pagam 800 de fee pra mim.",
        "Então não fecha a conta pra mim. Tem algum plano menor, só com WhatsApp?",
        "E se eu começar com 2 clientes pra testar?",
        "Como funciona o onboarding? Eu que configuro ou vocês?",
        "Tem API? Preciso puxar os leads pro meu dashboard.",
        "Vou testar com um cliente de clínica. Me passa o link.",
    ]),
    ("restaurante_fora_do_perfil", [
        "Oi, tenho um restaurante. Vocês fazem reserva de mesa e pedido pelo WhatsApp?",
        "A gente recebe uns 200 pedidos por dia no delivery. A IA aguenta?",
        "Precisa integrar com o iFood ou com o cardápio digital?",
        "Entendi. E reserva de mesa, ela marca na agenda?",
        "O cardápio muda toda semana. Como ela aprende?",
        "Quanto custa?",
        "Pro meu volume, 150 conversas não dá pra nada. Como fica o preço com 2 mil conversas?",
        "Isso fica caro demais. Vocês não têm plano por volume?",
        "Acho que não é pra mim então. Tem algum concorrente de vocês que você indica pra restaurante?",
        "Valeu pela sinceridade. Se mudar, eu volto.",
    ]),
    ("advogado_privacidade", [
        "Boa tarde. Sou advogado, escritório pequeno, 3 pessoas. Recebo contato de cliente por WhatsApp o dia todo.",
        "Minha preocupação principal é sigilo. Onde ficam as conversas? Quem tem acesso?",
        "A IA é treinada com as conversas dos meus clientes?",
        "E a LGPD? Vocês têm DPO, contrato de tratamento de dados?",
        "Ela pode dar orientação jurídica? Isso seria infração ética.",
        "Como eu limito o que ela fala?",
        "Quanto custa?",
        "Dá pra ela só agendar consulta e anotar o caso, sem opinar?",
        "E se o cliente mandar documento, foto de processo?",
        "Vou ler os termos. Onde estão?",
    ]),
    ("academia_desconto", [
        "Oi, tenho uma academia e recebo muita pergunta de preço no Instagram às 22h, 23h.",
        "Quanto custa o plano de vocês?",
        "Tem desconto se eu pagar anual?",
        "Então não tem desconto nenhum? Nem pra quem fecha hoje?",
        "A IA consegue oferecer aula experimental e marcar?",
        "Ela sabe lidar com quem pergunta preço e some? Isso é 90% dos meus leads.",
        "Me explica o follow-up. Ela chama o cara de novo?",
        "Mas isso não vai irritar o lead? Odeio spam.",
        "Beleza. E eu consigo ver as conversas no celular?",
        "Vou testar. Mas se eu não gostar, cancelo quando?",
    ]),
    ("dentista_concorrente", [
        "Olá, sou dentista. Já uso o Zenvia há um ano e tô insatisfeito.",
        "O chatbot deles é por fluxo, o paciente trava quando sai do script. Vocês são iguais?",
        "Me dá um exemplo de como a IA responderia um paciente com dor de dente às 2 da manhã.",
        "E se ele pedir pra marcar, ela vê minha agenda real?",
        "Quanto custa? Pago 350 na Zenvia.",
        "Então é mais caro. O que justifica?",
        "Migrar é trabalhoso? Tenho 2 anos de contatos lá.",
        "Dá pra importar minha base e mandar uma mensagem avisando?",
        "Tem risco de banimento do meu número?",
        "Ok, me convenceu a testar. Qual o primeiro passo?",
    ]),
    ("loja_fisica_sem_site", [
        "Oi, tenho uma loja de presentes de bairro. Não tenho site, só Instagram.",
        "A IA aprende o que eu vendo como, se eu não tenho site?",
        "Eu tenho uns 300 produtos. Preciso cadastrar um por um?",
        "E foto? As clientes sempre pedem foto.",
        "Quanto custa?",
        "Eu vendo uns 8 mil por mês. É muito pra mim.",
        "Não tem um plano mais simples, só pra responder as perguntas básicas?",
        "Entendi. E se eu usar só pra horário e endereço, gasto as 150 conversas rápido?",
        "Como funciona a conversa que conta e a que não conta?",
        "Vou pensar. Me manda um resumo.",
    ]),
    ("saas_b2b_demo", [
        "Boa tarde. Sou head de vendas de um SaaS B2B. Queremos qualificar inbound antes do SDR.",
        "Vocês atendem B2B ou é só varejo?",
        "Preciso que a IA pergunte cargo, tamanho da empresa e ferramenta atual. Dá pra configurar?",
        "E ela agenda reunião no calendário do SDR certo? Temos 4 SDRs por região.",
        "Integra com HubSpot?",
        "Quanto custa? E tem SLA?",
        "Preciso de uma demo ao vivo com meu time. Vocês fazem?",
        "Qual o tempo de resposta médio da IA?",
        "E segurança: SSO, auditoria, logs?",
        "Me manda uma proposta formal pra eu levar pro meu diretor.",
    ]),
    ("psicologa_tom", [
        "Oi. Sou psicóloga e atendo online. Recebo mensagem de gente em sofrimento, às vezes de madrugada.",
        "Me preocupa muito uma IA responder alguém em crise. Como vocês tratam isso?",
        "Ela consegue identificar urgência e me acordar?",
        "O tom dela é muito vendedor? Meus pacientes não são leads, são pessoas.",
        "Posso ver e aprovar cada mensagem antes de enviar?",
        "Quanto custa?",
        "Eu atendo 25 pessoas por semana, é um consultório pequeno. Compensa?",
        "E o agendamento: ela respeita meus horários bloqueados?",
        "Como eu ensino o que ela não pode falar?",
        "Vou experimentar no modo de aprovação. Como ativo?",
    ]),
    ("ecommerce_nuvemshop_frete", [
        "Oi, loja na Nuvemshop, suplementos. Pergunta número 1 dos meus clientes é frete e prazo.",
        "A IA calcula frete pelo CEP ou manda pro site?",
        "E estoque? Às vezes acaba e o cliente compra.",
        "Dá pra ela fechar o pedido na Nuvemshop e cair como pago?",
        "Quanto custa?",
        "Tenho uns 600 atendimentos por mês. Passa das 150, quanto fica?",
        "Então uns 1.300 reais no total. Meu lucro líquido é 6 mil. Pesado.",
        "Tem cupom ou condição pra quem vem da Nuvemshop?",
        "E se eu desconectar a loja, perco o que ela aprendeu?",
        "Vou testar com meu catálogo. Quanto tempo leva pra ela aprender?",
    ]),
    ("barbearia_whatsapp_pessoal", [
        "E aí! Tenho barbearia, 3 barbeiros. Cada um usa o próprio WhatsApp. Dá pra ligar os três?",
        "Precisa ser número oficial ou pode ser o número normal?",
        "Tem risco de bloquear meu número? Já aconteceu com um amigo.",
        "A IA marca na agenda de cada barbeiro separado?",
        "Quanto custa pros três?",
        "Caro. Corte é 40 reais. Preciso de 10 cortes só pra pagar a IA.",
        "Ela lembra o cliente do horário? Falta é meu maior problema.",
        "E cliente que falta, ela cobra ou remarca?",
        "Posso testar só no meu número primeiro?",
        "Bora. Como conecto meu WhatsApp?",
    ]),
    ("curso_online_pix", [
        "Oi, vendo curso online de confeitaria. Faço lançamento 2x por ano, recebo mil mensagens em uma semana.",
        "A IA aguenta mil conversas em uma semana? E depois fica parada 5 meses.",
        "Ela fecha a venda com Pix dentro do WhatsApp?",
        "Tem como ela dar o cupom só pra quem hesita?",
        "Quanto custa? Eu só uso em lançamento, não quero pagar o ano todo.",
        "Então eu pago 397 nos dois meses de lançamento e cancelo?",
        "E as conversas extras, quanto saem?",
        "Mil conversas em uma semana vai dar quanto no total?",
        "Entendi. E ela responde objeção tipo 'já tentei outro curso e não deu certo'?",
        "Fechado, vou assinar na semana do lançamento. Me avisa que precisa de quanto tempo pra configurar?",
    ]),
    ("pet_shop_delivery", [
        "Olá! Pet shop com banho e tosa e entrega de ração. Dois negócios num número só.",
        "A IA entende quando é agendamento de banho e quando é pedido de ração?",
        "Banho tem duração diferente por porte. Ela sabe disso?",
        "Ração: ela confere estoque? Uso o Tiny.",
        "Quanto custa?",
        "E se eu quiser só o agendamento, sem a parte de venda?",
        "Meus clientes mandam foto do cachorro pra perguntar o preço da tosa. Ela entende foto?",
        "Posso ver as conversas no computador e responder eu mesma quando quiser?",
        "Quando eu entro na conversa, a IA para?",
        "Vou testar. Tem vídeo de como configurar?",
    ]),
    ("consultoria_alto_ticket", [
        "Boa tarde. Vendo consultoria financeira, ticket de 15 mil. Meu funil é longo, 3 a 4 semanas.",
        "Faz sentido IA num funil desse? Minha venda é relacionamento.",
        "Ela lembra o contexto se o lead some por 3 semanas e volta?",
        "Como ela sabe a hora de me passar o lead?",
        "Quanto custa?",
        "Preço não é problema. O problema é ela queimar um lead de 15 mil com uma resposta genérica.",
        "Me dá um exemplo de como ela responderia 'é muito caro' pra um serviço de 15 mil.",
        "Ela pode falar com o tom de voz que eu escrevo? Tenho um jeito específico.",
        "Consigo ouvir ela em áudio com a minha voz?",
        "Quero testar com 5 leads reais. Me mostra como começo.",
    ]),
    ("marketplace_ml_canal", [
        "Oi, vendo no Mercado Livre e Shopee. Vocês atendem as perguntas de lá?",
        "Então só WhatsApp, Instagram e site? Minhas perguntas estão nos marketplaces.",
        "E o pós-venda? Cliente do ML pergunta do rastreio no WhatsApp.",
        "Ela consulta o rastreio?",
        "Quanto custa?",
        "Pra mim ia ser só pós-venda então. Não vale 397.",
        "Vocês vão ter Mercado Livre em breve?",
        "Me avisa quando tiver? Deixo meu e-mail?",
        "Tá. E o Instagram, ela responde comentário ou só direct?",
        "Ok, obrigado. Vou aguardar o Mercado Livre.",
    ]),
    ("escola_idiomas_equipe", [
        "Olá, escola de idiomas, 2 unidades, 4 atendentes. Queremos padronizar o atendimento.",
        "Cada unidade tem número próprio. Dá pra usar uma conta só?",
        "As atendentes vão continuar? A IA é pra substituir ou ajudar?",
        "Como a IA passa o lead pra atendente certa?",
        "Quanto custa pra duas unidades?",
        "E o relatório? A diretora quer saber quantos leads viraram matrícula.",
        "Ela faz pesquisa de satisfação depois?",
        "Minha diretora é desconfiada com IA. Dá pra fazer um piloto só numa unidade?",
        "Quanto tempo de piloto vocês recomendam?",
        "Vou agendar uma apresentação com ela. Vocês fazem call?",
    ]),
    ("oficina_mecanica_urgencia", [
        "Oi, oficina mecânica. Cliente liga desesperado com carro parado, preciso responder rápido.",
        "A IA responde em quanto tempo?",
        "Ela sabe dizer se eu atendo guincho ou não? Como ensino?",
        "Pode pedir foto do painel e me mandar?",
        "Quanto custa?",
        "Meu ticket médio é 600. Preciso de 1 cliente por mês pra pagar. Ok, isso fecha.",
        "E orçamento? Ela pode passar preço de serviço?",
        "Troca de óleo tem preço fixo, mas motor não. Como ela diferencia?",
        "Se eu estiver debaixo do carro e ela marcar, eu vejo depois onde?",
        "Fechou. Quero começar hoje.",
    ]),
    ("influencer_desconfiada", [
        "oi. me indicaram vcs. vendo cosmético pelo instagram, 50 mil seguidores",
        "mas IA responde igual eu? meu público me conhece, vai perceber que não sou eu",
        "e se perguntarem coisa pessoal? tipo 'vc usa esse produto?'",
        "quanto custa",
        "397?? eu pago 150 numa menina que responde pra mim",
        "ela não responde de madrugada, verdade. mas IA erra",
        "me mostra uma resposta sua pra 'esse sérum serve pra pele oleosa?'",
        "hmm. e se o cliente quiser comprar, vai pra onde?",
        "tá, e eu vejo tudo que ela fala?",
        "vou testar nos 7 dias. se for ruim eu cancelo",
    ]),
]


def _session_id() -> str:
    return secrets.token_hex(16)


def run_conversation(http, base: str, client_id: str, name: str, lines: list[str], delay: float) -> dict:
    sid = _session_id()
    url = f"{base.rstrip('/')}/api/web/{client_id}/message"
    turns: list[dict] = []
    errors = 0
    t0 = time.time()
    for i, text in enumerate(lines, 1):
        started = time.time()
        try:
            r = http.post(url, json={"session_id": sid, "text": text}, timeout=120)
            elapsed = time.time() - started
            if r.status_code == 429:
                turns.append({"lead": text, "huma": [], "ms": int(elapsed * 1000), "error": "429"})
                errors += 1
                time.sleep(20)
                continue
            if not r.ok:
                turns.append({"lead": text, "huma": [], "ms": int(elapsed * 1000), "error": f"HTTP {r.status_code}"})
                errors += 1
                continue
            data = r.json()
            turns.append({"lead": text, "huma": data.get("reply_parts") or [], "ms": int(elapsed * 1000), "cards": len(data.get("cards") or [])})
        except Exception as e:  # rede: registra e segue
            turns.append({"lead": text, "huma": [], "ms": 0, "error": type(e).__name__})
            errors += 1
        print(f"  [{name}] {i}/{len(lines)} {turns[-1].get('ms', 0)} ms {'ERRO ' + turns[-1]['error'] if turns[-1].get('error') else ''}", flush=True)
        time.sleep(delay)
    return {"name": name, "session_id": sid, "phone": f"web:{sid}", "turns": turns, "errors": errors, "seconds": round(time.time() - t0, 1)}


def write_transcript(path: Path, conv: dict, label: str) -> None:
    lines = [f"# {conv['name']} ({label})", "", f"sessão: `{conv['phone']}`  ·  {conv['seconds']}s  ·  erros: {conv['errors']}", ""]
    for t in conv["turns"]:
        lines.append(f"**Lead:** {t['lead']}")
        lines.append("")
        if t.get("error"):
            lines.append(f"_erro: {t['error']}_")
        for part in t["huma"]:
            lines.append(f"**HUMA:** {part}")
        if t.get("cards"):
            lines.append(f"_(+{t['cards']} card(s))_")
        lines.append(f"<sub>{t.get('ms', 0)} ms</sub>")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--client", default=DEFAULT_CLIENT)
    ap.add_argument("--label", default="run")
    ap.add_argument("--count", type=int, default=len(SCENARIOS))
    ap.add_argument("--delay", type=float, default=2.5, help="segundos entre mensagens (rate limit do site: 30/min por IP)")
    ap.add_argument("--dry-run", action="store_true", help="só valida os roteiros")
    args = ap.parse_args()

    scenarios = SCENARIOS[: max(1, min(args.count, len(SCENARIOS)))]
    total_msgs = sum(len(l) for _, l in scenarios)
    print(f"{len(scenarios)} conversas, {total_msgs} mensagens do lead, base={args.base}, conta={args.client}, label={args.label}")
    assert all(len(l) == 10 for _, l in scenarios), "todo cenário tem 10 mensagens"
    assert len({n for n, _ in SCENARIOS}) == len(SCENARIOS), "nomes de cenário repetidos"
    if args.dry_run:
        print("dry-run ok: roteiros válidos, nada enviado.")
        return 0
    if httpx is None:
        print("instale httpx: pip install httpx")
        return 2

    out = OUT_DIR / args.label
    out.mkdir(parents=True, exist_ok=True)
    summary = {"label": args.label, "base": args.base, "client": args.client, "started_at": datetime.now().isoformat(), "conversations": []}
    with httpx.Client(headers={"User-Agent": "HUMA-bench/1.0"}) as http:
        for n, (name, lines) in enumerate(scenarios, 1):
            print(f"\n== {n}/{len(scenarios)} {name}")
            conv = run_conversation(http, args.base, args.client, name, lines, args.delay)
            write_transcript(out / f"{n:02d}-{name}.md", conv, args.label)
            summary["conversations"].append({k: conv[k] for k in ("name", "session_id", "phone", "errors", "seconds")})
            (out / "resumo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(3)
    phones = [c["phone"] for c in summary["conversations"]]
    print("\nPronto. Transcrições em", out)
    print("Custo por conversa (rodar no Supabase):")
    print("SELECT phone, count(*) AS respostas, round(sum(cost_brl),4) AS custo_brl FROM ai_usage")
    print("WHERE phone = ANY(ARRAY[" + ", ".join(f"'{p}'" for p in phones) + "])")
    print("GROUP BY phone ORDER BY custo_brl DESC;")
    return 0


if __name__ == "__main__":
    sys.exit(main())
