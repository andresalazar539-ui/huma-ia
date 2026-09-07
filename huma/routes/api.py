# ================================================================
# huma/routes/api.py — Endpoints da API v9.5
#
# v9.5:
#   - ADICIONADO: /webhook/mercadopago (IPN do Mercado Pago)
#     → Recebe notificação de pagamento
#     → Cruza com lead pelo phone (via tabela payments)
#     → Se aprovado: notifica lead no WhatsApp + avança funil pra "won"
#     → Notifica dono do negócio
#   - Webhook Twilio atualizado (audio + imagem)
# ================================================================

import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import (
    APIRouter, BackgroundTasks, Cookie, Depends, File,
    HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.config import APP_VERSION, ELEVENLABS_API_KEY, ELEVENLABS_MODEL, GOOGLE_CALENDAR_CREDENTIALS
from huma.core.auth import verify_webhook, verify_api_key, verify_api_key_manual, bearer_scheme
from huma.core.orchestrator import handle_message, process_outbound_campaign
from huma.models.schemas import (
    ApprovalPayload, BusinessCategory, FunnelConfig,
    MediaAsset, MessagePayload, MessageResponse,
    OnboardingStatus, OutboundCampaign, PaymentRequest,
    SchedulingRequest, WhatsAppImportPayload,
)
from huma.onboarding.categories import get_onboarding_questions, FINAL_QUESTION
from huma.services import attribution_service as attribution
from huma.services import campaign_shield as shield
from huma.services import redis_service as cache
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.services import media_service as ms
from huma.services import payment_service as pay
from huma.services import ai_service as ai
from huma.services import audio_service as audio
from huma.services import voice_service as vs
from huma.utils.logger import get_logger

log = get_logger("routes")
router = APIRouter()


# ── Webhook WhatsApp ──

@router.post("/api/message", response_model=MessageResponse, tags=["Webhook"])
async def receive_message(payload: MessagePayload, bg: BackgroundTasks, _=Depends(verify_webhook)):
    """Recebe mensagem do WhatsApp via webhook."""
    if not payload.has_content():
        raise HTTPException(400, "Mensagem vazia")

    # Atribuição de origem (first-touch): só dispara se a mensagem traz
    # sinal (referral CTWA, código #h ou utm) — string-check barato.
    if attribution.has_signal(payload.referral, payload.text):
        bg.add_task(attribution.capture, payload.client_id, payload.phone,
                    payload.referral or {}, payload.text)

    result = await handle_message(payload, bg)

    if result.get("status") == "rate_limited":
        raise HTTPException(429, "Aguarde antes de enviar mais mensagens")
    if result.get("status") == "client_not_found":
        raise HTTPException(404, "Cliente não encontrado")

    return MessageResponse(status=result.get("status"))


# ── Aprovação ──

@router.post("/api/approve", tags=["Clone"])
async def approve_message(
    payload: ApprovalPayload,
    bg: BackgroundTasks,
    creds=Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
):
    """Aprova ou rejeita resposta pendente."""
    await verify_api_key_manual(payload.client_id, creds, huma_session)

    raw = await cache.get_pending(payload.client_id, payload.phone)
    if not raw:
        raise HTTPException(404, "Sem resposta pendente")

    pending = json.loads(raw)
    final_text = payload.edited_text or pending["ai_response"]

    if payload.approved:
        bg.add_task(wa.send_text, payload.phone, final_text, client_id=payload.client_id)
        await cache.delete_pending(payload.client_id, payload.phone)

        if payload.edited_text and payload.edited_text != pending["ai_response"]:
            client = await db.get_client(payload.client_id)
            if client:
                corrections = (client.correction_examples or [])[-19:]
                corrections.append({
                    "ai_said": pending["ai_response"],
                    "owner_corrected": payload.edited_text,
                    "context": pending.get("lead_message", ""),
                })
                await db.update_client(payload.client_id, {"correction_examples": corrections})

        return {"status": "sent"}

    await cache.delete_pending(payload.client_id, payload.phone)
    return {"status": "discarded"}


# ── Onboarding ──

@router.get("/api/onboarding/{client_id}/questions", tags=["Onboarding"])
async def get_questions(client_id: str, category: BusinessCategory, _=Depends(verify_api_key)):
    """Retorna perguntas de onboarding pra categoria do negócio."""
    questions = get_onboarding_questions(category)
    questions.append(FINAL_QUESTION)
    return {"client_id": client_id, "questions": questions, "total": len(questions)}


@router.post("/api/onboarding/{client_id}/activate", tags=["Onboarding"])
async def activate_client(client_id: str, _=Depends(verify_api_key)):
    """Ativa cliente pra produção."""
    await db.update_client(client_id, {"onboarding_status": OnboardingStatus.ACTIVE.value})
    # Sprint Billing: 2º ponto de ativação — mesmo trial idempotente do wizard
    from huma.services import subscription_service as subs
    await subs.start_trial_if_eligible(client_id, trigger="activation")
    return {"status": "active"}


# ── Config ──

@router.patch("/api/clients/{client_id}/mode", tags=["Config"])
async def update_mode(client_id: str, mode: str, _=Depends(verify_api_key)):
    """Altera modo de operação (auto/approval)."""
    if mode not in ("auto", "approval"):
        raise HTTPException(400, "Modo deve ser 'auto' ou 'approval'")
    await db.update_client(client_id, {"clone_mode": mode})
    return {"status": "updated", "mode": mode}


@router.put("/api/clients/{client_id}/funnel", tags=["Funil"])
async def update_funnel(client_id: str, config: FunnelConfig, _=Depends(verify_api_key)):
    """Atualiza funil customizado."""
    await db.update_client(client_id, {"funnel_config": config.model_dump()})
    return {"status": "updated"}


# ── Outbound ──

class CampaignReviewPayload(BaseModel):
    """Mensagem de campanha pra análise do Escudo antiban."""
    message: str = Field(..., min_length=1, max_length=2000)


@router.get("/api/clients/{client_id}/whatsapp/health", tags=["Outbound"])
async def whatsapp_number_health(client_id: str, client=Depends(verify_api_key)):
    """
    Saúde do número WhatsApp na Meta (Escudo antiban, Fase 2).

    Badge do Cockpit: quality_rating GREEN/YELLOW/RED traduzido pra
    otima/atencao/critica + tier de envio + último evento de qualidade
    recebido por webhook. Cache 10min. Canal não-Meta → not_applicable.
    """
    return await shield.get_number_health(client_id, identity=client)


@router.post("/api/clients/{client_id}/outbound/campaign/review", tags=["Outbound"])
async def review_campaign_message(
    client_id: str,
    payload: CampaignReviewPayload,
    client=Depends(verify_api_key),
):
    """
    Escudo antiban: analisa a mensagem da campanha ANTES do disparo.

    Retorna o veredito em semáforo {risco, bloqueio_definitivo, motivos,
    reescrita, dica} pro Cockpit mostrar ao vivo. Cache Redis por hash do
    texto — reanalisar a mesma mensagem não paga nova chamada de IA.
    Nunca falha por instabilidade do juiz (degrada pra "nao_analisado").
    """
    return await shield.review_campaign(client_id, payload.message)


@router.post("/api/clients/{client_id}/outbound/campaign", tags=["Outbound"])
async def create_campaign(
    client_id: str,
    campaign: OutboundCampaign,
    bg: BackgroundTasks,
    client=Depends(verify_api_key),
):
    """
    Cria E dispara campanha de prospecção outbound (em background).

    TRAVAS (decisão de produto 2026-07-05):
      1. SÓ WhatsApp oficial (Meta Cloud API). Disparo em massa por canal
         não-oficial (Evolution/Baileys) = banimento do número do cliente.
         O Cockpit mostra cadeado; aqui é a trava de verdade.
      2. Feature do plano ON (outbound_templates).
    """
    if not campaign.leads:
        raise HTTPException(400, "Mínimo 1 lead")
    if campaign.daily_send_limit > 200:
        raise HTTPException(400, "Máximo 200 envios/dia")

    provider = (getattr(client, "whatsapp_provider", "") or "").strip().lower()
    if provider != "meta":
        raise HTTPException(
            403,
            "Disparo em massa disponível apenas com WhatsApp oficial (API da Meta). "
            "Envio em massa por canal não-oficial arrisca o banimento do seu número.",
        )

    from huma.services import billing_service as billing
    plan_config = await billing.get_client_plan_config(client_id)
    if not plan_config.get("outbound_templates"):
        raise HTTPException(403, "Disparo em massa faz parte do plano ON. Faça upgrade pra liberar.")

    # ── Escudo antiban (Fase 1): revisão obrigatória ANTES de criar ──
    # Re-valida no backend (nunca confia no veredito que o Cockpit viu —
    # cache Redis faz esta chamada ser hit quando o front já analisou).
    #   - Conteúdo proibido pela Meta → bloqueio sem override (proteção
    #     também da reputação da HUMA como provedora de tecnologia).
    #   - Amarelo/vermelho → exige aceite explícito (risk_accepted), que
    #     fica auditado com data/hora na campanha.
    #   - Juiz indisponível → "nao_analisado", não trava o usuário.
    verdict = await shield.review_campaign(client_id, campaign.message_template)
    if verdict.get("bloqueio_definitivo"):
        raise HTTPException(
            403,
            verdict.get("dica")
            or "Essa mensagem viola as políticas do WhatsApp e colocaria seu número em risco real de bloqueio. A HUMA não envia esse conteúdo.",
        )
    if verdict.get("risco") in ("amarelo", "vermelho") and not campaign.risk_accepted:
        raise HTTPException(
            409,
            detail={"reason": "risk_confirmation_required", "verdict": verdict},
        )
    campaign.risk_level = verdict.get("risco", "")

    # ── Escudo antiban (Fase 2): auto-pausa por saúde do número ──
    # Nota RED na Meta = disparar acelera o banimento. Sem override:
    # aqui não é opinião da HUMA, é a nota oficial do número.
    health_gate = await shield.campaign_health_gate(client_id, identity=client)
    if not health_gate["allowed"]:
        raise HTTPException(
            403,
            "A Meta rebaixou a nota do seu número pra VERMELHA — disparar agora "
            "aceleraria o bloqueio. A HUMA pausou as campanhas pra proteger seu número; "
            "elas voltam sozinhas quando a nota se recuperar (normalmente alguns dias "
            "com atendimento normal e sem disparos).",
        )

    campaign.client_id = client_id
    campaign.campaign_id = f"camp_{client_id}_{int(datetime.utcnow().timestamp())}"
    await db.save_outbound_campaign(campaign)

    # Dispara o batch em background (respeita daily_send_limit e créditos)
    bg.add_task(process_outbound_campaign, client, campaign)

    return {
        "status": "created",
        "campaign_id": campaign.campaign_id,
        "leads": len(campaign.leads),
        "dispatching": True,
    }


# ── Mídia (Criativos) ──

@router.get("/api/clients/{client_id}/media", tags=["Mídia"])
async def list_media(client_id: str, _=Depends(verify_api_key)):
    """Lista todos os criativos do cliente."""
    assets = await ms.get_media_list(client_id)
    return {"total": len(assets), "assets": [a.model_dump() for a in assets]}


@router.post("/api/clients/{client_id}/media", tags=["Mídia"])
async def upload_media(
    client_id: str, name: str, tags: str, url: str,
    media_type: str = "image", description: str = "",
    _=Depends(verify_api_key),
):
    """Upload de criativo com tags."""
    asset = MediaAsset(
        asset_id=f"m_{client_id}_{int(datetime.utcnow().timestamp())}",
        client_id=client_id,
        name=name,
        url=url,
        media_type=media_type,
        tags=[t.strip() for t in tags.split(",")],
        description=description,
    )
    await ms.save_media_asset(asset)
    return {"status": "created", "asset_id": asset.asset_id}


# ── Pagamento ──

@router.post("/api/clients/{client_id}/payment", tags=["Pagamento"])
async def create_payment(client_id: str, request: PaymentRequest, _=Depends(verify_api_key)):
    """Cria cobrança manualmente (testes ou dashboard)."""
    request.client_id = client_id
    return await pay.create_payment(request)


# ── Métricas ──

@router.get("/api/clients/{client_id}/metrics", tags=["Métricas"])
async def get_metrics(client_id: str, _=Depends(verify_api_key)):
    """Métricas de conversas por estágio."""
    return await db.get_conversation_metrics(client_id)


@router.get("/api/clients/{client_id}/reports", tags=["Cockpit"])
async def get_reports(
    client_id: str,
    days: int = 30,
    date_from: str = "",
    date_to: str = "",
    client=Depends(verify_api_key),
) -> dict:
    """
    Relatório de outcome do período pro Cockpit (ReportsScreen).

    Seções condicionais às metas (capabilities) do cliente: vendas só
    pra quem vende, agenda só pra quem agenda, qualificação só pra
    quem qualifica. Atendimento/funil/follow-up/inteligência sempre.

    date_from/date_to (yyyy-mm-dd, opcionais): período personalizado e
    comparação com época arbitrária. Máx. 12 meses atrás.
    """
    days = max(1, min(days, 90))
    if date_from:
        # Sanidade: formato de data e janela máxima de 12 meses atrás
        import re as _re
        limite = (datetime.utcnow() - timedelta(days=366)).strftime("%Y-%m-%d")
        if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date_from) or not _re.match(r"^\d{4}-\d{2}-\d{2}$", date_to or ""):
            raise HTTPException(400, "Datas no formato AAAA-MM-DD")
        if date_from < limite:
            raise HTTPException(400, "Período máximo: 12 meses atrás")
    from huma.services import report_service
    return await report_service.build_report(
        client, days=days, date_from=date_from, date_to=date_to,
    )


@router.get("/api/clients/{client_id}/ai-usage", tags=["Cockpit"])
async def get_ai_usage(client_id: str, days: int = 30, _=Depends(verify_api_key)) -> dict:
    """
    F6 (medição) — custo real de IA do cliente no período: chamadas,
    conversas, tokens por tipo, custo em BRL, custo por conversa e share
    do modelo forte. Base da margem por cliente (tabela ai_usage).

    Sem a tabela (migration pendente) devolve zeros, nunca erro.
    """
    from huma.services.usage_service import get_ai_usage_summary
    return await get_ai_usage_summary(client_id, days=days)


@router.get("/api/clients/{client_id}/reports/export", tags=["Cockpit"])
async def export_report(
    client_id: str,
    format: str = "xlsx",
    days: int = 30,
    client=Depends(verify_api_key),
):
    """
    Exporta o relatório do período: planilha (.xlsx) ou apresentação
    (.pptx, editável no PowerPoint). Respeita as seções por meta.
    """
    if format not in ("xlsx", "pptx"):
        raise HTTPException(400, "Formato inválido. Use xlsx ou pptx.")
    days = max(1, min(days, 90))

    from huma.services import export_service, report_service
    report = await report_service.build_report(client, days=days)

    if format == "xlsx":
        payload = export_service.report_to_xlsx(client, report)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        payload = export_service.report_to_pptx(client, report)
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    filename = f"huma-relatorio-{days}d.{format}"
    log.info(f"Relatório exportado | client={client_id} | format={format} | days={days}")
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ReportTestRequest(BaseModel):
    """Body do envio de teste do relatório (drawer Receber automático)."""
    target: str = Field(
        default="",
        description=(
            "Destino único do teste: WhatsApp com DDI (só dígitos) ou "
            "e-mail. Vazio = dono + destinatários extras salvos."
        ),
    )


@router.post("/api/clients/{client_id}/reports/send-test", tags=["Cockpit"])
async def send_test_report(
    client_id: str,
    body: Optional[ReportTestRequest] = None,
    client=Depends(verify_api_key),
) -> dict:
    """
    Envia o relatório do período AGORA — botão "Salvar e enviar teste"
    do drawer Receber automático. Telefone → WhatsApp; e-mail → Resend.
    Não toca no dedup do job automático (o envio agendado segue normal).
    """
    target = (body.target if body else "").strip()
    if target and "@" not in target:
        digits = "".join(ch for ch in target if ch.isdigit())
        if not (10 <= len(digits) <= 15):
            raise HTTPException(400, "Destino inválido: use WhatsApp com DDI ou e-mail")
        target = digits

    from huma.services import report_service
    result = await report_service.send_report_now(client, target=target)
    if not result["total"]:
        raise HTTPException(
            400, "Nenhum destinatário: defina seu WhatsApp nos Ajustes ou informe um destino",
        )
    log.info(f"Relatório teste | client={client_id} | sent={result['sent']}/{result['total']}")
    return result


@router.get("/api/clients/{client_id}/tracking-link", tags=["Cockpit"])
async def get_tracking_link(
    client_id: str,
    source: str,
    campaign: str = "",
    phone: str = "",
    text: str = "",
    _=Depends(verify_api_key),
) -> dict:
    """
    Gera link wa.me rastreável pro dono colar em cada canal (Google Ads,
    LinkedIn, bio do Instagram, assinatura de e-mail...).

    O texto pré-preenchido termina num código curto (#h<code>-campanha);
    quando o lead manda a primeira mensagem, a HUMA identifica a origem
    e o relatório passa a mostrar de onde vêm conversas e conversões.

    Meta Ads NÃO precisa disso: anúncio click-to-WhatsApp já chega com
    referral automático no webhook.

    Query:
        source: origem (google_ads | linkedin | tiktok_ads | youtube |
            instagram | facebook | site | email | indicacao | meta_ads)
        campaign: nome da campanha (opcional, vira sufixo do código)
        phone: número de WhatsApp do negócio com DDI, só dígitos
            (ex.: 5511999999999). Vazio = retorna só texto+código.
        text: mensagem inicial customizada (opcional)
    """
    try:
        result = attribution.build_tracking_link(
            source=source, campaign=campaign, phone=phone, base_text=text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    log.info(f"Tracking link gerado | client={client_id} | source={result['source']} | campaign={campaign}")
    return result


# ── Settings do Cockpit (Sprint 2 — o botão Salvar salva de verdade) ──

# Whitelist de campos do ClientIdentity editáveis pela tela de Ajustes.
# NUNCA aceitar campo arbitrário: api_key, tokens OAuth, evolution_instance,
# capabilities e onboarding_status têm fluxos próprios e ficam de fora.
SETTINGS_EDITABLE_FIELDS = frozenset({
    "business_name", "business_description", "tone_of_voice", "working_hours",
    "custom_rules", "products_or_services", "faq", "forbidden_words",
    "personality_traits", "use_emojis", "fallback_message",
    "silent_hours_start", "silent_hours_end", "silent_hours_message",
    "ai_schedule",
    "owner_phone", "report_frequency", "report_hour", "report_day",
    "report_recipients", "report_formats",
    "notify_owner_on_appointment", "notify_owner_on_payment",
    "notify_owner_on_cancellation", "notify_owner_on_stuck_lead",
    "max_discount_percent", "max_installments", "accepted_payment_methods",
    # Negócio de verdade (2026-09-04): equipe técnica, vocabulário, dono.
    # knowledge_docs e team_members NÃO entram aqui — têm rotas próprias
    # (routes/business.py) porque envolvem processamento/e-mail.
    "professionals", "preferred_terms", "owner_name",
    # Missão, personalidade e identidade (2026-09-05): tudo que o
    # onboarding coleta passa a ser editável. `capabilities` sincroniza
    # as flags legadas enable_scheduling/enable_payments no PATCH.
    "category", "website", "competitors", "capabilities",
    "lead_collection_fields", "collect_before_offer",
})


@router.get("/api/clients/{client_id}/referrals", tags=["Cockpit"])
async def referral_stats(client_id: str, _=Depends(verify_api_key)) -> dict:
    """
    Programa de indicação: recompensas vigentes + indicados reais.

    O link de indicação é /login?ref=<client_id> (o front monta com a
    própria origem). Conversas ganhas vêm do razão (source='indicacao',
    créditos de conversão), não de conta de cabeça.
    """
    from fastapi.concurrency import run_in_threadpool
    from huma.services import billing_service as billing

    supa = db.get_supabase()

    try:
        rows = await run_in_threadpool(
            lambda: supa.table("clients")
                .select("business_name,created_at,referral_credited_at")
                .eq("referred_by", client_id)
                .order("created_at", desc=True)
                .limit(200).execute()
        )
        referrals = [
            {
                "business_name": r.get("business_name") or "",
                "created_at": r.get("created_at"),
                "converted": bool(r.get("referral_credited_at")),
            }
            for r in (rows.data or [])
        ]
    except Exception as e:
        # Ambiente sem a migration_referral.sql: tela mostra zeros em vez
        # de quebrar (indicação é bônus, não requisito).
        log.warning(f"Referrals | consulta falhou | client={client_id} | {type(e).__name__}: {e}")
        referrals = []

    try:
        credits = await run_in_threadpool(
            lambda: supa.table("credit_transactions")
                .select("amount,description")
                .eq("client_id", client_id).eq("source", "indicacao")
                .eq("type", "credit")
                .like("description", "conversão%")
                .limit(500).execute()
        )
        conversas_ganhas = sum(int(c.get("amount") or 0) for c in (credits.data or []))
    except Exception:
        conversas_ganhas = 0

    return {
        "status": "ok",
        "reward_conversations": billing.REFERRAL_REWARD_CONVERSATIONS,
        "welcome_bonus": billing.REFERRAL_WELCOME_BONUS,
        "monthly_cap": billing.REFERRAL_MONTHLY_CONVERSION_CAP,
        "conversas_ganhas": conversas_ganhas,
        "referrals": referrals,
    }


@router.get("/api/clients/{client_id}/settings", tags=["Cockpit"])
async def get_settings(client_id: str, _=Depends(verify_api_key)) -> dict:
    """Valores atuais dos campos editáveis (pra tela de Ajustes carregar dados reais)."""
    client = await db.get_client(client_id)
    if not client:
        raise HTTPException(404, "Cliente não encontrado")
    data = client.model_dump(mode="json")
    settings = {k: data.get(k) for k in SETTINGS_EDITABLE_FIELDS}
    # Capabilities efetivas (None no banco = derivadas das flags legadas):
    # a tela de Missão mostra o que VALE hoje, não o campo cru.
    settings["capabilities_resolved"] = sorted(c.value for c in client.capabilities_resolved)
    return {"settings": settings}


@router.patch("/api/clients/{client_id}/settings", tags=["Cockpit"])
async def update_settings(client_id: str, updates: dict, _=Depends(verify_api_key)) -> dict:
    """
    Salva campos editáveis do ClientIdentity (botão Salvar do Cockpit).

    Segurança: só aceita campos da whitelist — o resto é ignorado e
    reportado em "ignored". Validação: o payload mesclado é revalidado
    pelo model ClientIdentity ANTES de persistir; valor com tipo errado
    vira 422 e nada é gravado.
    """
    client = await db.get_client(client_id)
    if not client:
        raise HTTPException(404, "Cliente não encontrado")

    accepted = {k: v for k, v in (updates or {}).items() if k in SETTINGS_EDITABLE_FIELDS}
    ignored = sorted(set(updates or {}) - set(accepted))
    if not accepted:
        raise HTTPException(400, "Nenhum campo editável no payload.")

    # Revalida o identity inteiro com os novos valores (tipos/enums)
    from pydantic import ValidationError
    merged = {**client.model_dump(), **accepted}
    try:
        from huma.models.schemas import ClientIdentity
        validated = ClientIdentity(**merged)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        campo = ".".join(str(p) for p in first.get("loc", []))
        raise HTTPException(422, f"Valor inválido em '{campo}'.")

    # Persiste SÓ o que mudou (valores já normalizados pelo model)
    dumped = validated.model_dump(mode="json")
    persisted = {k: dumped[k] for k in accepted}
    # Missão do clone: capabilities explícitas mandam nas flags legadas
    # que partes do código (status da agenda, jobs) ainda leem.
    if "capabilities" in accepted:
        caps = set(dumped.get("capabilities") or [])
        persisted["enable_scheduling"] = "schedule" in caps
        persisted["enable_payments"] = bool(caps & {"sell_digital", "sell_physical"})
    await db.update_client(client_id, persisted)
    log.info(f"Settings salvos | client={client_id} | fields={sorted(accepted.keys())}")

    # Informação nova sobre o negócio = playbook novo sozinho (princípio
    # 2026-09-07). Só quando o valor MUDOU de fato (o Cockpit manda a tela
    # inteira) — uma chamada de IA por mudança real, em background.
    from huma.core.integration_effects import knowledge_changed
    changed = knowledge_changed(client.model_dump(mode="json"), persisted)
    if changed:
        from huma.services import playbook_service
        playbook_service.schedule_regenerate(client_id, "settings:" + ",".join(changed))
        log.info(f"Playbook regen agendado | client={client_id} | changed={changed}")
    return {"status": "ok", "updated": sorted(accepted.keys()), "ignored": ignored, "playbook_refresh": changed}


# ── Voz (Cockpit → sessão "Voz clonada" DE VERDADE) ──
#
# Multi-tenant: a conta ElevenLabs é uma só (da HUMA). A voz clonada de
# cada cliente chama "huma_{client_id}" e só aparece pro próprio dono.
# Toda seleção de voz passa por is_voice_allowed_for_client (premade OU
# o próprio clone) — nunca dá pra apontar pro clone de outro cliente.


class VoicePreviewBody(BaseModel):
    voice_id: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=vs.MAX_PREVIEW_CHARS)


class VoiceUpdateBody(BaseModel):
    enable_audio: Optional[bool] = None
    voice_id: Optional[str] = Field(default=None, max_length=64)


@router.get("/api/clients/{client_id}/voice", tags=["Voz"])
async def voice_status(client_id: str, client=Depends(verify_api_key)) -> dict:
    """
    Estado atual da voz do cliente pro Cockpit: voz ativa (com metadata
    da ElevenLabs), flag enable_audio, modelo TTS em uso e se a feature
    está configurada no servidor (API key presente).
    """
    voice = await vs.get_voice(client.voice_id) if client.voice_id else None
    is_cloned = bool(voice and voice.get("name") == vs.clone_voice_name(client_id))
    return {
        "configured": bool(ELEVENLABS_API_KEY),
        "enabled": client.enable_audio,
        "voice_id": client.voice_id,
        "voice": voice,
        "is_cloned": is_cloned,
        "model": ELEVENLABS_MODEL,
    }


@router.get("/api/clients/{client_id}/voice/catalog", tags=["Voz"])
async def voice_catalog(client_id: str, _=Depends(verify_api_key)) -> dict:
    """Vozes disponíveis pro cliente: estúdio (premade) + o próprio clone."""
    result = await vs.list_voices_for_client(client_id)
    if result["status"] != "ok":
        raise HTTPException(502, result["detail"])
    return {"cloned": result["cloned"], "premade": result["premade"]}


@router.post("/api/clients/{client_id}/voice/clone", tags=["Voz"])
async def voice_clone(
    client_id: str,
    files: list[UploadFile] = File(...),
    client=Depends(verify_api_key),
) -> dict:
    """
    Clona a voz do dono (IVC) a partir de amostras de áudio (gravadas no
    navegador ou arquivos). Ordem à prova de falha: cria a voz NOVA →
    persiste no cliente → só então apaga o clone antigo (best-effort).
    Se o treino falhar, nada muda.
    """
    if not files:
        raise HTTPException(400, "Envie pelo menos uma amostra de áudio.")
    if len(files) > vs.MAX_CLONE_FILES:
        raise HTTPException(400, f"No máximo {vs.MAX_CLONE_FILES} amostras por treino.")

    payload: list[tuple[str, bytes, str]] = []
    total = 0
    for f in files:
        blob = await f.read()
        ctype = (f.content_type or "").lower()
        if not (ctype.startswith("audio/") or ctype in ("video/webm", "application/octet-stream")):
            raise HTTPException(400, f"Formato não suportado: {ctype or 'desconhecido'}. Envie áudio (mp3, wav, m4a, ogg, webm).")
        if len(blob) > vs.MAX_CLONE_FILE_BYTES:
            raise HTTPException(400, "Cada amostra pode ter até 10MB.")
        total += len(blob)
        payload.append((f.filename or "amostra.webm", blob, ctype or "audio/webm"))

    if total > vs.MAX_CLONE_TOTAL_BYTES:
        raise HTTPException(400, "As amostras somadas passam de 30MB. Envie menos áudio.")
    if total < 50_000:
        raise HTTPException(400, "Áudio curto demais pra clonar. Grave pelo menos 1 minuto falando natural.")

    old_voice_id = client.voice_id
    old_voice = await vs.get_voice(old_voice_id) if old_voice_id else None

    result = await vs.create_instant_clone(client_id, payload)
    if result["status"] != "ok":
        raise HTTPException(502, result["detail"])

    new_voice_id = result["voice_id"]
    await db.update_client(client_id, {"voice_id": new_voice_id, "enable_audio": True})

    # Retreino: apaga o clone ANTIGO deste cliente (nunca premade, nunca
    # voz de outro). Best-effort — falha aqui não desfaz o treino novo.
    if (
        old_voice
        and old_voice.get("name") == vs.clone_voice_name(client_id)
        and old_voice_id != new_voice_id
    ):
        await vs.delete_voice(old_voice_id)

    voice = await vs.get_voice(new_voice_id)
    log.info(f"Voz clonada via Cockpit | client={client_id} | voice={new_voice_id[:8]}... | files={len(payload)} | bytes={total}")
    return {"status": "ok", "voice_id": new_voice_id, "voice": voice}


@router.post("/api/clients/{client_id}/voice/preview", tags=["Voz"])
async def voice_preview(
    client_id: str,
    payload: VoicePreviewBody,
    client=Depends(verify_api_key),
) -> dict:
    """
    Gera uma prévia REAL em PT-BR (TTS de verdade com o modelo em uso,
    não o sample em inglês da ElevenLabs) e devolve a URL do áudio.
    Sem voice_id no body, usa a voz ativa do cliente.
    """
    voice_id = (payload.voice_id or "").strip() or client.voice_id
    if not voice_id:
        raise HTTPException(400, "Nenhuma voz selecionada pra prévia.")

    voice = await vs.get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "Voz não encontrada na ElevenLabs.")
    if not vs.is_voice_allowed_for_client(voice, client_id):
        raise HTTPException(403, "Essa voz não pertence a este cliente.")

    text = (payload.text or "").strip() or vs.build_preview_text(client.business_name)
    url = await audio.generate_and_upload(text, voice_id, sentiment="neutral")
    if not url:
        raise HTTPException(502, "Falha ao gerar a prévia. Tenta de novo em instantes.")

    log.info(f"Prévia de voz gerada | client={client_id} | voice={voice_id[:8]}...")
    return {"status": "ok", "url": url, "text": text}


@router.patch("/api/clients/{client_id}/voice", tags=["Voz"])
async def voice_update(
    client_id: str,
    payload: VoiceUpdateBody,
    client=Depends(verify_api_key),
) -> dict:
    """
    Seleciona a voz ativa e/ou liga-desliga o envio de áudios.
    voice_id="" limpa a seleção (a IA volta a responder só em texto).
    """
    updates: dict = {}

    if payload.voice_id is not None:
        vid = payload.voice_id.strip()
        if vid:
            voice = await vs.get_voice(vid)
            if not voice:
                raise HTTPException(404, "Voz não encontrada na ElevenLabs.")
            if not vs.is_voice_allowed_for_client(voice, client_id):
                raise HTTPException(403, "Essa voz não pertence a este cliente.")
        updates["voice_id"] = vid

    if payload.enable_audio is not None:
        updates["enable_audio"] = payload.enable_audio

    if not updates:
        raise HTTPException(400, "Nada pra atualizar.")

    await db.update_client(client_id, updates)
    log.info(f"Voz atualizada | client={client_id} | fields={sorted(updates.keys())}")
    return {"status": "ok", "updated": sorted(updates.keys())}


@router.delete("/api/clients/{client_id}/voice", tags=["Voz"])
async def voice_delete(client_id: str, client=Depends(verify_api_key)) -> dict:
    """
    Remove a voz do cliente. Se for o clone dele, apaga da ElevenLabs
    também; voz de estúdio só desvincula. 404 na ElevenLabs é idempotente.
    """
    voice_id = client.voice_id
    if not voice_id:
        return {"status": "ok", "detail": "Nenhuma voz configurada."}

    voice = await vs.get_voice(voice_id)
    if voice and voice.get("name") == vs.clone_voice_name(client_id):
        result = await vs.delete_voice(voice_id)
        if result["status"] != "ok":
            raise HTTPException(502, result["detail"])

    await db.update_client(client_id, {"voice_id": ""})
    log.info(f"Voz removida do cliente | client={client_id} | voice={voice_id[:8]}...")
    return {"status": "ok"}


# ── Billing / Assinatura HUMA (recorrência via MP Assinaturas) ──

class SubscribeBody(BaseModel):
    plan: str = Field(..., min_length=1, max_length=30)
    coupon: str = Field(default="", max_length=40)


class SubscribeCardBody(BaseModel):
    plan: str = Field(..., min_length=1, max_length=30)
    coupon: str = Field(default="", max_length=40)
    card_token_id: str = Field(..., min_length=1, max_length=120)


class CouponBody(BaseModel):
    plan: str = Field(..., min_length=1, max_length=30)
    coupon: str = Field(..., min_length=1, max_length=40)


@router.get("/api/clients/{client_id}/billing", tags=["Billing"])
async def billing_status(client_id: str, _=Depends(verify_api_key)) -> dict:
    """Plano atual, status da assinatura e saldo de conversas (Cockpit)."""
    from huma.services import subscription_service as subs
    return await subs.get_billing_status(client_id)


@router.post("/api/clients/{client_id}/billing/subscribe", tags=["Billing"])
async def billing_subscribe(client_id: str, payload: SubscribeBody, client=Depends(verify_api_key)) -> dict:
    """
    Inicia assinatura recorrente: cria o preapproval no Mercado Pago e
    devolve checkout_url (cliente cadastra o cartão lá). A ativação e o
    crédito de conversas acontecem via webhook, nunca aqui.
    """
    from huma.services import subscription_service as subs
    result = await subs.create_checkout(
        client_id, payload.plan, getattr(client, "owner_email", "") or "", payload.coupon,
    )
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("detail", "Não foi possível iniciar a assinatura."))
    return result


@router.post("/api/clients/{client_id}/billing/subscribe-card", tags=["Billing"])
async def billing_subscribe_card(client_id: str, payload: SubscribeCardBody, client=Depends(verify_api_key)) -> dict:
    """
    Checkout transparente: cria a assinatura já autorizada com o cartão
    tokenizado no navegador (SDK MP). O cliente nunca sai do Cockpit.
    Crédito de conversas continua vindo só pelo webhook (regra de ouro).
    """
    from huma.services import subscription_service as subs
    result = await subs.create_subscription_with_card(
        client_id, payload.plan, getattr(client, "owner_email", "") or "",
        payload.card_token_id, payload.coupon,
    )
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("detail", "Não foi possível ativar a assinatura."))
    return result


@router.post("/api/clients/{client_id}/billing/validate-coupon", tags=["Billing"])
async def billing_validate_coupon(client_id: str, payload: CouponBody, _=Depends(verify_api_key)) -> dict:
    """
    Pré-valida um cupom pro plano (feedback na tela antes de assinar).
    Rate limit anti chute de código: 10 tentativas / 10 min por cliente.
    """
    attempts = await cache.incr_with_ttl(f"coupon:tries:{client_id}", 600)
    if attempts > 10:
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")

    from huma.services import subscription_service as subs
    return await subs.validate_coupon(payload.coupon, payload.plan)


class ExtraPackBody(BaseModel):
    """Compra de pacote de conversas extras (Pix ou cartão)."""
    pack_id: str = Field(..., min_length=1, max_length=40)
    method: str = Field(default="pix", pattern="^(pix|card)$")
    # Cartão: token gerado no NAVEGADOR pelo SDK do MP (dados do cartão
    # nunca passam pela HUMA). save_token_id = segundo token pra salvar
    # o cartão pro 1-clique (token do MP é de uso único).
    card_token_id: str = Field(default="", max_length=64)
    payment_method_id: str = Field(default="", max_length=40)
    save_token_id: str = Field(default="", max_length=64)


@router.post("/api/clients/{client_id}/billing/extra-pack", tags=["Billing"])
async def billing_buy_extra_pack(
    client_id: str, payload: ExtraPackBody, _=Depends(verify_api_key)
) -> dict:
    """
    Compra de pacote extra: Pix (QR + copia-e-cola, crédito no webhook)
    ou cartão (cobrança síncrona — aprovou, creditou na hora).
    """
    from huma.services import subscription_service as subs

    result = await subs.create_pack_payment(
        client_id, payload.pack_id,
        method=payload.method,
        card_token_id=payload.card_token_id,
        payment_method_id=payload.payment_method_id,
        save_token_id=payload.save_token_id,
    )
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("detail", "Não foi possível criar a cobrança."))
    return result


@router.get("/api/clients/{client_id}/billing/extra-pack/{payment_id}", tags=["Billing"])
async def billing_extra_pack_status(
    client_id: str, payment_id: str, _=Depends(verify_api_key)
) -> dict:
    """Poll do Cockpit: status do Pix do pacote (pending/approved) + creditado."""
    from huma.services import subscription_service as subs

    if not payment_id.isdigit() or len(payment_id) > 20:
        raise HTTPException(400, "payment_id inválido")
    return await subs.get_pack_payment_status(client_id, payment_id)


@router.post("/api/clients/{client_id}/billing/cancel", tags=["Billing"])
async def billing_cancel(client_id: str, _=Depends(verify_api_key)) -> dict:
    """Cancela a assinatura no MP (saldo já pago permanece na carteira)."""
    from huma.services import subscription_service as subs
    result = await subs.cancel_subscription(client_id)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("detail", "Não foi possível cancelar."))
    return result


# ── Controle de gasto (2026-09-04): modo travado / com limite / liberado ──

class SpendBody(BaseModel):
    """Modo de gasto do cliente e teto de excedente (R$) quando 'capped'."""
    mode: str = Field(..., pattern="^(locked|capped|unlimited)$")
    cap_brl: float = Field(default=0.0, ge=0, le=100000)


@router.post("/api/clients/{client_id}/billing/spend", tags=["Billing"])
async def billing_set_spend(client_id: str, payload: SpendBody, _=Depends(verify_api_key)) -> dict:
    """
    Salva o controle de gasto: locked (só o plano), capped (excedente até
    cap_brl no ciclo) ou unlimited. Efeito imediato no gate de conversa.
    """
    from huma.services import billing_service as billing
    result = await billing.set_spend_settings(client_id, payload.mode, payload.cap_brl)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("detail", "Não foi possível salvar."))
    return result


@router.get("/api/clients/{client_id}/billing/ledger", tags=["Billing"])
async def billing_ledger(client_id: str, limit: int = 30, _=Depends(verify_api_key)) -> dict:
    """Extrato das últimas conversas contadas (data, lead, excedente ou plano)."""
    from huma.services import billing_service as billing
    rows = await billing.list_conversation_ledger(client_id, limit=max(1, min(limit, 200)))
    return {"items": rows, "overage_price_brl": billing.OVERAGE_PRICE_BRL}


_SPEND_ACTION_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>HUMA · Controle de gasto</title>
<style>body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f4ef;color:#1d1b16;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}}
.card{{background:#fff;border:1px solid #e6e1d6;border-radius:16px;padding:28px;max-width:420px;width:100%}}
h1{{font-size:20px;margin:0 0 8px}}p{{margin:0 0 12px;line-height:1.5;color:#4a463f}}
a{{color:#1d1b16}}.ok{{color:#2f6b3a}}.err{{color:#a63d2f}}</style></head><body>
<div class="card"><h1 class="{cls}">{title}</h1><p>{body}</p>
<p><a href="/cockpit">Abrir o Cockpit</a></p></div></body></html>"""


@router.get("/billing/spend-action", response_class=HTMLResponse, include_in_schema=False)
async def billing_spend_action(token: str = "") -> HTMLResponse:
    """
    Link assinado dos avisos no WhatsApp: liberar até +R$100, liberar sem
    limite ou travar — sem login. Token HMAC com validade de 7 dias
    (billing_service.make_spend_action_token). Token inválido = nada muda.
    """
    from huma.services import billing_service as billing

    data = billing.verify_spend_action_token(token)
    if not data:
        html = _SPEND_ACTION_HTML.format(
            cls="err", title="Link inválido ou vencido",
            body="Esse link expirou. Ajuste o controle de gasto em Ajustes &gt; Uso no Cockpit.",
        )
        return HTMLResponse(html, status_code=400)

    result = await billing.apply_spend_action(data["client_id"], data["action"])
    if result.get("status") != "ok":
        html = _SPEND_ACTION_HTML.format(
            cls="err", title="Não consegui aplicar agora",
            body=result.get("detail", "Tente de novo em instantes ou ajuste no Cockpit."),
        )
        return HTMLResponse(html, status_code=400)

    mode = result.get("mode")
    if mode == billing.SPEND_MODE_UNLIMITED:
        title, body = "Liberado sem limite", "A HUMA continua atendendo. Cada conversa extra custa R$ %.2f e entra na sua fatura." % billing.OVERAGE_PRICE_BRL
    elif mode == billing.SPEND_MODE_CAPPED:
        title, body = "Liberado até R$ %.0f a mais" % float(result.get("cap_brl") or 0), "A HUMA continua atendendo até esse limite. Você recebe aviso antes de chegar nele."
    else:
        title, body = "Travado", "A HUMA só usa o que você já pagou. Leads novos ficam na sua fila."
    log.info(f"SpendControl | link aplicado | client={data['client_id']} | action={data['action']} | mode={mode}")
    return HTMLResponse(_SPEND_ACTION_HTML.format(cls="ok", title=title, body=body))


class AnalyticsIdsBody(BaseModel):
    """Cookies de analytics do navegador do dono (valores CRUS; o backend parseia)."""
    ga: str = Field(default="", max_length=128, description="Cookie _ga cru")
    ga_stream: str = Field(default="", max_length=256, description="Cookie _ga_<stream> cru")
    fbp: str = Field(default="", max_length=128, description="Cookie _fbp cru")
    fbc: str = Field(default="", max_length=256, description="Cookie _fbc cru")


@router.post("/api/clients/{client_id}/analytics-ids", tags=["Billing"])
async def save_analytics_ids(
    client_id: str, payload: AnalyticsIdsBody, _=Depends(verify_api_key)
) -> dict:
    """
    Guarda os IDs de analytics do dono logado (GA client_id, fbp/fbc).

    Alimenta as conversões server-side: quando o webhook do MP confirma
    uma venda, o purchase sai pro GA4/Meta com estes IDs e a venda é
    atribuída à campanha que trouxe o dono. Best-effort — nunca 500.
    """
    from huma.services import analytics_events as ae
    saved = await ae.save_web_ids(
        client_id, payload.ga, payload.ga_stream, payload.fbp, payload.fbc
    )
    return {"status": "ok", "saved": saved}


# ── Cockpit (T2) ──

@router.get("/api/conversations", tags=["Cockpit"])
async def list_conversations_cockpit(
    client_id: str,
    filter: str = "todas",
    limit: int = 50,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    T2 — lista conversas do cliente pra renderizar no cockpit.

    Backend devolve dados crus (stage, handoff_status, last_message_at).
    Frontend deriva o badge visual ("HUMA atendendo", "Aguarda", etc.)
    a partir desses campos — evita acoplar lógica de UI ao backend.

    Query params:
      - client_id (required)
      - filter: "todas" | "huma" | "aguarda" | "feitas" (default "todas")
      - limit: 1-200 (default 50)

    Auth: Bearer com api_key do client_id. IDOR enforced em verify_api_key_manual.
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    valid_filters = ("todas", "andamento", "confirmado", "feito", "aguardando", "cancelado")
    if filter not in valid_filters:
        raise HTTPException(400, f"filter deve ser um de: {', '.join(valid_filters)}")
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit deve estar entre 1 e 200")

    rows = await db.list_conversations_for_cockpit(client_id, filter, limit)

    import re as _re
    # Markers internos: mensagens que começam com "[MARKER..." (maiúsculas/underline/espaço).
    # Não exige `]` próximo porque o conteúdo do marker pode ter em-dash, parênteses,
    # números — ex: "[AGENDA CONSULTADA — próximos horários LIVRES (use APENAS...)]".
    INTERNAL_MARKER = _re.compile(r"^\[[A-Z][A-Z_ ]+")

    items = []
    for r in rows:
        history = r.get("history") or []
        preview = ""
        for msg in reversed(history):
            if msg.get("role") not in ("user", "assistant"):
                continue
            content = (msg.get("content") or "").strip()
            if not content or INTERNAL_MARKER.match(content):
                continue
            preview = content[:120]
            break
        items.append({
            "phone": r.get("phone", ""),
            "lead_name": r.get("lead_name_canonical", "") or "",
            "stage": r.get("stage", "discovery"),
            "handoff_status": r.get("handoff_status", "active"),
            "last_message_at": r.get("last_message_at"),
            "last_message_preview": preview,
            "active_appointment_datetime": r.get("active_appointment_datetime", "") or "",
            "active_appointment_service": r.get("active_appointment_service", "") or "",
            # Balcão (canal web): o front usa pra rotular "Visitante do
            # site" em vez de mascarar web:<sid> como telefone falso.
            "channel": r.get("channel", "whatsapp") or "whatsapp",
            "lead_whatsapp": r.get("lead_whatsapp", "") or "",
        })

    log.info(
        f"Cockpit list_conversations | client_id={client_id} | "
        f"filter={filter} | count={len(items)}"
    )
    return {"items": items, "total": len(items)}


@router.get("/api/conversations/{client_id}/{phone}", tags=["Cockpit"])
async def get_conversation_cockpit(
    client_id: str,
    phone: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    T2 — detalhe completo de uma conversa pra render no cockpit.

    Devolve history cru ([{role, content, ...}]). Frontend formata bolhas,
    horários, indicadores de áudio etc.

    404 se conversa nunca recebeu mensagem (history vazio e sem last_message_at).
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    log.info(
        f"Cockpit get_conversation | client_id={client_id} | "
        f"phone={phone} | history_len={len(conv.history)}"
    )
    return {
        "client_id": conv.client_id,
        "phone": conv.phone,
        "lead_name": conv.lead_name_canonical or "",
        "lead_email": conv.lead_email or "",
        "stage": conv.stage,
        "handoff_status": conv.handoff_status,
        "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "active_appointment_datetime": conv.active_appointment_datetime or "",
        "active_appointment_service": conv.active_appointment_service or "",
        "channel": conv.channel or "whatsapp",
        "lead_whatsapp": conv.lead_whatsapp or "",
        # Clientes (CRM do dono)
        "is_customer": bool(conv.is_customer),
        "customer_since": conv.customer_since.isoformat() if conv.customer_since else None,
        "customer_reason": conv.customer_reason or "",
        "owner_notes": conv.owner_notes or "",
        "lead_source": conv.lead_source or "",
        "history": conv.history,
    }


# ── Cockpit — Novo agendamento criado pelo dono (2026-09-07) ──
#
# Mesmo motor da HUMA: sched.create_appointment valida horário de
# funcionamento, checa FreeBusy no Google e cria o evento. Conflito ou
# fora do expediente volta como 409 (nunca grava). Quem agenda vira
# cliente (CRM do dono), igual ao caminho automático.


class NewAppointmentPayload(BaseModel):
    """Agendamento manual pelo Cockpit."""
    lead_name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., min_length=10, max_length=20, description="WhatsApp do cliente (só dígitos, com DDI)")
    service: str = Field(..., min_length=1, max_length=120)
    date_time: str = Field(..., min_length=10, max_length=40, description="'YYYY-MM-DD HH:MM' (horário local do negócio)")
    lead_email: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=500)
    notify_lead: bool = Field(default=False, description="Mandar a confirmação pro WhatsApp do cliente")


@router.post("/api/appointments", tags=["Cockpit"])
async def create_appointment_cockpit(
    client_id: str,
    payload: NewAppointmentPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Cria um agendamento pelo Cockpit (botão "Novo agendamento").

    Erros:
      400 — data/hora inválida ou dados faltando
      409 — horário ocupado (FreeBusy) ou fora do horário de funcionamento
      502 — agenda do Google não conectada / erro no Calendar
    """
    from huma.core.customers import mark_as_customer
    from huma.core.service_duration import config_for_service
    from huma.services import scheduling_service as sched

    await verify_api_key_manual(client_id, creds, huma_session)

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    phone = "".join(c for c in payload.phone if c.isdigit())
    if len(phone) < 10:
        raise HTTPException(400, "WhatsApp inválido: precisa de DDD + número.")
    if len(phone) <= 11:
        phone = "55" + phone

    # Data em formato fixo (o dono escolhe no calendário; nada de linguagem natural).
    raw = payload.date_time.strip().replace("T", " ")
    try:
        when = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, "Data/hora inválida. Use o seletor de data e horário.")
    if when < datetime.now() - timedelta(minutes=5):
        raise HTTPException(400, "Esse horário já passou. Escolha um horário futuro.")

    email = payload.lead_email.strip()
    if email and ("@" not in email or "." not in email.split("@", 1)[1]):
        raise HTTPException(400, "E-mail inválido.")

    conv = await db.get_conversation(client_id, phone)
    request = SchedulingRequest(
        client_id=client_id,
        phone=phone,
        lead_name=payload.lead_name.strip(),
        lead_email=email,
        lead_phone_confirmed=True,
        service=payload.service.strip(),
        date_time=when.strftime("%Y-%m-%dT%H:%M:%S"),
        notes=payload.notes.strip(),
        lead_context="Agendamento criado pelo dono no Cockpit da HUMA.",
        calendar_id=(getattr(identity, "google_calendar_id", "") or ""),
        schedule_config=config_for_service(
            identity.business_schedule, identity.products_or_services, payload.service.strip()
        ),
        allow_no_email=True,
    )

    result = await sched.create_appointment(request, existing_event_id=conv.active_appointment_event_id or "")
    status = result.get("status", "error")
    if status in ("conflict", "outside_hours"):
        detail = result.get("whatsapp_message") or result.get("detail") or "Horário indisponível."
        if status == "conflict" and result.get("available_slots"):
            detail = "Esse horário já está ocupado na sua agenda. Livres: " + ", ".join(result["available_slots"][:4])
        elif status == "conflict":
            detail = "Esse horário já está ocupado na sua agenda."
        log.info(f"Cockpit new_appointment | client_id={client_id} | phone={phone} | {status}")
        raise HTTPException(409, detail)
    if status == "incomplete":
        raise HTTPException(400, "Faltou: " + ", ".join(result.get("missing_fields") or []))
    if status != "confirmed":
        log.error(f"Cockpit new_appointment | client_id={client_id} | phone={phone} | {status} | {result.get('detail', '')}")
        raise HTTPException(502, "Não consegui criar o agendamento agora. Tenta de novo.")
    if not result.get("event_id"):
        raise HTTPException(502, "Sua agenda do Google não está conectada. Conecte em Integrações e tente de novo.")

    conv.active_appointment_event_id = result["event_id"]
    conv.active_appointment_datetime = result.get("date_time", "") or request.date_time
    conv.active_appointment_service = result.get("service", "") or request.service
    if not conv.lead_name_canonical:
        conv.lead_name_canonical = request.lead_name.split()[0]
    if email and not conv.lead_email:
        conv.lead_email = email
    if conv.cancel_attempts:
        conv.cancel_attempts = 0
    conv.history.append({
        "role": "assistant",
        "content": (
            f"[AGENDAMENTO CONFIRMADO] {request.service} em "
            f"{result.get('date_display', when.strftime('%d/%m/%Y às %H:%M'))} (criado pelo dono no Cockpit)"
        ),
        "timestamp": datetime.utcnow().isoformat(),
    })
    if conv.last_message_at is None:
        conv.last_message_at = datetime.utcnow()
    if mark_as_customer(conv, "appointment"):
        log.info(f"Customer | phone={phone} | motivo=appointment | via=cockpit")
    await db.save_conversation(conv)

    notified = False
    if payload.notify_lead and not phone.startswith(("web", "ig")):
        try:
            await wa.send_text(phone, result["confirmation_message"], client_id=client_id)
            notified = True
        except Exception as e:
            log.warning(f"Cockpit new_appointment | aviso ao lead falhou | phone={phone} | {type(e).__name__}: {e}")

    log.info(
        f"Cockpit new_appointment | client_id={client_id} | phone={phone} | "
        f"{conv.active_appointment_datetime} | calendar={'OK' if result.get('calendar_ok') else 'fallback'} | "
        f"update={bool(result.get('is_update'))} | notified={notified}"
    )
    return {
        "status": "ok",
        "event_id": result["event_id"],
        "date_time": conv.active_appointment_datetime,
        "date_display": result.get("date_display", ""),
        "service": conv.active_appointment_service,
        "phone": phone,
        "calendar_ok": bool(result.get("calendar_ok")),
        "is_update": bool(result.get("is_update")),
        "notified": notified,
    }


# ── Cockpit — Vendas (2026-09-07) ──
#
# Pedidos gerados pela HUMA a partir da tabela `payments` (Mercado Pago
# e Asaas). Sem migration: só leitura. A aba aparece quando o negócio
# vende (capability sell_digital/sell_physical); quem agenda vê Agenda.

_SALES_METHOD_LABEL = {
    "pix": "Pix", "boleto": "Boleto", "credit_card": "Cartão", "card": "Cartão",
    "debit_card": "Cartão de débito", "link": "Link",
}


def _sale_state(status: str, metadata: dict) -> str:
    """
    Estado humano do pedido:
      pago | pendente | link_enviado | recusado | cancelado
    'link_enviado' = cobrança pendente cuja entrega foi um link (checkout
    do MP ou link do Asaas) — o lead ainda não abriu/pagou.
    """
    st = (status or "").strip().lower()
    if st == "approved":
        return "pago"
    if st in ("rejected",):
        return "recusado"
    if st in ("cancelled", "canceled", "expired", "refunded", "charged_back"):
        return "cancelado"
    if (metadata or {}).get("checkout_url"):
        return "link_enviado"
    return "pendente"


def _sale_row(r: dict) -> dict:
    """Linha crua de payments → item da aba Vendas."""
    cents = int(r.get("amount_cents") or 0)
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    method = (r.get("method") or "").strip().lower()
    provider = "asaas" if (meta.get("provider") == "asaas") else "mercadopago"
    return {
        "id": str(r.get("id") or r.get("mp_payment_id") or r.get("external_reference") or ""),
        "phone": r.get("phone", "") or "",
        "lead_name": r.get("lead_name", "") or "",
        "description": r.get("description", "") or "",
        "amount_cents": cents,
        "amount_display": f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "method": method,
        "method_label": _SALES_METHOD_LABEL.get(method, method.replace("_", " ").capitalize() or "—"),
        "provider": provider,
        "provider_label": "Asaas" if provider == "asaas" else "Mercado Pago",
        "status": (r.get("status") or "").strip().lower(),
        "state": _sale_state(r.get("status", ""), meta),
        "created_at": r.get("created_at"),
        "paid_at": r.get("paid_at"),
        "checkout_url": meta.get("checkout_url", "") or "",
    }


def _sales_totals(items: list[dict]) -> dict:
    """Totais em BRT: pago hoje / pago no mês, mais contagens de pendente e link."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Sao_Paulo")
    now_local = datetime.now(tz)
    today = now_local.date()
    totals = {
        "today_cents": 0, "today_count": 0,
        "month_cents": 0, "month_count": 0,
        "pending_count": 0, "link_count": 0,
        "pending_cents": 0,
    }
    for it in items:
        if it["state"] == "pago":
            when = it.get("paid_at") or it.get("created_at")
            try:
                dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                local = dt.astimezone(tz).date()
            except (TypeError, ValueError):
                continue
            if local == today:
                totals["today_cents"] += it["amount_cents"]
                totals["today_count"] += 1
            if local.year == today.year and local.month == today.month:
                totals["month_cents"] += it["amount_cents"]
                totals["month_count"] += 1
        elif it["state"] == "pendente":
            totals["pending_count"] += 1
            totals["pending_cents"] += it["amount_cents"]
        elif it["state"] == "link_enviado":
            totals["link_count"] += 1
            totals["pending_cents"] += it["amount_cents"]
    return totals


@router.get("/api/sales", tags=["Cockpit"])
async def list_sales_cockpit(
    client_id: str,
    days: int = 30,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Aba Vendas — pedidos gerados pela HUMA (tabela payments), com estado
    humano (pago / pendente / link enviado / recusado / cancelado),
    método (Pix, boleto, cartão; Mercado Pago ou Asaas) e totais do dia e
    do mês. `days` limita a lista (1–365); os totais do mês consideram o
    mês corrente dentro dessa janela.
    """
    await verify_api_key_manual(client_id, creds, huma_session)
    if days < 1 or days > 365:
        raise HTTPException(400, "days deve estar entre 1 e 365")

    since_iso = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        rows = await db.list_payments_for_cockpit(client_id, since_iso=since_iso)
    except Exception as e:
        log.error(f"Cockpit list_sales | client_id={client_id} | {type(e).__name__}: {e}")
        raise HTTPException(502, "Não consegui carregar as vendas agora.")

    items = [_sale_row(r) for r in rows]
    totals = _sales_totals(items)
    log.info(f"Cockpit list_sales | client_id={client_id} | days={days} | count={len(items)}")
    return {"items": items, "total": len(items), "totals": totals, "days": days}


# ── Cockpit — Clientes (CRM do dono, 2026-09-07) ──
#
# Cliente = conversa promovida: pagou (payment.approved), agendou
# (appointment.confirmed) ou o dono marcou à mão. A aba Clientes NUNCA
# mostra lead. As anotações do dono entram no prompt dinâmico da HUMA
# só quando existem (core/customers.build_customer_prompt).


class CustomerFlagPayload(BaseModel):
    """Marcar/desmarcar conversa como cliente."""
    is_customer: bool = Field(..., description="True = vira cliente; False = volta a ser só conversa")


class OwnerNotesPayload(BaseModel):
    """Anotações do dono sobre o cliente."""
    owner_notes: str = Field(default="", max_length=4000)


def _customer_migration_error(e: Exception) -> HTTPException | None:
    """Traduz 'coluna não existe' em erro amigável pedindo a migration."""
    msg = str(e)
    if any(k in msg for k in ("is_customer", "customer_since", "customer_reason", "owner_notes")):
        return HTTPException(
            503,
            "A aba Clientes ainda não foi ativada neste banco (rodar scripts/migration_customers.sql).",
        )
    return None


def _customer_row(r: dict, purchases: dict[str, list[dict]]) -> dict:
    """Linha crua da tabela → item da aba Clientes (com compras e agendamento)."""
    from huma.core.customers import reason_label

    phone = r.get("phone", "") or ""
    channel = r.get("channel", "whatsapp") or "whatsapp"
    if phone.startswith("web:"):
        channel = "web"
    elif phone.startswith("ig:"):
        channel = "instagram"
    digits = "".join(c for c in phone if c.isdigit()) if channel == "whatsapp" else ""
    if channel == "web" and r.get("lead_whatsapp"):
        digits = "".join(c for c in str(r.get("lead_whatsapp")) if c.isdigit())
    bought = []
    for pmt in purchases.get(digits, []) if digits else []:
        cents = int(pmt.get("amount_cents") or 0)
        bought.append({
            "description": pmt.get("description", "") or "",
            "amount_cents": cents,
            "amount_display": f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "method": pmt.get("method", "") or "",
            "paid_at": pmt.get("paid_at") or pmt.get("created_at"),
        })
    appt_dt = r.get("active_appointment_datetime", "") or ""
    appt_svc = r.get("active_appointment_service", "") or ""
    return {
        "phone": phone,
        "channel": channel,
        "lead_name": r.get("lead_name_canonical", "") or "",
        "lead_email": r.get("lead_email", "") or "",
        "lead_whatsapp": r.get("lead_whatsapp", "") or "",
        "lead_source": r.get("lead_source", "") or "",
        "stage": r.get("stage", "discovery") or "discovery",
        "last_message_at": r.get("last_message_at"),
        "customer_since": r.get("customer_since"),
        "customer_reason": r.get("customer_reason", "") or "",
        "customer_reason_label": reason_label(r.get("customer_reason", "")),
        "owner_notes": r.get("owner_notes", "") or "",
        "purchases": bought,
        "appointment": {"datetime": appt_dt, "service": appt_svc} if appt_dt else None,
        "appointment_label": (f"{appt_svc} · {appt_dt}" if appt_svc else appt_dt) if appt_dt else "",
    }


@router.get("/api/customers", tags=["Cockpit"])
async def list_customers_cockpit(
    client_id: str,
    q: str = "",
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Aba Clientes — SÓ quem é cliente (is_customer=true), nunca lead.

    Junta as compras aprovadas (tabela payments, por telefone) e o
    agendamento ativo. `q` filtra por nome, telefone, e-mail ou anotação.
    """
    await verify_api_key_manual(client_id, creds, huma_session)
    try:
        rows = await db.list_customers_for_cockpit(client_id)
    except Exception as e:
        friendly = _customer_migration_error(e)
        if friendly:
            raise friendly
        log.error(f"Cockpit list_customers | client_id={client_id} | {type(e).__name__}: {e}")
        raise HTTPException(502, "Não consegui carregar os clientes agora.")

    purchases = await db.list_approved_payments_by_phone(client_id) if rows else {}
    items = [_customer_row(r, purchases) for r in rows]

    needle = (q or "").strip().lower()
    if needle:
        items = [
            it for it in items
            if needle in " ".join((
                it["lead_name"], it["phone"], it["lead_email"], it["owner_notes"],
                " ".join(p["description"] for p in it["purchases"]),
            )).lower()
        ]

    log.info(f"Cockpit list_customers | client_id={client_id} | count={len(items)} | q={'sim' if needle else 'não'}")
    return {"items": items, "total": len(items)}


@router.get("/api/customers/export.csv", tags=["Cockpit"])
async def export_customers_csv(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> Response:
    """Exporta a aba Clientes em CSV (UTF-8 com BOM, separador ';' — abre certo no Excel BR)."""
    from huma.core.customers import customers_to_csv

    await verify_api_key_manual(client_id, creds, huma_session)
    try:
        rows = await db.list_customers_for_cockpit(client_id)
    except Exception as e:
        friendly = _customer_migration_error(e)
        if friendly:
            raise friendly
        log.error(f"Cockpit export_customers | client_id={client_id} | {type(e).__name__}: {e}")
        raise HTTPException(502, "Não consegui exportar os clientes agora.")
    purchases = await db.list_approved_payments_by_phone(client_id) if rows else {}
    items = [_customer_row(r, purchases) for r in rows]
    for it in items:
        it["phone_display"] = (
            it["lead_whatsapp"] if it["channel"] == "web" and it["lead_whatsapp"]
            else ("" if it["channel"] in ("web", "instagram") else it["phone"])
        )
    csv_text = customers_to_csv(items)
    log.info(f"Cockpit export_customers | client_id={client_id} | count={len(items)}")
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="clientes-huma.csv"'},
    )


@router.post("/api/conversations/{client_id}/{phone}/customer", tags=["Cockpit"])
async def set_customer_cockpit(
    client_id: str,
    phone: str,
    payload: CustomerFlagPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Marca ou desmarca a conversa como cliente (botão da tela da conversa).

    Marcar: a primeira marcação vence (customer_since não é sobrescrito).
    Desmarcar: único caminho que grava is_customer=false; anotações ficam.
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    try:
        updates = await db.set_customer_flag(client_id, phone, payload.is_customer, reason="manual")
    except Exception as e:
        friendly = _customer_migration_error(e)
        if friendly:
            raise friendly
        log.error(f"Cockpit set_customer | client_id={client_id} | phone={phone} | {type(e).__name__}: {e}")
        raise HTTPException(502, "Não consegui salvar agora. Tenta de novo.")

    log.info(f"Cockpit set_customer | client_id={client_id} | phone={phone} | is_customer={payload.is_customer}")
    return {
        "status": "ok",
        "is_customer": payload.is_customer,
        "customer_since": updates.get("customer_since", conv.customer_since.isoformat() if conv.customer_since else None) if payload.is_customer else None,
        "customer_reason": updates.get("customer_reason", conv.customer_reason) if payload.is_customer else "",
    }


@router.patch("/api/conversations/{client_id}/{phone}/notes", tags=["Cockpit"])
async def set_owner_notes_cockpit(
    client_id: str,
    phone: str,
    payload: OwnerNotesPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Grava as anotações do dono sobre o cliente. Elas viram memória da
    HUMA pra esta conversa (bloco condicional no prompt dinâmico).
    """
    from huma.core.customers import clean_owner_notes

    await verify_api_key_manual(client_id, creds, huma_session)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    notes = clean_owner_notes(payload.owner_notes)
    try:
        await db.set_owner_notes(client_id, phone, notes)
    except Exception as e:
        friendly = _customer_migration_error(e)
        if friendly:
            raise friendly
        log.error(f"Cockpit owner_notes | client_id={client_id} | phone={phone} | {type(e).__name__}: {e}")
        raise HTTPException(502, "Não consegui salvar as anotações agora.")

    log.info(f"Cockpit owner_notes | client_id={client_id} | phone={phone} | chars={len(notes)}")
    return {"status": "ok", "owner_notes": notes}


# ── Cockpit (T3) — handoff humano + envio manual ──
#
# T3 entrega o ciclo de "conversas funcionais":
#   1. Dono clica "Assumir conversa" → IA para de responder pra esse lead
#   2. Dono digita resposta no composer e envia → vai pelo WhatsApp de verdade
#   3. Dono clica "Devolver para HUMA" → IA volta a responder
#
# Orchestrator JÁ trata handoff_status='handed_off' (suprime IA, só loga
# mensagens do lead no history). T3 só precisa flipar o flag pelo cockpit
# e enviar a msg do dono pelo WhatsApp.


class HandoffPayload(BaseModel):
    """Payload pra assumir/devolver conversa."""
    takeover: bool = Field(..., description="True = humano assume; False = devolve pra IA")
    summary: str = Field(default="", max_length=500, description="Resumo opcional do contexto pro humano")


class CockpitSendPayload(BaseModel):
    """Payload pra dono enviar mensagem manual via cockpit."""
    text: str = Field(..., min_length=1, max_length=1600)


@router.post("/api/conversations/{client_id}/{phone}/handoff", tags=["Cockpit"])
async def conversation_handoff_cockpit(
    client_id: str,
    phone: str,
    payload: HandoffPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    T3 — assume ou devolve a conversa pra IA.

    takeover=true  → flip handoff_status='handed_off' + handed_off_at=now.
                     Orchestrator passa a suprimir respostas da IA.
    takeover=false → flip handoff_status='active' + handed_off_at=None.
                     Orchestrator volta a deixar a IA responder.

    Não envia mensagem pro lead — só muda o estado interno. Se o dono
    quiser avisar o lead da transferência, manda manualmente via /send.
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    if payload.takeover:
        conv.handoff_status = "handed_off"
        conv.handed_off_at = datetime.utcnow()
        if payload.summary:
            conv.handoff_summary = payload.summary
    else:
        conv.handoff_status = "active"
        conv.handed_off_at = None
        conv.handoff_summary = ""

    await db.save_conversation(conv)

    log.info(
        f"Cockpit handoff | client_id={client_id} | phone={phone} | "
        f"takeover={payload.takeover}"
    )
    return {
        "status": "ok",
        "handoff_status": conv.handoff_status,
        "handed_off_at": conv.handed_off_at.isoformat() if conv.handed_off_at else None,
    }


@router.post("/api/conversations/{client_id}/{phone}/send", tags=["Cockpit"])
async def conversation_send_cockpit(
    client_id: str,
    phone: str,
    payload: CockpitSendPayload,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    T3 — dono envia mensagem manual pelo WhatsApp via cockpit.

    Fluxo:
      1. Envia msg via wa.send_text (Twilio/Meta)
      2. Salva no history com marker `by='owner'` pra distinguir da IA
      3. Atualiza last_message_at pra mover conversa pro topo da lista

    Não exige handoff_status='handed_off' — dono pode mandar paralelo se quiser
    (cenário raro mas válido). Se a IA também responder, ambos viram no histórico.

    Erros:
      400 — text vazio (Pydantic) ou muito longo
      404 — conversa inexistente
      502 — Twilio/Meta retornou erro (msg não foi enviada)
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    text = payload.text.strip()
    if not text:
        raise HTTPException(400, "text não pode ser vazio")

    conv = await db.get_conversation(client_id, phone)
    if not conv.history and not conv.last_message_at:
        raise HTTPException(404, "Conversa não encontrada")

    # Balcão (canal web): não existe envio de WhatsApp — a entrega é o
    # próprio history, que a página /c/<id> consome via poll de mensagens.
    # Só grava e retorna; o visitante vê quando a página estiver aberta.
    if conv.channel == "web" or phone.startswith("web:"):
        msg_id = "web"
    else:
        msg_id = await wa.send_text(phone, text, client_id=client_id)
        if not msg_id:
            log.error(f"Cockpit send WA falhou | client_id={client_id} | phone={phone}")
            raise HTTPException(502, "Falha ao enviar mensagem pelo WhatsApp")

    now = datetime.utcnow()
    conv.history.append({
        "role": "assistant",
        "content": text,
        "by": "owner",
        "timestamp": now.isoformat(),
    })
    conv.last_message_at = now
    await db.save_conversation(conv)

    log.info(
        f"Cockpit send | client_id={client_id} | phone={phone} | "
        f"chars={len(text)} | msg_id={msg_id}"
    )
    return {
        "status": "sent",
        "message_id": msg_id,
        "timestamp": now.isoformat(),
    }


# ── Cockpit (T4) — Agenda real ──
#
# Fonte: Supabase (conversations com active_appointment_*). Não chama
# Google Calendar API diretamente — agendamentos criados pelo HUMA já
# estão espelhados no banco via orchestrator. Vantagem: rápido, único
# por cliente, sem auth Google por client_id.
#
# Limitação aceita (MVP): eventos criados manualmente no Google Calendar
# pelo dono (fora do HUMA) não aparecem aqui. T-future pode sincronizar.


def _build_briefing(conv_row: dict) -> str:
    """
    Monta briefing curto pro dono ler ao clicar no agendamento.

    Estratégia barata (zero chamada a Claude): junta fatos coletados
    pela IA (lead_facts) + 1-2 últimas mensagens do lead pra dar contexto
    do que ele quer. Limitado a ~400 chars pra não estourar o drawer.

    Returns:
        String com briefing ou "" se não houver informação suficiente.
    """
    parts: list[str] = []

    facts = conv_row.get("lead_facts") or []
    if isinstance(facts, list) and facts:
        facts_str = " · ".join(str(f) for f in facts[:5] if f)
        if facts_str:
            parts.append(facts_str)

    history = conv_row.get("history") or []
    if isinstance(history, list):
        last_user_msgs = [
            (m.get("content") or "").strip()
            for m in history[-12:]
            if m.get("role") == "user" and (m.get("content") or "").strip()
        ][-2:]
        if last_user_msgs:
            quoted = " ".join(f"\"{m[:140]}\"" for m in last_user_msgs)
            parts.append(f"Últimas msgs do lead: {quoted}")

    briefing = " — ".join(parts)
    return briefing[:400] if briefing else ""


@router.get("/api/appointments", tags=["Cockpit"])
async def list_appointments_cockpit(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    T4 — lista agendamentos ativos do cliente pra renderizar a Agenda.

    Frontend (AgendaScreen) filtra por data e renderiza Dia/Semana/Mês/Lista.
    Backend devolve tudo (até 300 eventos). Em escala maior, adicionar
    query params from/to pra range filtering server-side.

    Shape de cada item (alinhado com AGENDA_EVENTS do AgendaScreen.jsx):
      - date: "YYYY-MM-DD"  (parsed de active_appointment_datetime)
      - start: "HH:MM"      (parsed)
      - end: "HH:MM"        (start + 60min default — duração do appointment)
      - name: lead_name_canonical
      - service: active_appointment_service
      - status: "confirmed" | "done" | "cancelled" (derivado de stage + data)
      - phone: pra cockpit linkar com a conversa
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    rows = await db.list_active_appointments(limit=300, client_id=client_id)

    now = datetime.utcnow()
    items: list[dict] = []
    for r in rows:
        raw_dt = (r.get("active_appointment_datetime") or "").strip()
        if not raw_dt:
            continue

        # Parse defensivo: aceita "YYYY-MM-DDTHH:MM:SS" e "YYYY-MM-DD HH:MM:SS"
        try:
            dt = datetime.fromisoformat(raw_dt.replace(" ", "T"))
        except (ValueError, TypeError):
            log.warning(f"Cockpit appointments | datetime inválido | client_id={client_id} | raw={raw_dt[:40]}")
            continue

        # Status derivado
        stage = r.get("stage", "")
        if stage == "lost":
            status = "cancelled"
        elif stage == "won" or dt < now:
            status = "done"
        else:
            status = "confirmed"

        start_hm = dt.strftime("%H:%M")
        end_dt = dt + timedelta(minutes=60)
        end_hm = end_dt.strftime("%H:%M")

        items.append({
            "date": dt.strftime("%Y-%m-%d"),
            "start": start_hm,
            "end": end_hm,
            "name": r.get("lead_name_canonical", "") or "",
            "service": r.get("active_appointment_service", "") or "",
            "status": status,
            "phone": r.get("phone", ""),
            "briefing": _build_briefing(r),
        })

    log.info(f"Cockpit list_appointments | client_id={client_id} | count={len(items)}")
    return {"items": items, "total": len(items)}


@router.get("/api/integrations/status", tags=["Cockpit"])
async def integrations_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Bloco C — status REAL de todas as integrações do cliente, pro
    IntegrationsScreen render Conectado/Desconectado sem mock.

    NÃO retorna tokens — só marcadores truthy/falsy ("ok" | "") pros
    cards do cockpit checarem com `client.bling_access_token` etc.
    Campos não-secretos (voice_id, phone_number_id) vão crus pra meta.

    Shape compatível com o que IntegrationsScreen.jsx já consome:
    `client.bling_access_token`, `client.crm_access_token`, etc.
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    def _truthy(v) -> str:
        """'ok' se truthy, '' caso contrário. Pra frontend checar sem expor token."""
        return "ok" if v else ""

    return {
        # Bling (Inventory) — card do T1 já checa client.bling_access_token
        "bling_access_token": _truthy(getattr(identity, "bling_access_token", "")),
        "bling_token_expires_at": (
            identity.bling_token_expires_at.isoformat()
            if getattr(identity, "bling_token_expires_at", None)
            else None
        ),
        # CRM (Pipedrive/RD)
        "crm_access_token": _truthy(
            getattr(identity, "crm_access_token", "")
            or getattr(identity, "crm_api_token", "")
        ),
        "crm_provider": getattr(identity, "crm_provider", "") or "",
        "crm_api_base_url": getattr(identity, "crm_api_base_url", "") or "",
        "crm_pipeline_ready": bool(getattr(identity, "crm_pipeline_id", "")),
        # ElevenLabs (voz clonada) — voice_id não é secret, devolve cru pra meta
        "voice_id": getattr(identity, "voice_id", "") or "",
        "enable_audio": bool(getattr(identity, "enable_audio", False)),
        # WhatsApp Meta Cloud API — phone_number_id não é secret
        "phone_number_id": getattr(identity, "phone_number_id", "") or "",
        "waba_id": getattr(identity, "waba_id", "") or "",
        # Canal WhatsApp ativo (meta|evolution|twilio|"") — o Cockpit usa
        # pra travar features exclusivas do canal oficial (ex.: Disparos)
        "whatsapp_provider": (getattr(identity, "whatsapp_provider", "") or "").strip().lower(),
        # Notificações pro dono
        "owner_phone": getattr(identity, "owner_phone", "") or "",
        # Identidade da conta (sidebar/workspace do Cockpit — nada de mock)
        "business_name": getattr(identity, "business_name", "") or "",
        "category": (identity.category.value if getattr(identity, "category", None) else ""),
        "owner_email": getattr(identity, "owner_email", "") or "",
        "owner_name": getattr(identity, "owner_name", "") or "",
        # Evolution (QR) — só marcador, o nome da instância não interessa ao front
        "evolution_instance": _truthy(getattr(identity, "evolution_instance", "")),
        # Google Calendar POR CLIENTE (2026-09-05): "conectada" quando o
        # servidor tem a credencial E o cliente colou a própria agenda.
        # Sem google_calendar_id o motor ainda usa a agenda global legada.
        "google_calendar": bool(GOOGLE_CALENDAR_CREDENTIALS) and bool(getattr(identity, "google_calendar_id", "")),
        "google_calendar_id": getattr(identity, "google_calendar_id", "") or "",
        "google_calendar_email": _calendar_service_email(),
        "google_calendar_server": bool(GOOGLE_CALENDAR_CREDENTIALS),
        "enable_scheduling": bool(getattr(identity, "enable_scheduling", False)),
        # ── Integrações nativas (2026-09-05) — só marcadores, nunca segredos ──
        "google_oauth": _truthy(getattr(identity, "google_oauth_refresh_token", "")),
        "google_oauth_email": getattr(identity, "google_oauth_email", "") or "",
        "google_oauth_server": _google_oauth_server(),
        "google_sheet_url": getattr(identity, "google_sheet_url", "") or "",
        "webhook_url": getattr(identity, "webhook_url", "") or "",
        "webhook_secret": getattr(identity, "webhook_secret", "") or "",  # o dono precisa dele pra validar
        "meta_pixel_id": getattr(identity, "meta_pixel_id", "") or "",
        "meta_capi_token": _truthy(getattr(identity, "meta_capi_token", "")),
        "meta_access_token": _truthy(getattr(identity, "meta_access_token", "")),
        "instagram_connected": _truthy(getattr(identity, "instagram_access_token", "")),
        "instagram_username": getattr(identity, "instagram_username", "") or "",
        "instagram_server": _instagram_server(),
        "nuvemshop_connected": _truthy(getattr(identity, "nuvemshop_access_token", "")),
        "nuvemshop_store_name": getattr(identity, "nuvemshop_store_name", "") or "",
        "nuvemshop_store_url": getattr(identity, "nuvemshop_store_url", "") or "",
        "nuvemshop_server": _nuvemshop_server(),
        "hubspot_server": _hubspot_server(),
        "asaas_connected": _truthy(getattr(identity, "asaas_api_key", "")),
        "payment_provider": getattr(identity, "payment_provider", "") or "",
        # Capabilities resolvidas (2026-09-07): a sidebar do Cockpit decide
        # Agenda (schedule) / Vendas (sell_digital|sell_physical) por aqui.
        "capabilities_resolved": sorted(c.value for c in identity.capabilities_resolved),
    }


def _google_oauth_server() -> bool:
    from huma.services import google_oauth
    return google_oauth.is_configured()


def _instagram_server() -> bool:
    from huma.services import instagram_service
    return instagram_service.is_configured()


def _nuvemshop_server() -> bool:
    from huma.providers.inventory import nuvemshop_oauth
    return nuvemshop_oauth.is_configured()


def _hubspot_server() -> bool:
    from huma.providers.crm import hubspot_oauth
    return hubspot_oauth.is_configured()


def _calendar_service_email() -> str:
    """E-mail da conta de serviço do Google (o cliente compartilha a agenda com ele)."""
    try:
        from huma.services import scheduling_service as sched
        return sched.service_account_email()
    except Exception as e:
        log.warning(f"Calendar | e-mail da conta de serviço indisponível | {type(e).__name__}: {e}")
        return ""


@router.post("/api/integrations/{integration_id}/disconnect", tags=["Cockpit"])
async def integrations_disconnect(
    integration_id: str,
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Bloco C — desconecta uma integração: limpa tokens OAuth do ClientIdentity.

    Hoje suporta apenas as integrações dinâmicas que o cockpit lista:
    `bling` (limpa bling_*) e `pipedrive` (limpa crm_*). Outras (hubspot,
    nuvemshop, tray) ainda são hardcoded como Desconectadas — disconnect
    delas é no-op por agora.

    NÃO revoga o token no provider (Bling/Pipedrive ficam com token
    válido até expirar do lado deles). Apenas para o HUMA de usar.
    Disconnect total verdadeiro = revogar via API do provider — sprint
    futuro se virar requisito de compliance.
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    integration_id = (integration_id or "").strip().lower()

    if integration_id == "bling":
        updates = {
            "bling_access_token": "",
            "bling_refresh_token": "",
            "bling_token_expires_at": None,
        }
        # ERP fora = catálogo dele sai do conhecimento; itens do dono ficam.
        identity = await db.get_client(client_id)
        existing = (getattr(identity, "products_or_services", None) or []) if identity else []
        if any(isinstance(p, dict) and p.get("source") == "bling" for p in existing):
            from huma.core.catalog_sync import remove_store_items
            updates["products_or_services"] = remove_store_items(existing, source="bling")
    elif integration_id in ("pipedrive", "hubspot", "rdstation", "rd_station", "crm"):
        updates = {
            "crm_access_token": "",
            "crm_refresh_token": "",
            "crm_token_expires_at": None,
            "crm_provider": "",
            "crm_api_base_url": "",
            "crm_pipeline_id": "",
            "crm_stage_id": "",
            "crm_owner_id": "",
            "crm_api_token": "",
        }
    elif integration_id == "google":
        # Revoga no Google (best-effort) e volta a agenda pro legado.
        identity = await db.get_client(client_id)
        refresh = (getattr(identity, "google_oauth_refresh_token", "") or "") if identity else ""
        if refresh:
            from huma.services import google_oauth
            await google_oauth.revoke(refresh)
        try:
            from huma.services import scheduling_service as sched
            sched.invalidate_oauth_cache(client_id)
        except Exception as e:
            log.warning(f"Calendar | cache OAuth não invalidado | {type(e).__name__}: {e}")
        updates = {
            "google_oauth_refresh_token": "",
            "google_oauth_email": "",
            "google_sheet_id": "",
            "google_sheet_url": "",
        }
        cal = (getattr(identity, "google_calendar_id", "") or "") if identity else ""
        if cal.startswith("oauth:"):
            updates["google_calendar_id"] = ""
    elif integration_id == "instagram":
        updates = {
            "instagram_user_id": "",
            "instagram_username": "",
            "instagram_access_token": "",
            "instagram_token_expires_at": None,
        }
    elif integration_id == "nuvemshop":
        updates = {
            "nuvemshop_store_id": "",
            "nuvemshop_access_token": "",
            "nuvemshop_store_url": "",
            "nuvemshop_store_name": "",
        }
        # Loja fora = catálogo dela sai do conhecimento na hora; os itens
        # cadastrados pelo dono ficam (catalog_sync, regra 2026-09-07).
        identity = await db.get_client(client_id)
        existing = (getattr(identity, "products_or_services", None) or []) if identity else []
        if any(isinstance(p, dict) and p.get("source") == "nuvemshop" for p in existing):
            from huma.core.catalog_sync import remove_store_items
            updates["products_or_services"] = remove_store_items(existing)
    elif integration_id == "webhook":
        updates = {"webhook_url": "", "webhook_secret": ""}
    elif integration_id == "pixel":
        updates = {"meta_pixel_id": "", "meta_capi_token": ""}
    elif integration_id == "asaas":
        updates = {"asaas_api_key": "", "asaas_webhook_token": "", "payment_provider": ""}
    else:
        raise HTTPException(
            400,
            f"Integração '{integration_id}' não suporta disconnect ainda. "
            f"Disponíveis: bling, pipedrive, hubspot, google, instagram, nuvemshop, webhook, pixel, asaas.",
        )

    await db.update_client(client_id, updates)
    log.info(
        f"Cockpit disconnect | client_id={client_id} | integration={integration_id}"
    )
    return {"status": "ok", "integration": integration_id}


@router.get("/api/crm/status", tags=["Cockpit"])
async def crm_status(
    client_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(None),
) -> dict:
    """
    Status da conexão de CRM do cliente, pro Cockpit mostrar
    "Conectar Pipedrive" ou "✓ Conectado".

    Returns:
        connected: tem provider + token configurados
        provider: "pipedrive" | "rd_station" | ""
        pipeline_ready: pipeline/estágio detectados (zero-config OK)
        account_url: base da conta (Pipedrive) pra linkar, se houver
        connect_url: pra onde o botão "Conectar" deve apontar
    """
    await verify_api_key_manual(client_id, creds, huma_session)

    identity = await db.get_client(client_id)
    if identity is None:
        raise HTTPException(404, f"Cliente {client_id} não encontrado")

    provider = (getattr(identity, "crm_provider", "") or "").strip()
    has_token = bool(
        getattr(identity, "crm_access_token", "")
        or getattr(identity, "crm_api_token", "")
    )
    connected = bool(provider and has_token)
    pipeline_ready = bool(getattr(identity, "crm_pipeline_id", ""))

    return {
        "connected": connected,
        "provider": provider,
        "pipeline_ready": pipeline_ready,
        "account_url": getattr(identity, "crm_api_base_url", "") or "",
        "connect_url": f"/oauth/crm/{provider or 'pipedrive'}/start?client_id={client_id}",
        "connect_urls": {
            "pipedrive": f"/oauth/crm/pipedrive/start?client_id={client_id}",
            "hubspot": f"/oauth/crm/hubspot/start?client_id={client_id}",
        },
    }


# ── Identidade ──

@router.post("/api/clients/{client_id}/import-whatsapp", tags=["Identidade"])
async def import_whatsapp(client_id: str, payload: WhatsAppImportPayload, _=Depends(verify_api_key)):
    """Importa padrões de fala do dono via export do WhatsApp."""
    patterns = await ai.analyze_speech_patterns(payload.chat_text)
    if not patterns:
        raise HTTPException(500, "Erro na análise de padrões")

    await db.update_client(client_id, {"speech_patterns": patterns})
    return {"status": "imported", "preview": patterns[:500]}


# ── Sistema ──

@router.get("/health", tags=["Sistema"])
async def health():
    """Health check."""
    redis_ok = False
    db_ok = False
    try:
        redis_ok = await cache.ping()
    except Exception:
        pass
    try:
        db_ok = await db.ping()
    except Exception:
        pass
    return {
        "status": "running",
        "version": APP_VERSION,
        "redis": "ok" if redis_ok else "unavailable",
        "db": "ok" if db_ok else "unavailable",
    }


@router.get("/api/admin/loop-stats/{client_id}", tags=["Sistema"])
async def loop_stats(client_id: str, _=Depends(verify_api_key)):
    """
    Sprint 4 / item 34 — stats do detector de loop por cliente.

    Retorna contadores da hora atual: turns processados vs safety nets
    acionados. Ratio > 0.20 com >= 10 turns indica bug — mesmo critério
    que dispara o alerta CRITICAL no log.
    """
    from huma.services import loop_detector
    return await loop_detector.get_stats(client_id)


@router.get("/health/deep", tags=["Sistema"])
async def health_deep():
    """
    Sprint 3 / item 17 — Health check profundo pra observabilidade.

    Diferente de /health (usado pelo Railway, precisa ser rápido), este endpoint
    reporta saúde de cada dependência. Não faz chamadas externas pagas — apenas:
      - Pings baratos onde já existe (Redis, Supabase)
      - Checagem de presença de credencial (Anthropic, Twilio, MP, ElevenLabs, GCal)

    Não retorna valores de credenciais. HTTP 200 sempre — o monitor lê o campo
    `overall` (ok|degraded|down) pra decidir alerta.
    """
    from huma.config import (
        ANTHROPIC_API_KEY, MERCADOPAGO_ACCESS_TOKEN, MERCADOPAGO_WEBHOOK_SECRET,
        ELEVENLABS_API_KEY, GOOGLE_CALENDAR_CREDENTIALS,
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
        META_APP_ID, META_APP_SECRET,
    )

    services: dict[str, str] = {}

    # Críticos — checagem real de conexão
    try:
        services["redis"] = "ok" if await cache.ping() else "unavailable"
    except Exception:
        services["redis"] = "unavailable"

    try:
        services["supabase"] = "ok" if await db.ping() else "unavailable"
    except Exception:
        services["supabase"] = "unavailable"

    # Supabase AUTH (login do Cockpit) — checagem real via GoTrue /health.
    # Pega apikey inválida/mal colada e GoTrue fora do ar (bug de 2026-07-04:
    # anon key com quebra de linha derrubava o login sem nenhum sinal aqui).
    from huma.config import SUPABASE_ANON_KEY, SUPABASE_URL
    if not (SUPABASE_URL and SUPABASE_ANON_KEY):
        services["supabase_auth"] = "not_configured"
    else:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=5.0) as http:
                r = await http.get(
                    f"{SUPABASE_URL}/auth/v1/health",
                    headers={"apikey": SUPABASE_ANON_KEY},
                )
            services["supabase_auth"] = "ok" if r.status_code == 200 else "unavailable"
        except Exception as e:
            log.error(f"Health | supabase_auth | {type(e).__name__}: {e}")
            services["supabase_auth"] = "unavailable"

    # APIs externas — só checagem de presença de credencial (zero custo)
    services["anthropic"] = "configured" if ANTHROPIC_API_KEY else "not_configured"
    services["twilio"] = "configured" if (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN) else "not_configured"
    services["meta"] = "configured" if (META_APP_ID and META_APP_SECRET) else "not_configured"
    services["mercadopago"] = "configured" if MERCADOPAGO_ACCESS_TOKEN else "not_configured"
    services["mercadopago_webhook"] = "configured" if MERCADOPAGO_WEBHOOK_SECRET else "not_configured"
    services["elevenlabs"] = "configured" if ELEVENLABS_API_KEY else "not_configured"
    services["google_calendar"] = "configured" if GOOGLE_CALENDAR_CREDENTIALS else "not_configured"

    # Overall: down se algum crítico off, degraded se Redis/login/IA com problema
    if services["supabase"] == "unavailable":
        overall = "down"
    elif (
        services["redis"] == "unavailable"
        or services["anthropic"] == "not_configured"
        or services["supabase_auth"] == "unavailable"
    ):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": "running",
        "overall": overall,
        "version": APP_VERSION,
        "services": services,
    }


@router.get("/", tags=["Sistema"])
async def root(request: Request) -> RedirectResponse:
    """Raiz do domínio: logado vai pro Cockpit, deslogado vai pro login."""
    from huma.core.auth import SESSION_COOKIE_NAME, verify_session_token

    session_client = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))
    return RedirectResponse("/cockpit" if session_client else "/login", status_code=307)


# ================================================================
# PLAYGROUND (teste web + ativação WhatsApp)
# ================================================================

# Rate limit in-memory (sem Redis) — 20 req/min por IP
_playground_rate: dict[str, list[float]] = {}


@router.post("/api/playground/chat", tags=["Playground"])
async def playground_chat(request: Request):
    """
    Chat direto com Claude pra teste na web.
    Sem billing, sem buffer, sem Supabase.
    """
    import time
    from huma.config import AI_MODEL_FAST

    # Rate limit: 20 req/min por IP
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _playground_rate.get(ip, [])
    timestamps = [t for t in timestamps if now - t < 60]
    if len(timestamps) >= 20:
        raise HTTPException(429, "Muitas requisições. Aguarde 1 minuto.")
    timestamps.append(now)
    _playground_rate[ip] = timestamps

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")

    system_prompt = (body.get("system_prompt") or "").strip()
    messages = body.get("messages") or []

    if not system_prompt:
        raise HTTPException(400, "system_prompt é obrigatório")
    if not messages:
        raise HTTPException(400, "messages é obrigatório")
    if len(system_prompt) > 5000:
        raise HTTPException(400, "system_prompt muito longo (max 5000 chars)")
    if len(messages) > 50:
        raise HTTPException(400, "Máximo 50 mensagens")

    # Limpa mensagens pro formato da API
    clean_msgs = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "").strip()
        if role in ("user", "assistant") and content:
            clean_msgs.append({"role": role, "content": content})

    if not clean_msgs:
        raise HTTPException(400, "Nenhuma mensagem válida")

    try:
        client = ai._get_ai_client()
        response = await client.messages.create(
            model=AI_MODEL_FAST,
            max_tokens=600,
            system=system_prompt,
            messages=clean_msgs,
        )
        reply = response.content[0].text.strip()
        parts = [p.strip() for p in reply.split("\n\n") if p.strip()]
        if not parts:
            parts = [reply]

        return {"reply": reply, "reply_parts": parts}

    except Exception as e:
        log.error(f"Playground chat erro | {type(e).__name__}: {e}")
        return {"reply": "Ops, tive um probleminha. Tenta de novo!", "reply_parts": ["Ops, tive um probleminha. Tenta de novo!"]}


@router.post("/api/playground/activate", tags=["Playground"])
async def playground_activate(request: Request):
    """
    Salva config do playground no Supabase como client_id='default'
    pra testar via WhatsApp Twilio.

    Sprint 1 / item 8 — protegido em produção:
      - PLAYGROUND_ENABLED=false (default em prod) → 403
      - PLAYGROUND_ENABLED=true + PLAYGROUND_TOKEN setado → exige X-Playground-Token
    """
    from fastapi.concurrency import run_in_threadpool
    import hmac as _hmac
    from huma.config import PLAYGROUND_ENABLED, PLAYGROUND_TOKEN
    from huma.core.orchestrator import invalidate_client_cache

    # Trava em produção
    if not PLAYGROUND_ENABLED:
        raise HTTPException(403, "Playground desabilitado neste ambiente")

    # Se token configurado, exige header
    if PLAYGROUND_TOKEN:
        provided = request.headers.get("X-Playground-Token", "")
        if not provided or not _hmac.compare_digest(provided, PLAYGROUND_TOKEN):
            raise HTTPException(401, "Playground token ausente ou inválido")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")

    if not body.get("business_name", "").strip():
        raise HTTPException(400, "business_name é obrigatório")

    # Parseia products_or_services de texto livre pra lista de objetos
    products = []
    raw_products = body.get("products_or_services", "")
    if isinstance(raw_products, str) and raw_products.strip():
        for line in raw_products.strip().split("\n"):
            line = line.strip().lstrip("- •")
            if not line:
                continue
            # Tenta parsear "Nome R$100" ou "Nome: R$100" ou "Nome - R$100"
            import re
            match = re.search(r'[Rr]\$\s*([\d.,]+)', line)
            if match:
                price_str = match.group(1).replace(".", "").replace(",", ".")
                name = line[:match.start()].strip().rstrip(":-–—")
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0
                products.append({"name": name, "description": "", "price": price})
            else:
                products.append({"name": line, "description": "", "price": 0})
    elif isinstance(raw_products, list):
        products = raw_products

    # Parseia FAQ de texto livre pra lista de objetos
    faq = []
    raw_faq = body.get("faq", "")
    if isinstance(raw_faq, str) and raw_faq.strip():
        lines = raw_faq.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.upper().startswith("P:") or line.startswith("?"):
                question = line.split(":", 1)[-1].strip() if ":" in line else line.lstrip("? ").strip()
                answer = ""
                if i + 1 < len(lines) and (lines[i + 1].strip().upper().startswith("R:") or lines[i + 1].strip().startswith(">")):
                    answer = lines[i + 1].strip().split(":", 1)[-1].strip() if ":" in lines[i + 1] else lines[i + 1].strip().lstrip("> ").strip()
                    i += 1
                if question:
                    faq.append({"question": question, "answer": answer})
            i += 1
    elif isinstance(raw_faq, list):
        faq = raw_faq

    # Monta update pro Supabase
    update_data = {
        "business_name": body.get("business_name", "").strip(),
        "business_description": body.get("business_description", "").strip(),
        "category": body.get("category", "outros").strip(),
        "tone_of_voice": body.get("tone_of_voice", "").strip(),
        "working_hours": body.get("working_hours", "").strip(),
        "products_or_services": products,
        "faq": faq,
        "custom_rules": body.get("custom_rules", "").strip(),
        "forbidden_words": body.get("forbidden_words", []),
        "personality_traits": body.get("personality_traits", ["Acolhedor"]),
        "use_emojis": body.get("use_emojis", True),
        "max_discount_percent": body.get("max_discount_percent", 0),
        "accepted_payment_methods": body.get("accepted_payment_methods", ["pix"]),
        "max_installments": body.get("max_installments", 12),
        "onboarding_status": "active",
    }

    try:
        supa = db.get_supabase()

        # Atualiza client
        await run_in_threadpool(
            lambda: supa.table("clients").update(update_data).eq("client_id", "default").execute()
        )

        # Limpa conversas anteriores
        await run_in_threadpool(
            lambda: supa.table("conversations").delete().eq("client_id", "default").execute()
        )

        # Invalida cache
        invalidate_client_cache("default")

        log.info(f"Playground ativado | {update_data['business_name']} | categoria={update_data['category']}")

        return {
            "status": "activated",
            "message": "Configuração ativada! Mande uma mensagem no WhatsApp pra testar.",
        }

    except Exception as e:
        log.error(f"Playground activate erro | {type(e).__name__}: {e}")
        return {"status": "error", "message": "Erro ao ativar. Tenta de novo."}


# ================================================================
# WEBHOOK TWILIO (WhatsApp Sandbox)
# ================================================================

@router.post("/webhook/twilio", tags=["Webhook"])
async def twilio_webhook(request: Request, bg: BackgroundTasks):
    """
    Recebe mensagem do Twilio WhatsApp Sandbox.
    Detecta texto, imagem e áudio.
    """
    form = await request.form()
    form_dict = dict(form)

    parsed = wa.parse_twilio_webhook(form_dict)
    phone = parsed["phone"]
    text = parsed.get("text", "")
    media_url = parsed.get("media_url", "")

    # Detecta tipo de mídia
    media_content_type = form_dict.get("MediaContentType0", "")
    is_audio = media_content_type.startswith("audio/") if media_content_type else False
    is_image = media_content_type.startswith("image/") if media_content_type else False

    # Auth do Twilio pra baixar mídia protegida
    from huma.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    twilio_auth = None
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Se é áudio, transcreve
    if is_audio and media_url and not text.strip():
        from huma.services.transcription_service import transcribe_audio
        transcribed = await transcribe_audio(media_url, auth=twilio_auth)
        if transcribed:
            text = transcribed
            log.info(f"Áudio transcrito | {phone} | chars={len(text)} | preview={text[:60]}...")
            media_url = ""  # Não é imagem
        else:
            log.warning(f"Transcrição falhou | {phone} | url={media_url[:80]}")
            text = "[áudio do lead - transcrição indisponível]"

    # Se é imagem, baixa como base64 (URLs do Twilio são protegidas)
    final_image_url = ""
    if is_image and media_url:
        try:
            import httpx
            import base64
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(media_url, auth=twilio_auth, follow_redirects=True)
                if resp.status_code == 200 and resp.content:
                    b64 = base64.b64encode(resp.content).decode("utf-8")
                    ct = resp.headers.get("content-type", "image/jpeg")
                    final_image_url = f"data:{ct};base64,{b64}"
                    log.info(f"Imagem baixada | size={len(resp.content)} | type={ct}")
        except Exception as e:
            log.error(f"Download imagem erro | {e}")

    if not phone or not text.strip():
        return Response(
            content='<Response></Response>',
            media_type="application/xml",
        )

    payload = MessagePayload(
        client_id="default",
        phone=phone,
        text=text,
        image_url=final_image_url,
    )

    bg.add_task(handle_message, payload, bg)

    return Response(
        content='<Response></Response>',
        media_type="application/xml",
    )


# ================================================================
# INGESTÃO DE MÍDIA DE ENTRADA (áudio/imagem do lead) — Meta + Evolution
#
# Rodam em background pra o webhook responder rápido (a Meta espera 200
# ágil). Baixam a mídia pelo canal certo, transcrevem áudio / convertem
# imagem em data URL, e delegam pro handle_message (mesmo motor).
# ================================================================

async def _ingest_media_message(
    client_id: str,
    phone: str,
    media_type: str,
    caption: str,
    raw_bytes: Optional[bytes],
    content_type: str,
    bg: BackgroundTasks,
) -> None:
    """Monta o MessagePayload a partir de mídia já baixada e processa."""
    text = caption or ""
    image_url = ""

    if media_type == "audio":
        if raw_bytes:
            from huma.services.transcription_service import transcribe_bytes
            transcribed = await transcribe_bytes(raw_bytes)
            if transcribed:
                text = transcribed
        if not text:
            text = "[áudio do lead - transcrição indisponível]"
    elif media_type == "image":
        if raw_bytes:
            import base64
            b64 = base64.b64encode(raw_bytes).decode("utf-8")
            image_url = f"data:{content_type or 'image/jpeg'};base64,{b64}"
        if not raw_bytes and not text:
            text = "[imagem do lead - não consegui carregar]"

    payload = MessagePayload(client_id=client_id, phone=phone, text=text, image_url=image_url)
    await handle_message(payload, bg)


async def _ingest_meta_media(
    client_id: str, phone: str, media_type: str, media_id: str, caption: str, bg: BackgroundTasks,
) -> None:
    """Baixa mídia do Meta (Graph) e delega pra ingestão comum."""
    raw, ct = await wa.fetch_media_meta(client_id, media_id)
    await _ingest_media_message(client_id, phone, media_type, caption, raw, ct, bg)


async def _ingest_evolution_media(
    client_id: str, phone: str, media_type: str, message: dict, caption: str, bg: BackgroundTasks,
) -> None:
    """Baixa mídia do Evolution (base64) e delega pra ingestão comum."""
    raw, ct = await wa.fetch_media_evolution(client_id, message)
    await _ingest_media_message(client_id, phone, media_type, caption, raw, ct, bg)


# ================================================================
# WEBHOOK EVOLUTION API v2 (WhatsApp não-oficial, self-hosted)
#
# Fluxo:
#   1. Lead manda mensagem → Evolution dispara messages.upsert
#   2. Parseia (instance, phone, text) — ignora eco do próprio número e grupos
#   3. Descobre o cliente HUMA pela instância (instance → client_id)
#   4. Monta MessagePayload e delega pro handle_message (mesmo motor do resto)
#
# Auth: token compartilhado no header x-huma-webhook-token, configurado
# na criação da instância (evo_create_instance). Evolution v2 não assina
# o corpo, então o token é a barreira. EVOLUTION_WEBHOOK_TOKEN vazio =
# validação pulada (dev). Roteamento segue dependendo da instância
# existir cadastrada num cliente — instância desconhecida é ignorada.
# ================================================================

@router.post("/webhook/evolution", tags=["Webhook"])
async def evolution_webhook(request: Request, bg: BackgroundTasks):
    """Recebe mensagem do Evolution API v2 e delega pro motor."""
    from huma.core.auth import verify_evolution_token
    if not verify_evolution_token(request.headers.get("x-huma-webhook-token", "")):
        log.warning("Webhook Evolution REJEITADO | token inválido")
        raise HTTPException(401, "Token inválido")

    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "bad_json"}

    parsed = wa.parse_evolution_webhook(body)
    if not parsed:
        return {"status": "ignored", "reason": "no_message"}

    # Só processa ENTRADA do lead — ignora eco do próprio número e grupos.
    if parsed["from_me"] or parsed["is_group"]:
        return {"status": "ignored", "reason": "from_me_or_group"}

    instance = parsed["instance"]
    phone = parsed["phone"]
    text = parsed["text"]
    media_type = parsed.get("media_type", "")

    if not instance or not phone:
        return {"status": "ignored", "reason": "no_instance_or_phone"}

    client = await db.get_client_by_evolution_instance(instance)
    if not client:
        log.warning(f"Webhook Evolution | instância sem cliente | instance={instance}")
        return {"status": "ignored", "reason": "unknown_instance"}

    # Guarda o endereço EXATO de entrada (dígitos -> jid completo) pra que a
    # resposta saia no MESMO jid. Crítico pra contatos @lid: o número real é
    # mascarado pelo WhatsApp, então responder pros dígitos não entrega —
    # responder pro jid @lid entrega. TTL 30 dias cobre a vida da conversa.
    remote_jid = parsed.get("remote_jid") or ""
    if remote_jid:
        await cache.set_with_ttl(f"wajid:{client.client_id}:{phone}", remote_jid, ttl=2592000)

    # Atribuição de origem (first-touch): referral CTWA do externalAdReply
    # ou código #h/utm no texto. ANTES do desvio de mídia — anúncio pode
    # chegar como imagem com caption.
    referral = parsed.get("referral") or {}
    if attribution.has_signal(referral, text):
        bg.add_task(attribution.capture, client.client_id, phone, referral, text)

    # Áudio/imagem do lead: baixa + processa em background (responde já).
    if media_type in ("audio", "image"):
        bg.add_task(
            _ingest_evolution_media,
            client.client_id, phone, media_type, parsed.get("raw") or {}, text, bg,
        )
        log.info(f"Webhook Evolution | mídia {media_type} | client={client.client_id} | phone={phone}")
        return {"status": "received"}

    # Outras mídias (vídeo/doc): placeholder pra IA não ignorar o lead.
    if not text and media_type:
        text = f"[{media_type} do lead - processamento de mídia no Evolution em breve]"

    if not text:
        return {"status": "ignored", "reason": "empty"}

    payload = MessagePayload(client_id=client.client_id, phone=phone, text=text)
    bg.add_task(handle_message, payload, bg)
    log.info(
        f"Webhook Evolution | client={client.client_id} | instance={instance} | "
        f"phone={phone} | chars={len(text)}"
    )
    return {"status": "received"}


# ================================================================
# WEBHOOK META WHATSAPP CLOUD API (produção oficial)
#
# GET  /webhook/meta → verificação do webhook (hub.challenge) no setup
# POST /webhook/meta → recebe mensagens. Valida X-Hub-Signature-256 com
#                      o corpo CRU, roteia phone_number_id → client_id e
#                      delega pro handle_message (mesmo motor do resto).
#
# Uma notificação pode trazer várias mensagens (batch) — processa todas.
# Atualizações de status (sent/delivered/read/failed) viram telemetria do
# Escudo antiban via _ingest_meta_statuses (contadores Redis + opt-out).
# ================================================================

async def _ingest_meta_statuses(statuses: list[dict]) -> None:
    """
    Ingere statuses de entrega da Meta como telemetria do Escudo antiban.

    Grava contadores diários no Redis (saúde do número / circuit breaker
    das próximas fases) e trata os erros que importam:
      - 131050 (usuário recusou marketing): marca opt-out do lead —
        campanhas param de enviar pra ele (checado no outbound).
      - 131049 (Meta segurou pra proteger o ecossistema): alerta de
        qualidade — logado como warning pra aparecer cedo.

    Silent-fail por item: Redis é opcional e telemetria nunca pode
    derrubar o webhook.
    """
    day = datetime.utcnow().strftime("%Y%m%d")
    for st in statuses:
        pnid = st.get("phone_number_id", "")
        status = st.get("status", "")
        if not pnid or not status:
            continue

        client = await db.get_client_by_phone_number_id(pnid)
        if not client:
            continue
        cid = client.client_id
        phone = st.get("phone", "")
        code = st.get("error_code", 0)

        try:
            # 7 dias de janela: suficiente pra tendência de saúde do número
            await cache.incr_with_ttl(f"wastatus:{cid}:{status}:{day}", ttl=7 * 86400)
            if status == "failed" and code:
                await cache.incr_with_ttl(f"wastatus:{cid}:err{code}:{day}", ttl=7 * 86400)
        except Exception as e:
            log.warning(f"Telemetria status falhou | client={cid} | {type(e).__name__}: {e}")

        if status != "failed":
            continue

        if code == 131050:
            # Opt-out de marketing: reenviar derruba a nota do número.
            # Caminho único de gravação (Redis + Supabase durável).
            await shield.register_optout(cid, phone, "meta_131050")
            log.warning(
                f"Meta OPT-OUT marketing | client={cid} | phone={phone} | "
                f"lead recusou mensagens de marketing — suprimido de campanhas"
            )
        elif code == 131049:
            log.warning(
                f"Meta SEGUROU envio | client={cid} | phone={phone} | code=131049 | "
                f"limite de marketing por usuário — sinal de qualidade, reduzir frequência"
            )
        else:
            log.warning(
                f"Meta status FAILED | client={cid} | phone={phone} | "
                f"code={code} | {st.get('error_title', '')}"
            )


# Eventos que indicam MELHORA (log informativo, sem alarme)
_QUALITY_EVENTS_POSITIVOS = ("UPGRADE", "UNFLAGGED", "APPROVED")


async def _ingest_meta_quality_events(events: list[dict]) -> None:
    """
    Ingere eventos de qualidade da WABA (FLAGGED, DOWNGRADE, template
    PAUSED, restrição de conta...). Grava o último evento por cliente e
    invalida o cache de saúde — o badge do Cockpit reflete na hora.
    Silent-fail por item: telemetria nunca derruba o webhook.
    """
    for ev in events:
        waba_id = ev.get("waba_id", "")
        if not waba_id:
            continue
        try:
            client = await db.get_client_by_waba_id(waba_id)
        except Exception as e:
            log.warning(f"Quality event lookup falhou | waba={waba_id} | {type(e).__name__}: {e}")
            continue
        if not client:
            log.warning(f"Quality event | waba_id sem cliente | waba={waba_id} | field={ev.get('field')}")
            continue

        cid = client.client_id
        event = ev.get("event", "")
        field = ev.get("field", "")
        detail = " | ".join(
            p for p in (ev.get("template_name", ""), ev.get("detail", "")) if p
        )
        await shield.record_quality_event(cid, field, event, detail)

        if event.upper() in _QUALITY_EVENTS_POSITIVOS:
            log.info(f"Meta qualidade MELHOROU | client={cid} | {field}={event} | {detail}")
        else:
            log.warning(
                f"Meta qualidade ALERTA | client={cid} | {field}={event} | {detail} | "
                f"Escudo: cache de saúde invalidado, badge atualiza no Cockpit"
            )

@router.get("/webhook/meta", tags=["Webhook"])
async def meta_webhook_verify(request: Request):
    """Verificação do webhook Meta (handshake do setup no painel)."""
    from huma.config import META_WEBHOOK_VERIFY_TOKEN

    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token == META_WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook Meta verificado com sucesso")
        return Response(content=challenge, media_type="text/plain")

    log.warning(f"Webhook Meta verify falhou | mode={mode} | token_ok={token == META_WEBHOOK_VERIFY_TOKEN}")
    raise HTTPException(403, "Verificação do webhook falhou")


@router.post("/webhook/meta", tags=["Webhook"])
async def meta_webhook(request: Request, bg: BackgroundTasks):
    """Recebe mensagens da Meta Cloud API e delega pro motor."""
    # Corpo CRU pra validar a assinatura ANTES de parsear (re-serializar muda os bytes).
    raw = await request.body()

    from huma.core.auth import verify_meta_signature
    signature = request.headers.get("x-hub-signature-256", "")
    if not verify_meta_signature(raw, signature):
        log.warning("Webhook Meta REJEITADO | assinatura inválida")
        raise HTTPException(401, "Assinatura inválida")

    try:
        body = json.loads(raw)
    except Exception:
        return {"status": "ignored", "reason": "bad_json"}

    # Statuses de entrega (sent/delivered/read/failed) → telemetria do
    # Escudo antiban em background. Não bloqueia o processamento de mensagens.
    statuses = wa.parse_meta_statuses(body)
    if statuses:
        bg.add_task(_ingest_meta_statuses, statuses)

    # Eventos de qualidade/saúde (FLAGGED, DOWNGRADE, template PAUSED...)
    # → a Meta avisa ANTES do bloqueio; o Escudo escuta e reage.
    quality_events = wa.parse_meta_quality_events(body)
    if quality_events:
        bg.add_task(_ingest_meta_quality_events, quality_events)

    messages = wa.parse_meta_webhook(body)
    if not messages:
        if statuses or quality_events:
            return {
                "status": "received",
                "statuses": len(statuses),
                "quality_events": len(quality_events),
                "processed": 0,
            }
        return {"status": "ignored", "reason": "no_message"}

    processed = 0
    for m in messages:
        pnid = m["phone_number_id"]
        phone = m["phone"]
        text = m["text"]
        media_type = m.get("media_type", "")

        if not pnid or not phone:
            continue

        client = await db.get_client_by_phone_number_id(pnid)
        if not client:
            log.warning(f"Webhook Meta | phone_number_id sem cliente | pnid={pnid}")
            continue

        # Fase D: marca como lida em background (✓✓ azul pro lead).
        # Best-effort — nunca bloqueia nem atrasa o processamento.
        if m.get("message_id"):
            bg.add_task(wa.mark_as_read, m["message_id"], client.client_id)

        # Fase C (username): grava o mapeamento telefone↔BSUID first-touch.
        # É o "contact book" da HUMA — quando o lead adotar username e o
        # telefone sumir do webhook, este mapeamento mantém o histórico.
        if m.get("bsuid"):
            bg.add_task(db.set_conversation_bsuid, client.client_id, phone, m["bsuid"])

        # Atribuição de origem (first-touch): referral CTWA (lead veio de
        # anúncio click-to-WhatsApp) ou código #h/utm no texto. ANTES do
        # desvio de mídia — anúncio pode chegar como imagem com caption.
        referral = m.get("referral") or {}
        if attribution.has_signal(referral, text):
            bg.add_task(attribution.capture, client.client_id, phone, referral, text)

        media_id = m.get("media_id", "")

        # Áudio/imagem do lead: baixa + processa em background (responde já).
        if media_type in ("audio", "image") and media_id:
            bg.add_task(
                _ingest_meta_media,
                client.client_id, phone, media_type, media_id, text, bg,
            )
            processed += 1
            log.info(f"Webhook Meta | mídia {media_type} | client={client.client_id} | phone={phone}")
            continue

        # Outras mídias (vídeo/doc): placeholder pra IA não ignorar o lead.
        if not text and media_type:
            text = f"[{media_type} do lead - processamento de mídia no Meta em breve]"

        if not text:
            continue

        payload = MessagePayload(client_id=client.client_id, phone=phone, text=text)
        bg.add_task(handle_message, payload, bg)
        processed += 1
        log.info(
            f"Webhook Meta | client={client.client_id} | pnid={pnid} | "
            f"phone={phone} | chars={len(text)}"
        )

    return {"status": "received", "processed": processed}


# ================================================================
# WEBHOOK MERCADO PAGO (IPN — Instant Payment Notification)
#
# Fluxo:
#   1. Lead paga (Pix, boleto, cartão)
#   2. Mercado Pago chama POST /webhook/mercadopago
#   3. Consultamos API do MP pra confirmar (nunca confia no body)
#   4. Cruzamos com lead pelo phone (tabela payments)
#   5. Se approved: confirmação WhatsApp + funil "won" + notifica dono
# ================================================================

@router.post("/webhook/mercadopago", tags=["Webhook"])
async def mercadopago_webhook(request: Request, bg: BackgroundTasks):
    """
    Recebe notificação IPN do Mercado Pago.

    O MP envia:
    - type: "payment" → pagamento direto (Pix, boleto)
    - type: "merchant_order" → Checkout Pro (ignoramos, esperamos o payment)
    - data.id: ID do pagamento

    Sprint 1 / item 2 — valida x-signature antes de processar.
    Em modo dev (MERCADOPAGO_WEBHOOK_SECRET vazio), pula validação com warning.
    """
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)

    topic = body.get("type") or body.get("topic", "")
    action = body.get("action", "")

    log.info(f"Webhook MP recebido | type={topic} | action={action} | body={json.dumps(body)[:500]}")

    # Assinatura HUMA (recorrência): eventos de preapproval do MP.
    # subscription_preapproval = mudança de status (ativou/pausou/cancelou);
    # subscription_authorized_payment = cobrança do mês (credita conversas).
    if topic in ("subscription_preapproval", "subscription_authorized_payment"):
        resource_id = ""
        if "data" in body and isinstance(body["data"], dict):
            resource_id = str(body["data"].get("id", ""))
        if not resource_id:
            resource_id = str(body.get("id", ""))
        if not resource_id:
            log.warning(f"Webhook MP assinatura sem resource_id | topic={topic}")
            return {"status": "ignored", "reason": "no_resource_id"}

        from huma.core.auth import verify_mercadopago_signature
        x_signature = request.headers.get("x-signature", "")
        x_request_id = request.headers.get("x-request-id", "")
        if not verify_mercadopago_signature(x_signature, x_request_id, resource_id):
            log.warning(f"Webhook MP assinatura REJEITADO | id={resource_id}")
            raise HTTPException(401, "Assinatura inválida")

        from huma.services import subscription_service as subs
        bg.add_task(subs.process_subscription_event, topic, resource_id)
        return {"status": "received"}

    # Só processa notificações de pagamento
    if topic in ("payment", "payment.updated"):
        mp_payment_id = ""

        # Formato v2 (IPN novo): data.id
        if "data" in body and isinstance(body["data"], dict):
            mp_payment_id = str(body["data"].get("id", ""))

        # Formato v1 (IPN antigo): id no root
        if not mp_payment_id:
            mp_payment_id = str(body.get("id", ""))

        if not mp_payment_id:
            log.warning("Webhook MP — sem payment_id")
            return {"status": "ignored", "reason": "no_payment_id"}

        # Sprint 1 / item 2 — valida HMAC antes de processar.
        # Sem isso, fraudador pode forjar webhook e marcar lead como "won".
        from huma.core.auth import verify_mercadopago_signature
        x_signature = request.headers.get("x-signature", "")
        x_request_id = request.headers.get("x-request-id", "")
        if not verify_mercadopago_signature(x_signature, x_request_id, mp_payment_id):
            log.warning(
                f"Webhook MP REJEITADO | assinatura inválida | "
                f"payment_id={mp_payment_id} | sig_present={bool(x_signature)}"
            )
            raise HTTPException(401, "Assinatura inválida")

        # Processa em background pra responder rápido (MP espera 200 em <500ms)
        bg.add_task(_process_mp_payment, mp_payment_id)
        return {"status": "received"}

    if topic == "merchant_order":
        log.debug("Webhook MP — merchant_order ignorado (esperando payment)")
        return {"status": "ignored", "reason": "merchant_order"}

    log.debug(f"Webhook MP — tipo ignorado | type={topic}")
    return {"status": "ignored", "reason": f"type_{topic}"}


async def _process_mp_payment(mp_payment_id: str):
    """
    Background task: processa pagamento do Mercado Pago.

    1. Consulta API do MP pra confirmar status
    2. Atualiza tabela payments no Supabase
    3. Se approved: notifica lead + avança funil + notifica dono
    """
    try:
        result = await pay.process_payment_notification(mp_payment_id)

        if not result.get("processed"):
            # Cobrança de ASSINATURA da HUMA chegando como topic payment
            # (formato alternativo do MP pra preapproval com cartão).
            # Antes era descartada aqui — renovação paga sem crédito
            # bloqueava cliente adimplente (bug E2E 2026-08-16).
            ext_ref = result.get("external_reference", "") or ""
            if ext_ref.startswith("humasub|"):
                from huma.services import subscription_service as subs
                await subs.credit_subscription_charge(
                    str(mp_payment_id), ext_ref, result.get("status", ""),
                )
                return
            # Pacote de conversas extras (Pix avulso do Cockpit)
            if ext_ref.startswith("humapack|"):
                from huma.services import subscription_service as subs
                await subs.credit_pack_purchase(
                    str(mp_payment_id), ext_ref, result.get("status", ""),
                )
                return
            log.warning(f"MP payment não processado | id={mp_payment_id} | reason={result.get('reason', '?')}")
            return

        await handle_payment_result(result, str(mp_payment_id))

    except Exception as e:
        log.error(f"_process_mp_payment erro | mp_id={mp_payment_id} | {type(e).__name__}: {e}")


async def handle_payment_result(result: dict, payment_id: str) -> None:
    """
    Efeitos de uma cobrança processada (Mercado Pago OU Asaas): confirma
    pro lead, avança o funil pra won, notifica o dono e dispara os
    eventos de lead (webhook / planilha / pixel). Nunca levanta.
    """
    try:
        status = result["status"]
        client_id = result["client_id"]
        phone = result["phone"]
        lead_name = result.get("lead_name", "")
        amount_display = result.get("amount_display", "")
        method = result.get("method", "")
        mp_payment_id = payment_id

        if not client_id or not phone:
            log.error(f"Payment sem client_id ou phone | id={mp_payment_id}")
            return

        # ── PAGAMENTO APROVADO ──
        if status == "approved":
            log.info(
                f"VENDA CONFIRMADA | mp_id={mp_payment_id} | "
                f"lead={lead_name} | phone={phone} | {amount_display} | {method}"
            )

            # 1. Confirmação pro lead no WhatsApp
            first_name = lead_name.split()[0] if lead_name else "você"

            if method == "pix":
                msg = (
                    f"Pix de {amount_display} confirmado! "
                    f"Obrigado pela confiança, {first_name}! "
                    f"Já vou preparar tudo pra você."
                )
            elif method == "boleto":
                msg = (
                    f"Boleto de {amount_display} compensado! "
                    f"Tudo certo, {first_name}! "
                    f"Vou dar andamento no seu pedido."
                )
            else:
                msg = (
                    f"Pagamento de {amount_display} confirmado! "
                    f"Valeu, {first_name}! "
                    f"Já vou cuidar de tudo pra você."
                )

            try:
                await wa.send_text(phone, msg, client_id=client_id)
            except Exception as e:
                log.error(f"Erro enviando confirmação | {phone} | {e}")

            # 2. Avança funil pra "won" (+ vira cliente: CRM do dono)
            try:
                from huma.core.customers import mark_as_customer

                conv = await db.get_conversation(client_id, phone)
                if conv and mark_as_customer(conv, "payment"):
                    log.info(f"Customer | phone={phone} | motivo=payment")
                    if conv.stage == "won":
                        await db.save_conversation(conv)
                if conv and conv.stage != "won":
                    prev_stage = conv.stage
                    conv.stage = "won"
                    conv.history.append({
                        "role": "system",
                        "content": (
                            f"[PAGAMENTO CONFIRMADO] {amount_display} via {method}. "
                            f"MP ID: {mp_payment_id}. Funil: {prev_stage} → won."
                        ),
                    })
                    conv.last_message_at = datetime.utcnow()
                    await db.save_conversation(conv)
                    log.info(f"Funil | {phone} | {prev_stage} → won")

                    # Motor de aprendizado
                    try:
                        from huma.services.learning_engine import analyze_completed_conversation
                        import asyncio
                        asyncio.create_task(
                            analyze_completed_conversation(client_id, conv, "won")
                        )
                    except Exception:
                        pass
            except Exception as e:
                log.error(f"Erro atualizando funil | {phone} | {e}")

            # 3. Notifica dono do negócio (Sprint 5 / item 21 — respeita opt-in)
            client_data = None
            try:
                client_data = await db.get_client(client_id)
                if (
                    client_data
                    and client_data.owner_phone
                    and getattr(client_data, "notify_owner_on_payment", True)
                ):
                    owner_msg = (
                        f"💰 Venda confirmada!\n"
                        f"Lead: {lead_name or phone}\n"
                        f"Valor: {amount_display}\n"
                        f"Método: {method.upper()}\n"
                        f"Telefone: {phone}"
                    )
                    await wa.notify_owner(
                        client_data.owner_phone,
                        owner_msg,
                        client_id=client_id,
                    )
                    log.info(f"Dono notificado (pagamento) | {client_id} | lead={phone}")
            except Exception as e:
                log.error(f"Erro notificando dono | {e}")

            # 4. Webhook / planilha / pixel do cliente (fire-and-forget)
            try:
                from huma.services import lead_events

                if client_data is None:
                    client_data = await db.get_client(client_id)
                conv_ev = await db.get_conversation(client_id, phone)
                if client_data:
                    lead_events.fire(
                        client_data, conv_ev, "payment.approved",
                        value_cents=int(result.get("amount_cents") or 0),
                        method=method, payment_id=mp_payment_id,
                        service=result.get("description", "") or "",
                    )
            except Exception as e:
                log.error(f"LeadEvents payment falhou | {client_id} | {type(e).__name__}: {e}")

        # ── PAGAMENTO REJEITADO ──
        elif status == "rejected":
            log.warning(f"Pagamento rejeitado | mp_id={mp_payment_id} | phone={phone}")

            try:
                await wa.send_text(
                    phone,
                    "Ops, parece que teve um probleminha com o pagamento. "
                    "Quer tentar de novo ou usar outro método?",
                    client_id=client_id,
                )
            except Exception as e:
                log.error(f"Erro enviando rejeição | {phone} | {e}")

        # ── OUTROS STATUS ──
        else:
            log.info(f"Payment status={status} | id={mp_payment_id} | phone={phone}")

    except Exception as e:
        log.error(f"handle_payment_result erro | id={payment_id} | {type(e).__name__}: {e}")
