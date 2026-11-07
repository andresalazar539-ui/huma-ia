"""
Actions que nascem das conexões por token (2026-11-06, huma/integrations):

- `send_contract`  (kind "signature": ZapSign, Clicksign, D4Sign): cria o contrato a
  partir do modelo do dono com os dados do lead e manda o link de assinatura.
- `lookup_charges` (kind "billing": Asaas, Vindi, Superlógica): o cliente mandou
  o CPF/CNPJ; lista as cobranças em aberto com link/Pix da segunda via.
- `lookup_case`    (kind "legal": ADVBOX, Projuris, Astrea): "como está meu
  processo?"; lista os processos do CPF com o último andamento.

Desenho: `action_lines(identity)` entra na descrição de `actions` da tool SÓ quando
há provider desse tipo conectado (SE/QUANDO; cliente sem conexão não vê a linha).
`handle(...)` executa, manda a mensagem factual ao lead (valores, datas, links:
nada de prosa inventada), grava no histórico o que o lead viu e um marker com
instrução pro turno seguinte. Nunca levanta: devolve {"executed": bool, ...}.
"""
from __future__ import annotations

import re
from typing import Any

from huma.integrations import creds_for, first_connected_of_kind, provider_for
from huma.services import db_service as db
from huma.services import whatsapp_service as wa
from huma.utils.logger import get_logger

log = get_logger("connection_actions")

ACTION_TYPES: tuple[str, ...] = ("send_contract", "lookup_charges", "lookup_case")

_DOC_RE = re.compile(r"\d")


def _digits(value: Any) -> str:
    return "".join(_DOC_RE.findall(str(value or "")))


def _brl(cents: int) -> str:
    try:
        v = int(cents) / 100
    except (TypeError, ValueError):
        return "R$ ?"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date_br(iso: str) -> str:
    s = str(iso or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[0:4]}"
    return s


def action_lines(identity: Any) -> list[str]:
    """Linhas da tool (SE/QUANDO) só pras conexões que este cliente tem."""
    if identity is None:
        return []
    lines: list[str] = []
    sig = first_connected_of_kind(identity, "signature")
    if sig is not None:
        meta = (creds_for(identity, sig.id).get("meta") or {})
        templates = meta.get("templates") if isinstance(meta, dict) else None
        names = ""
        if isinstance(templates, list) and templates:
            names = "; ".join(f"'{t.get('name', '')}'" for t in templates[:6] if isinstance(t, dict) and t.get("name"))
        lines.append(
            "- type='send_contract': lead_name (nome completo), lead_email, lead_cpf (só se o lead já disse), "
            "template (nome do modelo" + (f": {names}" if names else "") + "), fields (dict com variáveis do modelo que "
            "você souber: valor, serviço, data). Emita SOMENTE depois que o lead ACEITOU fechar e confirmou o nome "
            f"completo. O sistema cria o contrato no {sig.name} e manda o link de assinatura aqui na conversa. "
            "NUNCA cole link nem prometa prazo de assinatura; NUNCA emita duas vezes para o mesmo contrato."
        )
    bil = first_connected_of_kind(identity, "billing")
    if bil is not None:
        lines.append(
            "- type='lookup_charges': cpf_cnpj (só dígitos). Emita QUANDO o cliente pedir boleto, segunda via, "
            "'quanto estou devendo' ou 'minha fatura' E já tiver informado o CPF ou CNPJ. Se ainda não informou, "
            f"peça o CPF antes e NÃO emita. O sistema consulta o {bil.name} e manda as cobranças em aberto com o link "
            "de pagamento. NUNCA invente valor, vencimento ou link."
        )
    leg = first_connected_of_kind(identity, "legal")
    if leg is not None:
        lines.append(
            "- type='lookup_case': cpf_cnpj (só dígitos). Emita QUANDO o cliente perguntar como está o processo "
            "dele, andamento ou prazo, E já tiver informado o CPF. Se não informou, peça o CPF antes e NÃO emita. "
            f"O sistema consulta o {leg.name} e responde com o último andamento real. NUNCA invente andamento, "
            "prazo ou decisão; NUNCA dê opinião jurídica sobre o resultado."
        )
    return lines


def _record(conv: Any, text: str, **extra: Any) -> None:
    """Mesmo formato de orchestrator._record_sent: o Cockpit mostra o que o lead viu."""
    if conv is None:
        return
    entry = {"role": "assistant", "content": text or "📎", "by": "ai"}
    entry.update({k: v for k, v in extra.items() if v})
    conv.history.append(entry)


async def _send(phone: str, text: str, client_id: str, conv: Any) -> bool:
    try:
        await wa.send_text(phone, text, client_id=client_id)
    except Exception as e:
        log.error(f"Conexão action | envio falhou | {phone} | {type(e).__name__}: {e}")
        return False
    _record(conv, text)
    return True


async def handle(phone: str, action: dict, client_data: Any, conv: Any) -> dict:
    """Dispatcher das actions de conexão. Nunca levanta."""
    kind = str((action or {}).get("type") or "")
    try:
        if kind == "send_contract":
            return await _send_contract(phone, action, client_data, conv)
        if kind == "lookup_charges":
            return await _lookup_charges(phone, action, client_data, conv)
        if kind == "lookup_case":
            return await _lookup_case(phone, action, client_data, conv)
    except Exception as e:
        log.error(f"Conexão action | erro | {phone} | type={kind} | {type(e).__name__}: {e}")
        return {"executed": False, "status": "error", "detail": f"{type(e).__name__}: {e}"}
    return {"executed": False, "status": "unknown_action"}


async def _save(conv: Any) -> None:
    try:
        await db.save_conversation(conv)
    except Exception as e:
        log.error(f"Conexão action | save falhou | {type(e).__name__}: {e}")


# ----------------------------------------------------------------
# send_contract
# ----------------------------------------------------------------

async def _send_contract(phone: str, action: dict, client_data: Any, conv: Any) -> dict:
    cid = getattr(client_data, "client_id", "") or ""
    spec = first_connected_of_kind(client_data, "signature")
    provider = provider_for(client_data, spec.id) if spec else None
    if provider is None:
        return {"executed": False, "status": "no_provider"}

    # Dedup: um contrato por conversa enquanto o anterior não foi assinado/recusado.
    for m in reversed(getattr(conv, "history", []) or []):
        c = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(c, str) and c.startswith("[CONTRATO ENVIADO"):
            log.info(f"send_contract | dedup | {phone}")
            return {"executed": False, "status": "dedup"}
        if isinstance(c, str) and c.startswith(("[CONTRATO ASSINADO", "[CONTRATO RECUSADO")):
            break

    meta = creds_for(client_data, spec.id).get("meta") or {}
    templates = meta.get("templates") if isinstance(meta, dict) and isinstance(meta.get("templates"), list) else []
    wanted = str(action.get("template") or action.get("template_id") or "").strip().lower()
    template_id = ""
    for t in templates:
        if not isinstance(t, dict):
            continue
        if wanted and (wanted == str(t.get("id", "")).lower() or wanted in str(t.get("name", "")).lower()):
            template_id = str(t.get("id", ""))
            break
    if not template_id:
        default = str(meta.get("default_template_id") or "") if isinstance(meta, dict) else ""
        template_id = default or (str(templates[0].get("id", "")) if templates and isinstance(templates[0], dict) else "")
    if not template_id:
        log.warning(f"send_contract | sem modelo cadastrado | client={cid}")
        return {"executed": False, "status": "no_template"}

    lead_name = str(action.get("lead_name") or getattr(conv, "lead_name_canonical", "") or "").strip()
    lead_email = str(action.get("lead_email") or getattr(conv, "lead_email", "") or "").strip()
    lead_phone = _digits(getattr(conv, "lead_whatsapp", "") or phone)
    signer = {"name": lead_name, "email": lead_email, "phone": lead_phone, "cpf": _digits(action.get("lead_cpf"))}
    fields = action.get("fields") if isinstance(action.get("fields"), dict) else {}
    if not lead_name:
        return {"executed": False, "status": "missing_name"}

    res = await provider.create_from_template(template_id, signer, fields)
    if res.get("status") != "ok" or not (res.get("sign_url") or res.get("delivery")):
        log.warning(f"send_contract | provedor recusou | client={cid} | {res}")
        return {"executed": False, "status": res.get("status", "error"), "detail": res.get("detail", "")}

    url = str(res.get("sign_url") or "")
    first = lead_name.split(" ")[0]
    if url:
        text = (
            f"Seu contrato está pronto, {first}. É só abrir o link e assinar pelo celular, "
            f"leva menos de 2 minutos:\n\n{url}"
        )
    else:
        # Provedor que entrega o link por conta própria (Clicksign manda por e-mail/WhatsApp).
        via = str(res.get("delivery") or "e-mail")
        text = (
            f"Seu contrato está pronto, {first}. O link de assinatura acabou de ir pro seu {via}"
            + (f" ({lead_email})" if via == "e-mail" and lead_email else "")
            + ". É só abrir e assinar pelo celular, leva menos de 2 minutos."
        )
    sent = await _send(phone, text, cid, conv)
    if sent:
        _record(conv, f"[CONTRATO ENVIADO {spec.id} doc={res.get('document_id', '')} para {lead_name}. "
                      "O link de assinatura JÁ foi enviado. SE o lead disser que assinou, agradeça e siga; "
                      "SE perguntar do link, diga que está na mensagem acima; NUNCA gere outro contrato.]")
        await _save(conv)
    return {"executed": sent, "status": "ok" if sent else "send_failed", "document_id": res.get("document_id", "")}


# ----------------------------------------------------------------
# lookup_charges
# ----------------------------------------------------------------

async def _lookup_charges(phone: str, action: dict, client_data: Any, conv: Any) -> dict:
    cid = getattr(client_data, "client_id", "") or ""
    spec = first_connected_of_kind(client_data, "billing")
    provider = provider_for(client_data, spec.id) if spec else None
    if provider is None:
        return {"executed": False, "status": "no_provider"}
    doc = _digits(action.get("cpf_cnpj") or action.get("cpf") or action.get("cnpj"))
    if len(doc) not in (11, 14):
        return {"executed": False, "status": "missing_document"}

    res = await provider.list_open_charges(doc)
    status = res.get("status")
    if status == "not_found":
        text = "Não encontrei nenhum cadastro com esse CPF aqui no sistema. Pode conferir os números pra mim?"
        marker = f"[COBRANCA CONSULTADA {spec.id}: CPF não encontrado. SE o lead corrigir o CPF, emita lookup_charges de novo.]"
    elif status != "ok":
        log.warning(f"lookup_charges | erro | client={cid} | {res}")
        return {"executed": False, "status": status or "error", "detail": res.get("detail", "")}
    else:
        charges = [c for c in (res.get("charges") or []) if isinstance(c, dict)]
        if not charges:
            text = "Boa notícia: não tem nenhuma cobrança em aberto no seu CPF. Está tudo em dia."
            marker = f"[COBRANCA CONSULTADA {spec.id}: nenhuma cobrança em aberto. NÃO invente valores.]"
        else:
            lines = []
            for c in charges[:6]:
                due = _date_br(c.get("due_date", ""))
                tag = " (vencida)" if c.get("overdue") else ""
                desc = str(c.get("description") or "Cobrança")[:60]
                link = str(c.get("link") or "")
                pix = str(c.get("pix_code") or "")
                item = f"• {desc}: {_brl(c.get('value_cents', 0))}, vence {due}{tag}"
                if link:
                    item += f"\n  Pagar: {link}"
                elif pix:
                    item += f"\n  Pix copia e cola: {pix}"
                lines.append(item)
            name = str(res.get("customer_name") or "").split(" ")[0]
            head = f"Encontrei {len(charges)} cobrança{'s' if len(charges) > 1 else ''} em aberto" + (f", {name}" if name else "") + ":"
            text = head + "\n\n" + "\n".join(lines)
            marker = (
                f"[COBRANCA CONSULTADA {spec.id}: {len(charges)} em aberto, total "
                f"{_brl(sum(int(c.get('value_cents') or 0) for c in charges))}. Os links JÁ foram enviados. "
                "SE o lead disser que pagou, diga que a confirmação chega automaticamente; NÃO gere Pix novo nem repita os links.]"
            )
    sent = await _send(phone, text, cid, conv)
    if sent:
        _record(conv, marker)
        await _save(conv)
    return {"executed": sent, "status": status}


# ----------------------------------------------------------------
# lookup_case
# ----------------------------------------------------------------

async def _lookup_case(phone: str, action: dict, client_data: Any, conv: Any) -> dict:
    cid = getattr(client_data, "client_id", "") or ""
    spec = first_connected_of_kind(client_data, "legal")
    provider = provider_for(client_data, spec.id) if spec else None
    if provider is None or not hasattr(provider, "lookup_cases"):
        return {"executed": False, "status": "no_provider"}
    doc = _digits(action.get("cpf_cnpj") or action.get("cpf"))
    if len(doc) not in (11, 14):
        return {"executed": False, "status": "missing_document"}

    res = await provider.lookup_cases(doc)
    status = res.get("status")
    if status == "unsupported":
        return {"executed": False, "status": "unsupported"}
    if status == "not_found":
        text = "Não achei nenhum processo vinculado a esse CPF aqui. Pode conferir os números? Se preferir, me passa o número do processo."
        marker = f"[PROCESSO CONSULTADO {spec.id}: CPF não encontrado. SE o lead corrigir o CPF, emita lookup_case de novo.]"
    elif status != "ok":
        log.warning(f"lookup_case | erro | client={cid} | {res}")
        return {"executed": False, "status": status or "error", "detail": res.get("detail", "")}
    else:
        cases = [c for c in (res.get("cases") or []) if isinstance(c, dict)]
        if not cases:
            text = "Esse CPF está cadastrado, mas ainda não tem processo em andamento vinculado a ele."
            marker = f"[PROCESSO CONSULTADO {spec.id}: cadastro sem processo.]"
        else:
            lines = []
            for c in cases[:4]:
                title = str(c.get("title") or c.get("number") or "Processo")[:80]
                stage = str(c.get("stage") or "")
                mov = str(c.get("last_movement") or "")[:160]
                when = _date_br(c.get("last_update", ""))
                item = f"• {title}"
                if stage:
                    item += f" ({stage})"
                if mov:
                    item += f"\n  Último andamento{(' em ' + when) if when else ''}: {mov}"
                lines.append(item)
            text = "Aqui está o que consta no sistema:\n\n" + "\n".join(lines) + "\n\nQualquer novidade a equipe te avisa por aqui."
            marker = (
                f"[PROCESSO CONSULTADO {spec.id}: {len(cases)} processo(s), andamento JÁ informado acima. "
                "NÃO interprete o andamento nem preveja resultado; SE o lead quiser falar com o advogado, use handoff_to_human.]"
            )
    sent = await _send(phone, text, cid, conv)
    if sent:
        _record(conv, marker)
        await _save(conv)
    return {"executed": sent, "status": status}


__all__ = ["ACTION_TYPES", "action_lines", "handle"]
