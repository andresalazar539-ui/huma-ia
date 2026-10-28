"""
Onboarding que lê o site inteiro (2026-10-27): escolha das páginas, corpus,
fatos do site → identidade + regras, entrevista que pula o que o site
respondeu, compilação que não apaga o que o site trouxe, status da análise
de mercado. Tudo puro, sem rede.
"""
from __future__ import annotations

import json

from huma.models.schemas import ClientIdentity
from huma.onboarding import interview as iv


def _identity(**kw) -> ClientIdentity:
    base = {"client_id": "cli_t", "business_name": "Clínica Bela"}
    base.update(kw)
    return ClientIdentity(**base)


_PARSED = {
    "business_name": "Clínica Bela",
    "business_description": "Clínica de estética facial em Curitiba.",
    "category": "clinica",
    "subcategory": "clínica de estética facial",
    "tone_of_voice": "Acolhedor e direto.",
    "summary_for_owner": "Deixa eu ver se entendi: clínica de estética em Curitiba.",
    "working_hours": "Segunda a sexta, 9h às 19h; sábado até 13h",
    "address": "Rua das Flores, 100, Batel, Curitiba",
    "service_region": "Curitiba e região",
    "contact": {"phone": "(41) 3333-0000", "email": "oi@bela.com", "whatsapp": ""},
    "accepted_payment_methods": ["pix", "credit_card", "cheque"],
    "max_installments": 6,
    "price_policy": "sob_consulta",
    "products_or_services": [{"name": "Harmonização facial", "price": "", "description": "ácido hialurônico"}, {"name": "Botox", "price": "a partir de R$ 900", "description": ""}],
    "faq": [{"question": "Dói?", "answer": "Usamos anestésico tópico."}, {"question": "Tem estacionamento?", "answer": "Sim, conveniado."}],
    "policies": [{"topic": "reagendamento", "rule": "avisar com 24h de antecedência"}, {"topic": "", "rule": "menores só com responsável"}],
    "not_served": ["procedimentos cirúrgicos"],
    "differentials": ["12 anos de mercado", "equipamento Ultraformer"],
    "social_proof": ["nota 4,9 no Google com 300 avaliações"],
    "team": ["Dra. Ana, dermatologista"],
    "next_step": {"label": "Agendar avaliação gratuita", "url": "https://bela.com/agendar"},
    "open_questions": ["Vi que a avaliação é gratuita. Depois dela, quem fecha o procedimento? (ex.: 'a própria Dra. Ana')"],
}


class TestRankAndCorpus:
    def test_rank_prefers_relevant_pages_then_shallow(self):
        urls = [
            "https://x.com/", "https://x.com/blog/2021/post-longo", "https://x.com/servicos",
            "https://x.com/contato", "https://x.com/sobre", "https://x.com/a/b/c", "https://x.com/politica-de-troca",
        ]
        ranked = iv.rank_site_urls(urls, limit=5)
        assert ranked[0] == "https://x.com/"
        assert set(ranked[1:]) == {"https://x.com/servicos", "https://x.com/contato", "https://x.com/sobre", "https://x.com/politica-de-troca"}

    def test_rank_drops_useless_pages(self):
        urls = [
            "https://x.com/", "https://x.com/politica-de-cookies", "https://x.com/politica-de-privacidade",
            "https://x.com/vagas", "https://x.com/blog/post-1", "https://x.com/blog", "https://x.com/minha-conta",
            "https://x.com/perguntas-frequentes", "https://x.com/politica-de-troca",
        ]
        ranked = iv.rank_site_urls(urls)
        assert ranked[0] == "https://x.com/"
        assert set(ranked[1:]) == {"https://x.com/perguntas-frequentes", "https://x.com/politica-de-troca"}

    def test_rank_dedups_trailing_slash_and_limits(self):
        urls = ["https://x.com/", "https://x.com/sobre/", "https://x.com/sobre", "https://x.com/p1", "https://x.com/p2"]
        assert iv.rank_site_urls(urls, limit=3) == ["https://x.com/", "https://x.com/sobre/", "https://x.com/p1"]

    def test_corpus_caps_per_page_and_total(self):
        pages = [("https://x.com/", "Home", "a" * 10_000), ("https://x.com/sobre", "", "b" * 10_000), ("https://x.com/c", "C", "")]
        corpus = iv.build_corpus(pages, max_chars=12_000, page_chars=7_000)
        assert corpus.startswith("[PÁGINA: Home | https://x.com/]")
        assert "[PÁGINA: https://x.com/sobre | https://x.com/sobre]" in corpus
        assert len(corpus) <= 12_100
        assert "https://x.com/c" not in corpus


class TestSourceToIdentity:
    def test_everything_read_becomes_configuration_and_rules(self):
        proposal, facts = iv.source_to_identity(_PARSED, read_at="2026-10-27T12:00:00+00:00")
        assert proposal["working_hours"].startswith("Segunda a sexta")
        assert proposal["accepted_payment_methods"] == ["pix", "credit_card"]  # cheque não é slug válido
        assert proposal["max_installments"] == 6
        assert len(proposal["products_or_services"]) == 2 and len(proposal["faq"]) == 2
        assert proposal["self_serve_url"] == "https://bela.com/agendar" and proposal["self_serve_label"] == "Agendar avaliação gratuita"
        rules = proposal["custom_rules"]
        assert rules.startswith("FATOS DO SITE (lidos em 2026-10-27):")
        assert "QUANDO o cliente perguntar sobre reagendamento: avisar com 24h" in rules
        assert "REGRA: menores só com responsável" in rules
        assert "SE o cliente pedir procedimentos cirúrgicos" in rules
        assert "Região atendida: Curitiba e região" in rules
        assert "Endereço (responda só quando perguntarem onde fica): Rua das Flores" in rules
        assert "phone: (41) 3333-0000; email: oi@bela.com" in rules
        assert "12 anos de mercado; equipamento Ultraformer" in rules
        assert "nota 4,9 no Google" in rules
        assert "Dra. Ana, dermatologista" in rules
        assert "Preço é sob consulta: NUNCA invente valor" in rules
        assert "Próximo passo que o site propõe ao cliente: Agendar avaliação gratuita" in rules
        assert facts["subcategory"] == "clínica de estética facial"
        assert facts["price_policy"] == "sob_consulta" and facts["produtos"] == 2 and facts["faq"] == 2
        assert facts["contact"] == {"phone": "(41) 3333-0000", "email": "oi@bela.com"}

    def test_next_step_without_real_url_does_not_become_link(self):
        parsed = {**_PARSED, "next_step": {"label": "Chamar no WhatsApp", "url": "whatsapp"}}
        proposal, _ = iv.source_to_identity(parsed)
        assert "self_serve_url" not in proposal
        assert "Próximo passo que o site propõe ao cliente: Chamar no WhatsApp" in proposal["custom_rules"]

    def test_empty_facts_give_no_rules(self):
        proposal, facts = iv.source_to_identity({"business_name": "X", "business_description": "Y"})
        assert "custom_rules" not in proposal and facts["policies"] == [] and facts["price_policy"] == ""
        assert iv.site_facts_rules({}) == ""

    def test_garbage_is_safe(self):
        assert iv.source_to_identity("nada") == ({}, {})
        proposal, facts = iv.source_to_identity({"business_name": "X", "policies": "x", "not_served": [1, None], "price_policy": "qualquer"})
        assert facts["not_served"] == ["1"] and facts["price_policy"] == ""


class TestInterviewSkipsWhatTheSiteAnswered:
    def _with_facts(self, facts: dict, **kw) -> ClientIdentity:
        return _identity(onboarding_answers={iv.META_SITE_FACTS: json.dumps(facts)}, **kw)

    def test_hours_skipped_when_site_had_them(self):
        ident = _identity(working_hours="Seg a sex 9h-18h")
        state = iv.build_interview_state(ident)
        hours = next(q for q in state["questions"] if q["id"] == "hours")
        assert hours["skipped"] is True

    def test_offer_skipped_for_free_service(self):
        ident = self._with_facts({"price_policy": "gratuito"}, products_or_services=[{"name": "Assessoria", "price": "", "description": ""}])
        state = iv.build_interview_state(ident)
        assert next(q for q in state["questions"] if q["id"] == "offer")["skipped"] is True

    def test_offer_reworded_when_price_is_on_request(self):
        ident = self._with_facts({"price_policy": "sob_consulta"}, products_or_services=[{"name": "Harmonização", "price": "", "description": ""}])
        q = next(q for q in iv.get_interview_questions(ident) if q["id"] == "offer")
        assert "sob consulta" in q["question"] and "Harmonização" in q["question"]

    def test_goal_personalized_with_next_step(self):
        ident = self._with_facts({"next_step": {"label": "Agendar avaliação gratuita", "url": ""}})
        q = next(q for q in iv.get_interview_questions(ident) if q["id"] == "goal")
        assert "Agendar avaliação gratuita" in q["question"]

    def test_faq_skipped_with_three_or_more(self):
        ident = _identity(faq=[{"question": f"q{i}", "answer": "a"} for i in range(3)])
        assert next(q for q in iv.build_interview_state(ident)["questions"] if q["id"] == "faq_top")["skipped"] is True

    def test_get_site_facts_tolerates_bad_json(self):
        assert iv.get_site_facts(_identity(onboarding_answers={iv.META_SITE_FACTS: "{nope"})) == {}
        assert iv.get_site_facts(_identity(onboarding_answers={iv.META_SITE_FACTS: {"a": 1}})) == {"a": 1}


class TestCompileKeepsWhatTheSiteBrought:
    def test_merge_products_and_faq_by_name(self):
        ident = _identity(
            products_or_services=[{"name": "Botox", "price": "", "description": "toxina"}, {"name": "Peeling", "price": "R$ 300", "description": ""}],
            faq=[{"question": "Dói?", "answer": "pouco"}],
            custom_rules="FATOS DO SITE:\n- QUANDO perguntarem sobre troca: 7 dias",
            forbidden_words=["querida"],
        )
        updates = iv.merge_compiled_updates(ident, {
            "products_or_services": [{"name": "botox", "price": "R$ 900", "description": "x"}, {"name": "Limpeza de pele", "price": "R$ 150", "description": ""}],
            "faq": [{"question": "dói?", "answer": "usamos anestésico"}, {"question": "Parcela?", "answer": "6x"}],
            "custom_rules": "Nunca prometer resultado.",
            "forbidden_words": ["baratinho"],
            "working_hours": "Seg a sáb",
        })
        names = [p["name"] for p in updates["products_or_services"]]
        assert names == ["Botox", "Peeling", "Limpeza de pele"]
        assert updates["products_or_services"][0]["price"] == "R$ 900"  # preço do dono vence
        assert updates["products_or_services"][0]["description"] == "toxina"  # descrição do site fica
        assert [f["question"] for f in updates["faq"]] == ["Dói?", "Parcela?"]
        assert updates["faq"][0]["answer"] == "usamos anestésico"
        assert updates["custom_rules"].startswith("FATOS DO SITE:") and updates["custom_rules"].endswith("Nunca prometer resultado.")
        assert updates["forbidden_words"] == ["baratinho", "querida"]
        assert updates["working_hours"] == "Seg a sáb"

    def test_merge_without_existing_data_is_passthrough(self):
        updates = iv.merge_compiled_updates(_identity(), {"products_or_services": [{"name": "A", "price": "", "description": ""}]})
        assert updates == {"products_or_services": [{"name": "A", "price": "", "description": ""}]}

    def test_compile_prompt_tells_the_model_what_is_known(self):
        ident = _identity(working_hours="Seg a sex", products_or_services=[{"name": "Botox", "price": "R$ 900", "description": ""}], custom_rules="FATOS DO SITE:\n- x")
        prompt = iv._build_compile_prompt(ident, "P: a\nR: b")
        assert "O QUE EU JÁ SEI" in prompt and "Botox (R$ 900)" in prompt and "Horário: Seg a sex" in prompt
        assert "a palavra do dono vence o site" in prompt
        assert "O QUE EU JÁ SEI" not in iv._build_compile_prompt(_identity(), "P: a\nR: b")


class TestMarketStatus:
    def test_completed_when_analysis_exists(self):
        ident = _identity(market_analysis={"market_context": "x"})
        assert iv.get_market_status(ident)["status"] == "completed"

    def test_reads_metadata_and_defaults_to_pending(self):
        ident = _identity(onboarding_answers={iv.META_MARKET_STATUS: json.dumps({"status": "error", "detail": "timeout", "attempts": 2, "at": "2026-10-27"})})
        s = iv.get_market_status(ident)
        assert s == {"status": "error", "at": "2026-10-27", "detail": "timeout", "attempts": 2}
        assert iv.get_market_status(_identity())["status"] == "pending"
        assert iv.get_market_status(_identity(onboarding_answers={iv.META_MARKET_STATUS: "{x"}))["status"] == "pending"

    def test_metadata_keys_are_not_real_answers(self):
        answers = {iv.META_SITE_FACTS: "{}", iv.META_MARKET_STATUS: "{}", "goal": "agendar"}
        assert iv.real_answers(answers) == {"goal": "agendar"}
