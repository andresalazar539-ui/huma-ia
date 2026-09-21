# ================================================================
# huma/tests/test_onboarding_interview.py — Entrevista de onboarding
#
# Cobre (unit, sem serviços externos):
#   - coerce_identity_updates: whitelist + coerção de tipos
#     (products/faq SEMPRE list[dict] — prompt builders chamam .get())
#   - get_interview_questions / get_deferred_questions: fases
#   - skip logic: dado que já existe não vira pergunta
#   - build_interview_state: próxima pergunta, progresso, done
#   - _build_transcript: P/R a partir de onboarding_answers
#   - _validate_history (rotas): sanitização do histórico do playground
# ================================================================

from huma.models.schemas import (
    BusinessCategory, ClientIdentity, CloneMode, MessagingStyle,
    OnboardingStatus,
)
from huma.onboarding import interview
from huma.onboarding.categories import AUTONOMY_QUESTIONS, COMMON_QUESTIONS
from huma.routes.onboarding import _validate_history


def _identity(**overrides) -> ClientIdentity:
    base = dict(
        client_id="cli_interview",
        business_name="",
        clone_mode=CloneMode.APPROVAL,
        messaging_style=MessagingStyle.SPLIT,
        onboarding_status=OnboardingStatus.PENDING,
    )
    base.update(overrides)
    return ClientIdentity(**base)


# ================================================================
# COERÇÃO DE UPDATES (saída da IA → tipos do ClientIdentity)
# ================================================================


class TestCoerceIdentityUpdates:

    def test_non_dict_returns_empty(self):
        assert interview.coerce_identity_updates(None) == {}
        assert interview.coerce_identity_updates("texto") == {}
        assert interview.coerce_identity_updates([1, 2]) == {}

    def test_str_fields_trimmed_and_empty_dropped(self):
        updates = interview.coerce_identity_updates({
            "business_name": "  Clínica Sorriso  ",
            "business_description": "",
            "tone_of_voice": "   ",
        })
        assert updates == {"business_name": "Clínica Sorriso"}

    def test_fields_outside_whitelist_are_dropped(self):
        updates = interview.coerce_identity_updates({
            "api_key": "hack",
            "onboarding_status": "active",
            "meta_access_token": "tok",
            "capabilities": ["schedule"],
            "business_name": "Loja X",
        })
        assert set(updates.keys()) == {"business_name"}

    def test_products_become_dicts_with_all_keys(self):
        updates = interview.coerce_identity_updates({
            "products_or_services": [
                {"name": "Corte", "price": "R$50"},
                {"name": "  "},           # sem nome → descartado
                "string solta",            # não é dict → descartado
                {"description": "sem nome"},
            ],
        })
        assert updates["products_or_services"] == [
            {"name": "Corte", "price": "R$50", "description": ""},
        ]

    def test_faq_requires_question_and_answer(self):
        updates = interview.coerce_identity_updates({
            "faq": [
                {"question": "Onde fica?", "answer": "Rua A, 10"},
                {"question": "Sem resposta", "answer": ""},
                {"answer": "sem pergunta"},
            ],
        })
        assert updates["faq"] == [{"question": "Onde fica?", "answer": "Rua A, 10"}]

    def test_payment_methods_filtered_to_valid_slugs(self):
        updates = interview.coerce_identity_updates({
            "accepted_payment_methods": ["pix", "cheque", "credit_card", "bitcoin"],
        })
        assert updates["accepted_payment_methods"] == ["pix", "credit_card"]

    def test_numbers_clamped_and_bools_typed(self):
        updates = interview.coerce_identity_updates({
            "max_installments": 99,
            "max_discount_percent": -5,
            "use_emojis": True,
            "collect_before_offer": "sim",  # não é bool → descartado
        })
        assert updates["max_installments"] == 24
        assert updates["max_discount_percent"] == 0.0
        assert updates["use_emojis"] is True
        assert "collect_before_offer" not in updates

    def test_bool_disguised_as_number_is_not_installments(self):
        updates = interview.coerce_identity_updates({"max_installments": True})
        assert "max_installments" not in updates


# ================================================================
# FASES DA ENTREVISTA
# ================================================================


class TestInterviewQuestions:

    def test_roteiro_e_comum_mais_universal(self):
        from huma.onboarding.categories import UNIVERSAL_QUESTIONS
        questions = interview.get_interview_questions(_identity())
        assert [q["id"] for q in questions] == (
            [q["id"] for q in COMMON_QUESTIONS] + [q["id"] for q in UNIVERSAL_QUESTIONS]
        )

    def test_vertical_nao_muda_o_roteiro(self):
        # 2026-09-20: agência caía em "serviços" e ouvia "oferece garantia?".
        # As listas fixas por vertical saíram da entrevista.
        clinica = interview.get_interview_questions(_identity(category=BusinessCategory.CLINICA))
        servicos = interview.get_interview_questions(_identity(category=BusinessCategory.SERVICOS))
        assert [q["id"] for q in clinica] == [q["id"] for q in servicos]
        assert "guarantee" not in [q["id"] for q in servicos]

    def test_perguntas_de_lacuna_entram_no_fim(self):
        import json
        identity = _identity(onboarding_answers={
            interview.META_GAP_QUESTIONS: json.dumps([
                "Vi que vocês fazem tráfego e vídeo. Qual é a porta de entrada mais comum?",
                "curta",  # descartada: curta demais
            ]),
        })
        questions = interview.get_interview_questions(identity)
        assert questions[-1]["id"] == "gap_1"
        assert interview._MAX_GAP_QUESTIONS == 3  # com o site lido, 10 perguntas era demais
        assert "porta de entrada" in questions[-1]["question"]
        assert [q["id"] for q in questions].count("gap_2") == 0

    def test_lacuna_ilegivel_nao_quebra_a_entrevista(self):
        identity = _identity(onboarding_answers={interview.META_GAP_QUESTIONS: "{não é json"})
        assert interview.get_gap_questions(identity) == []

    def test_metadados_nao_contam_como_resposta(self):
        identity = _identity(onboarding_answers={
            interview.META_TEAM_SIZE: "2-5", interview.META_INSTAGRAM: "https://www.instagram.com/x/",
            "tone": "falo direto",
        })
        state = interview.build_interview_state(identity)
        assert state["answered_count"] == 1
        transcript = interview._build_transcript(identity)
        assert "falo direto" in transcript
        assert "2-5" not in transcript and "instagram" not in transcript

    def test_instagram_vira_url(self):
        assert interview._instagram_url("@lab8") == "https://www.instagram.com/lab8/"
        assert interview._instagram_url("instagram.com/lab8") == "https://instagram.com/lab8"
        assert interview._instagram_url("  ") == ""

    def test_deferred_is_autonomy_plus_final(self):
        deferred = interview.get_deferred_questions()
        assert [q["id"] for q in deferred[:-1]] == [q["id"] for q in AUTONOMY_QUESTIONS]
        assert deferred[-1]["id"] == "final"

    def test_no_id_collision_between_core_and_deferred(self):
        # onboarding_answers é dict por id: colisão entre fases
        # sobrescreveria resposta do dono (caso real: "payment" do
        # e-commerce vs autonomia, renomeado pra "payment_methods").
        deferred_ids = {q["id"] for q in interview.get_deferred_questions()}
        for category in BusinessCategory:
            core_ids = {
                q["id"] for q in interview.get_interview_questions(_identity(category=category))
            }
            assert not core_ids & deferred_ids, f"colisão de id na vertical {category.value}"


# ================================================================
# SKIP LOGIC (dado que já existe não vira pergunta)
# ================================================================


class TestSkipLogic:

    def test_business_name_from_signup_is_skipped(self):
        state = interview.build_interview_state(_identity(business_name="Clínica Sorriso"))
        skipped = {q["id"] for q in state["questions"] if q["skipped"]}
        assert "business_name" in skipped

    def test_signup_placeholder_name_is_not_skipped(self):
        state = interview.build_interview_state(_identity(business_name="Meu negócio"))
        skipped = {q["id"] for q in state["questions"] if q["skipped"]}
        assert "business_name" not in skipped

    def test_website_and_description_skipped_after_source(self):
        state = interview.build_interview_state(_identity(
            business_name="Loja X",
            website="https://lojax.com.br",
            business_description="Loja de roupas femininas em Curitiba",
        ))
        skipped = {q["id"] for q in state["questions"] if q["skipped"]}
        assert {"business_name", "website", "description"} <= skipped

    def test_tone_is_always_asked_even_if_filled(self):
        state = interview.build_interview_state(_identity(
            business_name="Loja X",
            tone_of_voice="Descontraído",
        ))
        tone = next(q for q in state["questions"] if q["id"] == "tone")
        assert tone["skipped"] is False


# ================================================================
# ESTADO DA ENTREVISTA
# ================================================================


class TestBuildInterviewState:

    def test_next_question_is_first_unanswered_unskipped(self):
        identity = _identity(business_name="Clínica Sorriso")
        state = interview.build_interview_state(identity)
        assert state["next_question"]["id"] == "website"
        assert state["done"] is False

    def test_answered_question_advances_next(self):
        identity = _identity(
            business_name="Clínica Sorriso",
            onboarding_answers={"website": "não tenho"},
        )
        state = interview.build_interview_state(identity)
        assert state["next_question"]["id"] == "description"
        assert state["answered_count"] == 1

    def test_done_when_all_answered_or_skipped(self):
        identity = _identity(business_name="Clínica Sorriso")
        pending = [
            q["id"] for q in interview.build_interview_state(identity)["questions"]
            if not q["answered"] and not q["skipped"]
        ]
        identity.onboarding_answers = {qid: f"resposta {qid}" for qid in pending}
        state = interview.build_interview_state(identity)
        assert state["done"] is True
        assert state["next_question"] is None


# ================================================================
# TRANSCRIÇÃO PRA COMPILAÇÃO
# ================================================================


class TestBuildTranscript:

    def test_pairs_question_text_with_answer(self):
        identity = _identity(
            business_name="Clínica Sorriso",
            category=BusinessCategory.CLINICA,
            onboarding_answers={"tone": "Falo de forma acolhedora, tipo: oi querida!"},
        )
        transcript = interview._build_transcript(identity)
        assert "Como você fala com seus clientes?" in transcript
        assert "oi querida" in transcript

    def test_empty_answers_are_excluded(self):
        identity = _identity(onboarding_answers={"tone": "  ", "website": "lojax.com"})
        transcript = interview._build_transcript(identity)
        assert "lojax.com" in transcript
        assert "Como você fala" not in transcript

    def test_no_answers_returns_empty(self):
        assert interview._build_transcript(_identity()) == ""


# ================================================================
# PÁGINA /onboarding/page
# ================================================================


class TestOnboardingPage:

    def _client(self):
        from fastapi.testclient import TestClient
        from huma.app import create_app
        return TestClient(create_app())

    def test_sem_sessao_redireciona_pro_login(self):
        resp = self._client().get("/onboarding/page", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"

    def test_com_sessao_injeta_client_id(self, monkeypatch):
        import huma.core.auth as auth
        monkeypatch.setattr(auth, "SESSION_SECRET", "segredo-teste")
        token = auth.create_session_token("cli_ob")
        client = self._client()
        client.cookies.set(auth.SESSION_COOKIE_NAME, token)
        resp = client.get("/onboarding/page")
        assert resp.status_code == 200
        assert 'window.HUMA_CLIENT_ID = "cli_ob"' in resp.text
        assert "/static/onboarding/api.js" in resp.text


# ================================================================
# COMPILAÇÃO: análise de mercado em segundo plano (2026-09-20)
# ================================================================


class TestCompileMarketInBackground:

    def _setup(self, monkeypatch, identity, *, analysis=None, analyze_spy=None):
        """Mocka db, compilação, site, IA e Redis; devolve a lista de updates gravados."""
        import huma.routes.onboarding as ob
        import huma.services.redis_service as cache

        store = {"identity": identity}
        saved: list[dict] = []

        async def get_client(_cid):
            return store["identity"]

        async def update_client(_cid, updates):
            saved.append(dict(updates))
            data = store["identity"].model_dump()
            data.update(updates)
            store["identity"] = ClientIdentity(**data)

        async def compile_updates(_identity):
            return {"tone_of_voice": "direto", "custom_rules": "regra do dono"}

        async def site_text(_url):
            return ""

        async def fake_analyze(data, source_text=""):
            if analyze_spy is not None:
                analyze_spy.append(dict(data))
            return analysis or {
                "status": "completed",
                "analysis": {"market_context": "mercado aquecido", "playbook": {"objecoes": []}},
            }

        async def no_redis(*_a, **_k):
            return False

        monkeypatch.setattr(ob.db, "get_client", get_client)
        monkeypatch.setattr(ob.db, "update_client", update_client)
        monkeypatch.setattr(ob.interview, "compile_identity_updates", compile_updates)
        monkeypatch.setattr(ob, "_fetch_site_text", site_text)
        monkeypatch.setattr(ob, "analyze_market", fake_analyze)
        monkeypatch.setattr(cache, "exists", no_redis)
        monkeypatch.setattr(cache, "set_with_ttl", no_redis)
        ob._market_started.clear()
        return ob, saved, store

    def test_compile_libera_o_playground_sem_esperar_a_analise(self, monkeypatch):
        import asyncio
        identity = _identity(onboarding_answers={"tone": "falo direto"})
        ob, saved, _ = self._setup(monkeypatch, identity)
        scheduled: list[str] = []
        monkeypatch.setattr(ob, "_schedule_market_analysis", lambda cid: scheduled.append(cid) or True)

        out = asyncio.run(ob.compile_interview("cli_interview", None))

        assert out["onboarding_status"] == "sandbox"
        assert out["market_analysis_status"] == "running"
        assert scheduled == ["cli_interview"]
        # o que foi gravado na resposta HTTP é só a compilação + status
        assert len(saved) == 1
        assert saved[0]["onboarding_status"] == "sandbox"
        assert "market_analysis" not in saved[0]

    def test_background_aplica_em_cima_do_dado_fresco(self, monkeypatch):
        import asyncio
        identity = _identity(custom_rules="regra antiga", onboarding_answers={"tone": "x"})
        ob, saved, store = self._setup(monkeypatch, identity)

        # o dono mexe nas regras ENQUANTO a IA analisa (playground / ajustes)
        async def analyze_and_owner_edits(data, source_text=""):
            fresh = store["identity"].model_dump()
            fresh["custom_rules"] = "regra que o dono acabou de editar"
            store["identity"] = ClientIdentity(**fresh)
            return {"status": "completed", "analysis": {"market_context": "mercado aquecido"}}
        monkeypatch.setattr(ob, "analyze_market", analyze_and_owner_edits)

        out = asyncio.run(ob._run_market_analysis("cli_interview"))

        assert out["status"] == "completed"
        assert "regra que o dono acabou de editar" in saved[-1]["custom_rules"]
        assert "CONTEXTO DE MERCADO: mercado aquecido" in saved[-1]["custom_rules"]
        assert saved[-1]["market_analysis"] == {"market_context": "mercado aquecido"}

    def test_background_nao_roda_duas_vezes_pro_mesmo_cliente(self, monkeypatch):
        import asyncio
        calls: list[dict] = []
        identity = _identity(onboarding_answers={"tone": "x"})
        ob, _, _ = self._setup(monkeypatch, identity, analyze_spy=calls)

        first = asyncio.run(ob._run_market_analysis("cli_interview"))
        second = asyncio.run(ob._run_market_analysis("cli_interview"))

        assert first["status"] == "completed"
        assert second == {"status": "skipped", "detail": "running"}
        assert len(calls) == 1

    def test_background_nunca_levanta_e_nao_grava_em_falha(self, monkeypatch):
        import asyncio
        identity = _identity(onboarding_answers={"tone": "x"})
        ob, saved, _ = self._setup(
            monkeypatch, identity, analysis={"status": "error", "detail": "api fora"},
        )

        out = asyncio.run(ob._run_market_analysis("cli_interview"))

        assert out["status"] == "error"
        assert saved == []

    def test_state_reagenda_quando_playbook_ficou_faltando(self, monkeypatch):
        import asyncio
        identity = _identity(
            onboarding_status=OnboardingStatus.SANDBOX, onboarding_answers={"tone": "x"},
        )
        ob, _, _ = self._setup(monkeypatch, identity)
        scheduled: list[str] = []
        monkeypatch.setattr(ob, "_schedule_market_analysis", lambda cid: scheduled.append(cid) or True)

        out = asyncio.run(ob.get_state("cli_interview", None))

        assert out["has_market_analysis"] is False
        assert scheduled == ["cli_interview"]

    def test_state_nao_agenda_antes_de_compilar(self, monkeypatch):
        import asyncio
        identity = _identity(
            onboarding_status=OnboardingStatus.IN_PROGRESS, onboarding_answers={"tone": "x"},
        )
        ob, _, _ = self._setup(monkeypatch, identity)
        scheduled: list[str] = []
        monkeypatch.setattr(ob, "_schedule_market_analysis", lambda cid: scheduled.append(cid) or True)

        asyncio.run(ob.get_state("cli_interview", None))

        assert scheduled == []


# ================================================================
# PULAR O QUE O SITE JÁ RESPONDEU (universais)
# ================================================================


class TestUniversalSkip:

    def _q(self, qid):
        from huma.onboarding.categories import UNIVERSAL_QUESTIONS
        return next(q for q in UNIVERSAL_QUESTIONS if q["id"] == qid)

    def test_offer_so_pula_quando_os_produtos_tem_preco(self):
        sem_preco = _identity(products_or_services=[{"name": "Tráfego pago", "price": "", "description": ""}])
        com_preco = _identity(products_or_services=[{"name": "Corte", "price": "R$ 50", "description": ""}])
        assert interview._is_question_skippable(self._q("offer"), sem_preco) is False
        assert interview._is_question_skippable(self._q("offer"), com_preco) is True

    def test_um_item_sem_preco_nao_traz_a_pergunta_generica_de_volta(self):
        # Caso real (Le Femme): 7 linhas lidas, 6 com preço. A HUMA perguntou
        # "o que você vende e como cobra?" logo depois de resumir a loja.
        loja = _identity(products_or_services=(
            [{"name": f"Linha {i}", "price": "A partir de R$ 284,91", "description": ""} for i in range(6)]
            + [{"name": "Acessórios", "price": "", "description": ""}]
        ))
        assert interview._is_question_skippable(self._q("offer"), loja) is True

    def test_sem_preco_a_pergunta_usa_o_que_foi_lido(self):
        agencia = _identity(products_or_services=[
            {"name": "Tráfego pago", "price": "", "description": ""},
            {"name": "Produção de vídeo", "price": "", "description": ""},
            {"name": "Social media", "price": "", "description": ""},
        ])
        q = next(q for q in interview.get_interview_questions(agencia) if q["id"] == "offer")
        assert "Tráfego pago, Produção de vídeo e Social media" in q["question"]
        assert "O que você vende" not in q["question"]

    def test_tom_lido_no_site_vira_confirmacao(self):
        identity = _identity(tone_of_voice="Próximo e direto, trata a cliente por você.")
        q = next(q for q in interview.get_interview_questions(identity) if q["id"] == "tone")
        assert "Pelo que eu li" in q["question"] and "Próximo e direto" in q["question"]

    def test_citacao_do_tom_nao_corta_palavra_no_meio(self):
        longo = ("A marca fala de forma leve, acolhedora e feminina, usando linguagem próxima e entusiasmada "
                 "para criar conexão com as clientes. O tom é descontraído, mas transmite confiança e sofisticação.")
        q = interview._short_quote(longo)
        assert q == ("A marca fala de forma leve, acolhedora e feminina, usando linguagem próxima e "
                     "entusiasmada para criar conexão com as clientes")
        sem_ponto = interview._short_quote("palavra " * 60)
        assert sem_ponto.endswith("...") and not sem_ponto[:-3].endswith(" ") and len(sem_ponto) <= 173
        assert interview._short_quote("Direto e simples.") == "Direto e simples"

    def test_toda_pergunta_fixa_tem_exemplo_e_nenhuma_tem_travessao(self):
        for identity in (_identity(), _identity(tone_of_voice="Direto.", products_or_services=[{"name": "X", "price": "", "description": ""}])):
            for q in interview.get_interview_questions(identity):
                if q["id"] == "business_name":
                    continue
                assert "(ex.:" in q["question"], q["id"]
                assert "\u2014" not in q["question"], q["id"]

    def test_hours_e_faq_pulam_quando_ja_existem(self):
        identity = _identity(
            working_hours="seg a sex, 9h às 18h",
            faq=[{"question": f"p{i}", "answer": "r"} for i in range(3)],
        )
        assert interview._is_question_skippable(self._q("hours"), identity) is True
        assert interview._is_question_skippable(self._q("faq_top"), identity) is True
        assert interview._is_question_skippable(self._q("goal"), identity) is False


# ================================================================
# PLAYGROUND EM MODO DEMONSTRAÇÃO (2026-09-20)
# ================================================================


class TestPlaygroundDemo:

    def test_horarios_de_exemplo_caem_em_dia_util(self):
        from datetime import datetime, timedelta, timezone
        from huma.core import playground_demo as demo
        sexta = datetime(2026, 9, 18, 22, 0, tzinfo=timezone(timedelta(hours=-3)))
        slots = demo.example_slots(sexta)
        assert slots == ["segunda-feira, 21/09 às 10h", "terça-feira, 22/09 às 15h"]

    def test_marker_e_condicional_e_respeita_capabilities(self):
        from huma.core import playground_demo as demo
        agenda = demo.build_demo_marker({"schedule"})
        assert "SE o cliente pedir horário" in agenda
        assert "NÃO emita check_availability" in agenda
        assert "Pix" not in agenda
        vende = demo.build_demo_marker({"sell_physical"})
        assert "SE o cliente quiser pagar" in vende
        assert "SE o cliente pedir horário" not in vende
        assert "—" not in agenda + vende

    def test_topicos_simulados(self):
        from huma.core import playground_demo as demo
        assert demo.detect_demo_topics([], "Tenho segunda-feira, 21/09 às 10h. Serve?") == ["agenda"]
        assert demo.detect_demo_topics([{"type": "check_availability"}], "um segundo") == ["agenda"]
        # falar do horário de funcionamento NÃO é simulação de agenda
        assert demo.detect_demo_topics([], "A gente atende das 9h às 19h.") == []
        assert demo.detect_demo_topics([], "Te mando o Pix aqui mesmo.") == ["pagamento"]

    def test_rota_injeta_o_marker_e_devolve_os_topicos(self, monkeypatch):
        import asyncio
        import huma.routes.onboarding as ob
        seen: dict = {}

        async def get_client(_cid):
            return _identity()

        async def fake_generate(identity, conv, text, tier=3, followup_hint=""):
            seen["history"] = list(conv.history)
            return {"reply": "Tenho terça-feira, 22/09 às 15h.", "reply_parts": [], "actions": []}

        monkeypatch.setattr(ob.db, "get_client", get_client)
        monkeypatch.setattr(ob.ai, "generate_response", fake_generate)
        ob._playground_rate.clear()

        payload = ob.PlaygroundChatPayload(message="tem horário?", history=[{"role": "user", "content": "oi"}])
        out = asyncio.run(ob.playground_chat("cli_interview", payload, None))

        assert seen["history"][-1]["role"] == "assistant"
        assert seen["history"][-1]["content"].startswith("[MODO DEMONSTRAÇÃO")
        assert out["demo_topics"] == ["agenda"]
        assert out["reply_parts"] == ["Tenho terça-feira, 22/09 às 15h."]


# ================================================================
# TESTE DO CLONE NUNCA DEIXA NO VÁCUO + CARDS DE PRODUTO (2026-09-20)
# Caso real: "Quer que eu mande umas fotos?" / "quero" / "Segura aí, Paula!"
# e mais nada. No atendimento real o sistema roda o segundo turno; no
# playground esse turno não existia.
# ================================================================


_LOJA = [
    {"name": "Conjuntos (tricô, moletinho)", "price": "A partir de R$ 284,91", "description": "conjuntos"},
    {"name": "Vestidos (tule, tomara que caia)", "price": "A partir de R$ 379,91", "description": "vestidos"},
    {"name": "Bodies", "price": "A partir de R$ 151,91", "description": ""},
]


class TestPlaygroundNoVacuum:

    def test_pedido_direto_e_aceite_de_oferta(self):
        from huma.core import playground_demo as demo
        assert demo.wants_products("me manda foto dos vestidos")
        assert demo.wants_products("quais modelos vocês têm?")
        assert demo.wants_products("quero", "Quer que eu mande umas fotos dos modelos disponíveis pra você escolher?")
        assert not demo.wants_products("quero", "A gente atende das 9h às 18h.")
        assert not demo.wants_products("qual o horário de vocês?")

    def test_reconhece_resposta_que_so_pede_pra_esperar(self):
        from huma.core import playground_demo as demo
        for vazio in ("Segura aí, Paula!", "Um segundo que eu já te mando!", "Deixa eu verificar aqui", ""):
            assert demo.is_placeholder_reply(vazio), vazio
        assert not demo.is_placeholder_reply("O tomara que caia cai muito bem! É despojado e fica lindo pra comemorar.")
        assert demo.is_placeholder_reply("Olha essas opções", [{"type": "show_products", "query": "vestidos"}])

    def test_cards_trazem_primeiro_o_que_o_cliente_pediu_e_nao_reformatam_preco(self):
        from huma.core import playground_demo as demo
        cards = demo.demo_cards(_LOJA, "ela é casual, quero ver o tomara que caia")
        assert cards[0]["title"].startswith("Vestidos")
        assert cards[0]["price"] == "A partir de R$ 379,91"
        assert demo.demo_cards([], "qualquer coisa") == []
        assert "\u2014" not in demo.build_cards_marker(cards) + demo.NO_WAIT_MARKER + demo.safety_reply(cards)

    def _route(self, monkeypatch, replies, products=_LOJA):
        import huma.routes.onboarding as ob
        calls: list[dict] = []

        async def get_client(_cid):
            return _identity(products_or_services=products)

        async def fake_generate(identity, conv, text, tier=3, followup_hint=""):
            calls.append({"history": list(conv.history), "hint": followup_hint})
            return dict(replies[min(len(calls) - 1, len(replies) - 1)])

        monkeypatch.setattr(ob.db, "get_client", get_client)
        monkeypatch.setattr(ob.ai, "generate_response", fake_generate)
        ob._playground_rate.clear()
        return ob, calls

    def test_quero_depois_da_oferta_mostra_os_cards_numa_chamada_so(self, monkeypatch):
        import asyncio
        ob, calls = self._route(monkeypatch, [{"reply": "Olha que lindos! Qual chamou a sua atenção?", "actions": []}])
        payload = ob.PlaygroundChatPayload(message="quero", history=[
            {"role": "user", "content": "ela é mais casual"},
            {"role": "assistant", "content": "Quer que eu mande umas fotos dos modelos disponíveis pra você escolher?"},
        ])
        out = asyncio.run(ob.playground_chat("cli_interview", payload, None))
        assert len(calls) == 1                                   # sem custo extra
        assert [c["title"] for c in out["cards"]][0].startswith(("Vestidos", "Conjuntos", "Bodies"))
        assert len(out["cards"]) == 3
        assert calls[0]["history"][-1]["content"].startswith("[PRODUTOS MOSTRADOS")
        assert "produtos" in out["demo_topics"]

    def test_segura_ai_dispara_o_segundo_turno_e_entrega(self, monkeypatch):
        import asyncio
        ob, calls = self._route(monkeypatch, [
            {"reply": "Segura aí, Paula!", "actions": []},
            {"reply": "Temos tomara que caia a partir de R$ 379,91. Quer ver as cores?", "actions": []},
        ])
        payload = ob.PlaygroundChatPayload(message="tem tomara que caia?", history=[])
        out = asyncio.run(ob.playground_chat("cli_interview", payload, None))
        assert len(calls) == 2
        assert out["reply"].startswith("Temos tomara que caia")
        assert calls[1]["hint"]                                   # o 2º turno recebe a instrução

    def test_se_o_segundo_turno_ainda_enrolar_sai_texto_de_seguranca(self, monkeypatch):
        import asyncio
        ob, calls = self._route(monkeypatch, [
            {"reply": "Um segundo!", "actions": [{"type": "show_products", "query": "vestidos"}]},
            {"reply": "Já te mando!", "actions": []},
        ])
        payload = ob.PlaygroundChatPayload(message="oi, tudo bem?", history=[])
        out = asyncio.run(ob.playground_chat("cli_interview", payload, None))
        assert len(out["cards"]) == 3
        assert out["reply"] == "Olha só o que eu separei pra você. Algum chamou a sua atenção?"
        assert out["reply_parts"] == [out["reply"]]

    def test_conversa_normal_nao_gasta_segunda_chamada(self, monkeypatch):
        import asyncio
        ob, calls = self._route(monkeypatch, [{"reply": "Oi! Tudo ótimo, e você? Procurando algo especial?", "actions": []}])
        out = asyncio.run(ob.playground_chat("cli_interview", ob.PlaygroundChatPayload(message="oi", history=[]), None))
        assert len(calls) == 1 and out["cards"] == []


# ================================================================
# PERFIL DO DONO + FONTE COM SITE E INSTAGRAM (rotas)
# ================================================================


class TestProfileAndSource:

    def _db(self, monkeypatch, identity):
        import huma.routes.onboarding as ob
        saved: list[dict] = []

        async def get_client(_cid):
            return identity

        async def update_client(_cid, updates):
            saved.append(dict(updates))

        monkeypatch.setattr(ob.db, "get_client", get_client)
        monkeypatch.setattr(ob.db, "update_client", update_client)
        return ob, saved

    def test_profile_grava_nome_e_metadados(self, monkeypatch):
        import asyncio
        ob, saved = self._db(monkeypatch, _identity(onboarding_answers={"tone": "x"}))
        out = asyncio.run(ob.save_profile(
            "cli_interview", ob.ProfilePayload(owner_name="  André   Salazar ", team_size="2-5"), None,
        ))
        assert out["status"] == "ok"
        assert saved[0]["owner_name"] == "André Salazar"
        assert saved[0]["onboarding_answers"] == {"tone": "x", "_team_size": "2-5"}

    def test_profile_recusa_tamanho_invalido(self, monkeypatch):
        import asyncio
        import pytest
        from fastapi import HTTPException
        ob, saved = self._db(monkeypatch, _identity())
        with pytest.raises(HTTPException) as err:
            asyncio.run(ob.save_profile("cli_interview", ob.ProfilePayload(team_size="mil"), None))
        assert err.value.status_code == 400
        assert saved == []

    def test_source_exige_site_ou_instagram(self, monkeypatch):
        import asyncio
        import pytest
        from fastapi import HTTPException
        ob, _ = self._db(monkeypatch, _identity())
        with pytest.raises(HTTPException) as err:
            asyncio.run(ob.analyze_business_source("cli_interview", ob.SourcePayload(), None))
        assert err.value.status_code == 400
        assert "pelo menos um" in err.value.detail

    def test_apply_guarda_instagram_e_lacunas(self, monkeypatch):
        import asyncio
        import json
        ob, saved = self._db(monkeypatch, _identity())
        payload = ob.SourceApplyPayload(
            url="https://agencialab8.com.br", instagram="@lab8",
            proposal={
                "business_name": "Lab8",
                "open_questions": ["Vi que vocês fazem tráfego e vídeo. Qual é a porta de entrada?"],
            },
        )
        asyncio.run(ob.apply_business_source("cli_interview", payload, None))
        assert saved[0]["website"] == "https://agencialab8.com.br"
        meta = saved[0]["onboarding_answers"]
        assert meta["_instagram"] == "https://www.instagram.com/lab8/"
        assert "porta de entrada" in json.loads(meta["_gap_questions"])[0]

    def test_apply_so_com_instagram_usa_ele_como_website(self, monkeypatch):
        import asyncio
        ob, saved = self._db(monkeypatch, _identity())
        payload = ob.SourceApplyPayload(instagram="lab8", proposal={"business_name": "Lab8"})
        asyncio.run(ob.apply_business_source("cli_interview", payload, None))
        assert saved[0]["website"] == "https://www.instagram.com/lab8/"


# ================================================================
# "ME PREPARA": horário que a tela do onboarding gera (ai_schedule)
# ================================================================


class TestPrepScheduleShape:
    """Espelha buildOffHoursSchedule de static/onboarding/ob-prepara.jsx."""

    def _schedule(self, open_at="08:00", close_at="18:00", mode="approval") -> dict:
        return {
            "enabled": True, "default_mode": "off",
            "windows": [
                {"days": [5, 6], "start": "00:00", "end": "23:59", "mode": mode},
                {"days": [0, 1, 2, 3, 4, 5, 6], "start": close_at, "end": open_at, "mode": mode},
            ],
        }

    def test_formato_passa_no_validador(self):
        from huma.core.ai_schedule import validate_ai_schedule
        assert validate_ai_schedule(self._schedule()) == []

    def test_nao_sobra_buraco_fora_do_expediente(self):
        from datetime import datetime
        from huma.core.ai_schedule import resolve_effective_mode
        s = self._schedule()
        # 2026-09-21 é segunda-feira
        casos = {
            datetime(2026, 9, 21, 3, 0): "approval",    # madrugada de segunda (vem do domingo)
            datetime(2026, 9, 22, 10, 0): "off",        # terça no expediente: equipe atende
            datetime(2026, 9, 25, 19, 0): "approval",   # sexta à noite
            datetime(2026, 9, 26, 12, 0): "approval",   # sábado de dia
            datetime(2026, 9, 26, 23, 59): "approval",  # último minuto de sábado
            datetime(2026, 9, 27, 23, 59): "approval",  # último minuto de domingo
        }
        for instante, esperado in casos.items():
            assert resolve_effective_mode(s, fallback_mode="approval", now=instante) == esperado, instante


# ================================================================
# LEITURA DA FONTE: rota direta + leitor reserva (2026-09-20)
# Caso real: loja atrás de CloudFront devolveu 405 pro servidor e o
# Instagram 429; o dono viu "não consegui ler" em menos de 1 segundo.
# ================================================================


class TestSourceReaderFallback:

    def _mock(self, monkeypatch, direct, reader):
        calls = {"direct": 0, "reader": 0}

        async def fake_direct(_url):
            calls["direct"] += 1
            return direct

        async def fake_reader(_url):
            calls["reader"] += 1
            return reader

        monkeypatch.setattr(interview, "_fetch_direct", fake_direct)
        monkeypatch.setattr(interview, "_fetch_via_reader", fake_reader)
        return calls

    def test_direta_boa_nao_chama_o_reserva(self, monkeypatch):
        import asyncio
        calls = self._mock(monkeypatch, "x" * 900, "y" * 5000)
        out = asyncio.run(interview.fetch_source_text("https://loja.com.br"))
        assert out == "x" * 900
        assert calls == {"direct": 1, "reader": 0}

    def test_direta_bloqueada_cai_no_reserva(self, monkeypatch):
        import asyncio
        calls = self._mock(monkeypatch, None, "texto do leitor reserva " * 40)
        out = asyncio.run(interview.fetch_source_text("loja.com.br"))
        assert out.startswith("texto do leitor reserva")
        assert calls == {"direct": 1, "reader": 1}

    def test_direta_pobre_fica_com_o_texto_maior(self, monkeypatch):
        import asyncio
        self._mock(monkeypatch, "pouco texto " * 10, "bem mais texto renderizado " * 60)
        out = asyncio.run(interview.fetch_source_text("https://spa.com.br"))
        assert out.startswith("bem mais texto")

    def test_instagram_nao_gasta_tempo_no_reserva(self, monkeypatch):
        import asyncio
        calls = self._mock(monkeypatch, None, "nunca deveria ser usado " * 40)
        out = asyncio.run(interview.fetch_source_text("https://www.instagram.com/loja/"))
        assert out is None
        assert calls["reader"] == 0

    def test_nada_legivel_explica_o_motivo(self, monkeypatch):
        import asyncio
        self._mock(monkeypatch, None, None)
        so_insta = asyncio.run(interview.analyze_source("", instagram="@loja"))
        assert so_insta["status"] == "unavailable"
        assert so_insta["sources"] == {"site": "", "instagram": "failed"}
        assert "Instagram não deixa" in so_insta["detail"]
        os_dois = asyncio.run(interview.analyze_source("https://loja.com.br", instagram="@loja"))
        assert os_dois["sources"] == {"site": "failed", "instagram": "failed"}
        assert "bloqueou a minha leitura" in os_dois["detail"]
        assert "—" not in so_insta["detail"] + os_dois["detail"]

    def test_instagram_colado_no_campo_do_site_vira_instagram(self, monkeypatch):
        import asyncio
        self._mock(monkeypatch, None, None)
        out = asyncio.run(interview.analyze_source("instagram.com/loja"))
        assert out["sources"] == {"site": "", "instagram": "failed"}


# ================================================================
# ANÁLISE DA FONTE: resposta da IA cortada no limite (2026-09-20)
# Caso real: loja com muitos produtos estourou max_tokens=1500, o JSON veio
# cortado e o dono viu "não consegui estruturar o que li".
# ================================================================


class _Block:
    def __init__(self, type_, text="", input_=None):
        self.type, self.text, self.input = type_, text, input_


class _Resp:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content, self.stop_reason = blocks, stop_reason


class TestSourceAnalysisParsing:

    def _run(self, monkeypatch, response):
        import asyncio
        seen: dict = {}

        async def fake_fetch(_url):
            return "texto da loja " * 80

        class _Messages:
            async def create(self, **kwargs):
                seen.update(kwargs)
                return response

        class _Client:
            def __init__(self, **_k):
                self.messages = _Messages()

        monkeypatch.setattr(interview, "fetch_source_text", fake_fetch)
        monkeypatch.setattr(interview.anthropic, "AsyncAnthropic", _Client)
        return asyncio.run(interview.analyze_source("https://loja.com.br")), seen

    def test_pede_saida_estruturada_com_limite_folgado(self, monkeypatch):
        resp = _Resp([_Block("tool_use", input_={"business_name": "Le Femme", "business_description": "Boutique."})])
        out, seen = self._run(monkeypatch, resp)
        assert out["status"] == "ok"
        assert seen["max_tokens"] >= 4000
        assert seen["tool_choice"] == {"type": "tool", "name": "source_analysis"}

    def test_json_cortado_no_meio_e_recuperado(self, monkeypatch):
        cortado = (
            '{"business_name": "Le Femme", "business_description": "Boutique feminina.", '
            '"open_questions": ["Vi que tem frete grátis. Vale pro Brasil todo?"], '
            '"products_or_services": [{"name": "Conjunto Ibiza", "price": "R$ 455,91", "description": "tricô"}, '
            '{"name": "Vestido Posit'
        )
        out, _ = self._run(monkeypatch, _Resp([_Block("text", text=cortado)], stop_reason="max_tokens"))
        assert out["status"] == "ok"
        assert out["proposal"]["business_name"] == "Le Femme"
        assert [p["name"] for p in out["proposal"]["products_or_services"]] == ["Conjunto Ibiza"]
        assert out["proposal"]["open_questions"] == ["Vi que tem frete grátis. Vale pro Brasil todo?"]

    def test_tool_cortada_usa_o_que_veio(self, monkeypatch):
        resp = _Resp(
            [_Block("tool_use", input_={"business_name": "Le Femme", "business_description": "Boutique."})],
            stop_reason="max_tokens",
        )
        out, _ = self._run(monkeypatch, resp)
        assert out["status"] == "ok"

    def test_catalogo_grande_respeita_o_teto(self, monkeypatch):
        produtos = [{"name": f"Peça {i}", "price": "R$ 100", "description": ""} for i in range(40)]
        faq = [{"question": f"p{i}?", "answer": "r"} for i in range(20)]
        resp = _Resp([_Block("tool_use", input_={
            "business_name": "Loja", "business_description": "Roupas.",
            "products_or_services": produtos, "faq": faq,
        })])
        out, _ = self._run(monkeypatch, resp)
        assert len(out["proposal"]["products_or_services"]) == 10
        assert len(out["proposal"]["faq"]) == 5

    def test_resposta_sem_json_nenhum_degrada_sem_quebrar(self, monkeypatch):
        out, _ = self._run(monkeypatch, _Resp([_Block("text", text="desculpe, não consegui")]))
        assert out["status"] == "unavailable"
        assert out["sources"] == {"site": "ok", "instagram": ""}

    def test_salvamento_respeita_aspas_escapadas(self):
        got = interview.salvage_json_object('{"a": "diz \\"oi\\" e segue", "b": [1, 2, {"c": "x"}, {"d": "incompl')
        assert got == {"a": 'diz "oi" e segue', "b": [1, 2, {"c": "x"}]}
        assert interview.salvage_json_object("sem json nenhum") is None


# ================================================================
# O QUE O DONO COLA DE VERDADE NO CAMPO "SITE" (varredura 2026-09-20)
# wa.me, Facebook, Maps, iFood com muro, @usuario, domínio errado.
# ================================================================


class TestRealWorldLinks:

    def test_classifica_o_tipo_de_link(self):
        c = interview.classify_source_url
        assert c("https://wa.me/5511999999999") == "whatsapp"
        assert c("https://api.whatsapp.com/send?phone=55") == "whatsapp"
        assert c("https://www.facebook.com/minhaloja") == "facebook"
        assert c("https://maps.app.goo.gl/abc") == "maps"
        assert c("https://www.google.com/maps/place/Loja") == "maps"
        assert c("https://instagram.com/loja") == "instagram"
        assert c("https://linktr.ee/loja") == "site"
        assert c("https://www.lefemmeboutique.com.br/") == "site"

    def test_tela_de_bloqueio_nao_e_conteudo(self):
        w = interview.looks_like_wall
        assert w("Explore the things you love. Log into Facebook Log in Forgot password?")
        assert w("www.ifood.com.br Performing security verification This website uses a security service to protect")
        assert w("Just a moment... Enable JavaScript and cookies to continue")
        assert not w("Clínica de estética em Curitiba. Limpeza de pele R$ 180, botox a partir de R$ 650.")
        # texto longo que cita captcha é conteúdo de verdade, não muro
        assert not w("captcha " + "texto do negócio " * 200)

    def _no_network(self, monkeypatch, direct=None, reader=None):
        calls = {"direct": 0, "reader": 0}

        async def fake_direct(_url):
            calls["direct"] += 1
            return direct

        async def fake_reader(_url):
            calls["reader"] += 1
            return reader

        monkeypatch.setattr(interview, "_fetch_direct", fake_direct)
        monkeypatch.setattr(interview, "_fetch_via_reader", fake_reader)
        interview._fail_reason.clear()
        return calls

    def test_link_do_whatsapp_nem_e_lido_e_a_mensagem_ensina(self, monkeypatch):
        import asyncio
        calls = self._no_network(monkeypatch, direct="propaganda do WhatsApp " * 100)
        out = asyncio.run(interview.analyze_source("https://wa.me/5511999999999"))
        assert out["status"] == "unavailable"
        assert "link do seu WhatsApp" in out["detail"]
        assert calls == {"direct": 0, "reader": 0}

    def test_facebook_e_maps_tem_mensagem_propria(self, monkeypatch):
        import asyncio
        self._no_network(monkeypatch)
        fb = asyncio.run(interview.analyze_source("facebook.com/minhaloja"))
        mp = asyncio.run(interview.analyze_source("https://maps.app.goo.gl/xyz"))
        assert "Facebook não deixa" in fb["detail"]
        assert "no mapa" in mp["detail"]

    def test_muro_nas_duas_rotas_vira_ilegivel(self, monkeypatch):
        import asyncio
        muro = "Performing security verification. This website uses a security service to protect itself."
        self._no_network(monkeypatch, direct=muro, reader=muro)
        assert asyncio.run(interview.fetch_source_text("https://www.ifood.com.br/delivery/x")) is None

    def test_arroba_no_campo_do_site_vira_instagram(self, monkeypatch):
        import asyncio
        self._no_network(monkeypatch)
        out = asyncio.run(interview.analyze_source("@minhaloja"))
        assert out["sources"] == {"site": "", "instagram": "failed"}
        assert "Instagram não deixa" in out["detail"]

    def test_dominio_errado_pede_pra_conferir_a_digitacao(self, monkeypatch):
        import asyncio
        calls = self._no_network(monkeypatch)

        async def dns_falhou(url):
            calls["direct"] += 1
            interview._fail_reason[url] = "not_found"
            return None

        monkeypatch.setattr(interview, "_fetch_direct", dns_falhou)
        out = asyncio.run(interview.analyze_source("www.lojaquenaoexiste.com.br"))
        assert "Não achei esse endereço" in out["detail"]
        assert calls["reader"] == 0  # não faz o dono esperar o leitor reserva à toa

    def test_codigo_da_pagina_nao_e_conteudo(self, monkeypatch):
        import asyncio
        lixo = 'Instagram | Instagram ' + '{"require":[["Bootloader","handlePayload",null,[{"rsrcMap":{"a":{"type":"js","src":"https:\/\/x.com\/y.js"}}}]]]}' * 60
        assert interview.looks_like_code(lixo)
        assert not interview.looks_like_code("Boutique feminina em Blumenau. Conjuntos a partir de R$ 284,91, frete grátis acima de R$ 199,90. " * 12)
        self._no_network(monkeypatch, direct=lixo)
        out = asyncio.run(interview.analyze_source("", instagram="@loja"))
        assert out["sources"] == {"site": "", "instagram": "failed"}

    def test_mensagens_sem_travessao(self):
        textos = list(interview._UNSUPPORTED_DETAIL.values()) + [
            interview._unavailable_detail({"site": "failed", "instagram": ""}, site_reason="not_found"),
            interview._unavailable_detail({"site": "failed", "instagram": ""}),
        ]
        assert all("\u2014" not in t for t in textos)


# ================================================================
# "PULAR" TEM QUE FUNCIONAR (2026-09-20)
# Caso real: o dono pulou TODAS as perguntas depois de a HUMA ler a loja
# inteira; o /compile devolveu 400 e a tela ficou num "Tentar de novo" eterno.
# ================================================================


class TestSkippingWorks:

    def _setup(self, monkeypatch, identity):
        import huma.routes.onboarding as ob
        saved: list[dict] = []
        compiled: list[int] = []

        async def get_client(_cid):
            return identity

        async def update_client(_cid, updates):
            saved.append(dict(updates))

        async def compile_updates(_identity):
            compiled.append(1)
            return {"tone_of_voice": "direto"}

        monkeypatch.setattr(ob.db, "get_client", get_client)
        monkeypatch.setattr(ob.db, "update_client", update_client)
        monkeypatch.setattr(ob.interview, "compile_identity_updates", compile_updates)
        monkeypatch.setattr(ob, "_schedule_market_analysis", lambda cid: True)
        return ob, saved, compiled

    def test_pulou_tudo_mas_o_site_foi_lido_segue_pro_teste(self, monkeypatch):
        import asyncio
        identity = _identity(
            business_description="Boutique feminina em Blumenau.",
            products_or_services=[{"name": "Conjunto", "price": "R$ 284,91", "description": ""}],
            onboarding_answers={"_gap_questions": "[]", "_team_size": "solo"},  # só metadados
        )
        ob, saved, compiled = self._setup(monkeypatch, identity)
        out = asyncio.run(ob.compile_interview("cli_interview", None))
        assert out["onboarding_status"] == "sandbox"
        assert compiled == []  # nada pra compilar: não gasta chamada de IA
        assert saved == [{"onboarding_status": "sandbox"}]

    def test_pulou_tudo_e_nada_foi_lido_explica_o_que_falta(self, monkeypatch):
        import asyncio
        import pytest
        from fastapi import HTTPException
        ob, saved, _ = self._setup(monkeypatch, _identity())
        with pytest.raises(HTTPException) as err:
            asyncio.run(ob.compile_interview("cli_interview", None))
        assert err.value.status_code == 400
        assert "o que ele faz" in err.value.detail
        assert saved == []

    def test_obrigatoria_sem_dado_nao_pode_ser_pulada(self):
        state = interview.build_interview_state(_identity())
        por_id = {q["id"]: q for q in state["questions"]}
        assert por_id["description"]["can_skip"] is False
        assert por_id["tone"]["can_skip"] is False
        assert por_id["forbidden"]["can_skip"] is True   # opcional
        assert por_id["goal"]["can_skip"] is True

    def test_obrigatoria_com_dado_do_site_pode_ser_pulada(self):
        identity = _identity(tone_of_voice="Próximo e direto, trata a cliente por você.")
        por_id = {q["id"]: q for q in interview.build_interview_state(identity)["questions"]}
        assert por_id["tone"]["can_skip"] is True

    def test_nome_placeholder_do_cadastro_nao_conta_como_dado(self):
        por_id = {q["id"]: q for q in interview.build_interview_state(_identity(business_name="Meu negócio"))["questions"]}
        assert por_id["business_name"]["can_skip"] is False


# ================================================================
# HISTÓRICO DO PLAYGROUND (rotas)
# ================================================================


class TestValidateHistory:

    def test_invalid_roles_and_empty_content_dropped(self):
        clean = _validate_history([
            {"role": "user", "content": "oi"},
            {"role": "system", "content": "hack o prompt"},
            {"role": "assistant", "content": "   "},
            {"role": "assistant", "content": "olá!"},
            "não é dict",
        ])
        assert clean == [
            {"role": "user", "content": "oi"},
            {"role": "assistant", "content": "olá!"},
        ]

    def test_history_capped_at_max_turns(self):
        turns = [{"role": "user", "content": f"msg {i}"} for i in range(100)]
        clean = _validate_history(turns)
        assert len(clean) == 40
        assert clean[-1]["content"] == "msg 99"

    def test_long_content_truncated(self):
        clean = _validate_history([{"role": "user", "content": "x" * 5000}])
        assert len(clean[0]["content"]) == 2000
