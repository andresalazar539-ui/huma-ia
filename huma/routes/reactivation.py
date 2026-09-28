# ================================================================
# huma/routes/reactivation.py — Reativação da base (2026-09-27)
#
# Tudo debaixo de /api/clients/{id}/outbound/reactivation, então a
# permissão é "disparos" (dono e administrativo), pelo mapa de
# huma/core/permissions.py.
#
#   GET    ""                     estado da conta + reativações
#   POST   "/import"              sobe a planilha (ou a lista colada)
#   GET    "/{rid}"               detalhe, números, custo
#   POST   "/{rid}/draft"         a HUMA escreve as mensagens
#   PUT    "/{rid}/messages"      grava a régua e envia pra Meta
#   POST   "/{rid}/test"          manda a mensagem pro WhatsApp do dono
#   POST   "/{rid}/start"         confirma e começa
#   POST   "/{rid}/pause|resume|cancel"
#   GET    "/{rid}/skipped.csv"   quem ficou de fora e por quê
#
# Trava de canal em DUAS camadas (regra do produto): aqui (403) e no
# motor (reactivation_engine.send_batch pausa se o canal não é oficial).
# ================================================================

from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from huma.core import contact_import
from huma.core import template_rules as rules
from huma.core.auth import bearer_scheme, verify_api_key
from huma.services import campaign_shield as shield
from huma.services import reactivation_engine as engine
from huma.services import wa_templates
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("reactivation_routes")
router = APIRouter(tags=["Reativação"])

_BASE = "/api/clients/{client_id}/outbound/reactivation"

OFFICIAL_ONLY_PT = (
    "A reativação só sai pelo WhatsApp oficial. Mandar mensagem em volume por número conectado "
    "por QR Code leva a bloqueio. Conecte o WhatsApp oficial em Integrações."
)
NOT_READY_PT = "A reativação ainda não foi ativada neste servidor. Fale com o suporte HUMA."
NOT_FOUND_PT = "Não achei essa reativação."

STATUS_LABELS: dict[str, str] = {
    engine.ST_DRAFT: "Rascunho",
    engine.ST_WAITING: "Esperando a Meta aprovar",
    engine.ST_RUNNING: "Enviando",
    engine.ST_PAUSED: "Pausada",
    engine.ST_DONE: "Terminada",
    engine.ST_CANCELLED: "Encerrada",
}


class DraftBody(BaseModel):
    goal: str = Field(default="", max_length=400)
    steps: int = Field(default=2, ge=1, le=rules.MAX_STEPS)


class MessagesBody(BaseModel):
    steps: list[dict] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    risk_accepted: bool = False


class TestBody(BaseModel):
    step: int = Field(default=0, ge=0, le=rules.MAX_STEPS - 1)


class StartBody(BaseModel):
    consent: bool = False
    assign_mode: str = Field(default="", max_length=20)
    assign_to: str = Field(default="", max_length=254)
    hour_start: Optional[int] = None
    hour_end: Optional[int] = None
    weekend: Optional[bool] = None


def _actor_email(creds: Any, huma_session: Optional[str]) -> str:
    from huma.routes.api import _session_email
    return _session_email(creds, huma_session)


def _require_official(client: Any) -> None:
    if not engine.is_official(client):
        raise HTTPException(403, OFFICIAL_ONLY_PT)


async def _load(client_id: str, rid: str) -> dict:
    reactivation = await engine.get(client_id, rid)
    if reactivation is None:
        raise HTTPException(404, NOT_FOUND_PT)
    return reactivation


def _public(reactivation: dict) -> dict:
    """A reativação do jeito que a tela precisa (sem a lista de rejeitados, que é grande)."""
    summary = dict(reactivation.get("import_summary") or {})
    summary.pop("rejeitados_planilha", None)
    summary["por_motivo"] = [
        {"id": k, "label": contact_import.REASON_LABELS.get(k, k), "total": v}
        for k, v in sorted((summary.get("por_motivo") or {}).items(), key=lambda kv: -kv[1])
    ]
    steps = []
    for s in reactivation.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append({
            "body": s.get("body", ""),
            "delay_days": int(s.get("delay_days") or 0),
            "status": s.get("status") or rules.STATUS_DRAFT,
            "status_label": rules.STATUS_LABELS.get(s.get("status") or rules.STATUS_DRAFT, ""),
            "reason": s.get("reason") or "",
            "rewritten": bool(s.get("rewritten")),
            "template_name": s.get("template_name") or "",
        })
    status = reactivation.get("status") or engine.ST_DRAFT
    return {
        "id": reactivation.get("id"),
        "name": reactivation.get("name") or "",
        "goal": reactivation.get("goal") or "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "pause_reason": reactivation.get("pause_reason") or "",
        "pause_label": engine.PAUSE_LABELS.get(reactivation.get("pause_reason") or "", ""),
        "steps": steps,
        "columns": list(reactivation.get("columns") or []),
        "import": summary,
        "assign_mode": reactivation.get("assign_mode") or "ninguem",
        "assign_to": reactivation.get("assign_to") or "",
        "hour_start": int(reactivation.get("hour_start") or 9),
        "hour_end": int(reactivation.get("hour_end") or 19),
        "weekend": bool(reactivation.get("weekend")),
        "created_at": reactivation.get("created_at"),
        "started_at": reactivation.get("started_at"),
        "finished_at": reactivation.get("finished_at"),
    }


async def _detail(client: Any, reactivation: dict, gate_state: Optional[dict] = None) -> dict:
    steps = len([s for s in (reactivation.get("steps") or []) if isinstance(s, dict)]) or 1
    data = _public(reactivation)
    data["numbers"] = await engine.counts(reactivation["id"], steps)
    data["estimate"] = await engine.estimate_for(client, reactivation, gate_state)
    data["sales"] = 0
    if data["numbers"]["responderam"]:
        data["sales"] = await engine.sales_from(client.client_id, reactivation["id"])
    return data


@router.get(_BASE)
async def reactivation_home(client_id: str, client=Depends(verify_api_key)) -> dict:
    """Estado da conta (canal, assinatura, saúde, limite, preço) e as reativações."""
    state = await engine.gate(client)
    items = []
    if state["ready"]:
        for row in await engine.list_for_client(client_id):
            item = _public(row)
            if row.get("status") != engine.ST_DRAFT:
                item["numbers"] = await engine.counts(row["id"], len(item["steps"]) or 1)
            items.append(item)
    return {"gate": state, "items": items}


@router.post(_BASE + "/import")
async def reactivation_import(
    client_id: str,
    file: Optional[UploadFile] = File(default=None),
    text: str = Form(default=""),
    default_ddd: str = Form(default=""),
    include_customers: bool = Form(default=False),
    name: str = Form(default=""),
    client=Depends(verify_api_key),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Lê a planilha (ou a lista colada), limpa, cruza com o que a HUMA sabe e cria o rascunho."""
    _require_official(client)
    if not await engine.tables_ready():
        raise HTTPException(503, NOT_READY_PT)

    if file is not None and (file.filename or ""):
        blob = await file.read()
        if len(blob) > contact_import.MAX_FILE_BYTES:
            raise HTTPException(400, "O arquivo passa de 5 MB. Divida a planilha em partes menores.")
        result = contact_import.parse_contacts(blob, file.filename or "", default_ddd)
        source = file.filename or "arquivo"
    elif (text or "").strip():
        if len(text) > contact_import.MAX_FILE_BYTES:
            raise HTTPException(400, "A lista colada está grande demais. Suba como planilha.")
        result = contact_import.parse_contacts(text, "", default_ddd)
        source = "lista colada"
    else:
        raise HTTPException(400, "Suba uma planilha ou cole a lista de contatos.")

    if result.error:
        raise HTTPException(400, result.error)
    if not result.contacts:
        raise HTTPException(
            400,
            "Nenhum número dessa planilha pôde ser usado. Confira se os telefones têm DDD e são de celular.",
        )

    created = await engine.create_from_import(
        client, result, name=name, include_customers=include_customers,
        actor_email=_actor_email(creds, huma_session),
    )
    if created is None:
        raise HTTPException(502, "Não consegui guardar a lista agora. Tente de novo em instantes.")
    log.info(
        f"Reativação | importação pela tela | client={client_id} | id={created['id']} | fonte={source} | "
        f"linhas={result.total_rows}"
    )
    state = await engine.gate(client)
    return {"gate": state, "reactivation": await _detail(client, created, state)}


@router.get(_BASE + "/{rid}")
async def reactivation_detail(client_id: str, rid: str, client=Depends(verify_api_key)) -> dict:
    """Detalhe, números e custo. Atualiza o status das mensagens na Meta quando estão em análise."""
    reactivation = await _load(client_id, rid)
    waiting = any(
        isinstance(s, dict) and s.get("status") == rules.STATUS_PENDING
        for s in reactivation.get("steps") or []
    )
    if waiting and engine.is_official(client):
        reactivation = await engine.refresh_templates(client, reactivation, ask_meta=True)
    state = await engine.gate(client)
    return {"gate": state, "reactivation": await _detail(client, reactivation, state)}


@router.post(_BASE + "/{rid}/draft")
async def reactivation_draft(client_id: str, rid: str, body: DraftBody, client=Depends(verify_api_key)) -> dict:
    """A HUMA escreve as mensagens da régua pra este negócio e esta lista."""
    _require_official(client)
    reactivation = await _load(client_id, rid)
    if reactivation.get("status") not in (engine.ST_DRAFT, engine.ST_WAITING, engine.ST_PAUSED):
        raise HTTPException(409, "Essa reativação já começou. As mensagens não podem mais mudar.")
    summary = reactivation.get("import_summary") or {}
    drafted = await wa_templates.draft_messages(
        client, body.goal, list(reactivation.get("columns") or []),
        [s for s in (summary.get("amostra") or []) if isinstance(s, dict)], body.steps,
    )
    await engine._update(rid, {"goal": body.goal.strip()[:400]})
    delays = [0] + [rules.DEFAULT_DELAYS[i] - rules.DEFAULT_DELAYS[i - 1] for i in range(1, len(drafted["bodies"]))]
    return {
        "by_ai": drafted["by_ai"],
        "steps": [
            {"body": b, "delay_days": delays[i], "problems": drafted["problems"][i]}
            for i, b in enumerate(drafted["bodies"])
        ],
        "optout_button": rules.OPTOUT_BUTTON_TEXT,
        "max_chars": rules.MAX_BODY_CHARS,
    }


@router.put(_BASE + "/{rid}/messages")
async def reactivation_messages(client_id: str, rid: str, body: MessagesBody, client=Depends(verify_api_key)) -> dict:
    """Confere as mensagens (regras da Meta + Escudo), grava a régua e envia pra aprovação."""
    _require_official(client)
    reactivation = await _load(client_id, rid)
    if reactivation.get("status") not in (engine.ST_DRAFT, engine.ST_WAITING, engine.ST_PAUSED):
        raise HTTPException(409, "Essa reativação já começou. As mensagens não podem mais mudar.")

    steps = rules.normalize_steps(body.steps)
    if not steps:
        raise HTTPException(400, "Escreva pelo menos uma mensagem.")
    problems = [rules.validate_body(s["body"]) for s in steps]
    if any(problems):
        return {"ok": False, "problems": problems, "verdicts": [], "error": "Tem mensagem que a Meta recusaria. Veja o que ajustar em cada uma."}

    verdicts = []
    for step in steps:
        sample = rules.render(step["body"], rules.params_for(step["body"], "Maria"))
        verdicts.append(await shield.review_campaign(client_id, sample))
    if any(v.get("bloqueio_definitivo") for v in verdicts):
        raise HTTPException(403, "Uma das mensagens trata de assunto que a Meta proíbe. Ela não pode ser enviada.")
    risky = [v for v in verdicts if v.get("risco") in ("amarelo", "vermelho")]
    if risky and not body.risk_accepted:
        return {
            "ok": False, "problems": problems, "verdicts": verdicts, "needs_confirmation": True,
            "error": "O Escudo achou risco em uma das mensagens. Veja a sugestão ou confirme que quer enviar assim.",
        }

    allowed = set(reactivation.get("columns") or []) | set((reactivation.get("import_summary") or {}).get("colunas") or [])
    mapping = [c for c in body.columns if c in allowed][:2] or None
    result = await engine.set_messages(client, reactivation, steps, mapping)
    fresh = await engine.get(client_id, rid) or reactivation
    return {
        "ok": result["ok"], "problems": result["problems"], "verdicts": verdicts,
        "error": result["error"], "reactivation": await _detail(client, fresh),
    }


@router.post(_BASE + "/{rid}/test")
async def reactivation_test(client_id: str, rid: str, body: TestBody, client=Depends(verify_api_key)) -> dict:
    """
    Manda a mensagem aprovada pro WhatsApp do dono, exatamente como o
    contato vai receber. É uma mensagem de verdade: a Meta cobra.
    """
    _require_official(client)
    reactivation = await _load(client_id, rid)
    steps = [s for s in (reactivation.get("steps") or []) if isinstance(s, dict)]
    if body.step >= len(steps):
        raise HTTPException(400, "Essa mensagem não existe na régua.")
    step = steps[body.step]
    owner_phone = (getattr(client, "owner_phone", "") or "").strip()
    owner_name = contact_import.first_name(getattr(client, "owner_name", "") or "") or "Maria"
    sample = ((reactivation.get("import_summary") or {}).get("amostra") or [{}])[0]
    params = rules.params_for(step["body"], owner_name, sample, reactivation.get("columns") or [])
    text = rules.render(step["body"], params)

    if step.get("status") != rules.STATUS_APPROVED:
        return {
            "sent": False, "text": text, "status": step.get("status") or rules.STATUS_DRAFT,
            "reason": "A Meta ainda está analisando essa mensagem. Dá pra testar assim que ela aprovar.",
        }
    if not owner_phone:
        raise HTTPException(400, "Cadastre o seu WhatsApp no Perfil pra receber o teste.")
    message_id = await wa.send_template(
        owner_phone, step.get("template_name", ""), params, client_id=client_id, language=rules.LANGUAGE,
    )
    log.info(
        f"Reativação | teste pro dono | client={client_id} | id={rid} | passo={body.step + 1} | "
        f"aceito_pelo_canal={bool(message_id)}"
    )
    return {
        "sent": bool(message_id), "text": text, "status": rules.STATUS_APPROVED,
        "reason": "" if message_id else "A Meta não aceitou o envio agora. Confira a conexão em Integrações.",
    }


@router.post(_BASE + "/{rid}/start")
async def reactivation_start(
    client_id: str,
    rid: str,
    body: StartBody,
    client=Depends(verify_api_key),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    huma_session: Optional[str] = Cookie(default=None),
) -> dict:
    """Confirma a reativação. Começa na hora ou assim que a Meta aprovar as mensagens."""
    _require_official(client)
    reactivation = await _load(client_id, rid)
    reactivation = await engine.refresh_templates(client, reactivation, ask_meta=True)
    result = await engine.start(
        client, reactivation, body.consent, actor_email=_actor_email(creds, huma_session),
        assign_mode=body.assign_mode, assign_to=body.assign_to,
        hour_start=body.hour_start, hour_end=body.hour_end, weekend=body.weekend,
    )
    if not result["ok"]:
        raise HTTPException(400, result["error"])
    fresh = await engine.get(client_id, rid) or reactivation
    return {"status": result["status"], "reactivation": await _detail(client, fresh)}


@router.post(_BASE + "/{rid}/pause")
async def reactivation_pause(client_id: str, rid: str, client=Depends(verify_api_key)) -> dict:
    """Pausa. A fila fica guardada."""
    reactivation = await _load(client_id, rid)
    if not await engine.pause(reactivation):
        raise HTTPException(409, "Essa reativação não está em andamento.")
    fresh = await engine.get(client_id, rid) or reactivation
    return {"reactivation": await _detail(client, fresh)}


@router.post(_BASE + "/{rid}/resume")
async def reactivation_resume(client_id: str, rid: str, client=Depends(verify_api_key)) -> dict:
    """Retoma de onde parou."""
    _require_official(client)
    reactivation = await _load(client_id, rid)
    result = await engine.resume(client, reactivation)
    if not result["ok"]:
        raise HTTPException(409, result["error"])
    fresh = await engine.get(client_id, rid) or reactivation
    return {"reactivation": await _detail(client, fresh)}


@router.post(_BASE + "/{rid}/cancel")
async def reactivation_cancel(client_id: str, rid: str, client=Depends(verify_api_key)) -> dict:
    """Encerra de vez. Quem estava na fila não recebe mais nada."""
    reactivation = await _load(client_id, rid)
    if not await engine.cancel(reactivation):
        raise HTTPException(409, "Essa reativação já terminou.")
    fresh = await engine.get(client_id, rid) or reactivation
    return {"reactivation": await _detail(client, fresh)}


@router.get(_BASE + "/{rid}/skipped.csv")
async def reactivation_skipped(client_id: str, rid: str, client=Depends(verify_api_key)) -> Response:
    """Planilha de quem ficou de fora, com o motivo de cada um."""
    reactivation = await _load(client_id, rid)
    content = await engine.skipped_csv(reactivation)
    return Response(
        content="﻿" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ficaram-de-fora-{rid}.csv"'},
    )
