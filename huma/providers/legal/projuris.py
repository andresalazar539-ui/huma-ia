# ================================================================
# huma/providers/legal/projuris.py: Adapter Projuris ADV (sistema jurídico)
#
# LegalProvider contra a API REST do Projuris ADV
# (https://api.projurisadv.com.br/adv-service). Credencial: Client ID +
# Client Secret (OAuth2 client_credentials) que o administrador copia em
# Perfil > Dados da Conta > aba Integração API, depois de DEFINIR O USUÁRIO
# DA INTEGRAÇÃO na mesma aba (sem isso a API responde 401 mesmo com token
# válido). Moram em `clients.integrations["projuris"]`.
#
# Token: `POST https://apigw.projurisadv.com.br/auth/token` (form
# `grant_type=client_credentials`), vale 8 h (`expires_in` 28800); cache em
# memória do processo por Client ID. Chamadas com `Authorization: Bearer`;
# em 401 persistente a ficha manda tentar sem o prefixo (páginas antigas da
# doc), feito como última tentativa dentro de `_request`.
#
# Endpoints (ficha research_notes/integracoes/juridico.md, 2026-11-06):
#   GET  /pessoa/consulta?filtro-geral=&quan-registros=   dedup por telefone/CPF (e validação)
#   POST /pessoa                              pessoaDadosBasicosWs (tipoPessoa obrigatório)
#   POST /atendimento                         AtendimentoDadosBasicosWs (pré-cadastro = atendimento)
#   GET  /atendimento/{codigo} + PUT /atendimento   anotação: acrescenta em `observacao`
#   POST /processo/consulta                   cpfCnpjCliente + flSouResponsavel/flEstouEnvolvido (obrigatórios)
#   GET  /processo/{codigo}/andamento/consulta?quan-registros=1   último andamento (dataAndamento em epoch ms)
#
# Limite: 480 req/min (FAQ antiga); a nova diz "varia por escritório" com
# cache de 10 min em consulta repetida. Não aperta: sem cache local.
#
# Webhook: existe no Projuris (cadastro/atualização/exclusão de PROCESSO,
# sem evento de andamento nem de atendimento), mas o negócio da HUMA é o
# ATENDIMENTO, então nenhum evento mapeia pro crm_deal_id: `parse_outcome`
# devolve "unknown" e o spec não registra on_webhook.
#
# NÃO validado com API real: valor do enum `tipoPessoa` ("FISICA"/"JURIDICA"
# assumido), forma da resposta de POST /pessoa e POST /atendimento (código
# cru ou {codigoPessoa|codigoAtendimento|codigo}), nome da lista em
# GET /pessoa/consulta (`pessoaConsultaWs` assumido por analogia com
# `processoConsultaWs`), campos de PUT /atendimento, e o fallback sem "Bearer".
# ================================================================

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from huma.providers.legal.base import LegalProvider
from huma.utils.logger import get_logger

if TYPE_CHECKING:
    from huma.models.schemas import ClientIdentity

log = get_logger("projuris")

INTEGRATION_ID = "projuris"
_DEFAULT_TIMEOUT = 15.0
_ORIGIN_TAG = "Pré-atendimento feito pela HUMA IA"
_TOKEN_SAFETY_SECONDS = 120     # renova 2 min antes de vencer
_MAX_CASES_WITH_MOVEMENT = 3    # 1 POST /processo/consulta + até 3 GET de andamento
_BRT = timezone(timedelta(hours=-3))

# Cache do access_token por Client ID: (vence_em_monotonic, token)
_TOKEN_CACHE: dict[str, tuple[float, str]] = {}

_DOC_RE = re.compile(r"\b\d{2,3}\.?\d{3}\.?\d{3}[/-]?\d{2,4}-?\d{0,2}\b")


def _auth_url() -> str:
    from huma.config import PROJURIS_AUTH_URL
    return PROJURIS_AUTH_URL or "https://apigw.projurisadv.com.br/auth/token"


def _base_url() -> str:
    from huma.config import PROJURIS_API_BASE_URL
    return (PROJURIS_API_BASE_URL or "https://api.projurisadv.com.br/adv-service").rstrip("/")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _brl(cents: int) -> str:
    v = f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {v}"


def national_phone(phone: str) -> str:
    """
    Telefone como o escritório digita no Projuris: DDD + número, sem o 55
    (o campo é texto livre; buscar com o 55 na frente não casa com o que foi
    cadastrado à mão). Sintético (ig:/web:) ou fora do padrão = "".
    """
    raw = str(phone or "")
    if raw.startswith(("ig:", "web:")):
        return ""
    d = _digits(raw.split("@")[0])
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    return d if len(d) in (10, 11) else ""


def document_from_lead(lead: dict) -> str:
    """CPF (11) ou CNPJ (14) só dígitos, das chaves do lead ou dos fatos. "" se não há."""
    for key in ("cpf", "cpf_cnpj", "identification", "document", "cnpj"):
        d = _digits((lead or {}).get(key))
        if len(d) in (11, 14):
            return d
    for fact in (lead or {}).get("facts") or []:
        if not isinstance(fact, str):
            continue
        for m in _DOC_RE.findall(fact):
            d = _digits(m)
            if len(d) in (11, 14):
                return d
    return ""


def epoch_ms_to_date(value: Any) -> str:
    """`dataAndamento` (epoch em ms, ex. 1544580000000) ou texto de data → "AAAA-MM-DD"; "" se não entende."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            ms = float(value)
            if ms > 10_000_000_000:  # está em milissegundos
                ms = ms / 1000.0
            return datetime.fromtimestamp(ms, tz=_BRT).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(value)[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    return ""


def _first_list(body: Any, *keys: str) -> list[dict]:
    """Lista de registros: pelas chaves conhecidas, senão a primeira lista do dict, senão a lista crua."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if not isinstance(body, dict):
        return []
    for k in keys:
        v = body.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    for v in body.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return [x for x in v if isinstance(x, dict)]
    return []


def _code_from(body: Any, *keys: str) -> str:
    """Código devolvido por um POST: número cru, string ou dict com uma das chaves."""
    if isinstance(body, (int, float)):
        return str(int(body))
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, dict):
        for k in keys:
            v = body.get(k)
            if v not in (None, ""):
                return str(v)
        for k in ("codigo", "id"):
            v = body.get(k)
            if v not in (None, ""):
                return str(v)
    return ""


class ProjurisAdapter(LegalProvider):
    """Adapter Projuris ADV (Client ID + Client Secret por cliente). Nenhum método levanta exceção."""

    def __init__(
        self,
        identity: "ClientIdentity | None" = None,
        client_id: str = "",
        client_secret: str = "",
        auth_url: str = "",
        base_url: str = "",
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.identity = identity
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        # `api_client_id` é o Client ID do Projuris (não confundir com o client_id da HUMA).
        self.api_client_id = str(creds.get("client_id") or client_id or "")
        self.api_client_secret = str(creds.get("client_secret") or client_secret or "")
        self.auth_url = auth_url or _auth_url()
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout

    @property
    def _has_creds(self) -> bool:
        return bool(self.api_client_id and self.api_client_secret)

    # ── token ────────────────────────────────────────────────────

    async def _get_token(self, force: bool = False) -> tuple[str, int]:
        """
        (access_token, status HTTP do /auth/token). Reusa o token em memória
        até 2 min antes de vencer. Token vazio + status explica a falha
        (0 = rede, 400/401 = credencial recusada).
        """
        if not self._has_creds:
            return "", 401
        now = time.monotonic()
        if not force:
            hit = _TOKEN_CACHE.get(self.api_client_id)
            if hit and hit[0] > now:
                return hit[1], 200
        form = {
            "grant_type": "client_credentials",
            "client_id": self.api_client_id,
            "client_secret": self.api_client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request("POST", self.auth_url, data=form, headers=headers)
        except httpx.TimeoutException:
            log.error("Timeout | service=projuris | POST auth/token")
            return "", 0
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=projuris | POST auth/token | {type(e).__name__}: {e}")
            return "", 0
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = None
        if resp.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            log.warning(f"Projuris | token recusado | HTTP {resp.status_code}")
            return "", resp.status_code or 0
        token = str(body["access_token"])
        try:
            ttl = float(body.get("expires_in") or 28800)
        except (TypeError, ValueError):
            ttl = 28800.0
        _TOKEN_CACHE[self.api_client_id] = (now + max(ttl - _TOKEN_SAFETY_SECONDS, 60.0), token)
        log.info(f"Projuris | token obtido | validade={int(ttl)}s")
        return token, 200

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self, method: str, path: str, params: dict | None = None, json_body: Any = None,
    ) -> tuple[int, Any]:
        """
        (status, json). 0 = falha de rede. Em 401 renova o token e repete uma
        vez; se seguir 401, tenta sem o prefixo "Bearer" (doc antiga). Nunca levanta.
        """
        if not self._has_creds:
            return 401, None
        token, tst = await self._get_token()
        if not token:
            return (0 if tst == 0 else 401), None
        st, body = await self._call(method, path, params, json_body, f"Bearer {token}")
        if st != 401:
            return st, body
        token, tst = await self._get_token(force=True)
        if not token:
            return (0 if tst == 0 else 401), None
        st, body = await self._call(method, path, params, json_body, f"Bearer {token}")
        if st != 401:
            return st, body
        return await self._call(method, path, params, json_body, token)

    async def _call(
        self, method: str, path: str, params: dict | None, json_body: Any, authorization: str,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": authorization, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.request(method, url, params=params, json=json_body, headers=headers)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = resp.text.strip() if resp.content else None
            if resp.status_code >= 400:
                log.warning(f"Projuris HTTP {resp.status_code} | {method} {path} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Timeout | service=projuris | {method} {path}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"HTTP erro | service=projuris | {method} {path} | {type(e).__name__}: {e}")
            return 0, None

    @staticmethod
    def _err(st: int) -> dict:
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized"}
        if st == 429:
            return {"status": "error", "detail": "rate_limited"}
        return {"status": "error", "detail": f"http_{st}"}

    # ── conta / validação ────────────────────────────────────────

    async def check_credentials(self) -> dict:
        """
        Token por client_credentials + `GET /pessoa/consulta?quan-registros=1`.
        401 depois do token = usuário da integração não definido na aba.

        Returns:
            {"status": "ok", "label", "meta"} ou {"status": "error", "detail", "user_message"}.
        """
        if not self._has_creds:
            return {
                "status": "error", "detail": "no_credentials",
                "user_message": "Cole o Client ID e o Client Secret do Projuris ADV.",
            }
        token, tst = await self._get_token(force=True)
        if not token:
            if tst == 0:
                return {
                    "status": "error", "detail": "network_error",
                    "user_message": "Não consegui falar com o Projuris agora. Tente de novo em instantes.",
                }
            return {
                "status": "error", "detail": "unauthorized",
                "user_message": "O Projuris não aceitou o Client ID e o Client Secret. Confira em "
                                "Perfil > Dados da Conta > Integração API e cole de novo.",
            }
        st, body = await self._request("GET", "/pessoa/consulta", params={"quan-registros": 1, "pagina": 1})
        if st in (401, 403):
            return {
                "status": "error", "detail": "integration_user_missing",
                "user_message": "Defina o usuário da integração em Perfil > Dados da Conta > "
                                "Integração API e tente de novo.",
            }
        if st == 0:
            return {
                "status": "error", "detail": "network_error",
                "user_message": "Não consegui falar com o Projuris agora. Tente de novo em instantes.",
            }
        if st == 429:
            return {
                "status": "error", "detail": "rate_limited",
                "user_message": "O Projuris está limitando as consultas agora. Espere um minuto e tente de novo.",
            }
        if st != 200:
            return {"status": "error", "detail": f"http_{st}", "user_message": "O Projuris respondeu com erro. Tente de novo."}
        total = body.get("totalRegistros") if isinstance(body, dict) else None
        meta = {
            "auth": "client_credentials",
            "validated_endpoint": "pessoa/consulta",
            "pessoas_total": int(total) if isinstance(total, (int, float)) else None,
        }
        label = f"Projuris ADV ({self.api_client_id})"
        log.info(f"Projuris | credenciais ok | client_id={self.api_client_id[:6]}*** | pessoas={meta['pessoas_total']}")
        return {"status": "ok", "label": label, "meta": meta}

    # ── upsert_lead ──────────────────────────────────────────────

    async def upsert_lead(self, identity: "ClientIdentity", lead: dict) -> dict:
        if not self._has_creds:
            return {"status": "no_credentials"}
        cid = getattr(identity, "client_id", "") or ""
        phone = national_phone((lead or {}).get("phone") or "")
        email = str((lead or {}).get("email") or "").strip()
        doc = document_from_lead(lead or {})
        name = str((lead or {}).get("name") or "").strip() or (phone and f"Lead WhatsApp {phone}") or "Lead WhatsApp"

        existing = await self._find_pessoa(phone, doc, email)
        if existing:
            log.info(f"Projuris | upsert_lead | reusou pessoa | client={cid} | codigo={existing}")
            return {"status": "ok", "crm_contact_id": existing}

        facts = [f for f in ((lead or {}).get("facts") or []) if isinstance(f, str)]
        pessoa: dict = {
            "tipoPessoa": "JURIDICA" if len(doc) == 14 else "FISICA",
            "nomePessoa": name[:200],
            "observacaoGeral": (_ORIGIN_TAG + ("\n" + "\n".join(facts) if facts else ""))[:4000],
            "codigoExterno": f"huma:{_digits((lead or {}).get('phone')) or phone or 'lead'}"[:100],
        }
        if len(doc) == 14:
            pessoa["razaoSocial"] = name[:200]
        if doc:
            pessoa["cpfCnpj"] = doc
        else:
            pessoa["flagSemCpfCnpj"] = True
            pessoa["justificativaSemCpfCnpj"] = "Pré-atendimento pelo WhatsApp (HUMA IA)"
        if email:
            pessoa["email"] = email
        if phone:
            pessoa["telefone"] = phone

        st, resp = await self._request("POST", "/pessoa", json_body=pessoa)
        if st not in (200, 201):
            return self._err(st)
        code = _code_from(resp, "codigoPessoa", "codigo")
        if not code:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Projuris | upsert_lead | pessoa criada | client={cid} | codigo={code}")
        return {"status": "ok", "crm_contact_id": code}

    async def _find_pessoa(self, phone: str, doc: str, email: str = "") -> str:
        """`GET /pessoa/consulta?filtro-geral=` por telefone, CPF/CNPJ e e-mail. "" se não achou."""
        for term in (phone, doc, email):
            if not term:
                continue
            st, body = await self._request(
                "GET", "/pessoa/consulta", params={"filtro-geral": term, "quan-registros": 5, "pagina": 1},
            )
            if st != 200:
                continue
            for item in _first_list(body, "pessoaConsultaWs", "pessoas", "content"):
                code = _code_from(item, "codigoPessoa")
                if code:
                    return code
        return ""

    # ── upsert_deal ──────────────────────────────────────────────

    async def upsert_deal(self, identity: "ClientIdentity", deal: dict) -> dict:
        """
        Pré-cadastro = ATENDIMENTO (`POST /atendimento`): `nomeAssunto` = título,
        `observacao` = resumo, `codigoExterno` = pessoa da HUMA. Com
        `crm_deal_id` já gravado devolve o mesmo código sem chamar a API.
        O vínculo pessoa↔atendimento (`envolvidos[envolvidoEntidadeWs]`) não
        tem exemplo na doc; a pessoa vai em `nomeCliente` e no `codigoExterno`.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        cid = getattr(identity, "client_id", "") or ""
        deal_id = str((deal or {}).get("crm_deal_id") or "").strip()
        if deal_id:
            return {"status": "ok", "crm_deal_id": deal_id}
        contact_id = str((deal or {}).get("crm_contact_id") or "").strip()
        title = str((deal or {}).get("title") or "Pré-atendimento via HUMA").strip()
        value_cents = int((deal or {}).get("value_cents") or 0)
        observacao = f"{_ORIGIN_TAG}\n{title}"
        if value_cents > 0:
            observacao += f"\nValor estimado: {_brl(value_cents)}"
        body: dict = {
            "nomeAssunto": title[:200],
            "observacao": observacao[:4000],
            "dataAtendimento": datetime.now(_BRT).strftime("%Y-%m-%d"),
            "habilitado": True,
        }
        if contact_id:
            body["codigoExterno"] = f"huma:pessoa:{contact_id}"[:100]
            body["nomeCliente"] = title.split("·")[0].strip()[:200] or title[:200]
        st, resp = await self._request("POST", "/atendimento", json_body=body)
        if st not in (200, 201):
            return self._err(st)
        code = _code_from(resp, "codigoAtendimento", "codigo")
        if not code:
            return {"status": "error", "detail": "no_id_in_response"}
        log.info(f"Projuris | upsert_deal | atendimento criado | client={cid} | codigo={code}")
        return {"status": "ok", "crm_deal_id": code}

    # ── log_activity ─────────────────────────────────────────────

    async def log_activity(self, identity: "ClientIdentity", activity: dict) -> dict:
        """
        Escolha: ACRESCENTA a anotação em `observacao` do atendimento
        (`GET /atendimento/{codigo}` → `PUT /atendimento` com o mesmo registro),
        pra manter UM caso por lead. Se o PUT for recusado, cria um atendimento
        novo (`POST /atendimento`) com a anotação, pra nunca perder o resumo.
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        deal_id = str((activity or {}).get("crm_deal_id") or "").strip()
        if not deal_id:
            return {"status": "error", "detail": "missing_deal_id"}
        kind = str((activity or {}).get("kind") or "note")
        summary = str((activity or {}).get("summary") or "").strip()
        value_cents = int((activity or {}).get("value_cents") or 0)
        when = str((activity or {}).get("when") or "").strip()
        stamp = datetime.now(_BRT).strftime("%d/%m/%Y %H:%M")

        if kind == "meeting":
            quando = ""
            if when:
                try:
                    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
                    parsed = parsed if parsed.tzinfo is None else parsed.astimezone(_BRT)
                    quando = parsed.strftime(" em %d/%m/%Y às %H:%M")
                except ValueError:
                    quando = f" em {when}"
            line = f"[{stamp}] Reunião agendada pela HUMA IA{quando}: {summary or 'atendimento'}"
        else:
            line = f"[{stamp}] {_ORIGIN_TAG}: {summary}" if summary else f"[{stamp}] {_ORIGIN_TAG}"
        if value_cents > 0:
            line += f" (valor estimado {_brl(value_cents)})"

        st, current = await self._request("GET", f"/atendimento/{deal_id}")
        if st == 200 and isinstance(current, dict):
            updated = dict(current)
            updated["codigoAtendimento"] = current.get("codigoAtendimento") or deal_id
            prev = str(current.get("observacao") or "")
            updated["observacao"] = (prev + ("\n" if prev else "") + line)[:4000]
            st2, _ = await self._request("PUT", "/atendimento", json_body=updated)
            if st2 in (200, 201, 204):
                return {"status": "ok"}
            log.warning(f"Projuris | log_activity | PUT recusado ({st2}), criando atendimento novo | codigo={deal_id}")
        elif st in (0, 401, 403, 429):
            return self._err(st)

        fallback = {
            "nomeAssunto": f"Anotação HUMA: {(summary or 'atendimento')[:150]}",
            "observacao": line[:4000],
            "dataAtendimento": datetime.now(_BRT).strftime("%Y-%m-%d"),
            "codigoExterno": f"huma:nota:{deal_id}"[:100],
            "habilitado": True,
        }
        st3, _ = await self._request("POST", "/atendimento", json_body=fallback)
        if st3 not in (200, 201):
            return self._err(st3)
        return {"status": "ok"}

    # ── lookup_cases ─────────────────────────────────────────────

    async def lookup_cases(self, cpf_cnpj: str, limit: int = 5) -> dict:
        """
        `POST /processo/consulta` com `cpfCnpjCliente` (+ os booleanos
        obrigatórios `flSouResponsavel`/`flEstouEnvolvido` = false) e, pros 3
        primeiros, `GET /processo/{codigo}/andamento/consulta?quan-registros=1`.
        Sem processo, `GET /pessoa/consulta?filtro-geral=<cpf>` separa "não
        cadastrado" de "cadastrado sem processo".
        """
        if not self._has_creds:
            return {"status": "no_credentials"}
        doc = _digits(cpf_cnpj)
        if len(doc) not in (11, 14):
            return {"status": "not_found"}
        filtro = {"cpfCnpjCliente": doc, "flSouResponsavel": False, "flEstouEnvolvido": False}
        st, body = await self._request(
            "POST", "/processo/consulta", params={"quan-registros": max(1, min(limit, 20)), "pagina": 1}, json_body=filtro,
        )
        if st != 200:
            return self._err(st)
        processos = _first_list(body, "processoConsultaWs", "processos", "content")

        if not processos:
            st2, body2 = await self._request(
                "GET", "/pessoa/consulta", params={"filtro-geral": doc, "quan-registros": 1, "pagina": 1},
            )
            if st2 != 200:
                return self._err(st2)
            pessoas = _first_list(body2, "pessoaConsultaWs", "pessoas", "content")
            if not pessoas:
                return {"status": "not_found"}
            nome = str(pessoas[0].get("nomePessoa") or pessoas[0].get("nome") or "")
            return {"status": "ok", "client_name": nome, "cases": []}

        cases: list[dict] = []
        client_name = ""
        for idx, p in enumerate(processos[:limit]):
            codigo = _code_from(p, "codigoProcesso")
            last_date, last_text, responsible = "", "", ""
            if codigo and idx < _MAX_CASES_WITH_MOVEMENT:
                st3, body3 = await self._request(
                    "GET", f"/processo/{codigo}/andamento/consulta", params={"quan-registros": 1, "pagina": 1},
                )
                andamentos = _first_list(body3, "andamentoWs", "andamentos", "content") if st3 == 200 else []
                if andamentos:
                    a = andamentos[0]
                    last_date = epoch_ms_to_date(a.get("dataAndamento"))
                    last_text = str(a.get("descricaoAndamento") or a.get("nomeTipoAndamento") or "")
                    resp_list = a.get("responsaveis") or []
                    if resp_list and isinstance(resp_list[0], dict):
                        responsible = str(resp_list[0].get("valor") or "")
            if not client_name:
                client_name = str(p.get("nomeCliente") or "")
            number = str(p.get("numeroProcesso") or "")
            title = str(p.get("identificador") or p.get("nomePasta") or p.get("assunto") or "") or number or "Processo"
            if number and number not in title:
                title = f"{title} {number}".strip()
            cases.append({
                "id": codigo,
                "number": number,
                "title": title[:120],
                "area": str(p.get("nomeAssunto") or p.get("assunto") or p.get("nomeTipoJustica") or ""),
                "stage": str(p.get("nomeSituacao") or ""),
                "last_update": last_date,
                "last_movement": " ".join(last_text.split())[:300],
                "responsible": responsible or str(p.get("nomeResponsavel") or ""),
            })
        log.info(f"Projuris | lookup_cases | processos={len(cases)} | doc=***{doc[-3:]}")
        return {"status": "ok", "client_name": client_name, "cases": cases}

    # ── webhook ──────────────────────────────────────────────────

    def parse_outcome(self, payload: dict, headers: dict) -> dict:
        """
        Os eventos do webhook do Projuris são de PROCESSO (cadastro/atualização/
        exclusão); o negócio da HUMA é o ATENDIMENTO, então não há como mapear
        ganho/perdido: devolve sempre "unknown" com o id que vier.
        """
        deal_id = ""
        if isinstance(payload, dict):
            inner = payload.get("payload")
            if isinstance(inner, str):
                try:
                    import json
                    parsed = json.loads(inner)
                    inner = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    inner = {}
            if not isinstance(inner, dict):
                inner = {}
            deal_id = str(
                inner.get("codigoAtendimento") or payload.get("codigoAtendimento")
                or inner.get("codigoProcesso") or payload.get("codigoProcesso") or ""
            )
        return {"crm_deal_id": deal_id, "outcome": "unknown"}


__all__ = ["ProjurisAdapter", "national_phone", "document_from_lead", "epoch_ms_to_date", "INTEGRATION_ID"]
