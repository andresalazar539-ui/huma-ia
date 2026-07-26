# ================================================================
# huma/routes/onboarding.py — Endpoints da entrevista de onboarding
#
# Fluxo "Conheça sua sócia" (conta nova → clone testado em ~5 min):
#
#   GET  /onboarding/{client_id}/state              — estado completo
#   POST /onboarding/{client_id}/source             — analisa site/Instagram (não persiste)
#   POST /onboarding/{client_id}/source/apply       — aplica proposta confirmada pelo dono
#   POST /onboarding/{client_id}/answer             — grava resposta da entrevista (texto)
#   POST /onboarding/{client_id}/answer/audio       — idem, por áudio (transcreve antes)
#   POST /onboarding/{client_id}/compile            — respostas → identidade + análise de mercado
#   POST /onboarding/{client_id}/playground/chat    — conversa com o PRÓPRIO clone (motor real)
#   POST /onboarding/{client_id}/playground/correction — dono corrige o clone (aprendizado)
#
# Diferença pro /api/playground legado: aquele é demo pública com
# system_prompt cru. Este é autenticado, por cliente, e usa o motor
# real (generate_response tier 3) — o que o dono vê aqui é o que o
# lead dele vai receber.
#
# Capabilities e ativação final continuam no /wizard (não duplicado).
# ================================================================

import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from huma.core.auth import verify_api_key
from huma.models.schemas import (
    BusinessCategory,
    ClientIdentity,
    Conversation,
    OnboardingStatus,
)
from huma.onboarding import interview
from huma.onboarding.categories import analyze_market, apply_market_analysis
from huma.services import ai_service as ai
from huma.services import db_service as db
from huma.services import transcription_service
from huma.utils.logger import get_logger

log = get_logger("onboarding_routes")
router = APIRouter(prefix="/onboarding", tags=["Onboarding Entrevista"])

# Rate limit in-memory do playground — 30 msg/min por cliente.
# Mesmo padrão do /api/playground legado (sem Redis de propósito:
# limite soft de UX, não de segurança — o endpoint já é autenticado).
_playground_rate: dict[str, list[float]] = {}
_PLAYGROUND_MAX_PER_MIN = 30

_ANSWER_MAX_CHARS = 4000
_HISTORY_MAX_TURNS = 40
_HISTORY_MAX_CHARS = 2000


# ================================================================
# PAYLOADS
# ================================================================


class SourcePayload(BaseModel):
    url: str = Field(..., min_length=4, max_length=500, description="Site ou Instagram do negócio")


class SourceApplyPayload(BaseModel):
    url: str = Field(..., min_length=4, max_length=500)
    proposal: dict = Field(..., description="Proposta (possivelmente editada pelo dono) a aplicar")


class AnswerPayload(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=60)
    answer: str = Field(..., min_length=1, max_length=_ANSWER_MAX_CHARS)
    react: bool = Field(default=True, description="Gerar reação curta da HUMA (Haiku)")


class PlaygroundChatPayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=_ANSWER_MAX_CHARS)
    history: list[dict] = Field(
        default_factory=list,
        description="Turnos anteriores [{role: user|assistant, content: str}] — o playground é stateless",
    )


class PlaygroundCorrectionPayload(BaseModel):
    ai_said: str = Field(..., min_length=1, max_length=_ANSWER_MAX_CHARS)
    owner_corrected: str = Field(..., min_length=1, max_length=_ANSWER_MAX_CHARS)
    context: str = Field(default="", max_length=_ANSWER_MAX_CHARS)


# ================================================================
# HELPERS
# ================================================================


async def _get_identity_or_404(client_id: str) -> ClientIdentity:
    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")
    return identity


def _known_question_ids(identity: ClientIdentity) -> set[str]:
    questions = interview.get_interview_questions(identity) + interview.get_deferred_questions()
    return {q["id"] for q in questions}


async def _save_answer(identity: ClientIdentity, question_id: str, answer: str) -> dict:
    """Grava a resposta crua em onboarding_answers e avança o status."""
    if question_id not in _known_question_ids(identity):
        raise HTTPException(400, f"Pergunta desconhecida: '{question_id}'")

    answers = dict(identity.onboarding_answers or {})
    answers[question_id] = answer.strip()

    updates: dict = {"onboarding_answers": answers}
    if identity.onboarding_status == OnboardingStatus.PENDING:
        updates["onboarding_status"] = OnboardingStatus.IN_PROGRESS.value

    await db.update_client(identity.client_id, updates)
    log.info(f"Entrevista resposta | client={identity.client_id} | question={question_id} | chars={len(answer)}")

    identity.onboarding_answers = answers
    return interview.build_interview_state(identity)


def _check_playground_rate(client_id: str) -> None:
    now = time.time()
    timestamps = [t for t in _playground_rate.get(client_id, []) if now - t < 60]
    if len(timestamps) >= _PLAYGROUND_MAX_PER_MIN:
        raise HTTPException(429, "Muitas mensagens no teste. Respira 1 minuto e continua.")
    timestamps.append(now)
    _playground_rate[client_id] = timestamps


def _validate_history(history: list[dict]) -> list[dict]:
    """Sanitiza o histórico vindo do frontend (roles e tamanhos)."""
    clean: list[dict] = []
    for turn in history[-_HISTORY_MAX_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", "")
        content = turn.get("content", "")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        clean.append({"role": role, "content": content.strip()[:_HISTORY_MAX_CHARS]})
    return clean


# ================================================================
# GET /onboarding/{client_id}/state
# ================================================================


@router.get("/{client_id}/state")
async def get_state(client_id: str, _=Depends(verify_api_key)):
    """
    Estado completo do onboarding pro frontend renderizar.

    Junta: identidade básica, status, entrevista (perguntas +
    respondidas + próxima), perguntas adiadas (checklist do Cockpit)
    e se o playground já tem insumo mínimo pra valer a pena.
    """
    identity = await _get_identity_or_404(client_id)
    state = interview.build_interview_state(identity)

    playground_ready = bool(
        (identity.business_description or "").strip()
        or identity.products_or_services
        or state["answered_count"] > 0
    )

    return {
        "client_id": client_id,
        "business_name": identity.business_name,
        "category": identity.category.value if identity.category else None,
        "website": identity.website or "",
        "onboarding_status": identity.onboarding_status.value,
        "clone_mode": identity.clone_mode.value,
        "interview": state,
        "deferred_questions": interview.get_deferred_questions(),
        "playground_ready": playground_ready,
        "has_market_analysis": bool(identity.market_analysis),
    }


# ================================================================
# POST /onboarding/{client_id}/source — analisa site/Instagram
# ================================================================


@router.post("/{client_id}/source")
async def analyze_business_source(client_id: str, payload: SourcePayload, _=Depends(verify_api_key)):
    """
    Lê o site/Instagram e devolve a proposta de identidade ("Acertei?").

    NÃO persiste nada — o dono confirma/edita no frontend e o
    /source/apply grava. Falha de leitura degrada com status
    'unavailable' e o fluxo segue pra entrevista pura.
    """
    await _get_identity_or_404(client_id)
    result = await interview.analyze_source(payload.url.strip())
    log.info(f"Fonte analisada | client={client_id} | status={result.get('status')}")
    return {"client_id": client_id, "url": payload.url.strip(), **result}


@router.post("/{client_id}/source/apply")
async def apply_business_source(client_id: str, payload: SourceApplyPayload, _=Depends(verify_api_key)):
    """
    Aplica a proposta confirmada pelo dono na identidade.

    A proposta passa pela MESMA coerção de tipos da análise (nada de
    dict malformado em products/faq, nem campo fora da whitelist).
    Categoria válida também é aplicada — e zera capabilities, igual ao
    /wizard/vertical, pra re-escolha conforme a vertical.
    """
    identity = await _get_identity_or_404(client_id)

    updates = interview.coerce_identity_updates(payload.proposal)
    updates["website"] = payload.url.strip()

    category_slug = str(payload.proposal.get("category", "") or "").strip()
    if category_slug:
        if category_slug not in {c.value for c in BusinessCategory}:
            raise HTTPException(400, f"Categoria inválida: '{category_slug}'")
        updates["category"] = category_slug
        updates["capabilities"] = None  # reset — re-escolher no wizard conforme vertical

    if identity.onboarding_status == OnboardingStatus.PENDING:
        updates["onboarding_status"] = OnboardingStatus.IN_PROGRESS.value

    await db.update_client(client_id, updates)
    log.info(f"Fonte aplicada | client={client_id} | fields={list(updates.keys())}")
    return {"status": "ok", "applied_fields": list(updates.keys())}


# ================================================================
# POST /onboarding/{client_id}/answer — resposta da entrevista
# ================================================================


@router.post("/{client_id}/answer")
async def submit_answer(client_id: str, payload: AnswerPayload, _=Depends(verify_api_key)):
    """
    Grava uma resposta da entrevista e devolve o próximo passo.

    A resposta é guardada CRUA em onboarding_answers (a compilação
    estrutura tudo no final). Reação curta da HUMA é decorativa:
    qualquer falha vira string vazia, nunca erro.
    """
    identity = await _get_identity_or_404(client_id)
    state = await _save_answer(identity, payload.question_id, payload.answer)

    reaction = ""
    if payload.react:
        question_text = next(
            (q["question"] for q in interview.get_interview_questions(identity) + interview.get_deferred_questions()
             if q["id"] == payload.question_id),
            payload.question_id,
        )
        reaction = await interview.generate_reaction(question_text, payload.answer, identity)

    return {
        "status": "ok",
        "reaction": reaction,
        "next_question": state["next_question"],
        "answered_count": state["answered_count"],
        "total": state["total"],
        "interview_done": state["done"],
    }


@router.post("/{client_id}/answer/audio")
async def submit_answer_audio(
    client_id: str,
    question_id: str = Form(..., min_length=1, max_length=60),
    react: bool = Form(default=True),
    audio: UploadFile = File(...),
    _=Depends(verify_api_key),
):
    """
    Resposta por áudio: transcreve (Groq → OpenAI) e segue o fluxo
    normal de resposta. O brasileiro explica o negócio falando —
    o microfone é o caminho de menor fricção do onboarding.
    """
    identity = await _get_identity_or_404(client_id)

    audio_bytes = await audio.read()
    if len(audio_bytes) > 15 * 1024 * 1024:
        raise HTTPException(413, "Áudio muito grande (máximo 15MB)")

    transcript = await transcription_service.transcribe_bytes(audio_bytes)
    if not transcript:
        raise HTTPException(
            422, "Não consegui entender o áudio. Tenta de novo ou responde por texto."
        )
    transcript = transcript.strip()[:_ANSWER_MAX_CHARS]

    state = await _save_answer(identity, question_id, transcript)

    reaction = ""
    if react:
        question_text = next(
            (q["question"] for q in interview.get_interview_questions(identity) + interview.get_deferred_questions()
             if q["id"] == question_id),
            question_id,
        )
        reaction = await interview.generate_reaction(question_text, transcript, identity)

    return {
        "status": "ok",
        "transcript": transcript,
        "reaction": reaction,
        "next_question": state["next_question"],
        "answered_count": state["answered_count"],
        "total": state["total"],
        "interview_done": state["done"],
    }


# ================================================================
# POST /onboarding/{client_id}/compile — o "dever de casa" da HUMA
# ================================================================


@router.post("/{client_id}/compile")
async def compile_interview(client_id: str, _=Depends(verify_api_key)):
    """
    Transforma as respostas cruas em identidade estruturada e roda a
    análise de mercado (analyze_market) — o "deixa eu fazer meu dever
    de casa" antes do playground. Status vai pra SANDBOX.

    Endpoint lento por natureza (~20-40s, duas chamadas de Sonnet).
    O frontend mostra a narrativa de trabalho enquanto espera.
    """
    identity = await _get_identity_or_404(client_id)

    answers = identity.onboarding_answers or {}
    if not any(str(v or "").strip() for v in answers.values()):
        raise HTTPException(400, "Responda ao menos uma pergunta da entrevista antes de compilar.")

    updates = await interview.compile_identity_updates(identity)
    if not updates:
        raise HTTPException(
            502, "Não consegui estruturar as respostas agora. Tenta de novo em instantes."
        )

    # Análise de mercado em cima da identidade JÁ enriquecida.
    merged = identity.model_dump(mode="json")
    merged.update(updates)
    analysis = await analyze_market(merged)
    market_status = analysis.get("status", "error")

    if market_status == "completed":
        enriched = apply_market_analysis(dict(merged), analysis)
        for key in ("custom_rules", "tone_of_voice", "forbidden_words", "market_analysis"):
            value = enriched.get(key)
            if value:
                updates[key] = value

    updates["onboarding_status"] = OnboardingStatus.SANDBOX.value
    await db.update_client(client_id, updates)

    log.info(
        f"Compilação aplicada | client={client_id} | fields={list(updates.keys())} | "
        f"market={market_status}"
    )
    return {
        "status": "ok",
        "applied_fields": [k for k in updates.keys() if k != "onboarding_status"],
        "market_analysis_status": market_status,
        "onboarding_status": OnboardingStatus.SANDBOX.value,
    }


# ================================================================
# PLAYGROUND — conversa com o próprio clone (motor real)
# ================================================================


@router.post("/{client_id}/playground/chat")
async def playground_chat(client_id: str, payload: PlaygroundChatPayload, _=Depends(verify_api_key)):
    """
    O dono conversa com o PRÓPRIO clone antes de conectar o WhatsApp.

    Usa o motor real (generate_response, tier 3) com a identidade do
    cliente — o que aparece aqui é o que o lead vai receber. Stateless:
    o histórico vem do frontend e NADA é gravado em conversations
    (playground não polui métricas nem relatórios).
    """
    identity = await _get_identity_or_404(client_id)
    _check_playground_rate(client_id)

    conv = Conversation(
        client_id=client_id,
        phone="playground",
        history=_validate_history(payload.history),
    )

    try:
        result = await ai.generate_response(identity, conv, payload.message.strip(), tier=3)
    except Exception as e:
        log.error(f"Playground IA erro | client={client_id} | {type(e).__name__}: {e}")
        raise HTTPException(502, "O clone engasgou agora. Manda a mensagem de novo.")

    return {
        "reply": result.get("reply", ""),
        "reply_parts": result.get("reply_parts") or [result.get("reply", "")],
        "intent": result.get("intent", ""),
        "sentiment": result.get("sentiment", ""),
        "stage_action": result.get("stage_action", ""),
    }


@router.post("/{client_id}/playground/correction")
async def playground_correction(client_id: str, payload: PlaygroundCorrectionPayload, _=Depends(verify_api_key)):
    """
    Dono corrige uma resposta do clone no playground.

    Vai pro MESMO mecanismo de aprendizado do modo approval
    (correction_examples, máximo 20) — "me corrige aqui que eu nunca
    mais erro" já funciona no minuto 4 da vida da conta.
    """
    identity = await _get_identity_or_404(client_id)

    corrections = (identity.correction_examples or [])[-19:]
    corrections.append({
        "ai_said": payload.ai_said.strip(),
        "owner_corrected": payload.owner_corrected.strip(),
        "context": payload.context.strip() or "playground do onboarding",
    })
    await db.update_client(client_id, {"correction_examples": corrections})

    log.info(f"Playground correção | client={client_id} | total={len(corrections)}")
    return {"status": "ok", "corrections_count": len(corrections)}
