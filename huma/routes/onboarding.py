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
#   POST /onboarding/{client_id}/compile            — respostas → identidade (análise de mercado segue em background)
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

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from huma.core import playground_demo
from huma.core.auth import SESSION_COOKIE_NAME, verify_api_key, verify_session_token
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
from huma.utils.analytics import inject_gtm
from huma.utils.logger import get_logger

log = get_logger("onboarding_routes")
router = APIRouter(prefix="/onboarding", tags=["Onboarding Entrevista"])

ONBOARDING_HTML = Path(__file__).resolve().parent.parent / "static" / "onboarding" / "Onboarding.html"

# Rate limit in-memory do playground — 30 msg/min por cliente.
# Mesmo padrão do /api/playground legado (sem Redis de propósito:
# limite soft de UX, não de segurança — o endpoint já é autenticado).
_playground_rate: dict[str, list[float]] = {}
_PLAYGROUND_MAX_PER_MIN = 30

_ANSWER_MAX_CHARS = 4000
_TRANSCRIPTION_HINT = "Entrevista de cadastro da HUMA, a IA de atendimento. Termos: HUMA, WhatsApp, Instagram, Pix, Cockpit."
_HISTORY_MAX_TURNS = 40
_HISTORY_MAX_CHARS = 2000


# ================================================================
# PAYLOADS
# ================================================================


class SourcePayload(BaseModel):
    # 2026-09-20: site E Instagram em campos separados; pelo menos um é
    # obrigatório (validado na rota, com erro que ensina). `url` sozinho
    # continua valendo (contrato antigo do front).
    url: str = Field(default="", max_length=500, description="Site do negócio")
    instagram: str = Field(default="", max_length=200, description="@ ou link do Instagram")


class SourceApplyPayload(BaseModel):
    url: str = Field(default="", max_length=500)
    instagram: str = Field(default="", max_length=200)
    proposal: dict = Field(..., description="Proposta (possivelmente editada pelo dono) a aplicar")


class ProfilePayload(BaseModel):
    """Quem é o dono e como quer trabalhar. Tudo opcional: só grava o que vier."""
    owner_name: str | None = Field(default=None, max_length=80)
    team_size: str | None = Field(default=None, max_length=10)
    voice_pref: str | None = Field(default=None, max_length=10)


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
# GET /onboarding/page — a página do fluxo "Conheça sua sócia"
# ================================================================


@router.get("/page", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    """
    Serve o onboarding standalone (React via CDN, arquivos em
    /static/onboarding/). Mesmo padrão do /cockpit: injeta
    window.HUMA_CLIENT_ID da sessão logada.

    Diferença do Cockpit: sem sessão válida redireciona pro /login —
    sem client_id o api.js entraria em modo demo, e demo em produção
    confunde (o dono acharia que configurou e nada foi salvo).
    """
    session_client = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))
    if not session_client:
        return RedirectResponse("/login", status_code=307)

    html = ONBOARDING_HTML.read_text(encoding="utf-8")
    inject = f"<script>window.HUMA_CLIENT_ID = {json.dumps(session_client)};</script>"
    html = html.replace("<head>", "<head>" + inject, 1)
    # GTM por último (ancora antes de </head>, depois do HUMA_CLIENT_ID)
    html = inject_gtm(html)
    return HTMLResponse(content=html)


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

    # Auto-cura: a análise de mercado roda em background depois do /compile.
    # Se o processo reiniciou no meio (deploy), o playbook ficaria faltando
    # pra sempre e em silêncio. Já compilou e ainda não tem análise = agenda
    # de novo (a guarda de _run_market_analysis impede corrida e repetição).
    compiled = identity.onboarding_status in (OnboardingStatus.SANDBOX, OnboardingStatus.ACTIVE)
    if compiled and not identity.market_analysis and state["answered_count"] > 0:
        _schedule_market_analysis(client_id)

    meta = identity.onboarding_answers or {}
    return {
        "client_id": client_id,
        "business_name": identity.business_name,
        # Quem é o dono e como quer trabalhar (POST /profile)
        "owner_name": getattr(identity, "owner_name", "") or "",
        "team_size": meta.get(interview.META_TEAM_SIZE, ""),
        "voice_pref": meta.get(interview.META_VOICE_PREF, ""),
        "instagram": meta.get(interview.META_INSTAGRAM, ""),
        "capabilities": sorted(c.value for c in identity.capabilities_resolved),
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
# POST /onboarding/{client_id}/profile — quem é o dono (2026-09-20)
# ================================================================


@router.post("/{client_id}/profile")
async def save_profile(client_id: str, payload: ProfilePayload, _=Depends(verify_api_key)):
    """
    Grava o nome do dono, o tamanho da equipe e a preferência de voz.

    owner_name vai na coluna que já existe (a mesma da tela de Perfil: é
    o "Boa noite, André." do Cockpit e o nome nos avisos ao dono).
    team_size e voice_pref são metadados em onboarding_answers (zero
    migration): informativos, NÃO limitam nada.
    """
    identity = await _get_identity_or_404(client_id)
    updates: dict = {}

    if payload.owner_name is not None:
        name = " ".join(payload.owner_name.split())[:80]
        if name:
            updates["owner_name"] = name

    meta = dict(identity.onboarding_answers or {})
    if payload.team_size is not None:
        if payload.team_size not in interview.VALID_TEAM_SIZES:
            raise HTTPException(400, "Tamanho de equipe inválido.")
        meta[interview.META_TEAM_SIZE] = payload.team_size
    if payload.voice_pref is not None:
        if payload.voice_pref not in interview.VALID_VOICE_PREFS:
            raise HTTPException(400, "Preferência de voz inválida.")
        meta[interview.META_VOICE_PREF] = payload.voice_pref
    if meta != (identity.onboarding_answers or {}):
        updates["onboarding_answers"] = meta

    if not updates:
        return {"status": "ok", "applied_fields": []}
    await db.update_client(client_id, updates)
    log.info(f"Perfil do dono | client={client_id} | fields={sorted(updates.keys())}")
    return {"status": "ok", "applied_fields": sorted(updates.keys())}


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
    url, instagram = payload.url.strip(), payload.instagram.strip()
    if len(url) < 4 and len(instagram) < 2:
        raise HTTPException(400, "Me passa o site ou o Instagram do seu negócio (pelo menos um dos dois).")
    result = await interview.analyze_source(url, instagram=instagram)
    log.info(
        f"Fonte analisada | client={client_id} | status={result.get('status')} | "
        f"site={bool(url)} | instagram={bool(instagram)}"
    )
    return {"client_id": client_id, "url": url, "instagram": instagram, **result}


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
    url, instagram = payload.url.strip(), payload.instagram.strip()
    # `website` é UMA url (o playbook relê daqui): site quando há, senão o Instagram.
    insta_url = interview._instagram_url(instagram)
    if url or insta_url:
        updates["website"] = url or insta_url

    # Metadados da entrevista (zero migration): o Instagram informado e as
    # perguntas de lacuna que a leitura gerou. Viram perguntas em
    # interview.get_interview_questions.
    meta = dict(identity.onboarding_answers or {})
    if insta_url:
        meta[interview.META_INSTAGRAM] = insta_url
    gaps = interview.coerce_gap_questions(payload.proposal.get("open_questions"))
    if gaps:
        meta[interview.META_GAP_QUESTIONS] = json.dumps([g["question"] for g in gaps], ensure_ascii=False)
    if meta != (identity.onboarding_answers or {}):
        updates["onboarding_answers"] = meta

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

    # hint: o dono fala "a HUMA" o tempo todo e o Whisper escrevia "a uma"
    transcript = await transcription_service.transcribe_bytes(audio_bytes, hint=_TRANSCRIPTION_HINT)
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
    Transforma as respostas cruas em identidade estruturada e libera o
    playground (status SANDBOX). A análise de mercado + playbook roda em
    SEGUNDO PLANO (2026-09-20): medida em produção levava ~95s dos ~110s
    do endpoint, com o dono parado na tela achando que travou. O clone já
    conversa sem o playbook; quando ele chega, entra sozinho no prompt.

    Endpoint ainda lento (~15-25s, uma chamada de Sonnet). O frontend
    mostra a narrativa de trabalho enquanto espera.
    """
    identity = await _get_identity_or_404(client_id)

    answers = interview.real_answers(identity.onboarding_answers)
    has_answers = any(str(v or "").strip() for v in answers.values())

    if has_answers:
        updates = await interview.compile_identity_updates(identity)
        if not updates:
            raise HTTPException(
                502, "Não consegui estruturar as respostas agora. Tenta de novo em instantes."
            )
    elif interview.has_minimum_identity(identity):
        # Dono pulou TODAS as perguntas, mas a leitura do site já trouxe o
        # negócio (descrição/produtos): não há o que compilar, e isso não é
        # erro. Antes devolvia 400 e a tela ficava num "Tentar de novo" eterno.
        log.info(f"Compilação sem respostas | client={client_id} | segue com o que a leitura do site trouxe")
        updates = {}
    else:
        # Sem respostas E sem nada lido: o clone não saberia onde trabalha.
        raise HTTPException(
            400,
            "Ainda não sei o que é o seu negócio. Me responde pelo menos o que ele faz, "
            "que com isso eu já consigo montar o seu atendimento.",
        )

    updates["onboarding_status"] = OnboardingStatus.SANDBOX.value
    await db.update_client(client_id, updates)

    # Análise de mercado em cima da identidade JÁ enriquecida (relida do
    # banco dentro da task), sem segurar a resposta HTTP.
    market_status = "running" if _schedule_market_analysis(client_id) else "error"

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
# Análise de mercado em segundo plano (pós-compilação)
# ================================================================

# Referência forte das tasks: o event loop só guarda referência fraca e
# uma task sem dono pode ser coletada no meio da chamada de IA.
_market_tasks: set[asyncio.Task] = set()

# Guarda contra rodar duas vezes pro mesmo cliente (compile + auto-cura do
# /state). TTL folgado: a análise mais lenta medida levou ~95s. NÃO é liberada
# ao terminar de propósito: se a IA estiver falhando, a auto-cura tenta no
# máximo uma vez a cada 5 min em vez de uma chamada de Sonnet por consulta.
_MARKET_RUNNING_KEY = "onboarding:{client_id}:market_running"
_MARKET_RUNNING_TTL = 300
_market_started: dict[str, float] = {}


def _schedule_market_analysis(client_id: str) -> bool:
    """Fire-and-forget da análise de mercado. True se a task foi agendada."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning(f"Análise de mercado sem event loop | client={client_id}")
        return False
    task = loop.create_task(_run_market_analysis(client_id))
    _market_tasks.add(task)
    task.add_done_callback(_market_tasks.discard)
    return True


async def _market_already_running(client_id: str) -> bool:
    """True se outra task já está rodando pra esse cliente (marca a janela se não)."""
    # Memória do processo primeiro: vale mesmo sem Redis (que é opcional).
    now = time.time()
    started = _market_started.get(client_id, 0.0)
    if now - started < _MARKET_RUNNING_TTL:
        return True
    _market_started[client_id] = now
    try:
        from huma.services import redis_service as cache
        key = _MARKET_RUNNING_KEY.format(client_id=client_id)
        if await cache.exists(key):
            return True
        await cache.set_with_ttl(key, "1", ttl=_MARKET_RUNNING_TTL)
    except Exception as e:  # Redis é opcional: sem ele, roda sem a guarda
        log.warning(f"Análise de mercado | guarda indisponível | client={client_id} | {type(e).__name__}: {e}")
    return False


async def _run_market_analysis(client_id: str) -> dict:
    """
    Roda analyze_market e aplica o resultado na identidade. Nunca levanta.

    Relê o cliente DEPOIS da chamada de IA e aplica em cima do dado fresco:
    nos ~90s de espera o dono está no playground e pode corrigir tom/regras;
    gravar a foto tirada antes da análise apagaria essas mudanças.

    Returns:
        {"status": "completed"|"skipped"|"partial"|"error", "detail": str}
    """
    if await _market_already_running(client_id):
        log.info(f"Análise de mercado | já em andamento | client={client_id}")
        return {"status": "skipped", "detail": "running"}

    try:
        identity = await db.get_client(client_id)
        if identity is None:
            return {"status": "skipped", "detail": "client_not_found"}

        # F3: passa o texto do site (se houver) pra instanciar o playbook com
        # fatos reais do negócio. Falha de leitura não bloqueia: segue sem site.
        source_text = await _fetch_site_text(identity.website)
        analysis = await analyze_market(identity.model_dump(mode="json"), source_text=source_text)
        market_status = analysis.get("status", "error")
        if market_status != "completed":
            log.error(
                f"Análise de mercado em background falhou | client={client_id} | status={market_status} | "
                f"detail={str(analysis.get('detail', ''))[:300]}"
            )
            return {"status": market_status, "detail": str(analysis.get("detail", ""))[:300]}

        fresh = await db.get_client(client_id)
        if fresh is None:
            return {"status": "skipped", "detail": "client_not_found"}
        enriched = apply_market_analysis(fresh.model_dump(mode="json"), analysis)
        updates: dict = {}
        for key in ("custom_rules", "tone_of_voice", "forbidden_words", "market_analysis"):
            value = enriched.get(key)
            if value:
                updates[key] = value
        if updates:
            await db.update_client(client_id, updates)

        log.info(
            f"Análise de mercado em background OK | client={client_id} | "
            f"fields={list(updates.keys())} | site_chars={len(source_text)}"
        )
        return {"status": "completed", "detail": ""}
    except Exception as e:
        log.error(f"Análise de mercado em background erro | client={client_id} | {type(e).__name__}: {e}")
        return {"status": "error", "detail": type(e).__name__}


# ================================================================
# POST /onboarding/{client_id}/playbook — reprocessa o playbook (F3)
#
# Pra clientes que fizeram onboarding ANTES do cérebro da vertical
# existir, ou quando o dono atualizou site/produtos. Só regrava
# market_analysis: tom, palavras proibidas e custom_rules do dono
# ficam intactos (diferente do /compile, que é a primeira vez).
# ================================================================


async def _fetch_site_text(website: str | None) -> str:
    """Lê o site/Instagram do negócio pro playbook. "" em qualquer falha."""
    url = (website or "").strip()
    if not url:
        return ""
    try:
        return (await interview.fetch_source_text(url)) or ""
    except Exception as e:  # leitura do site nunca bloqueia o onboarding
        log.warning(f"Site ilegível pro playbook | url={url} | {type(e).__name__}: {e}")
        return ""


@router.post("/{client_id}/playbook")
async def rebuild_playbook(client_id: str, _=Depends(verify_api_key)):
    """
    Regera análise de mercado + playbook do negócio (cérebro da vertical
    instanciado no cliente) e grava APENAS market_analysis.

    Endpoint lento (~15-30s, uma chamada de Sonnet).
    """
    identity = await _get_identity_or_404(client_id)
    if not identity.category:
        raise HTTPException(400, "Defina a categoria do negócio antes de gerar o playbook.")

    data = identity.model_dump(mode="json")
    source_text = await _fetch_site_text(identity.website)
    analysis = await analyze_market(data, source_text=source_text)
    if analysis.get("status") != "completed":
        log.error(
            f"Playbook falhou | client={client_id} | status={analysis.get('status')} | "
            f"detail={str(analysis.get('detail', ''))[:300]} | site_chars={len(source_text)}"
        )
        raise HTTPException(502, "Não consegui gerar o playbook agora. Tenta de novo em instantes.")

    market = analysis.get("analysis") or {}
    await db.update_client(client_id, {"market_analysis": market})

    playbook = market.get("playbook") if isinstance(market.get("playbook"), dict) else {}
    lacunas = playbook.get("lacunas") or []
    log.info(
        f"Playbook regerado | client={client_id} | site_chars={len(source_text)} | "
        f"objecoes={len(playbook.get('objecoes') or [])} | lacunas={len(lacunas)}"
    )
    return {
        "status": "ok",
        "has_playbook": bool(playbook),
        "used_site": bool(source_text),
        "lacunas": lacunas,
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

    # Modo demonstração (2026-09-20): o playground não executa actions e a
    # agenda/pagamento/loja nem estão conectados aqui. Sem o marker o clone
    # ficava preso em "deixa eu verificar a agenda". Mesmo mecanismo dos
    # markers do orchestrator: entrada `assistant` no fim do histórico.
    history = _validate_history(payload.history)
    message = payload.message.strip()
    caps = {c.value for c in identity.capabilities_resolved}
    last_assistant = next((h["content"] for h in reversed(history) if h["role"] == "assistant"), "")
    history.append({"role": "assistant", "content": playground_demo.build_demo_marker(caps)})

    # Cards de produto DENTRO do teste (2026-09-20). O motor real mostra os cards
    # num segundo turno; aqui esse turno não existia e o dono via "Segura aí!" e
    # mais nada. Camada 1: o cliente pediu pra ver (ou aceitou a oferta de ver) →
    # os cards entram ANTES da resposta e a IA já comenta eles, numa chamada só.
    cards: list[dict] = []
    if playground_demo.wants_products(message, last_assistant):
        cards = playground_demo.demo_cards(identity.products_or_services, f"{last_assistant} {message}")
        if cards:
            history.append({"role": "assistant", "content": playground_demo.build_cards_marker(cards)})

    conv = Conversation(client_id=client_id, phone="playground", history=history)

    try:
        result = await ai.generate_response(identity, conv, message, tier=3)

        # Camada 2 (rede de segurança): a resposta ainda pede pra esperar, ou
        # emitiu uma ação de mostrar que aqui ninguém executa → roda o segundo
        # turno na hora. O teste NUNCA deixa o dono no vácuo.
        # Com os cards já na tela, a ação show_products é redundante (não é vácuo):
        # só o TEXTO pedindo pra esperar dispara o segundo turno.
        first_actions = None if cards else result.get("actions")
        if playground_demo.is_placeholder_reply(result.get("reply", ""), first_actions):
            if not cards:
                cards = playground_demo.demo_cards(identity.products_or_services, f"{last_assistant} {message}") \
                    if (playground_demo.wants_products(message, last_assistant)
                        or any((a or {}).get("type") in ("show_products", "send_media") for a in (result.get("actions") or []))) \
                    else []
            marker2 = playground_demo.build_cards_marker(cards) if cards else playground_demo.NO_WAIT_MARKER
            conv2 = Conversation(client_id=client_id, phone="playground", history=history + [
                {"role": "assistant", "content": marker2},
            ])
            log.info(f"Playground | resposta pedia pra esperar, rodando 2º turno | client={client_id} | cards={len(cards)}")
            second = await ai.generate_response(identity, conv2, message, tier=3, followup_hint=marker2)
            if playground_demo.is_placeholder_reply(second.get("reply", ""), None):
                text = playground_demo.safety_reply(cards)
                second = {**second, "reply": text, "reply_parts": [text]}
            result = second
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Playground IA erro | client={client_id} | {type(e).__name__}: {e}")
        raise HTTPException(502, "O clone engasgou agora. Manda a mensagem de novo.")

    reply = result.get("reply", "")
    return {
        "reply": reply,
        "reply_parts": result.get("reply_parts") or [reply],
        # Cards de produto pra o front desenhar o carrossel dentro do celular do teste
        "cards": cards,
        "intent": result.get("intent", ""),
        "sentiment": result.get("sentiment", ""),
        "stage_action": result.get("stage_action", ""),
        # Assuntos simulados nesta resposta: o front mostra a nota FORA do balão.
        "demo_topics": playground_demo.detect_demo_topics(result.get("actions"), reply) + (["produtos"] if cards else []),
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
