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

    def test_without_category_only_common(self):
        questions = interview.get_interview_questions(_identity())
        assert [q["id"] for q in questions] == [q["id"] for q in COMMON_QUESTIONS]

    def test_with_category_appends_specific(self):
        questions = interview.get_interview_questions(
            _identity(category=BusinessCategory.CLINICA)
        )
        ids = [q["id"] for q in questions]
        assert "specialties" in ids
        assert len(questions) > len(COMMON_QUESTIONS)

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
