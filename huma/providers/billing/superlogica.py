# ================================================================
# huma/providers/billing/superlogica.py: adapter Superlógica (token colado)
#
# BillingProvider contra a API v2 da Superlógica (Postman publicado, lido em
# 2026-11-06, research_notes/integracoes/financeiro.md). Dois produtos com
# caminhos diferentes atrás do MESMO par de tokens:
#   - Assinaturas  https://api.superlogica.net/v2/financeiro/   cliente -> cobranças
#   - Condomínios  https://api.superlogica.net/v2/condor/       CPF -> unidade(s) -> cobranças
#
# Sem OAuth: o dono gera o par em menu do usuário > Todos os usuários >
# Aplicativos > Novo App Token e cola os dois. Credencial mora em
# clients.integrations["superlogica"] (huma.integrations.creds_for):
#   {"app_token", "access_token", "produto": "assinaturas"|"condominios",
#    "licenca": "", "meta": {"renovar_ate": "AAAA-MM-DD", ...}}
# Os tokens valem EXATAMENTE 1 ano: `connected_at` (gravado pela rota) +
# meta["renovar_ate"] deixam o Cockpit avisar; a API não avisa.
#
# Endpoints usados:
#   Assinaturas: GET clientes?pesquisa=<cpf>&status=2
#                GET cobranca?CLIENTES[0]=<id>&status=pendentes&semFiltroPorData=1
#   Condomínios: GET condominios/get?id=-1 (validação)
#                GET unidades/index?idCondominio=-1&pesquisa=<cpf>&exibirDadosDosContatos=1&exibirInadimplencia=1
#                GET cobranca/index?idCondominio=<id>&UNIDADES[0]=<id>&status=pendentes&dtInicio=&dtFim=
#
# Datas da API em MM/DD/AAAA (convertidas pra AAAA-MM-DD na borda); valores
# em string com ponto decimal (convertidos pra centavos). Case sensitive nos
# parâmetros (`CLIENTES[0]`, `UNIDADES[0]`). Não existe status "vencidas":
# vencida = pendente com vencimento anterior a hoje (Brasília). Pix copia e
# cola: NÃO ENCONTRADO NA DOC em nenhum dos dois produtos (pix_code fica "").
# A linha digitável (link_2via_json -> linhadigitavel) exigiria uma chamada
# por cobrança e a conversa só usa o link: barcode fica "" de propósito.
#
# Rate limit: NÃO ENCONTRADO NA DOC (só o bloqueio de itensPorPagina > 200).
# Sem webhook: o app Webhooks é à parte no Assinaturas e não existe no
# Condomínios; a confirmação de pagamento fica pra uma próxima etapa.
#
# NÃO validado com API real: formato exato da resposta de erro (status >=
# 300 com `msg`), se `pesquisa` casa CPF com pontuação, campos de contato
# em unidades/index e o comportamento de idCondominio=-1 em cobranca/index.
# ================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from huma.providers.billing.base import BillingProvider
from huma.utils.logger import get_logger

log = get_logger("superlogica")

INTEGRATION_ID = "superlogica"
PRODUTOS: tuple[str, ...] = ("assinaturas", "condominios")
_DEFAULT_TIMEOUT = 15.0
_MAX_ENTITIES = 3
_BRT = timezone(timedelta(hours=-3))
_TOKEN_DAYS = 365


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def to_cents(value: Any) -> int:
    """'199.00' | 199 | '1.234,56' -> centavos inteiros. Nunca levanta."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    s = str(value).strip().replace("R$", "").strip()
    if not s:
        return 0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def us_to_iso(value: Any) -> str:
    """'11/10/2026' | '11/10/2026 00:00:00' (MM/DD/AAAA) -> '2026-11-10'. Vazio se não parece data."""
    s = str(value or "").strip()[:10]
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return ""


def iso_to_us(day: Any) -> str:
    """date | 'AAAA-MM-DD' -> 'MM/DD/AAAA' (formato que a API exige)."""
    s = day.isoformat() if hasattr(day, "isoformat") else str(day or "")[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return f"{s[5:7]}/{s[8:10]}/{s[0:4]}"
    return ""


def _today_brt():
    return datetime.now(_BRT).date()


def _rows(body: Any) -> list[dict]:
    """A API devolve array; alguns retornos vêm embrulhados em {"data": [...]}."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for key in ("data", "itens", "items"):
            if isinstance(body.get(key), list):
                return [r for r in body[key] if isinstance(r, dict)]
    return []


def assinatura_to_charge(row: dict, today: str = "") -> dict:
    """Linha de /financeiro/cobranca -> item de `charges[]` do contrato. (puro)"""
    today = today or _today_brt().isoformat()
    due = us_to_iso(row.get("dt_vencimento_recb"))
    desc = str(row.get("st_label_recb") or row.get("st_observacaoexterna_recb") or "").strip()
    forma = str(row.get("ar_nomeformas_calc") or "").strip()
    if not desc:
        desc = f"{forma or 'Cobrança'} #{row.get('id_recebimento_recb', '')}".strip()
    overdue = bool(due and due < today)
    return {
        "id": str(row.get("id_recebimento_recb") or ""),
        "description": desc[:80],
        "value_cents": to_cents(row.get("vl_total_recb")),
        "due_date": due,
        "overdue": overdue,
        "status": "overdue" if overdue else "pending",
        "link": str(row.get("link_2via") or ""),
        "pix_code": "",
        "barcode": "",
    }


def condominio_to_charge(row: dict, today: str = "", unit_label: str = "") -> dict:
    """Linha de /condor/cobranca/index -> item de `charges[]` do contrato. (puro)"""
    today = today or _today_brt().isoformat()
    due = us_to_iso(row.get("dt_vencimento_recb"))
    cond = str(row.get("st_nome_cond") or "").strip()
    unidade = str(row.get("st_unidade_uni") or "").strip()
    bloco = str(row.get("st_bloco_uni") or "").strip()
    label = unit_label or (f"{cond}, unidade {unidade}" + (f" bloco {bloco}" if bloco else "")).strip(", ")
    desc = label or f"Cobrança #{row.get('id_recebimento_recb', '')}"
    overdue = bool(due and due < today)
    return {
        "id": str(row.get("id_recebimento_recb") or ""),
        "description": desc[:80],
        "value_cents": to_cents(row.get("vl_total_recb")),
        "due_date": due,
        "overdue": overdue,
        "status": "overdue" if overdue else "pending",
        "link": str(row.get("link_segundavia") or ""),
        "pix_code": "",
        "barcode": "",
    }


def _is_pending(row: dict) -> bool:
    """fl_status_recb: 0 pendente (válida); 3 liquidada; 1 cancelada; 2 cartório. Vazio = confia no filtro."""
    raw = str(row.get("fl_status_recb") if row.get("fl_status_recb") is not None else "").strip()
    return raw in ("", "0")


class SuperlogicaAdapter(BillingProvider):
    """Cobranças em aberto por CPF/CNPJ na Superlógica (Assinaturas ou Condomínios). Nunca levanta."""

    display_name = "Superlógica"

    def __init__(self, identity: Any = None, app_token: str = "", access_token: str = "",
                 produto: str = "", licenca: str = "") -> None:
        creds: dict = {}
        if identity is not None:
            from huma.integrations import creds_for
            creds = creds_for(identity, INTEGRATION_ID)
        self.identity = identity
        self.app_token = (app_token or str(creds.get("app_token") or "")).strip()
        self.access_token = (access_token or str(creds.get("access_token") or "")).strip()
        produto = (produto or str(creds.get("produto") or "") or "assinaturas").strip().lower()
        self.produto = produto if produto in PRODUTOS else "assinaturas"
        self.licenca = (licenca or str(creds.get("licenca") or "")).strip()
        self.client_id = str(getattr(identity, "client_id", "") or "")

    # ------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------

    def _base_url(self) -> str:
        from huma.config import SUPERLOGICA_API_BASE_URL
        base = (SUPERLOGICA_API_BASE_URL or "https://api.superlogica.net/v2").rstrip("/")
        return f"{base}/{'condor' if self.produto == 'condominios' else 'financeiro'}"

    def _headers(self) -> dict:
        return {
            "app_token": self.app_token,
            "access_token": self.access_token,
            "Accept": "application/json",
            "User-Agent": "HUMA IA",
        }

    async def _request(self, method: str, path: str, params: dict | None = None) -> tuple[int, Any]:
        """(status, json). 0 = rede. Nunca levanta; nunca loga os tokens."""
        url = f"{self._base_url()}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as http:
                resp = await http.request(method, url, params=params, headers=self._headers())
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = None
            if resp.status_code >= 300:
                log.warning(f"Superlógica | HTTP {resp.status_code} | {method} {path} | client={self.client_id} | {resp.text[:160]}")
            return resp.status_code, body
        except httpx.TimeoutException:
            log.error(f"Superlógica | timeout | {method} {path} | client={self.client_id}")
            return 0, None
        except httpx.HTTPError as e:
            log.error(f"Superlógica | erro de rede | {method} {path} | client={self.client_id} | {type(e).__name__}: {e}")
            return 0, None

    # ------------------------------------------------------------
    # Validação
    # ------------------------------------------------------------

    async def check_credentials(self) -> dict:
        """
        Leitura mínima no produto escolhido. {"status": "ok", "label", "renovar_ate"} ou
        {"status": "error", "user_message": <frase em PT>}.
        """
        if not self.app_token or not self.access_token:
            return {"status": "error", "detail": "no_credentials",
                    "user_message": "Cole o App Token e o Access Token da Superlógica."}
        if self.produto == "condominios":
            st, body = await self._request("GET", "condominios/get", params={
                "id": -1, "somenteCondominiosAtivos": 1, "ignorarCondominioModelo": 1, "apenasColunasPrincipais": 1,
            })
        else:
            st, body = await self._request("GET", "clientes", params={"itensPorPagina": 1, "status": 2})
        if st == 0:
            return {"status": "error", "detail": "network",
                    "user_message": "Não consegui falar com a Superlógica agora. Tente de novo em instantes."}
        if st in (401, 403):
            return {"status": "error", "detail": "unauthorized",
                    "user_message": "A Superlógica recusou os tokens. Confira em menu do usuário > Todos os usuários > "
                                    "Aplicativos se o App Token e o Access Token são do mesmo app e se ainda estão na validade (1 ano)."}
        if st >= 300:
            return {"status": "error", "detail": f"http_{st}",
                    "user_message": "A Superlógica devolveu um erro ao validar os tokens. Confira o produto escolhido "
                                    "(Assinaturas ou Condomínios) e tente de novo."}
        rows = _rows(body)
        label = ""
        if self.produto == "condominios":
            names = [str(r.get("st_fantasia_cond") or r.get("st_nome_cond") or "") for r in rows]
            names = [n for n in names if n]
            label = f"{len(names)} condomínio(s)" if len(names) != 1 else names[0]
        renovar = (_today_brt() + timedelta(days=_TOKEN_DAYS)).isoformat()
        log.info(f"Superlógica | tokens válidos | client={self.client_id} | produto={self.produto} | label={label or '-'}")
        return {"status": "ok", "label": label, "renovar_ate": renovar, "condominios": len(rows) if self.produto == "condominios" else 0}

    # ------------------------------------------------------------
    # Contrato
    # ------------------------------------------------------------

    async def list_open_charges(self, cpf_cnpj: str, limit: int = 10) -> dict:
        """Cobranças pendentes do CPF/CNPJ, vencidas primeiro. Ver BillingProvider."""
        if not self.app_token or not self.access_token:
            return {"status": "no_credentials"}
        doc = _digits(cpf_cnpj)
        if len(doc) not in (11, 14):
            return {"status": "error", "detail": "bad_document"}
        if self.produto == "condominios":
            return await self._charges_condominios(doc, limit)
        return await self._charges_assinaturas(doc, limit)

    async def _charges_assinaturas(self, doc: str, limit: int) -> dict:
        st, body = await self._request("GET", "clientes", params={"pesquisa": doc, "status": 2, "itensPorPagina": 10})
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st >= 300:
            return {"status": "error", "detail": f"http_{st}"}
        clients = [r for r in _rows(body) if r.get("id_sacado_sac")]
        exact = [r for r in clients if _digits(r.get("st_cgc_sac")) == doc]
        clients = exact or clients
        if not clients:
            log.info(f"Superlógica | CPF não encontrado | client={self.client_id} | produto=assinaturas")
            return {"status": "not_found"}

        today = _today_brt().isoformat()
        charges: list[dict] = []
        customer_name = ""
        for cli in clients[:_MAX_ENTITIES]:
            customer_name = customer_name or str(cli.get("st_nome_sac") or cli.get("st_nomeref_sac") or "")
            st, body = await self._request("GET", "cobranca", params={
                "CLIENTES[0]": cli.get("id_sacado_sac"), "status": "pendentes", "semFiltroPorData": 1,
                "apenasColunasPrincipais": 1, "itensPorPagina": 50,
            })
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            if st >= 300:
                return {"status": "error", "detail": f"http_{st}"}
            for row in _rows(body):
                if row.get("id_recebimento_recb") and _is_pending(row):
                    charges.append(assinatura_to_charge(row, today))
        charges.sort(key=lambda c: (not c["overdue"], c["due_date"] or "9999"))
        log.info(f"Superlógica | cobranças consultadas | client={self.client_id} | produto=assinaturas | abertas={len(charges)}")
        return {"status": "ok", "customer_name": customer_name, "charges": charges[: max(1, int(limit or 10))]}

    async def _charges_condominios(self, doc: str, limit: int) -> dict:
        st, body = await self._request("GET", "unidades/index", params={
            "idCondominio": -1, "pesquisa": doc, "exibirDadosDosContatos": 1, "exibirInadimplencia": 1,
            "itensPorPagina": 10,
        })
        if st == 0:
            return {"status": "error", "detail": "network_error"}
        if st >= 300:
            return {"status": "error", "detail": f"http_{st}"}
        units = [r for r in _rows(body) if r.get("id_unidade_uni") and r.get("id_condominio_cond")]
        if not units:
            log.info(f"Superlógica | CPF não encontrado | client={self.client_id} | produto=condominios")
            return {"status": "not_found"}

        today = _today_brt()
        dt_ini = iso_to_us(today - timedelta(days=365))
        dt_fim = iso_to_us(today + timedelta(days=60))
        charges: list[dict] = []
        customer_name = ""
        for unit in units[:_MAX_ENTITIES]:
            customer_name = customer_name or str(unit.get("st_nome_con") or unit.get("st_sacado_uni") or "")
            cond = str(unit.get("st_nome_cond") or "").strip()
            unidade = str(unit.get("st_unidade_uni") or "").strip()
            bloco = str(unit.get("st_bloco_uni") or "").strip()
            label = (f"{cond}, unidade {unidade}" + (f" bloco {bloco}" if bloco else "")).strip(", ")
            st, body = await self._request("GET", "cobranca/index", params={
                "idCondominio": unit.get("id_condominio_cond"), "UNIDADES[0]": unit.get("id_unidade_uni"),
                "status": "pendentes", "dtInicio": dt_ini, "dtFim": dt_fim,
                "apenasColunasPrincipais": 1, "itensPorPagina": 50,
            })
            if st == 0:
                return {"status": "error", "detail": "network_error"}
            if st >= 300:
                return {"status": "error", "detail": f"http_{st}"}
            for row in _rows(body):
                if row.get("id_recebimento_recb") and _is_pending(row):
                    charges.append(condominio_to_charge(row, today.isoformat(), label))
        charges.sort(key=lambda c: (not c["overdue"], c["due_date"] or "9999"))
        log.info(f"Superlógica | cobranças consultadas | client={self.client_id} | produto=condominios | unidades={len(units)} | abertas={len(charges)}")
        return {"status": "ok", "customer_name": customer_name, "charges": charges[: max(1, int(limit or 10))]}


__all__ = ["SuperlogicaAdapter", "INTEGRATION_ID", "PRODUTOS", "assinatura_to_charge", "condominio_to_charge",
           "to_cents", "us_to_iso", "iso_to_us"]
